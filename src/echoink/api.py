"""Speech-to-text using NVIDIA Parakeet-TDT-0.6B-v2 via onnx-asr (in-process, no server).

English-only, but state-of-the-art accuracy (~6% WER) with automatic punctuation
and capitalization, and sub-second latency even on CPU. Runs through ONNX Runtime,
so no PyTorch / NeMo dependency is required.
"""

import tempfile
import threading
from pathlib import Path

from .config import Config

# onnx-asr model id for the pre-converted Parakeet checkpoint
# (istupakov/parakeet-tdt-0.6b-v2-onnx on Hugging Face).
MODEL_NAME = "nemo-parakeet-tdt-0.6b-v2"

_model = None
_model_lock = threading.Lock()
_model_error: str | None = None
_model_device: str | None = None  # actual backend the loaded model runs on: "GPU" or "CPU"
_requested_device: str = "gpu"  # what the user asked for; set from config before loading


class WhisperAPIError(Exception):
    """Error during transcription."""
    pass


def _load_model(prefer: str = "gpu"):
    """Load the Parakeet model. Downloads the ONNX weights on first run (~2.4GB).

    ``prefer`` is "gpu" or "cpu". "gpu" uses CUDA when available and transparently
    falls back to CPU otherwise; "cpu" forces CPU regardless of hardware.
    """
    global _model_device
    import onnx_asr
    import onnxruntime as ort

    # Make onnxruntime find the CUDA / cuDNN shared libraries shipped as
    # nvidia-* pip wheels (no system CUDA Toolkit install required).
    if prefer != "cpu":
        try:
            ort.preload_dlls()  # available on onnxruntime-gpu >= 1.21
        except Exception:
            pass

    available = ort.get_available_providers()
    if prefer != "cpu" and "CUDAExecutionProvider" in available:
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        device = "GPU"
    else:
        providers = ["CPUExecutionProvider"]
        device = "CPU"
        if prefer != "cpu":
            print("GPU requested but CUDAExecutionProvider unavailable — using CPU.")

    try:
        model = onnx_asr.load_model(MODEL_NAME, providers=providers)
    except Exception as e:
        # A CUDA session can fail to initialise even when the provider lists it
        # (missing/mismatched driver, cuDNN, etc.) — retry once on pure CPU.
        if device == "GPU":
            print(f"GPU model load failed ({e}); falling back to CPU.")
            try:
                model = onnx_asr.load_model(MODEL_NAME, providers=["CPUExecutionProvider"])
                device = "CPU"
            except Exception as e2:
                raise RuntimeError(f"Failed to load Parakeet model: {e2}") from e2
        else:
            raise RuntimeError(f"Failed to load Parakeet model: {e}") from e

    _model_device = device
    print(f"Parakeet loaded: {MODEL_NAME} on {device}")
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
        want = "CPU" if device == "cpu" else "GPU"
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


def preload_model():
    """Start loading the model in background on app startup."""
    def _preload():
        try:
            get_model()
        except Exception as e:
            print(f"Model preload failed: {e}")
    threading.Thread(target=_preload, daemon=True, name="asr-preload").start()


class WhisperClient:
    """Transcription client using Parakeet (onnx-asr) running in-process.

    Name kept for backwards compatibility with the rest of the app.
    """

    def __init__(self, config: Config):
        self.config = config

    def transcribe_sync(self, audio_data: bytes) -> str:
        """Transcribe audio bytes. Blocks until model is ready and transcription is done.

        Note: Parakeet-TDT-0.6B-v2 is English-only, so ``config.language`` is ignored.
        """
        if not audio_data or len(audio_data) < 1000:
            return ""

        model = get_model()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            tmp_path = f.name

        try:
            text = model.recognize(tmp_path)
            return (text or "").strip()
        except Exception as e:
            raise WhisperAPIError(f"Transcription failed: {e}") from e
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
