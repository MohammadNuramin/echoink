"""Startup entry point for EchoInk.

This file exists to solve a Windows-specific incompatibility:
importing PyQt6 loads GPU/DirectX DLLs that conflict with CUDA initialization.
By loading the Whisper/CUDA model HERE (before main.py is imported), we avoid
the conflict. Only lightweight, non-Qt imports are allowed in this file.
"""


def _add_nvidia_dll_paths() -> None:
    """Add pip-installed NVIDIA DLL directories to the DLL search path."""
    import os
    import sys
    if sys.platform != "win32":
        return
    site_packages = next(
        (p for p in sys.path if p.endswith("site-packages")), None
    )
    if not site_packages:
        return
    for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_runtime/bin",
                "nvidia/cuda_nvrtc/bin", "nvidia/cufft/bin", "nvidia/nvjitlink/bin"):
        dll_dir = os.path.join(site_packages, sub)
        if os.path.isdir(dll_dir):
            os.add_dll_directory(dll_dir)
            os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")


def main() -> None:
    """Bootstrap entry point: load CUDA model before Qt, then run the app."""
    _add_nvidia_dll_paths()

    # Step 1: load the Whisper model (may use CUDA) before any Qt code
    # is imported. This is the ONLY way to avoid the CUDA+DirectX crash on
    # Windows where importing PyQt6.QtWidgets already initialises GPU state.
    from echoink.api import get_model, set_device  # noqa: PLC0415
    from echoink.config import Config  # noqa: PLC0415
    try:
        set_device(Config.load().compute_device)  # GPU by default, CPU if configured
    except Exception:
        pass
    try:
        get_model()
    except Exception:
        pass  # Non-fatal; retry happens on first recording

    # Step 2: now import and run the full Qt application
    from echoink.main import main as _run_app  # noqa: PLC0415
    _run_app()
