"""Local speech-to-text: Orukeet for English, plus Bangla with automatic switching.

English (and Orukeet's other European languages) runs on the CPU through the pinned
Orukeet ONNX INT8 release. When the converted Bangla model is installed, auto mode
checks each recording's language on the GPU and sends Bangla speech to the Bangla
model instead; see bangla.py.
"""

import io
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import bangla
from .config import Config

MODEL_NAME = "oruk/orukeet"
MODEL_REVISION = "55a984d46f68323301837194ce647c702f55facc"
MODEL_ARCHIVE = "onnx/sherpa-onnx-orukeet-v0.1.0-int8.tar.bz2"
MODEL_SHA256 = "f9191f30178cc9122ce2f023bf9fefafc822028307b0efa4caff645ba3fe8d0a"

LANGUAGE_MODES = ("auto", "en", "bn")


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
        # Public model: download anonymously so a stale saved HF token cannot cause a 401.
        archive = hf_hub_download(MODEL_NAME, MODEL_ARCHIVE, revision=MODEL_REVISION, token=False)
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
_model_device: str | None = None  # actual backend the Orukeet model runs on (always "CPU")
_requested_device: str = "gpu"  # where the Bangla models run; set from config before loading

# English decodes run here, one at a time; in auto mode alongside language detection.
_english_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr-en")


class WhisperAPIError(Exception):
    """Error during transcription."""
    pass


def _load_model(prefer: str = "gpu"):
    """Load Orukeet's INT8 release using its supported CPU transducer runtime.

    The GPU preference applies to the Bangla models; this quantized integration uses CPU.
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
    """Set the preferred compute device ("gpu" or "cpu") for the Bangla models.

    Orukeet always runs on the CPU. Bangla models already loaded on the other
    backend are unloaded and reload on the newly requested device.
    """
    global _requested_device, _model_error
    device = (device or "gpu").lower()
    if device not in ("gpu", "cpu"):
        device = "gpu"
    with _model_lock:
        _requested_device = device
        _model_error = None
    bangla.set_device(device)


def get_device() -> str | None:
    """Return the backend the Orukeet model is actually running on ("CPU"), or None."""
    return _model_device


def get_model():
    """Get or load the Orukeet model (thread-safe, blocks until ready)."""
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
    """Drop the loaded models and clear any cached load errors.

    The next transcription reloads from scratch on the requested device.
    Used by the tray "Reload" action to rebuild stale CUDA/session state after
    the machine sleeps/wakes without restarting the whole app.
    """
    global _model, _model_error, _model_device
    with _model_lock:
        _model = None
        _model_error = None
        _model_device = None
    bangla.reset()


def load_models(language_mode: str = "auto") -> None:
    """Load every model ``language_mode`` needs and wait for them; failures are only logged.

    Orukeet (CPU) loads on a worker thread while the Bangla models load on the
    calling thread, so the entry point can initialise CUDA before Qt starts.
    """
    def load_english():
        try:
            get_model()
        except Exception as e:
            print(f"Model preload failed: {e}")

    english = threading.Thread(target=load_english, name="asr-load-en")
    english.start()
    if language_mode != "en" and bangla.is_installed():
        try:
            bangla.get_recognizer()
            if language_mode != "bn":
                bangla.get_detector()
        except Exception as e:
            print(f"Bangla model preload failed: {e}")
    english.join()


def preload_model(language_mode: str = "auto") -> None:
    """Start loading the models in the background (app startup, reload, settings change)."""
    threading.Thread(
        target=load_models, args=(language_mode,), daemon=True, name="asr-preload"
    ).start()


def _read_wav(audio_data: bytes):
    """Decode 16-bit PCM WAV bytes to mono float32 samples in [-1, 1] and their sample rate."""
    import numpy as np

    with wave.open(io.BytesIO(audio_data), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("Expected 16-bit PCM WAV audio")
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    samples = samples.astype(np.float32).reshape(-1, channels).mean(axis=1)
    samples /= 32768.0
    return samples, sample_rate


def _to_16k(samples, sample_rate: int):
    """Resample (linearly) to the 16 kHz the Bangla and language-ID models expect."""
    if sample_rate == 16000:
        return samples
    import numpy as np

    count = int(round(len(samples) * 16000 / sample_rate))
    positions = np.arange(count) * (sample_rate / 16000)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def _transcribe_english(samples, sample_rate: int) -> str:
    model = get_model()
    try:
        stream = model.create_stream()
        stream.accept_waveform(sample_rate, samples)
        model.decode_stream(stream)
        return (stream.result.text or "").strip()
    except Exception as e:
        raise WhisperAPIError(f"Transcription failed: {e}") from e


def _transcribe_bangla(samples, sample_rate: int) -> str:
    try:
        return bangla.get_recognizer().transcribe(_to_16k(samples, sample_rate))
    except Exception as e:
        raise WhisperAPIError(f"Bangla transcription failed: {e}") from e


def _sounds_bangla(samples, sample_rate: int, margin: float) -> bool:
    """Auto-mode language check; a detection failure counts as English."""
    try:
        score = bangla.get_detector().bangla_score(_to_16k(samples, sample_rate))
    except Exception as e:
        print(f"Language detection failed ({e}); using English.")
        return False
    return score > margin


class WhisperClient:
    """Transcription client using Orukeet (sherpa-onnx) and the Bangla model in-process.

    Name kept for backwards compatibility with the rest of the app.
    """

    def __init__(self, config: Config):
        self.config = config

    def transcribe_sync(self, audio_data: bytes) -> str:
        """Transcribe audio bytes. Blocks until the models are ready and transcription is done.

        ``config.language_mode`` picks the model: "en" (Orukeet), "bn" (Bangla) or
        "auto", which uses Bangla when the recording's Bangla score beats English by
        more than ``config.bangla_margin``. Without the Bangla model, auto means English.
        """
        if not audio_data or len(audio_data) < 1000:
            return ""

        try:
            samples, sample_rate = _read_wav(audio_data)
        except Exception as e:
            raise WhisperAPIError(f"Transcription failed: {e}") from e

        mode = self.config.language_mode if self.config.language_mode in LANGUAGE_MODES else "auto"
        if mode == "bn":
            return _transcribe_bangla(samples, sample_rate)

        # Start Orukeet on the CPU straight away; in auto mode the GPU checks the
        # language meanwhile, so detection adds no delay to English dictation.
        english = _english_pool.submit(_transcribe_english, samples, sample_rate)
        if (
            mode == "auto"
            and bangla.is_installed()
            and _sounds_bangla(samples, sample_rate, self.config.bangla_margin)
        ):
            return _transcribe_bangla(samples, sample_rate)
        return english.result()
