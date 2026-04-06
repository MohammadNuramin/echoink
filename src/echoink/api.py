"""Whisper transcription using faster-whisper library (in-process, no server needed)."""

import tempfile
import threading
from pathlib import Path

from .config import Config

MODEL_NAME = "Systran/faster-whisper-medium.en"

_model = None
_model_lock = threading.Lock()
_model_error: str | None = None


class WhisperAPIError(Exception):
    """Error during transcription."""
    pass


def _load_model():
    """Load the Whisper model. Downloads on first run (~1.5GB)."""
    import struct
    import wave as _wave
    import tempfile
    from faster_whisper import WhisperModel

    def _make_silent_wav() -> str:
        """Create a tiny silent WAV for testing transcription."""
        path = tempfile.mktemp(suffix=".wav")
        with _wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(struct.pack("<" + "h" * 1600, *([0] * 1600)))
        return path

    # Try GPU first, fall back to CPU automatically
    for device, compute in [("cuda", "int8_float16"), ("cuda", "float16"), ("cpu", "int8")]:
        try:
            model = WhisperModel(MODEL_NAME, device=device, compute_type=compute)
            # Verify inference actually works (cuBLAS/cuDNN may be missing even
            # if model weights load onto GPU without error)
            test_wav = _make_silent_wav()
            try:
                list(model.transcribe(test_wav)[0])
            finally:
                try:
                    Path(test_wav).unlink()
                except Exception:
                    pass
            print(f"Whisper loaded: {MODEL_NAME} on {device.upper()}")
            return model
        except Exception as e:
            if device == "cuda":
                print(f"GPU inference unavailable, falling back to CPU: {e}")
            else:
                raise RuntimeError(f"Failed to load Whisper model: {e}") from e


def get_model():
    """Get or load the Whisper model (thread-safe, blocks until ready)."""
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
    threading.Thread(target=_preload, daemon=True, name="whisper-preload").start()


class WhisperClient:
    """Transcription client using faster-whisper running in-process."""

    def __init__(self, config: Config):
        self.config = config

    def transcribe_sync(self, audio_data: bytes) -> str:
        """Transcribe audio bytes. Blocks until model is ready and transcription is done."""
        if not audio_data or len(audio_data) < 1000:
            return ""

        model = get_model()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            tmp_path = f.name

        try:
            segments, _info = model.transcribe(
                tmp_path,
                language="en",
                beam_size=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
            )
            text = " ".join(seg.text.strip() for seg in segments)
            return text.strip()
        except Exception as e:
            raise WhisperAPIError(f"Transcription failed: {e}") from e
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
