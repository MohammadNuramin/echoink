"""Bangla speech recognition and English/Bangla language detection.

Both run in-process through ONNX Runtime, on the GPU (CUDA) when available:

* Bangla ASR: Bhatiyali (kazalbrur/bangla-asr-conformer-120m-dialects), a NeMo
  Conformer-CTC model trained on broadcast, spontaneous and Bangladeshi dialect
  speech. It is only published as a NeMo checkpoint, so scripts/export_bangla_model.py
  converts it to ONNX once, into the EchoInk models folder.
* Language detection: Whisper small's language-ID step, comparing only the Bangla
  and English scores of each recording.
"""

import json
import threading
from pathlib import Path

import numpy as np

from .config import Config

MODEL_DIRNAME = "bangla-conformer-120m-dialects-onnx"
DETECTOR_REPO = "onnx-community/whisper-small"
DETECTOR_REVISION = "36050c46d777d46dc4b5f43f6d90574fc38f8732"


def model_directory() -> Path:
    """Folder that scripts/export_bangla_model.py writes the ONNX model to."""
    return Config.get_config_path().parent / "models" / MODEL_DIRNAME


def is_installed() -> bool:
    """True once the converted Bangla model is in the models folder."""
    directory = model_directory()
    return all((directory / name).is_file() for name in ("model.onnx", "vocab.txt", "config.json"))


def _providers(device: str) -> tuple[list, str]:
    """ONNX Runtime providers for the preferred device, and the backend they give."""
    import onnxruntime as ort

    if device != "cpu" and "CUDAExecutionProvider" in ort.get_available_providers():
        try:
            ort.preload_dlls()  # CUDA / cuDNN from the nvidia-* wheels (onnxruntime-gpu >= 1.21)
        except Exception:
            pass
        # Every recording has a new length, so skip cuDNN's per-shape benchmarking.
        cuda = ("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"})
        return [cuda, "CPUExecutionProvider"], "GPU"
    return ["CPUExecutionProvider"], "CPU"


def _load(model_class, device: str):
    """Create ``model_class`` on the preferred device, retrying on the CPU if the GPU fails."""
    providers, backend = _providers(device)
    try:
        return model_class(providers, backend)
    except Exception as e:
        if backend != "GPU":
            raise
        print(f"{model_class.__name__}: GPU load failed ({e}); falling back to CPU.")
        return model_class(["CPUExecutionProvider"], "CPU")


class BanglaRecognizer:
    """The Bhatiyali Conformer-CTC model, run through onnx-asr."""

    def __init__(self, providers: list, backend: str):
        if not is_installed():
            raise RuntimeError(
                f"Bangla model not found in {model_directory()}; "
                "convert it with scripts/export_bangla_model.py"
            )
        import onnx_asr

        self.device = backend
        self._model = onnx_asr.load_model(
            "nemo-conformer-ctc", model_directory(), providers=providers
        )
        self.transcribe(np.zeros(16000, dtype=np.float32))  # warm up CUDA kernels
        print(f"Bangla ASR loaded on {backend}", flush=True)

    def transcribe(self, samples: np.ndarray) -> str:
        """Transcribe 16 kHz mono float32 audio."""
        text = self._model.recognize(samples)
        # Treat any <unk> token (e.g. a sound the vocabulary lacks) as a word break.
        return " ".join(text.replace("<unk>", " ").split())


class LanguageDetector:
    """Scores Bangla against English with Whisper small's language-ID step."""

    def __init__(self, providers: list, backend: str):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from onnx_asr.preprocessors.numpy_preprocessor import WhisperPreprocessorNumpy

        def fetch(name: str) -> str:
            return hf_hub_download(DETECTOR_REPO, name, revision=DETECTOR_REVISION, token=False)

        self.device = backend
        # fp16 suits CUDA; the CPU provider lacks fp16 kernels, so it gets the int8 export.
        variant = "fp16" if backend == "GPU" else "quantized"
        self._encoder = ort.InferenceSession(
            fetch(f"onnx/encoder_model_{variant}.onnx"), providers=providers
        )
        self._decoder = ort.InferenceSession(
            fetch(f"onnx/decoder_model_{variant}.onnx"), providers=providers
        )
        with open(fetch("added_tokens.json"), encoding="utf-8") as f:
            tokens = json.load(f)
        self._prompt = np.array([[tokens["<|startoftranscript|>"]]], dtype=np.int64)
        self._english, self._bangla = tokens["<|en|>"], tokens["<|bn|>"]
        self._features = WhisperPreprocessorNumpy("whisper80")
        self._dtype = np.float16 if "float16" in self._encoder.get_inputs()[0].type else np.float32
        self.bangla_score(np.zeros(16000, dtype=np.float32))  # warm up CUDA kernels
        print(f"Language detection loaded on {backend}", flush=True)

    def bangla_score(self, samples: np.ndarray) -> float:
        """How much more likely Bangla is than English, as a logit difference (> 0 favours Bangla).

        ``samples`` is 16 kHz mono float32 audio; Whisper looks at the first 30 seconds.
        """
        features, _ = self._features(samples[None], np.array([len(samples)]))
        (hidden,) = self._encoder.run(
            ["last_hidden_state"], {"input_features": features.astype(self._dtype)}
        )
        (logits,) = self._decoder.run(
            ["logits"], {"input_ids": self._prompt, "encoder_hidden_states": hidden}
        )
        return float(logits[0, -1, self._bangla]) - float(logits[0, -1, self._english])


class _Lazy:
    """Loads a model on first use; remembers a load failure until reset()."""

    def __init__(self, factory):
        self._factory = factory
        self._lock = threading.Lock()
        self._value = None
        self._error: str | None = None

    def get(self):
        if self._value is not None:
            return self._value
        with self._lock:
            if self._value is None:
                if self._error is not None:
                    raise RuntimeError(self._error)
                try:
                    self._value = self._factory()
                except Exception as e:
                    self._error = str(e)
                    raise
        return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = None
            self._error = None


_device = "gpu"  # preferred backend: "gpu" (CUDA, falls back to CPU) or "cpu"
_recognizer = _Lazy(lambda: _load(BanglaRecognizer, _device))
_detector = _Lazy(lambda: _load(LanguageDetector, _device))


def set_device(device: str) -> None:
    """Prefer "gpu" or "cpu"; models loaded for the other preference reload on next use."""
    global _device
    device = "cpu" if device == "cpu" else "gpu"
    if device != _device:
        _device = device
        reset()


def get_recognizer() -> BanglaRecognizer:
    """The Bangla ASR model, loaded on first use (thread-safe, blocks until ready)."""
    return _recognizer.get()


def get_detector() -> LanguageDetector:
    """The English/Bangla language detector, loaded on first use."""
    return _detector.get()


def reset() -> None:
    """Drop both models and any cached load error."""
    _recognizer.reset()
    _detector.reset()
