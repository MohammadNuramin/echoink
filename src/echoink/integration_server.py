"""File-based signaling for external tool integration.

Uses a signal file instead of HTTP ports, making the app immune to
port-killing commands that other projects may run.
"""

import os
import sys
import time
from pathlib import Path


def _get_signal_dir() -> Path:
    """Get the platform-appropriate signal directory."""
    if sys.platform == "win32":
        config_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_dir / "echoink"


class IntegrationServer:
    """File-based integration for external tool communication.

    Replaces the old HTTP server approach. External tools write a timestamp
    to a signal file, and this class reads it to determine readiness.
    No network ports are used.
    """

    _signal_file: Path = _get_signal_dir() / "ready-signal"

    def __init__(self, port: int = 0):
        # port parameter kept for backward compatibility with config, but ignored
        pass

    def start(self) -> bool:
        """Ensure the signal directory exists. Always succeeds."""
        try:
            self._signal_file.parent.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            print(f"Integration setup failed: {e}")
            return False

    def stop(self) -> None:
        """No-op. Nothing to shut down with file-based signaling."""
        pass

    @staticmethod
    def get_signal_file() -> Path:
        """Return the path to the signal file."""
        return IntegrationServer._signal_file

    @staticmethod
    def is_ready(max_age: float = 5.0) -> bool:
        """Check if a ready signal was received within max_age seconds."""
        try:
            if not IntegrationServer._signal_file.exists():
                return False
            content = IntegrationServer._signal_file.read_text().strip()
            if not content:
                return False
            timestamp = float(content)
            return (time.time() - timestamp) < max_age
        except (ValueError, OSError):
            return False

    @staticmethod
    def reset_ready() -> None:
        """Reset the ready signal by removing the signal file."""
        try:
            if IntegrationServer._signal_file.exists():
                IntegrationServer._signal_file.unlink()
        except OSError:
            pass

    @staticmethod
    def signal_ready() -> None:
        """Write a ready signal (used for testing or local signaling)."""
        try:
            IntegrationServer._signal_file.parent.mkdir(parents=True, exist_ok=True)
            IntegrationServer._signal_file.write_text(str(time.time()))
        except OSError:
            pass
