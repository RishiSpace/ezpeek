"""
ViewerWindow - dedicated window for viewing a remote desktop + forwarding input.

Current design (practical for production):
- Spawns / manages the ffplay video window (external but excellent HW decode + low latency).
- Provides Qt controls for input grabbing.
- When "Grab Input" is active, this window captures mouse + keyboard (setFocus + mouse tracking) and forwards via ControlClient.
- Coordinates: simple mapping; user can resize/position the video separately.
- Future improvement: could switch to fully integrated video rendering (QOpenGL + ffmpeg frames or libmpv).

Usage from MainWindow or CLI:
    w = ViewerWindow(ip, video_port, control_port)
    w.show()
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QCheckBox, QSpinBox
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent

from ...core.control import ControlClient


class ViewerWindow(QMainWindow):
    def __init__(self, host_ip: str, video_port: int, ctrl_port: int | None = None, parent=None):
        super().__init__(parent)
        self.host_ip = host_ip
        self.video_port = video_port
        self.ctrl_port = ctrl_port

        self.setWindowTitle(f"EzPeek Viewer — {host_ip}:{video_port}")
        self.setMinimumSize(800, 500)

        self.control = ControlClient()
        self._grab_input = False
        self._last_mouse_pos = (0, 0)

        self._setup_ui()

        if ctrl_port:
            if self.control.connect(host_ip, ctrl_port):
                self.status_label.setText(f"Control connected to {host_ip}:{ctrl_port}")
            else:
                self.status_label.setText("Control connect failed (input disabled)")

        # Optional: auto open ffplay hint
        self._video_hint_timer = QTimer(self)
        self._video_hint_timer.setSingleShot(True)
        self._video_hint_timer.timeout.connect(self._show_video_hint)
        self._video_hint_timer.start(400)

    def _setup_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        self.status_label = QLabel("Ready. Double-click or use controls below to manage session.")
        self.status_label.setAlignment(Qt.AlignCenter)

        # Controls
        ctrl_layout = QHBoxLayout()

        self.grab_cb = QCheckBox("Grab Input (send mouse/keyboard)")
        self.grab_cb.stateChanged.connect(self._toggle_grab)

        self.btn_stop = QPushButton("Stop Viewing")
        self.btn_stop.clicked.connect(self.close)

        self.btn_ping = QPushButton("Ping Control")
        self.btn_ping.clicked.connect(lambda: self.control.send("PING") if self.ctrl_port else None)

        ctrl_layout.addWidget(self.grab_cb)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.btn_ping)
        ctrl_layout.addWidget(self.btn_stop)

        # Info / instructions
        info = QLabel(
            "Video plays in ffplay (external). Bring this window forward and enable 'Grab Input' to forward events.\n"
            "Tip: Click inside this window after enabling grab. Press ESC to release grab."
        )
        info.setStyleSheet("color:#888; font-size:11px;")
        info.setWordWrap(True)

        layout.addWidget(self.status_label)
        layout.addLayout(ctrl_layout)
        layout.addWidget(info)

        self.setCentralWidget(central)

        # Allow receiving keyboard even without focus on child widgets
        self.setFocusPolicy(Qt.StrongFocus)

        # Track mouse in this window
        self.setMouseTracking(True)

    def _toggle_grab(self, state: int):
        self._grab_input = bool(state)
        if self._grab_input:
            self.grabMouse()  # Qt grab for better mouse capture while window focused
            self.setFocus()
            self.status_label.setText("Input GRABBED — mouse & keys forwarded. ESC to release.")
        else:
            try:
                self.releaseMouse()
            except Exception:
                pass
            self.status_label.setText("Input released.")

    def _show_video_hint(self):
        self.status_label.setText(
            f"Start video with: ffplay -fflags nobuffer -flags low_delay "
            f"srt://{self.host_ip}:{self.video_port}?mode=caller&latency=20"
        )

    # ---- Input forwarding ----
    def mouseMoveEvent(self, event: QMouseEvent):
        if self._grab_input and self.ctrl_port:
            x = event.position().x()
            y = event.position().y()
            # Simple absolute mapping (user can scale on remote side if needed)
            self.control.mouse_move(int(x), int(y))
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if self._grab_input and self.ctrl_port:
            btn = self._qt_button_to_num(event.button())
            self.control.mouse_click(btn, down=True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._grab_input and self.ctrl_port:
            btn = self._qt_button_to_num(event.button())
            self.control.mouse_click(btn, down=False)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        if self._grab_input and self.ctrl_port:
            delta = event.angleDelta().y() // 120   # typical steps
            if delta:
                self.control.mouse_wheel(int(delta))
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if self._grab_input and self.ctrl_port:
            key = self._qt_key_to_name(event)
            if key:
                self.control.key(key, down=True)
            if event.key() == Qt.Key_Escape:
                self.grab_cb.setChecked(False)
                self._toggle_grab(0)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if self._grab_input and self.ctrl_port:
            key = self._qt_key_to_name(event)
            if key:
                self.control.key(key, down=False)
        else:
            super().keyReleaseEvent(event)

    # Mappings
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

        # Common special keys
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

        # Fallback to key name
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
