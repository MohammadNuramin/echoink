"""Startup entry point for EchoInk.

This file exists to solve a Windows-specific incompatibility:
importing PyQt6 loads GPU/DirectX DLLs that conflict with CUDA initialization.
By loading the Whisper/CUDA model HERE (before main.py is imported), we avoid
the conflict. Only lightweight, non-Qt imports are allowed in this file.
"""


def main() -> None:
    """Bootstrap entry point: load CUDA model before Qt, then run the app."""
    # Step 1: load the Whisper model (may use CUDA) before any Qt code
    # is imported. This is the ONLY way to avoid the CUDA+DirectX crash on
    # Windows where importing PyQt6.QtWidgets already initialises GPU state.
    from echoink.api import get_model  # noqa: PLC0415
    try:
        get_model()
    except Exception:
        pass  # Non-fatal; retry happens on first recording

    # Step 2: now import and run the full Qt application
    from echoink.main import main as _run_app  # noqa: PLC0415
    _run_app()
