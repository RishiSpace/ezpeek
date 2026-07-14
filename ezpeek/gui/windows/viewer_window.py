"""
Integrated ViewerWindow — remote video + mouse/keyboard in one surface.

Video is decoded by ffmpeg (SRT → MJPEG pipe) and painted in a Qt widget.
Input events on that widget are forwarded over the TCP control channel.
"""

from __future__ import annotations

import subprocess
import threading
from typing import Optional

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QCheckBox,
    QSizePolicy,
)
from PySide6.QtCore import Qt, Signal, QObject, QThread, Slot, QTimer
from PySide6.QtGui import (
    QImage,
    QPixmap,
    QKeyEvent,
    QMouseEvent,
    QWheelEvent,
    QPainter,
    QCursor,
)

from ...core.control import ControlClient
from ...core.viewer import build_integrated_decode_cmd
from ...utils import get_display_refresh_hz


class _MjpegReader(QObject):
    """Background reader: ffmpeg stdout (MJPEG) → QImage signals."""

    frame = Signal(QImage)
    failed = Signal(str)
    started_ok = Signal()

    def __init__(self, cmd: list[str], parent=None):
        super().__init__(parent)
        self._cmd = cmd
        self._proc: Optional[subprocess.Popen] = None
        self._stop = threading.Event()
        self._got_frame = False

    @Slot()
    def run(self):
        try:
            self._proc = subprocess.Popen(
                self._cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as e:
            self.failed.emit(f"Failed to start decoder: {e}")
            return

        self.started_ok.emit()
        assert self._proc.stdout is not None
        buf = bytearray()
        err_chunks: list[bytes] = []

        def _drain_err():
            try:
                assert self._proc and self._proc.stderr
                while not self._stop.is_set():
                    chunk = self._proc.stderr.read(512)
                    if not chunk:
                        break
                    err_chunks.append(chunk)
                    if sum(len(c) for c in err_chunks) > 64_000:
                        del err_chunks[:-20]
            except Exception:
                pass

        t = threading.Thread(target=_drain_err, daemon=True)
        t.start()

        SOI = b"\xff\xd8"
        EOI = b"\xff\xd9"
        import time as _time

        start_t = _time.time()
        try:
            while not self._stop.is_set():
                # Stall watchdog: no frame in 12s → fail with ffmpeg stderr
                if not self._got_frame and (_time.time() - start_t) > 12:
                    err = b"".join(err_chunks).decode(errors="ignore").strip()
                    self.failed.emit(
                        err[-500:]
                        if err
                        else "No video frames in 12s (firewall UDP 2734? host still hosting?)"
                    )
                    break

                chunk = self._proc.stdout.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)

                while True:
                    s0 = buf.find(SOI)
                    if s0 < 0:
                        if len(buf) > 2:
                            del buf[:-2]
                        break
                    if s0 > 0:
                        del buf[:s0]
                    end = buf.find(EOI, 2)
                    if end < 0:
                        break
                    end += 2
                    jpeg = bytes(buf[:end])
                    del buf[:end]
                    img = QImage.fromData(jpeg, "JPEG")
                    if not img.isNull():
                        self._got_frame = True
                        self.frame.emit(img)

                if len(buf) > 8_000_000:
                    buf.clear()
        except Exception as e:
            if not self._stop.is_set():
                self.failed.emit(str(e))
        finally:
            self._stop.set()
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1.5)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
            err = b"".join(err_chunks).decode(errors="ignore").strip()
            if (
                not self._got_frame
                and self._proc
                and self._proc.returncode not in (0, None, -15, 255)
            ):
                msg = err[-800:] if err else f"decoder exit {self._proc.returncode}"
                self.failed.emit(msg)

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass


class VideoSurface(QWidget):
    """Paints the remote frame and optionally captures input."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: Optional[QPixmap] = None
        self._frame_w = 0
        self._frame_h = 0
        self.grab_input = False
        self.control: Optional[ControlClient] = None
        self.setMinimumSize(640, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background: #000;")
        self.setCursor(Qt.ArrowCursor)

    def set_frame(self, image: QImage):
        self._frame_w = image.width()
        self._frame_h = image.height()
        self._pixmap = QPixmap.fromImage(image)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if not self._pixmap or self._pixmap.isNull():
            p.setPen(Qt.gray)
            p.drawText(self.rect(), Qt.AlignCenter, "Connecting to stream…")
            return
        # Letterbox scale
        scaled = self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        p.drawPixmap(x, y, scaled)
        self._draw_rect = (x, y, scaled.width(), scaled.height())

    def _map_to_remote(self, pos) -> Optional[tuple[int, int]]:
        if not self._frame_w or not self._frame_h:
            return None
        if not hasattr(self, "_draw_rect"):
            return None
        x0, y0, dw, dh = self._draw_rect
        if dw <= 0 or dh <= 0:
            return None
        lx = pos.x() - x0
        ly = pos.y() - y0
        if lx < 0 or ly < 0 or lx > dw or ly > dh:
            return None
        rx = int(lx * self._frame_w / dw)
        ry = int(ly * self._frame_h / dh)
        rx = max(0, min(self._frame_w - 1, rx))
        ry = max(0, min(self._frame_h - 1, ry))
        return rx, ry

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.grab_input and self.control and self.control.connected:
            mapped = self._map_to_remote(event.position())
            if mapped:
                self.control.mouse_move(mapped[0], mapped[1])
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if self.grab_input and self.control and self.control.connected:
            btn = self._qt_button(event.button())
            self.control.mouse_click(btn, down=True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.grab_input and self.control and self.control.connected:
            btn = self._qt_button(event.button())
            self.control.mouse_click(btn, down=False)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        if self.grab_input and self.control and self.control.connected:
            delta = event.angleDelta().y() // 120
            if delta:
                self.control.mouse_wheel(int(delta))
        super().wheelEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Escape and self.grab_input:
            # Parent window handles un-grab via signal-less callback
            w = self.window()
            if hasattr(w, "release_grab"):
                w.release_grab()  # type: ignore[attr-defined]
            return
        if self.grab_input and self.control and self.control.connected:
            key = self._qt_key(event)
            if key:
                self.control.key(key, down=True)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if self.grab_input and self.control and self.control.connected:
            key = self._qt_key(event)
            if key:
                self.control.key(key, down=False)
            return
        super().keyReleaseEvent(event)

    @staticmethod
    def _qt_button(qt_btn) -> int:
        if qt_btn == Qt.LeftButton:
            return 1
        if qt_btn == Qt.RightButton:
            return 3
        if qt_btn == Qt.MiddleButton:
            return 2
        return 1

    @staticmethod
    def _qt_key(ev: QKeyEvent) -> str:
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
        return text.lower() if text else ""


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

        self.setWindowTitle(f"EzPeek — {host_ip}:{video_port}")
        self.setMinimumSize(960, 540)
        self.resize(1280, 720)

        self.control = ControlClient()
        self.control_connected = False
        self._thread: Optional[QThread] = None
        self._reader: Optional[_MjpegReader] = None

        self.local_hz = get_display_refresh_hz()
        print(f"[ezpeek viewer] Local display refresh ≈ {self.local_hz:.2f} Hz")

        self._build_ui()
        # Control first (sends CLIENT_CAPS → host may restart encoder at min FPS).
        # Delay video so we don't connect mid-restart and hang on "Connecting…".
        self._connect_control()
        self.status_label.setText(
            (self.status_label.text() or "Control…")
            + " · waiting for host stream…"
        )
        QTimer.singleShot(1800, self._start_video)

    def _build_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.surface = VideoSurface()
        self.surface.control = self.control
        layout.addWidget(self.surface, stretch=1)

        bar = QHBoxLayout()
        self.status_label = QLabel("Starting…")
        self.status_label.setStyleSheet("color: #ccc;")
        self.grab_cb = QCheckBox("Grab Input (mouse & keyboard → remote)")
        self.grab_cb.stateChanged.connect(self._toggle_grab)
        self.btn_fullscreen = QPushButton("Fullscreen")
        self.btn_fullscreen.clicked.connect(self._toggle_fullscreen)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.close)
        bar.addWidget(self.grab_cb)
        bar.addStretch()
        bar.addWidget(self.status_label)
        bar.addWidget(self.btn_fullscreen)
        bar.addWidget(self.btn_close)
        layout.addLayout(bar)

        tip = QLabel("Tip: enable Grab Input, then click the video. ESC releases grab. Scroll/keys go to the host.")
        tip.setStyleSheet("color:#777; font-size:11px;")
        layout.addWidget(tip)

        self.setCentralWidget(central)
        self.setStyleSheet("QMainWindow { background: #111; } QPushButton { padding: 6px 12px; }")

    def _connect_control(self):
        if not self.ctrl_port:
            self.status_label.setText("Video only (no control port advertised)")
            return
        self.control_connected = self.control.connect(
            self.host_ip, int(self.ctrl_port), timeout=4.0, retries=4
        )
        if self.control_connected:
            # Negotiate stream FPS: host will use min(host_hz, our_hz)
            self.control.send_client_caps(self.local_hz)
            self.status_label.setText(
                f"Control OK → {self.host_ip}:{self.ctrl_port} · our panel {self.local_hz:.0f} Hz"
            )
        else:
            self.status_label.setText(
                f"Control failed ({self.host_ip}:{self.ctrl_port}) — video may still work"
            )

    def _start_video(self):
        try:
            # Software decode by default — more reliable across Windows/Linux codecs.
            cmd = build_integrated_decode_cmd(
                self.host_ip, int(self.video_port), use_hwaccel=False
            )
        except Exception as e:
            self.status_label.setText(f"Decoder setup failed: {e}")
            return

        self._thread = QThread(self)
        self._reader = _MjpegReader(cmd)
        self._reader.moveToThread(self._thread)
        self._thread.started.connect(self._reader.run)
        self._reader.frame.connect(self._on_frame)
        self._reader.failed.connect(self._on_decode_fail)
        self._reader.started_ok.connect(
            lambda: self.status_label.setText(
                f"Connecting to srt://{self.host_ip}:{self.video_port} …"
            )
        )
        self._thread.start()

    @Slot(QImage)
    def _on_frame(self, img: QImage):
        self.surface.set_frame(img)
        if "Decoding" in self.status_label.text() or "decoding" in self.status_label.text():
            ctrl = " · input ready" if self.control_connected else ""
            self.status_label.setText(
                f"Live {img.width()}x{img.height()} from {self.host_ip}:{self.video_port}{ctrl}"
            )

    @Slot(str)
    def _on_decode_fail(self, msg: str):
        # Surface a short, actionable line in the UI
        short = msg.replace("\n", " ").strip()
        if "Connection to srt" in short or "Input/output error" in short:
            tip = (
                f"Cannot reach srt://{self.host_ip}:{self.video_port}. "
                "Wrong IP, host not hosting, or firewall blocking UDP 2734."
            )
            self.status_label.setText(tip)
        else:
            self.status_label.setText(f"Video error: {short[:220]}")
        print(f"[ezpeek viewer] decode failed for {self.host_ip}:{self.video_port}: {msg}")

    def _toggle_grab(self, state: int):
        on = bool(state)
        if on and not self.control_connected:
            self.grab_cb.setChecked(False)
            self.status_label.setText("Cannot grab — control channel not connected")
            return
        self.surface.grab_input = on
        if on:
            self.surface.grabMouse()
            self.surface.setFocus()
            self.surface.setCursor(Qt.BlankCursor)
            self.status_label.setText("INPUT GRABBED — ESC to release")
        else:
            self.release_grab()

    def release_grab(self):
        self.surface.grab_input = False
        try:
            self.surface.releaseMouse()
        except Exception:
            pass
        self.surface.setCursor(Qt.ArrowCursor)
        if self.grab_cb.isChecked():
            self.grab_cb.blockSignals(True)
            self.grab_cb.setChecked(False)
            self.grab_cb.blockSignals(False)
        if self.control_connected:
            self.status_label.setText(
                f"Live from {self.host_ip}:{self.video_port} · input released"
            )

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.btn_fullscreen.setText("Fullscreen")
        else:
            self.showFullScreen()
            self.btn_fullscreen.setText("Exit Fullscreen")

    def closeEvent(self, event):
        self.release_grab()
        if self._reader:
            self._reader.stop()
        if self._thread:
            self._thread.quit()
            self._thread.wait(2000)
        try:
            self.control.close()
        except Exception:
            pass
        super().closeEvent(event)
