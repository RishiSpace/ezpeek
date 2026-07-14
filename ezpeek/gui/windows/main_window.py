from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QPushButton,
    QMessageBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut

from ...core.discovery import DiscoveryService
from ...core.encoder import describe_encode_choice, EncodeSpec
from ...core.host import HostService
from ...utils import (
    BITRATE_MAX_KBPS,
    BITRATE_MIN_KBPS,
    BITRATE_TARGET_KBPS,
    get_display_refresh_hz,
)
from .viewer_window import ViewerWindow


class MainWindow(QMainWindow):
    def __init__(self, test_pattern: bool = False):
        super().__init__()

        self.setWindowTitle("EzPeek")
        self.setMinimumSize(900, 600)

        self.local_hz = get_display_refresh_hz()
        self.host = HostService(
            test_pattern=test_pattern,
            codec="auto",
            host_hz=self.local_hz,
            bitrate_kbps=BITRATE_TARGET_KBPS,
            bitrate_min_kbps=BITRATE_MIN_KBPS,
            bitrate_max_kbps=BITRATE_MAX_KBPS,
        )
        self._current_peer: dict = {}
        self.viewer_win: ViewerWindow | None = None
        self._test_pattern = test_pattern
        # ip -> last known peer refresh Hz from discovery
        self._peer_hz: dict[str, float] = {}

        print(f"[ezpeek] Local display refresh ≈ {self.local_hz:.2f} Hz")
        self.setup_ui()

        self._host_shortcut = QShortcut(QKeySequence("H"), self)
        self._host_shortcut.activated.connect(self.toggle_hosting)

        self.discovery = DiscoveryService(
            on_peer_found=self.add_peer,
            get_advertisement=self._my_advertisement,
        )
        self.discovery.start()

        self._hosting_poll = QTimer(self)
        self._hosting_poll.timeout.connect(self._poll_hosting)
        self._hosting_poll.start(1500)

        if test_pattern:
            self.status.setText("Status: Test-pattern mode (no screen capture). Press H to host.")

    def setup_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        title = QLabel("EzPeek — Devices on your Network")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 24px; color: white;")

        hint = QLabel(
            "Press H to host · Double-click a peer with video ports to view · "
            "Video + mouse/keyboard share one window (Grab Input) · "
            f"This display ≈ {self.local_hz:.0f} Hz · "
            f"stream FPS = min(host, viewer) · "
            f"CBR {BITRATE_TARGET_KBPS//1000} Mbps · "
            f"{describe_encode_choice(EncodeSpec(codec='auto', fps=int(self.local_hz), bitrate_kbps=BITRATE_TARGET_KBPS, bitrate_min_kbps=BITRATE_MIN_KBPS, bitrate_max_kbps=BITRATE_MAX_KBPS))}"
        )
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #bbbbbb;")
        hint.setWordWrap(True)

        self.status = QLabel("Status: Not hosting")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet("color: #bbbbbb;")

        btn_row = QHBoxLayout()
        self.btn_host = QPushButton("Start Hosting (H)")
        self.btn_host.clicked.connect(self.toggle_hosting)
        self.btn_refresh = QPushButton("Force Discovery Ping")
        self.btn_refresh.clicked.connect(self._force_discovery)
        btn_row.addWidget(self.btn_host)
        btn_row.addWidget(self.btn_refresh)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            """
            QListWidget {
                background: #111111;
                color: #ffffff;
                border: 1px solid #333;
            }
            QListWidget::item:selected {
                background: #444;
            }
            """
        )
        self.list_widget.itemDoubleClicked.connect(self._connect_to_selected)

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.status)
        layout.addLayout(btn_row)
        layout.addWidget(self.list_widget)

        self.setCentralWidget(central)
        self.setStyleSheet(
            """
            QMainWindow { background-color: #0b0b0b; }
            QPushButton {
                background: #2a2a2a; color: white; padding: 8px 14px;
                border: 1px solid #444; border-radius: 4px;
            }
            QPushButton:hover { background: #3a3a3a; }
            """
        )

    def _force_discovery(self):
        try:
            self.discovery.force_broadcast()
            self.status.setText("Status: Discovery ping sent")
        except Exception as e:
            self.status.setText(f"Status: Discovery ping failed: {e}")

    def _my_advertisement(self):
        # Always advertise our display Hz so peers can show/log it.
        adv: dict = {"hz": f"{self.local_hz:.2f}"}
        if self.host.state.proc and self.host.state.proc.poll() is None:
            adv["port"] = self.host.state.port
            if self.host.state.control_port:
                adv["ctrl"] = self.host.state.control_port
        return adv

    def add_peer(self, name, ip, port, ctrl_port=None, refresh_hz=None):
        if refresh_hz:
            try:
                self._peer_hz[ip] = float(refresh_hz)
            except (TypeError, ValueError):
                pass
        hz = self._peer_hz.get(ip)

        label = f"{name}  —  {ip}"
        if hz:
            label += f"  [{hz:.0f} Hz]"
        if port:
            label += f"  (video {port}"
            if self.host.state.stream_fps and port:
                pass
            label += ")"
        else:
            label += "  (not hosting)"
        if ctrl_port:
            label += f" +ctrl {ctrl_port}"

        data = {"name": name, "ip": ip, "port": port, "ctrl": ctrl_port, "hz": hz}

        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            existing = item.data(Qt.UserRole) or {}
            if existing.get("ip") == ip:
                item.setText(label)
                item.setData(Qt.UserRole, data)
                return

        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, data)
        self.list_widget.addItem(item)

    def _connect_to_selected(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        ip = data.get("ip")
        port = data.get("port")
        ctrl = data.get("ctrl")

        if not ip:
            self.status.setText("Status: Invalid peer (no IP)")
            return

        if not port:
            self.status.setText(
                "Status: Peer is online but not hosting yet. "
                "On the other PC press H, wait until it shows video ports, then double-click again."
            )
            return

        if self.viewer_win:
            try:
                self.viewer_win.close()
            except Exception:
                pass
            self.viewer_win = None

        self._current_peer = {"ip": ip, "port": port, "ctrl": ctrl}
        print(f"[ezpeek] Connecting (integrated viewer) → {ip} video={port} ctrl={ctrl}")

        try:
            self.viewer_win = ViewerWindow(ip, int(port), int(ctrl) if ctrl else None)
            self.viewer_win.show()
            self.viewer_win.raise_()
            self.viewer_win.activateWindow()
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.status.setText(f"Status: Viewer failed: {e}")
            QMessageBox.warning(
                self,
                "Viewer failed",
                f"Could not open viewer for {ip}:{port}\n\n{e}\n\n"
                f"Check host is still hosting and firewall allows UDP {port} / TCP control.",
            )
            return

        ctrl_ok = bool(self.viewer_win.control_connected)
        if ctrl and ctrl_ok:
            self.status.setText(f"Status: Viewing {ip}:{port} (integrated · control OK)")
        elif ctrl:
            self.status.setText(
                f"Status: Viewing {ip}:{port} (video starting; control failed — TCP {ctrl}?)"
            )
        else:
            self.status.setText(f"Status: Viewing {ip}:{port} (no control advertised)")

    def toggle_hosting(self) -> None:
        try:
            if self.host.state.proc and self.host.state.proc.poll() is None:
                self.host.stop()
                self.btn_host.setText("Start Hosting (H)")
                self.status.setText("Status: Not hosting")
                try:
                    self.discovery.force_broadcast()
                except Exception:
                    pass
            else:
                st = self.host.start()
                ctrl = f"  ctrl:{st.control_port}" if st.control_port else "  (no control)"
                mode = " [test pattern]" if self._test_pattern else ""
                codec = describe_encode_choice(
                    EncodeSpec(
                        codec="auto",
                        fps=st.stream_fps,
                        bitrate_kbps=st.bitrate_target_kbps,
                        bitrate_min_kbps=st.bitrate_min_kbps,
                        bitrate_max_kbps=st.bitrate_max_kbps,
                    )
                )
                self.btn_host.setText("Stop Hosting (H)")
                self.status.setText(
                    f"Status: HOSTING{mode} — {st.host_ip}:{st.port}{ctrl} · "
                    f"panel {st.host_hz:.0f} Hz → {st.stream_fps} fps · {codec}"
                )
                try:
                    self.discovery.force_broadcast()
                except Exception:
                    pass
                QTimer.singleShot(500, self._force_discovery)
                QTimer.singleShot(1500, self._force_discovery)
        except Exception as e:
            import traceback

            print("Host start failed:")
            traceback.print_exc()
            msg = str(e).strip()
            first = msg.splitlines()[0] if msg else repr(e)
            self.status.setText(f"Status: Host failed: {first}")
            QMessageBox.critical(
                self,
                "Host failed",
                f"Could not start hosting.\n\n{msg[:1500]}\n\n"
                "Linux Wayland: accept the screen-share portal prompt.\n"
                "Windows: ensure FFmpeg works and firewall allows 2734/UDP + 2735/TCP.\n"
                f"Log: {getattr(self.host.state, 'log_path', '')}",
            )

    def _poll_hosting(self):
        if self.host.state.proc is not None and self.host.state.proc.poll() is not None:
            err = self.host.state.last_error
            self.host.state.proc = None
            try:
                self.host._stop_control()
            except Exception:
                pass
            try:
                self.host._stop_screencast()
            except Exception:
                pass
            self.btn_host.setText("Start Hosting (H)")
            self.status.setText(
                f"Status: Hosting stopped (sender died){': ' + err[:120] if err else ''}"
            )
            try:
                self.discovery.force_broadcast()
            except Exception:
                pass

    def closeEvent(self, event):
        try:
            self.discovery.stop()
        except Exception:
            pass
        try:
            self.host.stop()
        except Exception:
            pass
        if self.viewer_win:
            try:
                self.viewer_win.close()
            except Exception:
                pass
        super().closeEvent(event)
