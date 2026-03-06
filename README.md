<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo.svg">
    <img alt="EchoInk" src="assets/logo.svg" width="100%">
  </picture>
</p>

# EchoInk

**Free, open source voice dictation for Linux, macOS, and Windows.** Press a hotkey, speak, and your words are instantly typed into any window. No cloud service, no API key, no server required.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)

## Features

- **Floating mic button** — always-on-top orange button, drag anywhere on screen
- **Global hotkey** — start/stop recording from any application
- **Animated waveform** — see your audio levels in real time
- **Built-in Whisper model** — runs fully offline, no internet after first download
- **GPU + CPU** — uses CUDA automatically, falls back to CPU if unavailable
- **Auto-type** — transcribed text typed directly into whatever window has focus
- **Clipboard copy** — text also copied to clipboard
- **System tray** — runs quietly in background
- **Autostart** — launch on login with crash recovery (Windows Task Scheduler)
- **Clip history** — last 20 transcriptions always available

## Installation

### Windows

```powershell
git clone https://github.com/MohammadNuramin/echoink.git
cd echoink
python -m venv .venv
.venv\Scripts\activate
pip install -e .
echoink
```

An orange floating button appears at the bottom-right of your screen. The first launch downloads the Whisper model (~460 MB).

### Linux (Ubuntu/Debian)

```bash
sudo apt install python3-pyaudio portaudio19-dev xdotool xclip
git clone https://github.com/MohammadNuramin/echoink.git
cd echoink
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
echoink
```

### Linux (Arch)

```bash
sudo pacman -S python-pyaudio portaudio xdotool xclip
git clone https://github.com/MohammadNuramin/echoink.git
cd echoink
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
echoink
```

### macOS

```bash
brew install portaudio
git clone https://github.com/MohammadNuramin/echoink.git
cd echoink
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
echoink
```

## Usage

1. Launch `echoink` — an orange circle appears in the bottom-right corner
2. **Hold** the button or press the hotkey to start recording
3. Speak
4. **Release** the button or press the hotkey again to stop
5. Text is transcribed and typed into the focused window

### Custom Hotkey

Edit `%APPDATA%\echoink\config.json` (Windows) or `~/.config/echoink/config.json` (Linux/macOS):

```json
{
  "hotkey": ["ctrl", "shift", "space"]
}
```

## Autostart on Login

**Windows** — right-click the system tray icon → **Start on Login**. Uses Windows Task Scheduler with automatic crash recovery.

**Linux:**
```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/echoink.desktop << 'EOF'
[Desktop Entry]
Name=EchoInk
Exec=echoink
Type=Application
X-GNOME-Autostart-enabled=true
EOF
```

**macOS** — System Preferences → Users & Groups → Login Items.

## GPU Support

EchoInk uses CUDA automatically on NVIDIA GPUs. Requirements:

- NVIDIA GPU
- CUDA Toolkit 12.x (provides `cublas64_12.dll` on Windows)

If CUDA is not available, EchoInk falls back to CPU automatically — no configuration needed.

| Device | Typical transcription latency |
|--------|-------------------------------|
| NVIDIA GPU (CUDA 12) | ~0.5 seconds |
| CPU (int8) | 2–5 seconds |

## Configuration

Config file: `%APPDATA%\echoink\config.json` (Windows) or `~/.config/echoink/config.json` (Linux/macOS)

| Key | Default | Description |
|-----|---------|-------------|
| `hotkey` | `["ctrl","alt","w"]` | Global recording hotkey |
| `language` | `"en"` | Transcription language |
| `auto_paste` | `true` | Type text into focused window |
| `copy_to_clipboard` | `true` | Also copy to clipboard |
| `waveform_color` | `"#84cc16"` | Waveform color (hex) |
| `hold_to_talk_button` | `true` | Show floating mic button |
| `max_recording_seconds` | `45` | Maximum recording length |

## Troubleshooting

**Windows: App won't start** — check `%APPDATA%\echoink\crash.log`.

**Windows: No GPU transcription** — install [CUDA Toolkit 12.x](https://developer.nvidia.com/cuda-downloads). EchoInk falls back to CPU automatically.

**Linux: Hotkey not working** — change the hotkey in config if it conflicts with another app.

**Linux: PyAudio install fails** — `sudo apt install python3-pyaudio portaudio19-dev`

**macOS: Accessibility denied** — System Preferences → Security & Privacy → Privacy → Accessibility → add your terminal.

## License

MIT License — see [LICENSE](LICENSE) for details.
