"""Main application entry point for EchoInk."""

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# Platform-specific imports for single-instance locking
if sys.platform != "win32":
    import fcntl

from PyQt6.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSlider,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from . import bangla
from .api import WhisperAPIError, WhisperClient
from .config import Config
from .hotkey import create_hotkey_manager
from .icons import (
    get_check_icon,
    get_chevron_down_icon,
    get_chevron_up_icon,
    get_close_icon,
    get_mic_icon,
    get_copy_icon,
    get_play_icon,
    get_stop_icon,
    get_tray_icon,
)
from .recorder import AudioRecorder
from .typer import Typer
from .waveform import WaveformWidget


class SignalBridge(QObject):
    """Bridge for thread-safe Qt signals."""

    toggle_recording = pyqtSignal()
    update_waveform = pyqtSignal(float, list)
    transcription_complete = pyqtSignal(str)
    transcription_error = pyqtSignal(str)
    show_status = pyqtSignal(str)
    typing_finished = pyqtSignal()


class TickMarksWidget(QWidget):
    """Widget that draws tick mark notches for a slider."""

    def __init__(self, num_ticks: int = 11, parent=None):
        super().__init__(parent)
        self.num_ticks = num_ticks  # 0%, 20%, 40%... 200% = 11 ticks
        self.setFixedHeight(6)

    def paintEvent(self, event):
        from PyQt6.QtGui import QColor, QPainter, QPen

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(QColor("#666"))
        pen.setWidth(1)
        painter.setPen(pen)

        width = self.width()
        # Account for slider handle padding (roughly 8px on each side)
        padding = 8
        usable_width = width - 2 * padding

        for i in range(self.num_ticks):
            x = padding + int(i * usable_width / (self.num_ticks - 1))
            # Draw shorter tick for non-100% marks, taller for 100% (middle)
            if i == 5:  # 100% mark (middle)
                painter.drawLine(x, 0, x, 5)
            else:
                painter.drawLine(x, 2, x, 5)

        painter.end()


class HoldToTalkButton(QPushButton):
    """Floating push-to-talk button with animated visuals."""

    hold_started = pyqtSignal()
    hold_released = pyqtSignal()
    position_changed = pyqtSignal(int, int)

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._drag_start = None
        self._window_start = None
        self._dragging = False
        self._drag_threshold = 10
        self._recording = False
        self._hovered = False

        # Animation state
        self._pulse_phase = 0.0        # 0..2*pi, drives idle breathing ring
        self._bar_phase = 0.0          # drives recording sound-wave bars
        self._glow_opacity = 0.0       # smooth transition glow
        self._mic_scale = 1.0          # mic icon bounce on press

        self.setFixedSize(64, 64)
        self.setToolTip("Hold to talk")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent; border: none;")

        # 60 fps animation timer
        self._anim_timer = QTimer()
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.setInterval(16)  # ~60fps
        self._anim_timer.start()

        self._set_initial_position()

        # Periodically re-raise to stay on top of fullscreen/other always-on-top windows
        self._raise_timer = QTimer()
        self._raise_timer.timeout.connect(self._ensure_on_top)
        self._raise_timer.setInterval(1000)

    def _set_initial_position(self) -> None:
        """Position button at saved location or bottom-right corner.

        Validates saved position is actually on-screen to prevent invisible button.
        """
        screen = QApplication.primaryScreen().availableGeometry()
        if self.config.hold_to_talk_x is not None and self.config.hold_to_talk_y is not None:
            x = self.config.hold_to_talk_x
            y = self.config.hold_to_talk_y
            # Ensure button is on-screen (with at least half visible)
            half_w = self.width() // 2
            half_h = self.height() // 2
            if (x + half_w < screen.left() or x > screen.right() - half_w
                    or y + half_h < screen.top() or y > screen.bottom() - half_h):
                # Saved position is off-screen, reset to default
                x = screen.right() - self.width() - 30
                y = screen.bottom() - self.height() - 80
                self.config.hold_to_talk_x = x
                self.config.hold_to_talk_y = y
                self.config.save()
        else:
            x = screen.right() - self.width() - 30
            y = screen.bottom() - self.height() - 80
        self.move(x, y)

    def _ensure_on_top(self) -> None:
        """Re-raise button to stay on top of all windows."""
        if self.isVisible():
            self.raise_()
            if sys.platform == "win32":
                try:
                    import ctypes
                    import ctypes.wintypes

                    user32 = ctypes.windll.user32
                    # Set argtypes so 64-bit HWNDs aren't truncated to 32-bit
                    user32.SetWindowPos.argtypes = [
                        ctypes.c_void_p,  # hWnd
                        ctypes.c_void_p,  # hWndInsertAfter
                        ctypes.c_int, ctypes.c_int,  # X, Y
                        ctypes.c_int, ctypes.c_int,  # cx, cy
                        ctypes.c_uint,               # uFlags
                    ]
                    user32.SetWindowPos.restype = ctypes.c_bool

                    hwnd = int(self.winId())
                    HWND_TOPMOST = ctypes.c_void_p(-1)
                    SWP_NOMOVE = 0x0002
                    SWP_NOSIZE = 0x0001
                    SWP_NOACTIVATE = 0x0010
                    user32.SetWindowPos(
                        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                    )
                except Exception:
                    pass

    def showEvent(self, event) -> None:
        """Start the always-on-top timer when button becomes visible."""
        super().showEvent(event)
        self._strip_native_border()
        if not self._raise_timer.isActive():
            self._raise_timer.start()
            self._ensure_on_top()

    def _strip_native_border(self) -> None:
        """Remove the Windows 11 window outline DWM paints around the button.

        The button is a frameless, translucent top-level window, so Windows
        draws a rounded-rectangle border/backdrop around the window bounds —
        which appears as a box around the black circle. Tell DWM to draw no
        border and not round the corners so only the circle is visible.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi

            # DWMWA_BORDER_COLOR = 34, DWMWA_COLOR_NONE removes the border.
            border_color = ctypes.c_uint(0xFFFFFFFE)
            dwm.DwmSetWindowAttribute(
                hwnd, 34, ctypes.byref(border_color), ctypes.sizeof(border_color)
            )
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_DONOTROUND = 1.
            corner_pref = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(corner_pref), ctypes.sizeof(corner_pref)
            )
        except Exception:
            pass

    def _tick(self) -> None:
        """Advance animation state each frame — only when recording."""
        import math
        if not self._recording:
            return
        dt = 0.016
        self._bar_phase = (self._bar_phase + dt * 6.0) % (2 * math.pi)
        self.update()

    def enterEvent(self, event) -> None:
        self._hovered = True
        if not self._recording:
            self.hold_started.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        """Custom-paint the button: flat black + white."""
        import math
        from PyQt6.QtGui import QBrush, QColor, QPainter, QPen

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = 26

        # Flat black circle
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0)))
        p.drawEllipse(int(cx - radius), int(cy - radius), radius * 2, radius * 2)

        white = QColor(255, 255, 255)

        if self._recording:
            # Animated equalizer bars in white
            bar_w = 3
            bar_gap = 5
            num_bars = 5
            total_w = num_bars * bar_w + (num_bars - 1) * bar_gap
            start_x = cx - total_w / 2
            p.setBrush(QBrush(white))
            for i in range(num_bars):
                phase_offset = i * 0.9
                bar_h = 6 + 14 * abs(math.sin(self._bar_phase + phase_offset))
                bx = start_x + i * (bar_w + bar_gap)
                by = cy - bar_h / 2
                p.drawRoundedRect(int(bx), int(by), bar_w, int(bar_h), 1.5, 1.5)
        else:
            # White mic icon
            from PyQt6.QtCore import QRectF
            pen = QPen(white, 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)

            p.save()
            p.translate(cx, cy)

            mic_w, mic_h = 8, 14
            p.drawRoundedRect(int(-mic_w / 2), int(-mic_h / 2 - 2),
                              mic_w, mic_h, mic_w / 2, mic_w / 2)
            arc_rect = QRectF(-10, -10, 20, 20)
            p.drawArc(arc_rect, 210 * 16, 120 * 16)
            p.drawLine(0, 10, 0, 14)
            p.drawLine(-5, 14, 5, 14)

            p.restore()

        p.end()

    def set_recording(self, recording: bool) -> None:
        """Update visual state for recording activity."""
        self._recording = recording
        if recording:
            self._mic_scale = 0.7  # bounce effect on press
        self.update()

    def mousePressEvent(self, event) -> None:
        """Click to stop recording, or start drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.globalPosition().toPoint()
            self._window_start = self.pos()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Allow dragging the floating button."""
        if self._drag_start is not None and self._window_start is not None:
            delta = event.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() > self._drag_threshold:
                self._dragging = True
                self.move(self._window_start + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Click stops recording. Drag saves position."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                self.position_changed.emit(self.x(), self.y())
            elif self._recording:
                self.hold_released.emit()
            self._drag_start = None
            self._window_start = None
            self._dragging = False
        super().mouseReleaseEvent(event)


class RecordingWindow(QWidget):
    """Floating window showing waveform during recording."""

    # Signal emitted when ESC is pressed to cancel
    cancel_requested = pyqtSignal()

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._drag_pos = None  # For dragging support
        self._setup_ui()

        # Timer to refresh integration status while settings panel is open
        self._integration_timer = QTimer()
        self._integration_timer.timeout.connect(self._update_integration_status)
        self._integration_timer.setInterval(1000)  # Update every second

    def _setup_ui(self) -> None:
        """Set up the recording window UI."""
        # Set window icon for taskbar (orange = idle)
        self.setWindowIcon(EchoInk._get_app_icon())

        # Frameless, always on top, floating window that doesn't steal focus
        # Store base flags for toggling focus behavior
        self._base_window_flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowFlags(self._base_window_flags | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # Don't steal focus
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Never accept keyboard focus
        # Allow resize via mouse
        self._resize_edge = None

        # Main container with rounded corners and purple gradient
        container = QWidget(self)
        container.setObjectName("container")
        container.setStyleSheet(
            """
            #container {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #2d1b4e,
                    stop:0.5 #1a1033,
                    stop:1 #0f0a1a
                );
                border-radius: 12px;
                border: 1px solid #4a3070;
            }
        """
        )

        # Use a stacked layout - waveform behind, controls on top
        from PyQt6.QtWidgets import QFrame

        # Container layout
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Create a frame for the main content
        content_frame = QFrame()
        content_frame.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(content_frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        container_layout.addWidget(content_frame)

        # Waveform
        self.waveform = WaveformWidget(
            color="#84cc16",  # Same bright green as buttons
            bg_color=self.config.background_color,
        )
        self.waveform.setMinimumHeight(160)  # Bigger orb
        layout.addWidget(self.waveform, stretch=2)  # Give it more priority

        # Status row - transparent background so orb shows through
        status_widget = QWidget()
        status_widget.setStyleSheet("background: transparent;")
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(4, 0, 4, 0)

        self.status_label = QLabel("Listening...")
        self.status_label.setStyleSheet(
            """
            color: #888;
            font-size: 11px;
        """
        )
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        # Hint label - show configured hotkey
        self._hotkey_str = "+".join(k.title() for k in self.config.hotkey)
        self.hints_label = QLabel(f"Start: {self._hotkey_str}")
        self.hints_label.setStyleSheet(
            """
            color: #666;
            font-size: 10px;
        """
        )
        status_layout.addWidget(self.hints_label)

        # Animated status timer
        self._status_dots = 0
        self._status_timer = QTimer()
        self._status_timer.timeout.connect(self._animate_status)
        self._status_timer.setInterval(400)

        layout.addWidget(status_widget)

        # More toggle button - chevron icon
        self.settings_btn = QPushButton()
        self.settings_btn.setIcon(get_chevron_down_icon(20, "#84cc16"))
        self.settings_btn.setFixedSize(40, 28)
        self.settings_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Prevent SPACE triggering
        self.settings_btn.setStyleSheet(
            """
            QPushButton {
                background: rgba(132, 204, 22, 0.1);
                border: 1px solid rgba(132, 204, 22, 0.3);
                border-radius: 6px;
            }
            QPushButton:hover {
                background: rgba(132, 204, 22, 0.2);
            }
        """
        )
        self.settings_btn.clicked.connect(self._toggle_settings)
        layout.addWidget(self.settings_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Collapsible settings panel
        self.settings_panel = QWidget()
        self.settings_panel.setStyleSheet(
            """
            QWidget {
                background-color: rgba(0, 0, 0, 0.3);
                border-radius: 8px;
            }
            QLabel {
                color: #888;
                font-size: 10px;
            }
            QSlider::groove:horizontal {
                background: #333;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #84cc16;
                width: 14px;
                margin: -4px 0;
                border-radius: 7px;
            }
        """
        )
        settings_layout = QVBoxLayout(self.settings_panel)
        settings_layout.setContentsMargins(12, 8, 12, 8)
        settings_layout.setSpacing(8)

        # Microphone selection
        mic_label = QLabel("Microphone")
        self.mic_combo = QComboBox()
        self.mic_combo.setStyleSheet(
            """
            QComboBox {
                background-color: rgba(255, 255, 255, 0.1);
                border: 1px solid #4a3070;
                border-radius: 4px;
                color: #fff;
                padding: 6px;
                font-size: 11px;
            }
            QComboBox::drop-down {
                border: none;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #888;
                margin-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1033;
                border: 1px solid #4a3070;
                color: #fff;
                selection-background-color: rgba(132, 204, 22, 0.3);
            }
        """
        )
        self._populate_mic_dropdown()
        settings_layout.addWidget(mic_label)
        settings_layout.addWidget(self.mic_combo)

        # Language: auto switches between English and Bangla per recording.
        installed = bangla.is_installed()
        language_label = QLabel(
            "Language" if installed else "Language (Bangla model not installed)"
        )
        self.language_combo = QComboBox()
        self.language_combo.setStyleSheet(self.mic_combo.styleSheet())
        self.language_combo.addItem("Auto: English + Bangla", "auto")
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("Bangla", "bn")
        index = self.language_combo.findData(self.config.language_mode)
        self.language_combo.setCurrentIndex(max(0, index))
        if not installed:
            self.language_combo.setToolTip(
                "Convert the Bangla model with scripts/export_bangla_model.py (see README)."
            )
        settings_layout.addWidget(language_label)
        settings_layout.addWidget(self.language_combo)

        # Orukeet (English) always runs on the CPU; this picks where Bangla and
        # language detection run.
        device_label = QLabel("Processing")
        self.device_combo = QComboBox()
        self.device_combo.setStyleSheet(self.mic_combo.styleSheet())
        self.device_combo.addItem("GPU for Bangla (fastest)", "gpu")
        self.device_combo.addItem("CPU only (slower)", "cpu")
        want = "cpu" if getattr(self.config, "compute_device", "gpu") == "cpu" else "gpu"
        self.device_combo.setCurrentIndex(1 if want == "cpu" else 0)
        settings_layout.addWidget(device_label)
        settings_layout.addWidget(self.device_combo)

        # Gain slider with dynamic level display in groove
        # 0-200% range, with 100% (1.0x) in the middle
        gain_row = QHBoxLayout()
        self.gain_label = QLabel("Mic Gain:")
        self.gain_value_label = QLabel("100%")
        self.gain_value_label.setStyleSheet("color: #84cc16; font-weight: bold;")
        gain_row.addWidget(self.gain_label)
        gain_row.addStretch()
        gain_row.addWidget(self.gain_value_label)
        self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(0, 200)
        self.sensitivity_slider.setValue(100)  # 100% = no gain adjustment
        self.sensitivity_slider.setSingleStep(20)  # Arrow keys move by 20%
        self.sensitivity_slider.setPageStep(20)  # Page up/down move by 20%
        self.sensitivity_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.sensitivity_slider.setTickInterval(20)  # Tick every 20% (20 units = 20%)
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity_changed)
        self._current_mic_level = 0  # Track current level for styling
        self._update_sensitivity_style()
        settings_layout.addLayout(gain_row)
        settings_layout.addWidget(self.sensitivity_slider)

        # Tick marks below slider (visual notches at 20% intervals)
        tick_marks = TickMarksWidget(num_ticks=11)  # 0%, 20%, 40%... 200%
        settings_layout.addWidget(tick_marks)

        # History section
        history_label = QLabel("Recent Clips")
        settings_layout.addWidget(history_label)

        self.history_list = QListWidget()
        self.history_list.setMinimumHeight(200)
        self.history_list.setMaximumHeight(320)  # ~10 items at 32px each
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.history_list.setStyleSheet(
            """
            QListWidget {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid #4a3070;
                border-radius: 4px;
                color: #ccc;
                font-size: 11px;
            }
            QListWidget::item {
                padding: 2px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            }
            QListWidget::item:hover {
                background-color: rgba(132, 204, 22, 0.1);
            }
        """
        )
        self._refresh_history()
        settings_layout.addWidget(self.history_list)

        # External integration status
        integration_row = QHBoxLayout()
        integration_row.setSpacing(8)
        integration_label = QLabel("External Tools")
        integration_label.setStyleSheet("color: #888; font-size: 11px;")
        self._integration_status = QLabel()
        self._integration_status.setStyleSheet("font-size: 11px;")
        self._update_integration_status()
        integration_row.addWidget(integration_label)
        integration_row.addWidget(self._integration_status)
        integration_row.addStretch()
        settings_layout.addLayout(integration_row)

        # Save button - at the bottom, vibrant green
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #84cc16;
                color: #000;
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #9ae62a;
            }
        """
        )
        self.save_btn.clicked.connect(self._save_settings)
        settings_layout.addWidget(self.save_btn)

        self.settings_panel.hide()  # Hidden by default
        layout.addWidget(self.settings_panel)

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        # Close button - overlaid in top-right corner (not in layout)
        self.close_btn = QPushButton(container)
        self.close_btn.setIcon(get_close_icon(14, "#666666"))
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setToolTip("Close")
        self.close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Prevent SPACE triggering
        self.close_btn.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: none;
            }
        """
        )
        self.close_btn.clicked.connect(self._close_window)
        # Hover behavior - change icon to green instead of background
        self.close_btn.enterEvent = lambda e: self.close_btn.setIcon(get_close_icon(14, "#84cc16"))
        self.close_btn.leaveEvent = lambda e: self.close_btn.setIcon(get_close_icon(14, "#666666"))
        self.close_btn.move(self.config.window_width - 28, 8)  # Top-right corner
        self.close_btn.raise_()  # Bring to front

        # Version label - overlaid in top-left corner (not in layout)
        self.version_label = QLabel("v1.0.0", container)
        self.version_label.setStyleSheet(
            """
            color: #666;
            font-size: 10px;
        """
        )
        self.version_label.move(12, 8)

        # Size
        self.setFixedSize(self.config.window_width, self.config.window_height)

    def update_icon(self, recording: bool) -> None:
        """Update window icon based on recording state."""
        self.setWindowIcon(EchoInk._get_app_icon())

    def keyPressEvent(self, event) -> None:
        """Handle key presses - ESC cancels recording."""
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
        else:
            super().keyPressEvent(event)

    def set_status(self, text: str, animate: bool = False) -> None:
        """Update status label."""
        self._base_status = text
        self._status_dots = 0
        self.status_label.setText(text)
        if animate:
            self._status_timer.start()
        else:
            self._status_timer.stop()

    def set_recording_hint(self, recording: bool) -> None:
        """Update hint text based on recording state."""
        action = "Stop" if recording else "Start"
        self.hints_label.setText(f"{action}: {self._hotkey_str}")

    def update_mic_level(self, level: float) -> None:
        """Update the mic level display in sensitivity slider (0.0 to 1.0 scale)."""
        # Only update if level changed significantly (reduces stylesheet updates)
        if abs(level - self._current_mic_level) > 0.01 or level == 0:
            self._current_mic_level = level
            self._update_sensitivity_style()

    def _animate_status(self) -> None:
        """Animate the status text with dots."""
        self._status_dots = (self._status_dots + 1) % 4
        dots = "." * self._status_dots
        self.status_label.setText(f"{self._base_status}{dots}")

    def _toggle_settings(self) -> None:
        """Toggle settings panel visibility."""
        if self.settings_panel.isVisible():
            self.settings_panel.hide()
            self.settings_btn.setIcon(get_chevron_down_icon(20, "#84cc16"))
            # Shrink window
            self.setFixedSize(self.config.window_width, self.config.window_height)
            # Stop integration status updates
            self._integration_timer.stop()
            # Restore no-focus behavior for recording
            self.setWindowFlags(self._base_window_flags | Qt.WindowType.WindowDoesNotAcceptFocus)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.show()  # setWindowFlags hides the window, so re-show it
        else:
            self.settings_panel.show()
            self.settings_btn.setIcon(get_chevron_up_icon(20, "#84cc16"))
            # Expand window - make it tall enough for all settings + taller history
            self.setFixedSize(self.config.window_width, self.config.window_height + 570)
            # Refresh integration status and start auto-update timer
            self._update_integration_status()
            self._integration_timer.start()
            # Allow focus so user can edit settings
            self.setWindowFlags(self._base_window_flags)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.show()  # setWindowFlags hides the window, so re-show it
            self.activateWindow()  # Bring to front and activate

    def _copy_to_clipboard(self, text: str, button: QPushButton = None) -> None:
        """Copy text to clipboard and show feedback on button."""
        clipboard = QApplication.clipboard()
        clipboard.setText(text)

        # Show "Copied" feedback on button if provided
        if button:
            original_icon = button.icon()
            button.setIcon(get_check_icon(16, "#84cc16"))
            QTimer.singleShot(1500, lambda: button.setIcon(original_icon))

    def _on_sensitivity_changed(self, value: int) -> None:
        """Handle gain slider change - update in real-time with 20% snapping."""
        # Snap to nearest 20% increment
        snapped = round(value / 20) * 20
        if snapped != value:
            self.sensitivity_slider.blockSignals(True)
            self.sensitivity_slider.setValue(snapped)
            self.sensitivity_slider.blockSignals(False)
            value = snapped

        self.waveform.sensitivity = value
        self.gain_value_label.setText(f"{value}%")
        self._update_sensitivity_style()

    def _update_sensitivity_style(self) -> None:
        """Update the gain slider groove to show current mic level after gain."""
        # Apply gain to the raw level for visualization
        gain = self.sensitivity_slider.value() / 100.0  # 0-2.0
        gained_level = min(1.0, self._current_mic_level * gain * 5)  # Scale for visibility
        level_pct = int(gained_level * 100)

        self.sensitivity_slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #84cc16,
                    stop:{level_pct / 100:.2f} #84cc16,
                    stop:{min(1.0, level_pct / 100 + 0.01):.2f} #333,
                    stop:1 #333
                );
                height: 8px;
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: #fff;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
                border: 2px solid #84cc16;
            }}
            QSlider::sub-page:horizontal {{
                background: transparent;
            }}
            QSlider::add-page:horizontal {{
                background: transparent;
            }}
            QSlider {{
                height: 24px;
            }}
        """
        )

    def _populate_mic_dropdown(self) -> None:
        """Populate the microphone dropdown with available devices."""
        import sys

        from .recorder import get_pipewire_sources

        self.mic_combo.clear()
        self.mic_combo.addItem("System Default", None)

        # Get input devices - use PipeWire on Linux, PyAudio elsewhere
        if sys.platform.startswith("linux"):
            pw_sources = get_pipewire_sources()
            if pw_sources:
                for src in pw_sources:
                    idx = src["id"]  # PipeWire source ID
                    name = src["description"]
                    display = f"{name} (48000Hz)"
                    self.mic_combo.addItem(display, idx)
                return

        # Fallback to PyAudio device enumeration
        import pyaudio

        try:
            audio = pyaudio.PyAudio()
            for i in range(audio.get_device_count()):
                try:
                    info = audio.get_device_info_by_index(i)
                    if info["maxInputChannels"] > 0 and info["maxOutputChannels"] == 0:
                        name = info["name"]
                        rate = int(info["defaultSampleRate"])
                        self.mic_combo.addItem(f"{name} ({rate}Hz)", i)
                except Exception:
                    pass
            audio.terminate()
        except Exception as e:
            print(f"Could not enumerate audio devices: {e}")

        # Select the saved device
        if self.config.input_device_index is not None:
            for i in range(self.mic_combo.count()):
                if self.mic_combo.itemData(i) == self.config.input_device_index:
                    self.mic_combo.setCurrentIndex(i)
                    break

    def _save_settings(self) -> None:
        """Save settings to config."""
        self.config.input_device_index = self.mic_combo.currentData()
        self.config.input_device_name = self.mic_combo.currentText()

        # Language and compute device (GPU/CPU). If either changed, load the models
        # the new setting needs in the background so the switch takes effect immediately.
        new_language = self.language_combo.currentData()
        new_device = self.device_combo.currentData()
        language_changed = new_language != self.config.language_mode
        device_changed = new_device != getattr(self.config, "compute_device", "gpu")
        self.config.language_mode = new_language
        self.config.compute_device = new_device
        if language_changed or device_changed:
            try:
                from . import api
                api.set_device(new_device)
                api.preload_model(new_language)
            except Exception as e:
                print(f"Could not switch speech models: {e}")

        self.config.save()
        # Brief confirmation
        self.save_btn.setText("✓ Saved!")
        QTimer.singleShot(1500, lambda: self.save_btn.setText("Save Settings"))

    def _update_integration_status(self) -> None:
        """Update the external integration status indicator."""
        if not self.config.external_integration:
            self._integration_status.setText("Disabled")
            self._integration_status.setStyleSheet("color: #666; font-size: 11px;")
            return

        from .integration_server import IntegrationServer

        # Ready if signal within last 30 seconds (matches typing logic)
        if IntegrationServer.is_ready(max_age=30.0):
            self._integration_status.setText("Ready")
            self._integration_status.setStyleSheet("color: #84cc16; font-size: 11px;")
        else:
            self._integration_status.setText("Busy")
            self._integration_status.setStyleSheet("color: #f59e0b; font-size: 11px;")

    def _refresh_history(self) -> None:
        """Refresh the history list from config."""
        self.history_list.clear()
        for entry in self.config.history:
            # Handle both old (string) and new (dict) formats
            if isinstance(entry, dict):
                text = entry.get("text", "")
                timestamp = entry.get("timestamp", "")
                audio_file = entry.get("audio_file", "")
            else:
                text = entry
                timestamp = ""
                audio_file = ""

            # Format timestamp for display (date and time)
            time_str = ""
            if timestamp:
                try:
                    from datetime import datetime

                    dt = datetime.fromisoformat(timestamp)
                    time_str = dt.strftime("%b %d %H:%M") + " "  # "Dec 30 14:35"
                except ValueError:
                    pass

            # Create custom widget for this entry
            widget = QWidget()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(4, 2, 4, 2)
            layout.setSpacing(4)

            # Text label (truncated)
            display = text[:40] + "..." if len(text) > 40 else text
            display = f"{time_str}{display}"
            label = QLabel(display)
            label.setStyleSheet("color: #ccc; font-size: 11px;")
            label.setToolTip(text)  # Full text on hover
            layout.addWidget(label, stretch=1)

            # Copy button
            copy_btn = QPushButton()
            copy_btn.setIcon(get_copy_icon(14, "#888"))
            copy_btn.setFixedSize(24, 24)
            copy_btn.setToolTip("Copy to clipboard")
            copy_btn.setStyleSheet(
                """
                QPushButton {
                    background: transparent;
                    border: none;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background: rgba(132, 204, 22, 0.2);
                }
            """
            )
            copy_btn.clicked.connect(lambda checked, t=text: self._copy_history_item(t))
            layout.addWidget(copy_btn)

            # Play button (only if audio file exists)
            if audio_file:
                play_btn = QPushButton()
                play_btn.setIcon(get_play_icon(14, "#888"))
                play_btn.setFixedSize(24, 24)
                play_btn.setToolTip("Play recording")
                play_btn.setStyleSheet(
                    """
                    QPushButton {
                        background: transparent;
                        border: none;
                        border-radius: 4px;
                    }
                    QPushButton:hover {
                        background: rgba(132, 204, 22, 0.2);
                    }
                """
                )
                play_btn.clicked.connect(
                    lambda checked, f=audio_file, b=play_btn: self._play_audio(f, b)
                )
                layout.addWidget(play_btn)

            # Add item to list
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            self.history_list.addItem(item)
            self.history_list.setItemWidget(item, widget)

    def _copy_history_item(self, text: str) -> None:
        """Copy a history item to clipboard."""
        self._copy_to_clipboard(text)
        # Show brief status update
        self.set_status("Copied!")

    def _play_audio(self, filename: str, button: QPushButton) -> None:
        """Play or stop an audio recording."""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

        # If already playing this file, stop it
        if hasattr(self, "_playing_button") and self._playing_button == button:
            self._media_player.stop()
            button.setIcon(get_play_icon(14, "#888"))
            button.setToolTip("Play recording")
            self._playing_button = None
            return

        audio_path = self.config.get_recordings_dir() / filename
        if not audio_path.exists():
            self.set_status("Audio file not found")
            return

        # Create or reuse media player
        if not hasattr(self, "_media_player"):
            self._media_player = QMediaPlayer()
            self._audio_output = QAudioOutput()
            self._media_player.setAudioOutput(self._audio_output)
            # Connect to playback state changes
            self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)

        # Stop any current playback and reset previous button
        if hasattr(self, "_playing_button") and self._playing_button:
            self._playing_button.setIcon(get_play_icon(14, "#888"))
            self._playing_button.setToolTip("Play recording")
        self._media_player.stop()

        # Update button to stop icon
        button.setIcon(get_stop_icon(14, "#888"))
        button.setToolTip("Stop playback")
        self._playing_button = button

        # Play the file
        self._media_player.setSource(QUrl.fromLocalFile(str(audio_path)))
        self._audio_output.setVolume(1.0)
        self._media_player.play()

    def _on_playback_state_changed(self, state) -> None:
        """Handle media player state changes."""
        from PyQt6.QtMultimedia import QMediaPlayer

        if state == QMediaPlayer.PlaybackState.StoppedState:
            # Reset button icon when playback stops
            if hasattr(self, "_playing_button") and self._playing_button:
                self._playing_button.setIcon(get_play_icon(14, "#888"))
                self._playing_button.setToolTip("Play recording")
                self._playing_button = None

    def _close_window(self) -> None:
        """Close the window (emits cancel if recording)."""
        self.cancel_requested.emit()
        self.hide()

    def center_on_screen(self) -> None:
        """Center window on the screen."""
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = int(screen.height() * 0.3)  # Upper third of screen
        self.move(x, y)

    def mousePressEvent(self, event) -> None:
        """Handle mouse press for dragging."""
        if event.button() == Qt.MouseButton.LeftButton:
            # Use startSystemMove for Wayland compatibility
            if hasattr(self.windowHandle(), "startSystemMove"):
                self.windowHandle().startSystemMove()
            else:
                # Fallback for X11
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move for dragging (X11 fallback)."""
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        """Handle mouse release."""
        self._drag_pos = None


class EchoInk:
    """Main application class."""

    def __init__(self):
        # Set AppUserModelID so Windows taskbar shows the correct icon
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("echoink.app")

        self.config = Config.load()
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("EchoInk")
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setWindowIcon(self._get_app_icon())

        # Components
        self.recorder = AudioRecorder(self.config)
        self.client = WhisperClient(self.config)
        self.typer = Typer(typing_delay_ms=self.config.typing_delay_ms)
        self.signals = SignalBridge()

        # UI
        self.window = RecordingWindow(self.config)
        self.hold_to_talk_button = HoldToTalkButton(self.config)
        self._setup_tray()

        # State
        self.is_recording = False
        self.is_typing = False
        self._typing_thread = None
        self._typing_stop_event = threading.Event()
        self._pending_waveform_data = None  # Thread-safe buffer for waveform data
        self._transcription_pool = ThreadPoolExecutor(max_workers=1)

        # Connect signals
        self.signals.toggle_recording.connect(self._toggle_recording)
        self.signals.transcription_complete.connect(self._on_transcription_complete)
        self.signals.transcription_error.connect(self._on_transcription_error)
        self.signals.show_status.connect(self.window.set_status)
        self.signals.typing_finished.connect(self._on_typing_finished)
        self.window.cancel_requested.connect(self._cancel_recording)
        self.hold_to_talk_button.hold_started.connect(self._on_hold_to_talk_start)
        self.hold_to_talk_button.hold_released.connect(self._on_hold_to_talk_release)
        self.hold_to_talk_button.position_changed.connect(self._save_hold_to_talk_position)

        # Timer to poll waveform data from recorder thread (avoids cross-thread signal issues)
        self._waveform_timer = QTimer()
        self._waveform_timer.timeout.connect(self._poll_waveform_data)
        self._waveform_timer.setInterval(30)  # Poll at ~33 FPS

        # Failsafe: stop long-running recordings automatically.
        self._recording_timeout_timer = QTimer()
        self._recording_timeout_timer.setSingleShot(True)
        self._recording_timeout_timer.timeout.connect(self._on_recording_timeout)

        # Hotkey - use appropriate backend for platform
        self.hotkey_manager = create_hotkey_manager(
            self.config.hotkey,
            lambda: self.signals.toggle_recording.emit(),
        )
        if self.hotkey_manager is None:
            print("Warning: Global hotkeys not available on this platform")

        # File-based external tool integration (no ports needed)
        self.integration_server = None
        if self.config.external_integration:
            from .integration_server import IntegrationServer

            self.integration_server = IntegrationServer()
            if not self.integration_server.start():
                self.integration_server = None

    @staticmethod
    def _get_app_icon() -> QIcon:
        """Load the app icon from assets/echoink.ico."""
        from pathlib import Path
        ico_path = Path(__file__).parent.parent.parent / "assets" / "echoink.ico"
        if ico_path.exists():
            return QIcon(str(ico_path))
        return get_tray_icon(64, recording=False)

    def _setup_tray(self) -> None:
        """Set up system tray icon."""
        self.tray = QSystemTrayIcon(self.app)
        self.tray.setIcon(self._get_app_icon())
        hotkey_str = "+".join(k.capitalize() for k in self.config.hotkey)
        self.tray.setToolTip(f"EchoInk - Press {hotkey_str} to dictate")

        # Context menu
        menu = QMenu()

        show_action = QAction("Show Window", menu)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        self.hold_to_talk_action = QAction("", menu)
        self.hold_to_talk_action.triggered.connect(self._toggle_hold_to_talk_button)
        menu.addAction(self.hold_to_talk_action)
        self._update_hold_to_talk_menu_label()

        self.toggle_action = QAction("Start Recording", menu)
        self.toggle_action.triggered.connect(self._toggle_recording)
        menu.addAction(self.toggle_action)

        menu.addSeparator()

        settings_action = QAction("Settings...", menu)
        settings_action.triggered.connect(self._show_settings)
        menu.addAction(settings_action)

        reload_action = QAction("Reload audio && model", menu)
        reload_action.triggered.connect(self._reload_engine)
        menu.addAction(reload_action)

        menu.addSeparator()

        if sys.platform == "win32":
            self.startup_action = QAction("Start on Login", menu)
            self.startup_action.setCheckable(True)
            self.startup_action.setChecked(self._is_startup_enabled())
            self.startup_action.triggered.connect(self._toggle_startup)
            menu.addAction(self.startup_action)

            menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon clicks."""
        # Trigger = left click, DoubleClick = double click
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def _update_icons(self, recording: bool) -> None:
        """Update all icons based on recording state."""
        self.tray.setIcon(EchoInk._get_app_icon())
        self.window.update_icon(recording=recording)
        self.hold_to_talk_button.set_recording(recording=recording)

    def _update_hold_to_talk_menu_label(self) -> None:
        """Update tray menu label for hold-to-talk button visibility."""
        if self.config.hold_to_talk_button:
            self.hold_to_talk_action.setText("Hide Hold-to-Talk Button")
        else:
            self.hold_to_talk_action.setText("Show Hold-to-Talk Button")

    def _toggle_hold_to_talk_button(self) -> None:
        """Toggle visibility of the floating hold-to-talk button."""
        self.config.hold_to_talk_button = not self.config.hold_to_talk_button
        if self.config.hold_to_talk_button:
            self.hold_to_talk_button.show()
        else:
            self.hold_to_talk_button.hide()
        self.config.save()
        self._update_hold_to_talk_menu_label()

    def _on_hold_to_talk_start(self) -> None:
        """Start recording when hold-to-talk button is pressed."""
        if self.is_typing:
            self._request_stop_typing()
            return
        if not self.is_recording:
            self._start_recording()

    def _on_hold_to_talk_release(self) -> None:
        """Stop/transcribe when hold-to-talk button is released."""
        if self.is_recording:
            self._stop_recording()

    def _save_hold_to_talk_position(self, x: int, y: int) -> None:
        """Persist floating button position after drag."""
        self.config.hold_to_talk_x = x
        self.config.hold_to_talk_y = y
        self.config.save()

    def _save_wav(self, path, audio_data: bytes) -> None:
        """Save audio data as a WAV file."""
        import wave

        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(self.config.channels)
            wf.setsampwidth(2)  # 16-bit audio = 2 bytes
            wf.setframerate(self.config.sample_rate)
            wf.writeframes(audio_data)

    def _show_window(self) -> None:
        """Show the window without starting recording (doesn't steal focus)."""
        self.window.waveform.set_recording(False)
        self.window.set_recording_hint(recording=False)
        self._update_icons(recording=False)
        self.window.set_status("Ready", animate=False)
        self.window.center_on_screen()
        self.window.show()
        self.window.raise_()
        # Note: Don't call activateWindow() - keeps focus in user's original app

    def _show_settings(self) -> None:
        """Show the window with settings panel expanded (takes focus for editing)."""
        self._show_window()
        # Expand settings if not already visible
        if not self.window.settings_panel.isVisible():
            self.window._toggle_settings()
        # Take focus so user can edit settings fields
        self.window.activateWindow()

    def _toggle_recording(self) -> None:
        """Toggle recording state."""
        if self.is_typing:
            self._request_stop_typing()
            return
        if self.is_recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _reload_engine(self) -> None:
        """Rebuild audio capture and the speech models without restarting the app.

        Recovers from a stale PortAudio handle or CUDA/session state after the
        machine sleeps/wakes or the USB mic power-cycles — the usual cause of
        "the button works but nothing transcribes".
        """
        if self.is_recording:
            try:
                self._cancel_recording()
            except Exception as e:
                print(f"Reload: cancel recording failed: {e}")

        try:
            self.recorder.reinit()
        except Exception as e:
            print(f"Reload: recorder reinit failed: {e}")

        from . import api
        api.reset_model()
        api.preload_model(self.config.language_mode)

        self.tray.showMessage(
            "EchoInk",
            "Audio and model reloaded.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def _start_recording(self) -> None:
        """Start recording audio."""
        if self.is_recording:
            return

        self.is_recording = True
        self.toggle_action.setText("Stop Recording")
        self._update_icons(recording=True)

        # Record silently without popping up the center waveform window.
        self.window.waveform.set_recording(True)
        self.window.set_recording_hint(recording=True)
        self.window.set_status("Listening", animate=True)
        self.window.hide()

        # Hide settings panel if open (it changes window focus behavior)
        if self.window.settings_panel.isVisible():
            self.window._toggle_settings()

        # Start waveform polling timer
        self._pending_waveform_data = None
        self._waveform_timer.start()

        # Start recording
        try:
            self.recorder.start(level_callback=self._on_audio_level)
        except RuntimeError as e:
            self.is_recording = False
            self.toggle_action.setText("Start Recording")
            self._update_icons(recording=False)
            self._waveform_timer.stop()
            self.window.waveform.set_recording(False)
            self.window.set_recording_hint(recording=False)
            self.window.hide()
            self.tray.showMessage(
                "EchoInk - Error",
                str(e),
                QSystemTrayIcon.MessageIcon.Critical,
                3000,
            )
            return
        timeout_ms = max(5, int(self.config.max_recording_seconds)) * 1000
        self._recording_timeout_timer.start(timeout_ms)

    def _cancel_recording(self) -> None:
        """Cancel recording without transcribing."""
        if not self.is_recording:
            return

        self.is_recording = False
        self.toggle_action.setText("Start Recording")
        self._update_icons(recording=False)

        # Stop waveform polling
        self._waveform_timer.stop()
        self._recording_timeout_timer.stop()

        # Stop recording and discard audio
        self.recorder.stop()

        # Hide window
        self.window.waveform.set_recording(False)
        self.window.hide()

        self.tray.showMessage(
            "EchoInk",
            "Recording cancelled",
            QSystemTrayIcon.MessageIcon.Information,
            1500,
        )

    def _stop_recording(self) -> None:
        """Stop recording and transcribe."""
        if not self.is_recording:
            return

        self.is_recording = False
        self.toggle_action.setText("Start Recording")
        self._update_icons(recording=False)

        # Stop waveform polling
        self._waveform_timer.stop()
        self._recording_timeout_timer.stop()

        # Keep UI hidden while processing.
        self.window.waveform.set_recording(False)
        self.window.set_recording_hint(recording=False)
        self.window.set_status("Processing", animate=True)
        self.window.hide()

        # Stop recording and get audio
        audio_data = self.recorder.stop()

        # Save audio to file if configured
        audio_filename = None
        if self.config.store_recordings and audio_data:
            from datetime import datetime

            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            audio_filename = f"{timestamp}.wav"
            audio_path = self.config.get_recordings_dir() / audio_filename
            try:
                self._save_wav(audio_path, audio_data)
            except Exception as e:
                print(f"Warning: Could not save audio: {e}")
                audio_filename = None

        # Store for use in transcription callback
        self._pending_audio_filename = audio_filename

        # Transcribe in thread pool (max 1 concurrent to prevent stacking)
        def transcribe():
            try:
                text = self.client.transcribe_sync(audio_data)
                self.signals.transcription_complete.emit(text)
            except WhisperAPIError as e:
                self.signals.transcription_error.emit(str(e))
            except Exception as e:
                self.signals.transcription_error.emit(f"Unexpected error: {e}")

        self._transcription_pool.submit(transcribe)

    def _on_recording_timeout(self) -> None:
        """Failsafe for stuck recording state."""
        if self.is_recording:
            print("Recording auto-stopped by safety timeout")
            try:
                self._stop_recording()
            except Exception as e:
                print(f"Error during timeout stop: {e}")
                self.is_recording = False
                self.window.set_recording_hint(recording=False)

    def _should_auto_type(self, text: str) -> bool:
        """Apply safety gates before typing into active window."""
        if len(text) > self.config.max_auto_type_chars:
            print(
                f"Auto-type skipped: transcription too long ({len(text)} chars, "
                f"limit {self.config.max_auto_type_chars})"
            )
            return False

        return True

    def _start_typing_async(self, text: str) -> None:
        """Type text in background so hotkey can still cancel output."""
        if self.is_typing:
            return

        self.is_typing = True
        self._typing_stop_event.clear()
        self.toggle_action.setText("Stop Typing")

        def worker():
            try:
                self.typer.type_text(text, stop_event=self._typing_stop_event)
            except Exception as e:
                print(f"Typing error: {e}")
            finally:
                self.signals.typing_finished.emit()

        self._typing_thread = threading.Thread(target=worker, daemon=True)
        self._typing_thread.start()

        # Safety: force-finish typing after 30 seconds to prevent hangs
        def typing_watchdog():
            if self._typing_thread and self._typing_thread.is_alive():
                self._typing_thread.join(timeout=30.0)
                if self._typing_thread and self._typing_thread.is_alive():
                    print("Typing watchdog: forcing stop after 30s")
                    self._typing_stop_event.set()
                    self.signals.typing_finished.emit()

        threading.Thread(target=typing_watchdog, daemon=True).start()

    def _request_stop_typing(self) -> None:
        """Request cancellation of in-progress typing."""
        if self.is_typing:
            self._typing_stop_event.set()

    def _on_typing_finished(self) -> None:
        """Reset state once background typing completes or is canceled."""
        self.is_typing = False
        self._typing_thread = None
        if not self.is_recording:
            self.toggle_action.setText("Start Recording")

    def _on_audio_level(self, level: float, waveform_buffer: list[float]) -> None:
        """Handle audio level update from recorder (called from recorder thread)."""
        # Store data for main thread to poll (thread-safe assignment)
        self._pending_waveform_data = (level, list(waveform_buffer))

    def _poll_waveform_data(self) -> None:
        """Poll waveform data from recorder thread (called from main thread timer)."""
        if self._pending_waveform_data is not None:
            level, waveform_buffer = self._pending_waveform_data
            self.window.waveform.update_waveform(level, waveform_buffer)
            # Update mic level meter (scale 0-1 to 0-100, cap at 100)
            self.window.update_mic_level(level)

    def _is_external_tool_running(self) -> bool:
        """Check if an external tool ready signal exists."""
        try:
            result = subprocess.run(
                ["pgrep", "-x", "echoink-external"],
                capture_output=True,
                timeout=1,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _wait_for_ready_signal(self) -> bool:
        """Wait for external ready signal, with timeout.

        Returns True if ready signal received or no signal expected.
        Returns False if timed out waiting.
        """
        if not self.config.external_integration or not self.integration_server:
            return True

        # Check if a signal was recently written
        if not self._is_external_tool_running():
            return True

        from .integration_server import IntegrationServer

        # Accept signal from last 30 seconds (covers recording + transcription time)
        if IntegrationServer.is_ready(max_age=30.0):
            IntegrationServer.reset_ready()
            return True

        # Otherwise wait for a new signal
        timeout = self.config.integration_timeout
        start = time.time()
        while (time.time() - start) < timeout:
            if IntegrationServer.is_ready(max_age=1.0):
                IntegrationServer.reset_ready()
                return True
            time.sleep(0.1)
        return False

    def _on_transcription_complete(self, text: str) -> None:
        """Handle completed transcription."""
        self.window.hide()

        # Get the audio filename that was saved during _stop_recording
        audio_filename = getattr(self, "_pending_audio_filename", None)
        self._pending_audio_filename = None

        if text:
            clean_text = " ".join(text.split())

            # Save to history (with audio file if available)
            self.config.add_to_history(clean_text, audio_file=audio_filename)
            self.window._refresh_history()

            # Copy to clipboard
            if self.config.copy_to_clipboard:
                self.typer.copy_to_clipboard(clean_text)

            # Type into focused window (wait for ready signal if integration enabled)
            if self.config.auto_paste:
                if self._wait_for_ready_signal():
                    if self._should_auto_type(clean_text):
                        self._start_typing_async(clean_text)
                else:
                    # Timeout waiting for ready signal - leave text in clipboard only
                    pass
            else:
                # Transcribed without auto-paste; no popup notification.
                pass
        else:
            # Transcription failed - delete saved audio if any
            if audio_filename:
                audio_path = self.config.get_recordings_dir() / audio_filename
                if audio_path.exists():
                    try:
                        audio_path.unlink()
                    except OSError:
                        pass
            # No speech captured; stay silent.
            pass

    def _on_transcription_error(self, error: str) -> None:
        """Handle transcription error."""
        self.window.hide()
        self.tray.showMessage(
            "EchoInk - Error",
            error,
            QSystemTrayIcon.MessageIcon.Critical,
            3000,
        )

    def _is_startup_enabled(self) -> bool:
        """Check if the app is registered in Windows Task Scheduler."""
        if sys.platform != "win32":
            return False
        result = subprocess.run(
            ["schtasks", "/query", "/tn", "EchoInk"],
            capture_output=True,
        )
        return result.returncode == 0

    def _toggle_startup(self, enabled: bool) -> None:
        """Add or remove the app from Windows Task Scheduler autostart.

        Uses Task Scheduler (not the Run registry key) because it supports
        restart-on-failure: if the app crashes, Windows restarts it automatically.
        """
        if sys.platform != "win32":
            return
        from pathlib import Path

        task_name = "EchoInk"

        if not enabled:
            # Also clean up legacy Run registry key if present
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0, winreg.KEY_SET_VALUE,
                )
                winreg.DeleteValue(key, "EchoInk")
                winreg.CloseKey(key)
            except OSError:
                pass
            subprocess.run(
                ["schtasks", "/delete", "/tn", task_name, "/f"],
                capture_output=True,
            )
            return

        exe = str(Path(sys.executable).parent / "echoink.exe")
        work_dir = str(Path(sys.executable).parent.parent.parent)  # venv/../.. = project root
        username = os.environ.get("USERNAME", "")

        # Use PowerShell Task Scheduler cmdlets — no elevation required,
        # and supports restart-on-failure natively.
        ps_script = (
            f"$a = New-ScheduledTaskAction -Execute '{exe}' -WorkingDirectory '{work_dir}';"
            f"$t = New-ScheduledTaskTrigger -AtLogOn -User '{username}';"
            f"$s = New-ScheduledTaskSettingsSet -RestartCount 10"
            f" -RestartInterval (New-TimeSpan -Minutes 1)"
            f" -ExecutionTimeLimit ([TimeSpan]::Zero)"
            f" -MultipleInstances IgnoreNew;"
            f"Register-ScheduledTask -TaskName 'EchoInk'"
            f" -Action $a -Trigger $t -Settings $s -Force"
        )
        result = subprocess.run(
            ["powershell.exe", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
        )
        if result.returncode != 0:
            # Fallback to Run registry key
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, "EchoInk", 0, winreg.REG_SZ, f'"{exe}"')
            winreg.CloseKey(key)

    def _quit(self) -> None:
        """Clean up and quit application."""
        self._recording_timeout_timer.stop()
        self._request_stop_typing()
        if self._typing_thread:
            self._typing_thread.join(timeout=2.0)
        self._transcription_pool.shutdown(wait=False)
        if self.hotkey_manager:
            self.hotkey_manager.stop()
        if self.integration_server:
            self.integration_server.stop()
        self.recorder.cleanup()
        self.app.quit()

    def run(self) -> int:
        """Run the application."""
        if self.hotkey_manager:
            self.hotkey_manager.start()

        if self.config.hold_to_talk_button:
            self.hold_to_talk_button.show()
        else:
            self.hold_to_talk_button.hide()

        hotkey_str = "+".join(k.title() for k in self.config.hotkey)
        self.tray.showMessage(
            "EchoInk",
            f"Press {hotkey_str} or hold the floating mic button to dictate",
            QSystemTrayIcon.MessageIcon.Information,
            3000,
        )

        return self.app.exec()


# Single-instance handles (kept alive for process lifetime)
_mutex_handle = None  # Windows: named kernel mutex
_lock_fd = None       # Unix: open fd holding flock


def ensure_single_instance() -> None:
    """Ensure only one instance of the app is running.

    Windows: named kernel mutex — OS releases it automatically when the
    process exits for any reason (crash, kill, normal exit). No stale files.

    Unix: fcntl.flock on a lock file — OS releases on process exit.
    """
    global _mutex_handle, _lock_fd

    if sys.platform == "win32":
        import ctypes

        # CreateMutexW returns a handle; if another process already owns
        # a mutex with this name, GetLastError() returns ERROR_ALREADY_EXISTS.
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(
            None, False, "Global\\EchoInk-SingleInstance"
        )
        ERROR_ALREADY_EXISTS = 183
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            if _mutex_handle:
                ctypes.windll.kernel32.CloseHandle(_mutex_handle)
                _mutex_handle = None
            print("EchoInk is already running.")
            sys.exit(0)
    else:
        # Unix: flock auto-released by OS when process dies — no stale locks
        lock_path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "echoink.lock")
        try:
            _lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(_lock_fd, 0)
            os.write(_lock_fd, str(os.getpid()).encode())
        except OSError:
            print("EchoInk is already running.")
            sys.exit(0)


def _crash_log_path():
    """Return path to the crash log file."""
    from pathlib import Path
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "echoink" / "crash.log"


def main():
    """Application entry point."""
    ensure_single_instance()
    try:
        app = EchoInk()
        sys.exit(app.run())
    except Exception:
        import datetime
        import traceback
        log_path = _crash_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"Crash at {datetime.datetime.now()}\n")
            f.write(traceback.format_exc())
        sys.exit(1)  # Non-zero tells Task Scheduler to restart


if __name__ == "__main__":
    main()
