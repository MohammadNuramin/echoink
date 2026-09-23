import io
import json
import unittest
import urllib.error
import urllib.request
import wave
from unittest.mock import patch

from echoink import phone_server
from echoink.config import Config

TOKEN = "pairing-token"


def wav_bytes(seconds=1.0):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(int(seconds * 16000) * 2))
    return output.getvalue()


def multipart(fields):
    boundary = "echoinkboundary"
    body = b""
    for name, value in fields.items():
        filename = '; filename="clip.wav"' if name == "file" else ""
        disposition = f'Content-Disposition: form-data; name="{name}"{filename}'
        body += f"--{boundary}\r\n{disposition}\r\n\r\n".encode()
        body += (value if isinstance(value, bytes) else value.encode()) + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class PhoneServerTests(unittest.TestCase):
    def setUp(self):
        self.server = phone_server.PhoneServer(Config(phone_token=TOKEN, phone_server_port=0))
        patcher = patch.object(
            self.server._client, "transcribe_with_language", return_value=("আমি ভাত খাই", "bn")
        )
        self.transcribe = patcher.start()
        self.addCleanup(patcher.stop)
        self.server.start(host="127.0.0.1")
        self.addCleanup(self.server.stop)

    def request(self, path, data=None, content_type=None, token=TOKEN):
        headers = {"Content-Type": content_type} if content_type else {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"http://127.0.0.1:{self.server.port}{path}"
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_raw_wav_body_returns_text_and_language(self):
        path = "/v1/audio/transcriptions?language=bn"
        status, body = self.request(path, wav_bytes(), "audio/wav")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"text": "আমি ভাত খাই", "language": "bn"})
        wav, mode = self.transcribe.call_args.args
        self.assertEqual(wav, wav_bytes())
        self.assertEqual(mode, "bn")

    def test_openai_style_multipart_upload(self):
        body, content_type = multipart(
            {"model": "whisper-1", "language": "en", "response_format": "text", "file": wav_bytes()}
        )
        status, text = self.request("/v1/audio/transcriptions", body, content_type)
        self.assertEqual((status, text.decode()), (200, "আমি ভাত খাই"))
        wav, mode = self.transcribe.call_args.args
        self.assertEqual((wav, mode), (wav_bytes(), "en"))

    def test_unknown_language_falls_back_to_the_configured_mode(self):
        self.request("/v1/audio/transcriptions?language=xx", wav_bytes(), "audio/wav")
        self.assertIsNone(self.transcribe.call_args.args[1])

    def test_wrong_or_missing_token_is_rejected(self):
        for token in ("wrong", None):
            status, _ = self.request("/transcribe", wav_bytes(), "audio/wav", token=token)
            self.assertEqual(status, 401)
        self.transcribe.assert_not_called()

    def test_health_needs_the_token(self):
        self.assertEqual(self.request("/health")[0], 200)
        self.assertEqual(self.request("/health", token=None)[0], 401)

    def test_overlong_or_unreadable_audio_is_rejected(self):
        self.assertEqual(self.request("/transcribe", wav_bytes(121), "audio/wav")[0], 413)
        garbage = b"not audio at all" * 100
        self.assertEqual(self.request("/transcribe", garbage, "audio/wav")[0], 400)
        self.transcribe.assert_not_called()

    def test_landing_page_does_not_leak_the_token(self):
        status, body = self.request("/", token=None)
        self.assertEqual(status, 200)
        self.assertNotIn(TOKEN.encode(), body)

    def test_pairing_link_carries_url_and_token(self):
        link = phone_server.pairing_link("http://100.83.187.43:8765", "a/b+c")
        self.assertEqual(link, "echoink://pair?url=http%3A%2F%2F100.83.187.43%3A8765&token=a%2Fb%2Bc")


if __name__ == "__main__":
    unittest.main()
