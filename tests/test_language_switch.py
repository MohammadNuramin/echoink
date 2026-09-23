import ctypes
import io
import sys
import unittest
import wave
from unittest.mock import MagicMock, patch

import numpy as np

from echoink import api, bangla
from echoink.config import Config
from echoink.typer import Typer


def wav_bytes(seconds=1.0, rate=16000):
    samples = (np.sin(np.arange(int(seconds * rate)) / 10) * 8000).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


class LanguageSwitchTests(unittest.TestCase):
    def setUp(self):
        self.detector = MagicMock()
        self.recognizer = MagicMock()
        self.recognizer.transcribe.return_value = "আমি ভাত খাই"
        patches = [
            patch.object(api, "_transcribe_english", return_value="Hello world."),
            patch.object(bangla, "is_installed", return_value=True),
            patch.object(bangla, "get_detector", return_value=self.detector),
            patch.object(bangla, "get_recognizer", return_value=self.recognizer),
        ]
        self.english = patches[0].start()
        self.installed = patches[1].start()
        self.get_detector = patches[2].start()
        for p in patches[3:]:
            p.start()
        for p in patches:
            self.addCleanup(p.stop)

    def transcribe(self, **config):
        return api.WhisperClient(Config(**config)).transcribe_sync(wav_bytes())

    def test_auto_uses_bangla_when_it_beats_english_by_the_margin(self):
        self.detector.bangla_score.return_value = 2.5
        self.assertEqual(self.transcribe(bangla_margin=1.0), "আমি ভাত খাই")
        self.recognizer.transcribe.assert_called_once()

    def test_auto_uses_english_within_the_margin(self):
        self.detector.bangla_score.return_value = 0.4
        self.assertEqual(self.transcribe(bangla_margin=1.0), "Hello world.")
        self.recognizer.transcribe.assert_not_called()

    def test_auto_falls_back_to_english_when_detection_fails(self):
        self.get_detector.side_effect = RuntimeError("no GPU")
        self.assertEqual(self.transcribe(), "Hello world.")

    def test_auto_without_bangla_model_is_english_only(self):
        self.installed.return_value = False
        self.assertEqual(self.transcribe(), "Hello world.")
        self.get_detector.assert_not_called()

    def test_english_mode_skips_detection(self):
        self.assertEqual(self.transcribe(language_mode="en"), "Hello world.")
        self.get_detector.assert_not_called()

    def test_bangla_mode_skips_detection_and_english(self):
        self.assertEqual(self.transcribe(language_mode="bn"), "আমি ভাত খাই")
        self.get_detector.assert_not_called()
        self.english.assert_not_called()

    def test_bangla_failure_is_a_transcription_error(self):
        self.recognizer.transcribe.side_effect = RuntimeError("model missing")
        with self.assertRaisesRegex(api.WhisperAPIError, "Bangla"):
            self.transcribe(language_mode="bn")

    def test_bangla_models_get_16khz_audio(self):
        self.detector.bangla_score.return_value = 5.0
        api.WhisperClient(Config()).transcribe_sync(wav_bytes(seconds=1.0, rate=48000))
        (samples,) = self.recognizer.transcribe.call_args.args
        self.assertEqual(len(samples), 16000)
        self.assertEqual(samples.dtype, np.float32)


class BanglaModelTests(unittest.TestCase):
    def test_unk_tokens_become_word_breaks(self):
        recognizer = object.__new__(bangla.BanglaRecognizer)
        recognizer._model = MagicMock()
        recognizer._model.recognize.return_value = "আমি<unk>ভাত খাই<unk> "
        self.assertEqual(recognizer.transcribe(np.zeros(16000, np.float32)), "আমি ভাত খাই")

    def test_switching_device_drops_loaded_models(self):
        self.addCleanup(bangla.set_device, "gpu")
        bangla.set_device("gpu")
        bangla._recognizer._value = object()
        bangla.set_device("cpu")
        self.assertIsNone(bangla._recognizer._value)

    def test_load_failure_is_remembered_until_reset(self):
        factory = MagicMock(side_effect=RuntimeError("download failed"))
        lazy = bangla._Lazy(factory)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "download failed"):
                lazy.get()
        self.assertEqual(factory.call_count, 1)
        lazy.reset()
        factory.side_effect = None
        factory.return_value = "model"
        self.assertEqual(lazy.get(), "model")


class UnicodeTypingTests(unittest.TestCase):
    def make_typer(self, system):
        typer = Typer.__new__(Typer)
        typer.system = system
        typer._typing_delay = 0
        typer._uinput = None
        return typer

    def test_bangla_on_windows_uses_unicode_input(self):
        typer = self.make_typer("Windows")
        with patch.object(typer, "_type_unicode_windows", return_value=True) as unicode, \
                patch.object(typer, "_type_pyautogui") as pyautogui:
            typer.type_text("আমি")
            typer.type_text("hello")
        unicode.assert_called_once()
        pyautogui.assert_called_once()

    def test_bangla_elsewhere_goes_to_the_clipboard(self):
        typer = self.make_typer("Linux")
        with patch.object(typer, "copy_to_clipboard", return_value=True) as clipboard, \
                patch.object(typer, "_type_linux") as keys:
            self.assertTrue(typer.type_text("আমি"))
        clipboard.assert_called_once_with("আমি")
        keys.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "SendInput is Windows-only")
    def test_sendinput_gets_a_key_down_and_up_per_utf16_unit(self):
        windll = MagicMock()
        windll.user32.SendInput.side_effect = lambda count, events, size: count
        with patch.object(ctypes, "windll", windll), patch("time.sleep"):
            self.assertTrue(self.make_typer("Windows")._type_unicode_windows("আমি"))
        count, events, size = windll.user32.SendInput.call_args.args
        self.assertEqual(count, 6)  # 3 code units x (down, up)
        self.assertEqual(size, 40 if sys.maxsize > 2**32 else 28)
        self.assertEqual([events[i].u.ki.wScan for i in range(0, 6, 2)], [0x0986, 0x09AE, 0x09BF])
        self.assertEqual([events[i].u.ki.dwFlags for i in range(2)], [0x0004, 0x0006])


if __name__ == "__main__":
    unittest.main()
