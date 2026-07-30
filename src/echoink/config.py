"""Configuration management for EchoInk."""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TypedDict


def _default_hotkey() -> list[str]:
    """Return platform-appropriate default hotkey."""
    import sys
    if sys.platform == "win32":
        # Alt+Space conflicts with Windows window menu
        # Ctrl+Shift+Space conflicts with various apps
        # Win+Shift+V conflicts with clipboard history
        return ["f8"]
    else:
        return ["alt", "space"]


class HistoryEntry(TypedDict, total=False):
    """A history entry with text, timestamp, and optional audio file."""

    text: str
    timestamp: str  # ISO format
    audio_file: str  # Filename (not full path) of WAV recording


@dataclass
class Config:
    """Application configuration."""

    # Hotkey settings (using pynput key names)
    # Default: F8 on Windows (Alt+Space conflicts with window menu)
    #          Alt+Space on Linux/macOS
    hotkey: list[str] = field(default_factory=_default_hotkey)

    # Audio settings
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 1024
    input_device_index: int | str | None = None  # None = system default, str for PipeWire source ID
    input_device_name: str = ""  # For display purposes

    # UI settings
    waveform_color: str = "#84cc16"
    background_color: str = "#1a1a2e"
    window_width: int = 520
    window_height: int = 260  # Taller window for bigger waveform

    # Behavior
    auto_paste: bool = True
    copy_to_clipboard: bool = True
    language: str = "en"
    compute_device: str = "gpu"  # "gpu" (CUDA, default) or "cpu"; GPU falls back to CPU if unavailable
    typing_delay_ms: int = 5  # Milliseconds between keystrokes (increase if terminal freezes)
    max_auto_type_chars: int = 500  # Safety cap: skip auto-typing very long transcriptions
    max_recording_seconds: int = 45  # Safety cap: auto-stop recording if stop signal is missed

    # Floating hold-to-talk button
    hold_to_talk_button: bool = True
    hold_to_talk_x: int | None = None
    hold_to_talk_y: int | None = None

    # External tool integration (file-based, no ports)
    external_integration: bool = True  # Enable file-based external tool integration
    integration_timeout: float = 30.0  # Max seconds to wait for ready signal

    # History (recent transcriptions)
    history: list[HistoryEntry] = field(default_factory=list)
    history_max: int = 20
    store_recordings: bool = True  # Save audio files with transcriptions

    def get_recordings_dir(self) -> Path:
        """Get the directory for storing audio recordings."""
        import sys
        if sys.platform == "win32":
            config_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        recordings_dir = config_dir / "echoink" / "recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)
        return recordings_dir

    def add_to_history(self, text: str, audio_file: str | None = None) -> None:
        """Add a transcription to history.

        Args:
            text: The transcribed text
            audio_file: Optional filename of the WAV recording
        """
        if text and text.strip():
            # Remove if already exists (move to top) and delete old audio
            for i, entry in enumerate(self.history):
                entry_text = entry["text"] if isinstance(entry, dict) else entry
                if entry_text == text:
                    # Delete old audio file if it exists
                    old_audio = entry.get("audio_file") if isinstance(entry, dict) else None
                    if old_audio:
                        old_path = self.get_recordings_dir() / old_audio
                        if old_path.exists():
                            old_path.unlink()
                    self.history.pop(i)
                    break
            # Add to front with timestamp
            entry: HistoryEntry = {
                "text": text,
                "timestamp": datetime.now().isoformat(),
            }
            if audio_file:
                entry["audio_file"] = audio_file
            self.history.insert(0, entry)
            # Trim to max size and clean up old recordings
            self._cleanup_old_recordings()
            self.save()

    def _cleanup_old_recordings(self) -> None:
        """Remove old recordings beyond history_max limit."""
        # Get entries that will be removed
        removed_entries = self.history[self.history_max :]
        self.history = self.history[: self.history_max]

        # Delete audio files for removed entries
        recordings_dir = self.get_recordings_dir()
        for entry in removed_entries:
            if isinstance(entry, dict) and entry.get("audio_file"):
                audio_path = recordings_dir / entry["audio_file"]
                if audio_path.exists():
                    try:
                        audio_path.unlink()
                    except OSError:
                        pass  # Ignore errors deleting files

    @classmethod
    def get_config_path(cls) -> Path:
        """Get the configuration file path."""
        import sys
        if sys.platform == "win32":
            # Windows: use APPDATA
            config_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            # Linux/macOS: use XDG_CONFIG_HOME
            config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return config_dir / "echoink" / "config.json"

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from file or create default."""
        config_path = cls.get_config_path()

        if config_path.exists():
            try:
                with open(config_path) as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    print("Warning: config file is not a JSON object, using defaults")
                    return cls()
                # Migrate old string-based history to new format
                if "history" in data and data["history"]:
                    migrated = []
                    for entry in data["history"]:
                        if isinstance(entry, str):
                            migrated.append({"text": entry, "timestamp": ""})
                        elif isinstance(entry, dict) and "text" in entry:
                            migrated.append(entry)
                        # Skip malformed entries
                    data["history"] = migrated
                # Strip removed config fields for backward compatibility
                data.pop("claude_integration_port", None)
                data.pop("api_url", None)
                data.pop("api_key", None)
                # Strip any unknown fields to prevent TypeError
                known_fields = {f.name for f in __import__("dataclasses").fields(cls)}
                data = {k: v for k, v in data.items() if k in known_fields}
                return cls(**data)
            except Exception as e:
                print(f"Warning: Could not load config: {e}")

        return cls()

    def save(self) -> None:
        """Save configuration to file."""
        config_path = self.get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, "w") as f:
            json.dump(self.__dict__, f, indent=2)
