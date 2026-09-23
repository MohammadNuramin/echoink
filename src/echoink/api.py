"""Local multilingual speech-to-text using the pinned Orukeet ONNX INT8 release."""

import io
import threading
import wave
from pathlib import Path

from .config import Config

MODEL_NAME = "oruk/orukeet"
MODEL_REVISION = "55a984d46f68323301837194ce647c702f55facc"
MODEL_ARCHIVE = "onnx/sherpa-onnx-orukeet-v0.1.0-int8.tar.bz2"
MODEL_SHA256 = "f9191f30178cc9122ce2f023bf9fefafc822028307b0efa4caff645ba3fe8d0a"


def _model_directory() -> Path:
    """Download and safely unpack the pinned, hash-checked ONNX release once."""
    import hashlib
    import shutil
    import tarfile
    import tempfile

    from filelock import FileLock
    from huggingface_hub import hf_hub_download

    cache = Config.get_config_path().parent / "models" / MODEL_REVISION
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / "sherpa-onnx-orukeet-v0.1.0-int8"
    required = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
    with FileLock(str(cache / "download.lock")):
        if (target / ".verified").is_file() and all((target / f).is_file() for f in required):
            return target
        archive = hf_hub_download(MODEL_NAME, MODEL_ARCHIVE, revision=MODEL_REVISION)
        digest = hashlib.sha256()
        with open(archive, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise RuntimeError("Orukeet download failed SHA-256 verification")
        with tempfile.TemporaryDirectory(dir=cache) as staging:
            root = Path(staging).resolve()
            with tarfile.open(archive, "r|bz2") as tar:
                # Reject links, devices, and any path escaping the staging directory.
                for member in tar:
                    dest = (root / member.name).resolve()
                    if not dest.is_relative_to(root) or not (member.isfile() or member.isdir()):
                        raise RuntimeError("Unsafe entry in Orukeet archive")
                    if hasattr(tarfile, "data_filter"):
                        tar.extract(member, root, filter="data")
                    else:  # Python 3.10 before extraction filters were backported
                        tar.extract(member, root)
            extracted = root / target.name
            if not all((extracted / f).is_file() for f in required):
                raise RuntimeError("Orukeet archive is missing required model files")
            shutil.copytree(extracted, target, dirs_exist_ok=True)
            (target / ".verified").write_text(MODEL_SHA256, encoding="ascii")
    return target


_model = None
_model_lock = threading.Lock()
_model_error: str | None = None
_model_device: str | None = None  # actual backend the loaded model runs on: "GPU" or "CPU"
_requested_device: str = "gpu"  # what the user asked for; set from config before loading


class WhisperAPIError(Exception):
    """Error during transcription."""
    pass


def _load_model(prefer: str = "gpu"):
    """Load Orukeet's INT8 release using its supported CPU transducer runtime.

    Legacy GPU preferences remain readable; this quantized integration uses CPU.
    """
    global _model_device
    import os
    import sherpa_onnx

    directory = _model_directory()
    model = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(directory / "encoder.int8.onnx"),
        decoder=str(directory / "decoder.int8.onnx"),
        joiner=str(directory / "joiner.int8.onnx"),
        tokens=str(directory / "tokens.txt"),
        model_type="nemo_transducer",
        sample_rate=16000,
        feature_dim=128,
        decoding_method="greedy_search",
        num_threads=min(4, os.cpu_count() or 1),
        provider="cpu",
    )
    _model_device = "CPU"
    print(f"Orukeet loaded: {MODEL_NAME} on CPU", flush=True)
    return model


def set_device(device: str) -> None:
    """Set the preferred compute device ("gpu" or "cpu").

    If the model is already loaded on a different backend, it is unloaded so the
    next transcription reloads it on the newly requested device.
    """
    global _requested_device, _model, _model_error, _model_device
    device = (device or "gpu").lower()
    if device not in ("gpu", "cpu"):
        device = "gpu"
    with _model_lock:
        _requested_device = device
        # Force a reload if the loaded backend no longer matches the request.
        want = "CPU"  # The released INT8 integration uses CPU for both legacy preferences.
        if _model is not None and _model_device is not None and _model_device != want:
            _model = None
            _model_device = None
        _model_error = None


def get_device() -> str | None:
    """Return the backend the model is actually running on ("GPU"/"CPU"), or None."""
    return _model_device


def get_model():
    """Get or load the ASR model (thread-safe, blocks until ready)."""
    global _model, _model_error
    if _model is not None:
        return _model
    if _model_error:
        raise WhisperAPIError(_model_error)
    with _model_lock:
        if _model is None and not _model_error:
            try:
                _model = _load_model(_requested_device)
            except Exception as e:
                _model_error = str(e)
                raise WhisperAPIError(_model_error) from e
    return _model


def reset_model() -> None:
    """Drop the loaded model and clear any cached load error.

    The next call to get_model() reloads from scratch on the requested device.
    Used by the tray "Reload" action to rebuild stale CUDA/session state after
    the machine sleeps/wakes without restarting the whole app.
    """
    global _model, _model_error, _model_device
    with _model_lock:
        _model = None
        _model_error = None
        _model_device = None


def preload_model():
    """Start loading the model in background on app startup."""
    def _preload():
        try:
            get_model()
        except Exception as e:
            print(f"Model preload failed: {e}")
    threading.Thread(target=_preload, daemon=True, name="asr-preload").start()


class WhisperClient:
    """Transcription client using Orukeet (sherpa-onnx) running in-process.

    Name kept for backwards compatibility with the rest of the app.
    """

    def __init__(self, config: Config):
        self.config = config

    def transcribe_sync(self, audio_data: bytes) -> str:
        """Transcribe audio bytes. Blocks until model is ready and transcription is done.

        Orukeet detects the spoken language automatically; ``config.language`` is ignored.
        """
        if not audio_data or len(audio_data) < 1000:
            return ""

        model = get_model()

        try:
            import numpy as np

            with wave.open(io.BytesIO(audio_data), "rb") as wav:
                if wav.getsampwidth() != 2:
                    raise ValueError("Expected 16-bit PCM WAV audio")
                sample_rate = wav.getframerate()
                channels = wav.getnchannels()
                samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
                samples = samples.astype(np.float32).reshape(-1, channels).mean(axis=1)
                samples /= 32768.0
            stream = model.create_stream()
            stream.accept_waveform(sample_rate, samples)
            model.decode_stream(stream)
            return (stream.result.text or "").strip()
        except Exception as e:
            raise WhisperAPIError(f"Transcription failed: {e}") from e
