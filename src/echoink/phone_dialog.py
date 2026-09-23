"""Tray dialog for pairing the EchoInk Android app with this PC."""

import io
import secrets

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .config import Config
from .phone_server import local_addresses, pairing_link


def new_token() -> str:
    return secrets.token_urlsafe(18)


class PhoneAccessDialog(QDialog):
    """Turns phone access on/off and shows the pairing QR code for the phone app.

    ``set_enabled(bool) -> bool`` starts or stops the phone server and reports
    whether it is running afterwards.
    """

    def __init__(self, config: Config, running: bool, set_enabled):
        super().__init__()
        self.config = config
        self._set_enabled = set_enabled
        self.setWindowTitle("EchoInk - Phone access")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Let the EchoInk phone app use this PC")
        self.enabled.setChecked(running)
        self.enabled.toggled.connect(self._toggle)
        layout.addWidget(self.enabled)

        layout.addWidget(QLabel("Address the phone uses (Tailscale works anywhere):"))
        self.address = QComboBox()
        for address in local_addresses():
            self.address.addItem(f"http://{address}:{config.phone_server_port}")
        self.address.currentIndexChanged.connect(self._refresh)
        layout.addWidget(self.address)

        self.qr = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.qr)
        self.steps = QLabel(wordWrap=True, textFormat=Qt.TextFormat.RichText)
        self.steps.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.steps)

        buttons = QHBoxLayout()
        copy = QPushButton("Copy pairing link")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self._link()))
        regenerate = QPushButton("New token")
        regenerate.setToolTip("Unpairs every phone; scan the new code afterwards.")
        regenerate.clicked.connect(self._regenerate)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for button in (copy, regenerate, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._refresh()

    def _link(self) -> str:
        return pairing_link(self.address.currentText(), self.config.phone_token)

    def _toggle(self, on: bool) -> None:
        if on and not self.config.phone_token:
            self.config.phone_token = new_token()
        self.config.phone_server = on
        self.config.save()
        running = self._set_enabled(on)
        if running != on:
            self.enabled.blockSignals(True)
            self.enabled.setChecked(running)
            self.enabled.blockSignals(False)
        self._refresh()

    def _regenerate(self) -> None:
        self.config.phone_token = new_token()
        self.config.save()
        self._refresh()

    def _refresh(self) -> None:
        on = self.enabled.isChecked()
        self.address.setEnabled(on)
        if not on or not self.config.phone_token:
            self.qr.clear()
            self.steps.setText("Phone access is off.")
            return
        import segno

        png = io.BytesIO()
        segno.make(self._link(), error="m").save(png, kind="png", scale=5, border=2)
        pixmap = QPixmap()
        pixmap.loadFromData(png.getvalue())
        self.qr.setPixmap(pixmap)
        url = self.address.currentText()
        self.steps.setText(
            f"1. On the phone, open <b>{url}</b> in the browser and install the app.<br>"
            "2. In the app, tap <b>Scan pairing QR</b> and scan this code.<br>"
            "If Windows asks whether to allow Python on your networks, allow private networks."
        )
