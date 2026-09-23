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
- **Built-in Orukeet model** — runs fully offline, no internet after first download
- **Local CPU inference** - Orukeet INT8 through sherpa-onnx
- **English + Bangla** — each recording is routed to the right model automatically; Bangla runs on the GPU (see [Bangla](#bangla))
- **Android phone app** — a floating mic over any app on your phone, transcribed by your PC (see [Phone](#phone-android))
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

An orange floating button appears at the bottom-right of your screen. The first launch downloads the Orukeet model (~487 MB download, ~672 MB extracted).

For Bangla, install with `pip install -e ".[gpu]"` (NVIDIA GPU) or `pip install -e ".[cpu]"` instead, then follow [Bangla](#bangla).

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

## Bangla

EchoInk can take dictation in Bangla as well as English and switches between them on its own:
speak English and you get English, speak Bangla and you get Bangla script.

For each recording, Whisper small's language-ID step compares how Bangla-like and how
English-like the audio is. If Bangla wins by more than `bangla_margin`, the recording goes to
[Bhatiyali](https://huggingface.co/kazalbrur/bangla-asr-conformer-120m-dialects), a Bangla
Conformer-CTC model trained on broadcast, spontaneous and Bangladeshi dialect speech;
otherwise it goes to Orukeet. Orukeet starts on the CPU at the same moment the GPU checks the
language, so the check adds no delay to English.

### Setup

1. Install ONNX Runtime for the GPU: `pip install -e ".[gpu]"` (CUDA 12 and cuDNN 9 come as
   pip wheels; no CUDA Toolkit needed). Without an NVIDIA GPU use `pip install -e ".[cpu]"`.
2. Convert the Bangla model to ONNX once. It is only published as a NeMo checkpoint, and
   [scripts/export_bangla_model.py](scripts/export_bangla_model.py) converts it in a throwaway
   Docker container, so EchoInk itself needs no PyTorch or NeMo. From the repository root:

   ```powershell
   # Windows (PowerShell)
   docker run --rm -v "$env:APPDATA\echoink\models:/models" -v "${PWD}\scripts:/scripts:ro" python:3.11 bash -c "pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu && pip install 'nemo_toolkit[asr]==3.0.0' torch==2.7.1 onnxruntime && python /scripts/export_bangla_model.py --out /models/bangla-conformer-120m-dialects-onnx"
   ```

   ```bash
   # Linux / macOS
   docker run --rm -v "$HOME/.config/echoink/models:/models" -v "$PWD/scripts:/scripts:ro" python:3.11 bash -c "pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu && pip install 'nemo_toolkit[asr]==3.0.0' torch==2.7.1 onnxruntime && python /scripts/export_bangla_model.py --out /models/bangla-conformer-120m-dialects-onnx"
   ```

3. Restart EchoInk. The first start downloads the Whisper small language-ID model
   (~485 MB on GPU, ~250 MB on CPU).

The settings panel has a **Language** choice (Auto: English + Bangla, English, Bangla) and a
**Processing** choice (GPU or CPU for Bangla). Without the converted model, Auto simply means English.

### Speed and accuracy

On an RTX 4090, Bangla text is ready about 0.06 s after you stop speaking in Bangla mode and
about 0.15 s in Auto mode (language check included). English is unchanged, since Orukeet runs on
the CPU either way. In testing, the language check picked the right model for all 141
full-length Bangla clips and 98% of 214 English ones (including South Asian accents); very
short phrases (about 1.5 s) are less reliable. Raise `bangla_margin` if English is taken for
Bangla, lower it if Bangla is taken for English.

On Bangladeshi news and YouTube clips, Bhatiyali gets about 86% of characters and 73% of
words right (CER 13.7%, WER 27%). It was picked
over three other open Bangla models tested on the same clips: `hishab/titu_stt_bn_fastconformer`
(CER 15.3%), its sibling `kazalbrur/Bangla-asr-fastconformer-116m-dialects` (14.1%) and
`speaklar/speaklar_stt_bn_fastconformer` (32%). Output has no punctuation.

On Windows, Bangla text is typed with Unicode keyboard input. On Linux and macOS it is copied
to the clipboard instead, because the key-press backends there only handle ASCII.

## Phone (Android)

The Android app puts a floating mic over every app on your phone. The phone only records;
EchoInk on your PC transcribes (English and Bangla, as above) and the text is typed into the
text field you are using on the phone.

1. **PC:** tray icon → **Phone access...** → tick *Let the EchoInk phone app use this PC*.
   If Windows asks, allow Python on **private** networks.
2. **Phone:** turn on [Tailscale](https://tailscale.com) (works anywhere) or join the same
   Wi-Fi, open the address shown in the dialog (e.g. `http://100.x.y.z:8765`) in the browser
   and install the app.
3. **In the app:** tap **Scan pairing QR** and scan the code in the Phone access dialog, then
   grant microphone, notifications, *display over other apps* and **Accessibility → EchoInk
   dictation**. On Android 13+, if that switch is greyed out: App info → ⋮ → *Allow restricted
   settings*, then try again.
4. Tap **Start floating mic**. Tap the bubble to record and tap it again to insert the text
   (if no text field is focused it goes to the clipboard). Drag the bubble to move it.

After setup, EchoInk opens and closes like a regular app: tap its icon and the mic bubble
appears (tapping the icon while the bubble is showing opens the settings); to close it, drag
the bubble onto the **X** that appears at the bottom of the screen, or tap **Stop** in its
notification.

The phone talks to `POST /v1/audio/transcriptions` on port 8765, the same shape as OpenAI's
Whisper API with the pairing token as the API key, so other Whisper-API clients can use your
PC too (send WAV, or install PyAV with `pip install av` for other formats). Traffic is plain
HTTP: over Tailscale it is encrypted by WireGuard, on a home network it is not. **New token**
in the dialog unpairs every phone. To build the app yourself, see
[android/README.md](android/README.md).

## Processing

Orukeet uses its 8-bit ONNX release on CPU. CUDA is not required for English.
Bangla and language detection use ONNX Runtime on the GPU (CUDA) when the `gpu` extra is
installed, and fall back to CPU otherwise. The Processing setting chooses between the two.

## Configuration

Config file: `%APPDATA%\echoink\config.json` (Windows) or `~/.config/echoink/config.json` (Linux/macOS)

| Key | Default | Description |
|-----|---------|-------------|
| `hotkey` | `["ctrl","alt","w"]` | Global recording hotkey |
| `language_mode` | `"auto"` | `"auto"` (English or Bangla per recording), `"en"` or `"bn"` |
| `bangla_margin` | `1.0` | How far Bangla must out-score English before Auto picks Bangla |
| `compute_device` | `"gpu"` | Where Bangla and language detection run: `"gpu"` or `"cpu"` |
| `language` | `"en"` | Legacy setting; replaced by `language_mode` |
| `phone_server` | `false` | Serve the Android app (tray → Phone access) |
| `phone_server_port` | `8765` | Port the phone app connects to |
| `phone_token` | `""` | Pairing token; created when phone access is turned on |
| `auto_paste` | `true` | Type text into focused window |
| `copy_to_clipboard` | `true` | Also copy to clipboard |
| `waveform_color` | `"#84cc16"` | Waveform color (hex) |
| `hold_to_talk_button` | `true` | Show floating mic button |
| `max_recording_seconds` | `45` | Maximum recording length |

## Troubleshooting

**Windows: App won't start** — check `%APPDATA%\echoink\crash.log`.


**Linux: Hotkey not working** — change the hotkey in config if it conflicts with another app.

**Linux: PyAudio install fails** — `sudo apt install python3-pyaudio portaudio19-dev`

**macOS: Accessibility denied** — System Preferences → Security & Privacy → Privacy → Accessibility → add your terminal.

## License

MIT License — see [LICENSE](LICENSE) for details.

## Speech model

EchoInk uses [Orukeet by Oruk AI](https://huggingface.co/oruk/orukeet), derived from
NVIDIA Parakeet TDT 0.6B v3, with automatic recognition across 25 languages.
The pinned ONNX INT8 release runs locally on CPU through sherpa-onnx. Existing GPU
preferences are accepted but use CPU with this backend; the optional GPU dependencies
are legacy and are not needed for Orukeet.

On first launch, the app downloads revision
`55a984d46f68323301837194ce647c702f55facc`, verifies its SHA-256, and extracts it
under the EchoInk configuration folder's `models` directory. Later launches work offline.
The download requires about 487 MB; keep additional space for extraction and the cache.

Model weights are licensed under CC BY-SA 4.0, separately from EchoInk's MIT code.
The archive's `LICENSE-WEIGHTS` and `NOTICE.md` are retained alongside the model.

The optional Bangla model, [Bhatiyali](https://huggingface.co/kazalbrur/bangla-asr-conformer-120m-dialects)
(`kazalbrur/bangla-asr-conformer-120m-dialects`), is licensed Apache 2.0. EchoInk does not
ship it; the export script downloads revision `b6c23d330a6b1cf8ff08af9f875298f9b485bff9`
and writes a `NOTICE.md` next to the converted model. Language detection uses
[Whisper small](https://huggingface.co/openai/whisper-small) (Apache 2.0) in the ONNX
conversion from `onnx-community/whisper-small`, pinned to revision
`36050c46d777d46dc4b5f43f6d90574fc38f8732`.
