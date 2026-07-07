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


class WhisperAPIError(Exception):
    """Error during transcription."""
    pass


def _load_model():
    """Load the Parakeet model. Downloads the ONNX weights on first run (~2.4GB)."""
    import onnx_asr
    import onnxruntime as ort

    # Prefer the GPU if an onnxruntime CUDA provider is available; otherwise
    # fall back to CPU (Parakeet is still sub-second on CPU for dictation clips).
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" in available:
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        device = "GPU"
    else:
        providers = ["CPUExecutionProvider"]
        device = "CPU"

    try:
        model = onnx_asr.load_model(MODEL_NAME, providers=providers)
    except Exception as e:
        raise RuntimeError(f"Failed to load Parakeet model: {e}") from e

    print(f"Parakeet loaded: {MODEL_NAME} on {device}")
    return model


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
                _model = _load_model()
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
