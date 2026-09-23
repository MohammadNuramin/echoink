"""HTTP server that lets the EchoInk Android app use this PC's speech models.

The phone records audio and posts it here; the text comes back as JSON. The
endpoint follows OpenAI's POST /v1/audio/transcriptions (multipart "file" field),
so other Whisper-API clients work too, and also accepts a raw WAV body. Every
request except the landing page and the app download needs the pairing token.
"""

import hmac
import io
import ipaddress
import json
import re
import socket
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from .api import WhisperAPIError, WhisperClient
from .config import Config

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # OpenAI's limit
MAX_AUDIO_SECONDS = 120
TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")
LANGUAGES = {
    "auto": "auto", "en": "en", "english": "en", "bn": "bn", "bengali": "bn", "bangla": "bn",
}

DOWNLOAD_LINK = '<p><a href="/echoink.apk" style="font-size:1.3em">Download the Android app</a></p>'
NOT_BUILT = "<p>The Android app has not been built on this PC yet.</p>"
LANDING_PAGE = """<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EchoInk</title></head>
<body style="font-family: sans-serif; max-width: 32em; margin: 2em auto; padding: 0 1em">
<h1>EchoInk</h1>
<p>This PC transcribes speech for the EchoInk phone app.</p>
{download}
<p>Then pair the app: on the PC, open the EchoInk tray menu, choose <b>Phone access</b>,
and scan the QR code from the app.</p>
</body></html>
"""


def apk_path() -> Path:
    """Where the built Android app is served from (see android/README.md for building it)."""
    return Config.get_config_path().parent / "echoink-android.apk"


def local_addresses() -> list[str]:
    """This machine's IPv4 addresses, Tailscale ones first."""
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    try:  # The address of the default route, which getaddrinfo can miss.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1; nothing is sent
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    usable = [
        a for a in addresses
        if not (ipaddress.ip_address(a).is_loopback or ipaddress.ip_address(a).is_link_local)
    ]
    return sorted(usable, key=lambda a: (ipaddress.ip_address(a) not in TAILSCALE_NETWORK, a))


def pairing_link(base_url: str, token: str) -> str:
    """The deep link the phone app reads from the pairing QR code."""
    return f"echoink://pair?url={quote(base_url, safe='')}&token={quote(token, safe='')}"


def _parse_multipart(content_type: str, body: bytes) -> dict[str, bytes]:
    """Split a multipart/form-data body into {field name: raw bytes}."""
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not match:
        raise ValueError("multipart body without a boundary")
    fields = {}
    for part in body.split(b"--" + match.group(1).encode("latin-1"))[1:]:
        if part.startswith(b"--"):
            break  # closing delimiter
        headers, _, data = part.removeprefix(b"\r\n").partition(b"\r\n\r\n")
        name = re.search(rb'\bname="([^"]*)"', headers)
        if name:
            fields[name.group(1).decode("utf-8", "replace")] = data.removesuffix(b"\r\n")
    return fields


def _as_wav(audio: bytes) -> bytes:
    """Return 16-bit PCM WAV bytes, decoding other formats with PyAV when installed."""
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(audio)) as wav:
                if wav.getsampwidth() == 2:
                    return audio
        except wave.Error:
            pass
    try:
        import av
    except ImportError:
        raise ValueError("send 16-bit PCM WAV (install PyAV for other formats)") from None
    pcm = bytearray()
    with av.open(io.BytesIO(audio)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for chunk in resampler.resample(frame):
                pcm += chunk.to_ndarray().tobytes()
        for chunk in resampler.resample(None):
            pcm += chunk.to_ndarray().tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(pcm))
    return output.getvalue()


def _duration(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        return wav.getnframes() / wav.getframerate()


class _Handler(BaseHTTPRequestHandler):
    server_version = "EchoInk"
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002
        pass  # stderr is None under pythonw; the default logger would raise

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json")

    def _error(self, status: int, message: str) -> None:
        # The request body may be unread, so this connection cannot be reused.
        self.close_connection = True
        self._json(status, {"error": {"message": message}})

    def _authorized(self) -> bool:
        token = self.server.phone.config.phone_token
        sent = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token and hmac.compare_digest(sent.encode(), token.encode()):
            return True
        self._error(401, "missing or wrong pairing token")
        return False

    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/":
            download = DOWNLOAD_LINK if apk_path().is_file() else NOT_BUILT
            page = LANDING_PAGE.format(download=download).encode()
            self._send(200, page, "text/html; charset=utf-8")
        elif path == "/echoink.apk" and apk_path().is_file():
            self._send(200, apk_path().read_bytes(), "application/vnd.android.package-archive")
        elif path == "/health":
            if self._authorized():
                self._json(200, {"ok": True})
        else:
            self._error(404, "not found")

    def do_POST(self):  # noqa: N802
        url = urlsplit(self.path)
        if url.path not in ("/v1/audio/transcriptions", "/transcribe"):
            self._error(404, "not found")
            return
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._error(413 if length else 400, "audio must be between 1 byte and 25 MB")
            return
        body = self.rfile.read(length)

        fields = {key: values[0].encode() for key, values in parse_qs(url.query).items()}
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            try:
                fields |= _parse_multipart(content_type, body)
            except ValueError as e:
                self._error(400, str(e))
                return
            audio = fields.get("file", b"")
        else:
            audio = body
        language = fields.get("language", b"").decode("utf-8", "replace").strip().lower()
        text_only = fields.get("response_format", b"").decode("utf-8", "replace") == "text"

        try:
            wav = _as_wav(audio)
            if _duration(wav) > MAX_AUDIO_SECONDS:
                self._error(413, f"audio is longer than {MAX_AUDIO_SECONDS} seconds")
                return
        except Exception as e:
            self._error(400, f"unreadable audio: {e}")
            return
        try:
            text, used = self.server.phone.transcribe(wav, LANGUAGES.get(language))
        except WhisperAPIError as e:
            self._error(500, str(e))
            return
        if text_only:
            self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
        else:
            self._json(200, {"text": text, "language": used})


class PhoneServer:
    """Serves transcription to the phone app on ``config.phone_server_port``."""

    def __init__(self, config: Config):
        self.config = config
        self._client = WhisperClient(config)
        self._lock = threading.Lock()  # one phone recording at a time
        self._httpd: ThreadingHTTPServer | None = None

    def transcribe(self, wav: bytes, mode: str | None) -> tuple[str, str]:
        with self._lock:
            return self._client.transcribe_with_language(wav, mode)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else self.config.phone_server_port

    def urls(self) -> list[str]:
        """Base URLs the phone can use, Tailscale first."""
        return [f"http://{address}:{self.port}" for address in local_addresses()]

    def start(self, host: str = "0.0.0.0") -> None:
        """Start listening (all interfaces by default); raises OSError if the port is taken."""
        self._httpd = ThreadingHTTPServer((host, self.config.phone_server_port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.phone = self
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="phone-server").start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
