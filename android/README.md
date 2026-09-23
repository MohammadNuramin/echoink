# EchoInk for Android

A floating mic for your phone. The phone only records; the EchoInk desktop app on your
PC does the transcription and sends the text back, which is typed into the text field you
are using. See the main [README](../README.md#phone-android) for setup.

## Build

No Android Studio needed; the Android SDK comes in a Docker image. From the repository root:

```bash
docker run --rm -v "$PWD/android:/project" -w /project ghcr.io/cirruslabs/android-sdk:35 \
    ./gradlew --no-daemon assembleDebug
```

The APK is written to `android/app/build/outputs/apk/debug/app-debug.apk`. Copy it to
`%APPDATA%\echoink\echoink-android.apk` (Windows) or `~/.config/echoink/echoink-android.apk`
and the desktop app offers it for download at `http://<PC address>:8765/echoink.apk`.

Debug builds are signed with the debug key in `~/.android` inside the container; mount a
volume there (`-v echoink-android-home:/root/.android`) so later builds install as updates.

## How it works

- `LaunchActivity` is the home-screen icon: once set up it starts the bubble without showing
  a screen, otherwise it opens the setup screen (`MainActivity`).
- `FloatingMicService` is a microphone foreground service, started from one of those
  activities, that shows the bubble (`MicButton`) with `SYSTEM_ALERT_WINDOW`. Tap to record
  16 kHz WAV (`AudioRecorder`), tap again to send it to `POST /v1/audio/transcriptions` on
  the PC. Dropping the bubble on the `CloseTarget` at the bottom of the screen stops it.
- `TextInsertService` is an accessibility service that pastes the text at the cursor of the
  focused field (falling back to setting the text), or leaves it on the clipboard.
- Pairing reads `echoink://pair?url=...&token=...` from the QR code in the desktop app's
  Phone access dialog.
