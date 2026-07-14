"""
ViewerWindow - control surface for a remote session.

Video is launched externally via ViewerService (ffplay).
This window owns the TCP control channel and optional input grab.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QCheckBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent

from ...core.control import ControlClient


class ViewerWindow(QMainWindow):
    def __init__(
        self,
        host_ip: str,
        video_port: int,
        ctrl_port: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.host_ip = host_ip
        self.video_port = video_port
        self.ctrl_port = ctrl_port

        self.setWindowTitle(f"EzPeek Control — {host_ip}:{video_port}")
        self.setMinimumSize(720, 420)

        self.control = ControlClient()
        self.control_connected = False
        self._grab_input = False

        self._setup_ui()

        if ctrl_port:
            self.control_connected = self.control.connect(host_ip, int(ctrl_port), timeout=4.0, retries=4)
            if self.control_connected:
                self.status_label.setText(
                    f"Control connected to {host_ip}:{ctrl_port}\n"
                    f"Video: separate ffplay window 'EzPeek Video - {host_ip}:{video_port}'\n"
                    "Enable Grab Input to forward mouse/keyboard."
                )
            else:
                self.status_label.setText(
                    f"Control connect FAILED to {host_ip}:{ctrl_port}\n"
                    f"Video may still work in ffplay.\n"
                    f"Check host firewall (TCP {ctrl_port}) and that host is still hosting."
                )
        else:
            self.status_label.setText(
                "No control port advertised. Video only (if ffplay window appeared)."
            )

    def _setup_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        self.status_label = QLabel("Connecting…")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #ddd; font-size: 13px;")

        ctrl_layout = QHBoxLayout()

        self.grab_cb = QCheckBox("Grab Input (send mouse/keyboard)")
        self.grab_cb.stateChanged.connect(self._toggle_grab)

        self.btn_ping = QPushButton("Ping Control")
        self.btn_ping.clicked.connect(self._ping)

        self.btn_reconnect = QPushButton("Reconnect Control")
        self.btn_reconnect.clicked.connect(self._reconnect)

        self.btn_stop = QPushButton("Close")
        self.btn_stop.clicked.connect(self.close)

        ctrl_layout.addWidget(self.grab_cb)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.btn_ping)
        ctrl_layout.addWidget(self.btn_reconnect)
        ctrl_layout.addWidget(self.btn_stop)

        info = QLabel(
            "Video appears in a separate ffplay window (best latency + HW decode).\n"
            "This window is only for remote input. ESC releases grab.\n"
            "If no video window: check terminal logs and ~/.cache/ezpeek/logs (Linux) "
            "or %LOCALAPPDATA%\\ezpeek\\logs (Windows)."
        )
        info.setStyleSheet("color:#888; font-size:11px;")
        info.setWordWrap(True)

        layout.addWidget(self.status_label)
        layout.addLayout(ctrl_layout)
        layout.addWidget(info)

        self.setCentralWidget(central)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

    def _ping(self):
        if self.control.send("PING"):
            self.status_label.setText(self.status_label.text().split("\n")[0] + "\nPing sent OK")
        else:
            self.status_label.setText("Ping failed — control not connected")

    def _reconnect(self):
        if not self.ctrl_port:
            self.status_label.setText("No control port to reconnect")
            return
        self.control_connected = self.control.connect(
            self.host_ip, int(self.ctrl_port), timeout=4.0, retries=4
        )
        if self.control_connected:
            self.status_label.setText(f"Control reconnected to {self.host_ip}:{self.ctrl_port}")
        else:
            self.status_label.setText(f"Reconnect failed to {self.host_ip}:{self.ctrl_port}")

    def _toggle_grab(self, state: int):
        self._grab_input = bool(state)
        if self._grab_input:
            if not self.control_connected:
                self.status_label.setText("Cannot grab — control is not connected")
                self.grab_cb.setChecked(False)
                self._grab_input = False
                return
            self.grabMouse()
            self.setFocus()
            self.status_label.setText("Input GRABBED — mouse & keys forwarded. ESC to release.")
        else:
            try:
                self.releaseMouse()
            except Exception:
                pass
            self.status_label.setText("Input released.")

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._grab_input and self.ctrl_port:
            pos = event.position()
            self.control.mouse_move(int(pos.x()), int(pos.y()))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if self._grab_input and self.ctrl_port:
            self.control.mouse_click(self._qt_button_to_num(event.button()), down=True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._grab_input and self.ctrl_port:
            self.control.mouse_click(self._qt_button_to_num(event.button()), down=False)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        if self._grab_input and self.ctrl_port:
            delta = event.angleDelta().y() // 120
            if delta:
                self.control.mouse_wheel(int(delta))
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if self._grab_input and self.ctrl_port:
            if event.key() == Qt.Key_Escape:
                self.grab_cb.setChecked(False)
                return
            key = self._qt_key_to_name(event)
            if key:
                self.control.key(key, down=True)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if self._grab_input and self.ctrl_port:
            key = self._qt_key_to_name(event)
            if key:
                self.control.key(key, down=False)
        else:
            super().keyReleaseEvent(event)

    def _qt_button_to_num(self, qt_btn) -> int:
        if qt_btn == Qt.LeftButton:
            return 1
        if qt_btn == Qt.RightButton:
            return 3
        if qt_btn == Qt.MiddleButton:
            return 2
        return 1

    def _qt_key_to_name(self, ev: QKeyEvent) -> str:
        key = ev.key()
        text = ev.text()
        special = {
            Qt.Key_Return: "Return",
            Qt.Key_Enter: "Return",
            Qt.Key_Escape: "Escape",
            Qt.Key_Tab: "Tab",
            Qt.Key_Backspace: "BackSpace",
            Qt.Key_Space: "space",
            Qt.Key_Up: "Up",
            Qt.Key_Down: "Down",
            Qt.Key_Left: "Left",
            Qt.Key_Right: "Right",
            Qt.Key_Shift: "Shift_L",
            Qt.Key_Control: "Control_L",
            Qt.Key_Alt: "Alt_L",
            Qt.Key_Meta: "Super_L",
            Qt.Key_F1: "F1",
            Qt.Key_F2: "F2",
            Qt.Key_F3: "F3",
            Qt.Key_F4: "F4",
            Qt.Key_F5: "F5",
            Qt.Key_F11: "F11",
        }
        if key in special:
            return special[key]
        if text and len(text) == 1:
            return text.lower()
        return ev.text().lower() or "space"

    def closeEvent(self, event):
        try:
            self.releaseMouse()
        except Exception:
            pass
        try:
            self.control.close()
        except Exception:
            pass
        super().closeEvent(event)
