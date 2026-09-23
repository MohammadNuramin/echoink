import io
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from echoink import api
from echoink.config import Config


def wav_bytes(samples, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(16000)
        wav.writeframes(samples)
    return output.getvalue()


class TranscriptionTests(unittest.TestCase):
    def test_stereo_pcm_is_normalized_and_mixed(self):
        model = MagicMock()
        stream = model.create_stream.return_value
        stream.result = SimpleNamespace(text="  Hello world.  ")
        samples = np.tile(np.array([16384, 0], dtype="<i2"), 1000)
        with patch.object(api, "get_model", return_value=model):
            client = api.WhisperClient(Config(language_mode="en"))
            text = client.transcribe_sync(wav_bytes(samples.tobytes(), 2))
        self.assertEqual(text, "Hello world.")
        rate, audio = stream.accept_waveform.call_args.args
        self.assertEqual(rate, 16000)
        np.testing.assert_allclose(audio, 0.25)
        model.decode_stream.assert_called_once_with(stream)

    def test_invalid_audio_is_a_transcription_error(self):
        with patch.object(api, "get_model", return_value=MagicMock()):
            with self.assertRaises(api.WhisperAPIError):
                api.WhisperClient(Config()).transcribe_sync(b"invalid" * 1000)
            with self.assertRaisesRegex(api.WhisperAPIError, "16-bit"):
                api.WhisperClient(Config()).transcribe_sync(wav_bytes(bytes(2000), width=1))

    def test_empty_audio_does_not_load_model(self):
        with patch.object(api, "get_model") as load:
            self.assertEqual(api.WhisperClient(Config()).transcribe_sync(b""), "")
            load.assert_not_called()

    def test_reset_clears_model_and_load_failure(self):
        with patch.object(api, "_model", object()), patch.object(api, "_model_error", "failed"), patch.object(api, "_model_device", "CPU"):
            api.reset_model()
            self.assertIsNone(api._model)
            self.assertIsNone(api._model_error)
            self.assertIsNone(api.get_device())


if __name__ == "__main__":
    unittest.main()
