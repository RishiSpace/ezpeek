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
from ...core.host import HostService
from ...core.viewer import ViewerService
from .viewer_window import ViewerWindow


class MainWindow(QMainWindow):
    def __init__(self, test_pattern: bool = False):
        super().__init__()

        self.setWindowTitle("EzPeek")
        self.setMinimumSize(900, 600)

        self.host = HostService(test_pattern=test_pattern)
        self.viewer = ViewerService()
        self._current_peer: dict = {}
        self.viewer_win: ViewerWindow | None = None
        self._test_pattern = test_pattern

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
            "Phase 1 (LAN): Press H to host · Double-click a peer that shows video ports · "
            "Enable Grab Input in the control window · Video opens in a separate ffplay window."
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
        if self.host.state.proc and self.host.state.proc.poll() is None:
            adv = {"port": self.host.state.port}
            if self.host.state.control_port:
                adv["ctrl"] = self.host.state.control_port
            return adv
        return {}

    def add_peer(self, name, ip, port, ctrl_port=None):
        label = f"{name}  —  {ip}"
        if port:
            label += f"  (video {port})"
        else:
            label += "  (not hosting)"
        if ctrl_port:
            label += f" +ctrl {ctrl_port}"

        data = {"name": name, "ip": ip, "port": port, "ctrl": ctrl_port}

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

        # Stop previous session
        try:
            self.viewer.stop()
        except Exception:
            pass
        if self.viewer_win:
            try:
                self.viewer_win.close()
            except Exception:
                pass
            self.viewer_win = None

        self._current_peer = {"ip": ip, "port": port, "ctrl": ctrl}
        print(f"[ezpeek] Connecting to {ip} video={port} ctrl={ctrl}")

        # 1) Launch video first
        try:
            self.viewer.start(ip, int(port))
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.status.setText(f"Status: Video failed: {e}")
            QMessageBox.warning(
                self,
                "Video failed",
                f"Could not start ffplay for {ip}:{port}\n\n{e}\n\n"
                f"Check host is still hosting and firewall allows UDP {port}.",
            )
            return

        # 2) Open control window (owns the control TCP connection)
        try:
            self.viewer_win = ViewerWindow(ip, int(port), int(ctrl) if ctrl else None)
            self.viewer_win.show()
            self.viewer_win.raise_()
            self.viewer_win.activateWindow()
        except Exception as e:
            print(f"[ezpeek] ViewerWindow error: {e}")

        ctrl_ok = bool(self.viewer_win and self.viewer_win.control_connected)
        if ctrl and ctrl_ok:
            self.status.setText(f"Status: Viewing {ip}:{port} + control OK")
        elif ctrl:
            self.status.setText(
                f"Status: Viewing {ip}:{port} (video OK, control failed — check firewall TCP {ctrl})"
            )
        else:
            self.status.setText(f"Status: Viewing {ip}:{port} (no control advertised)")

        print("[ezpeek] Connect flow done. External ffplay should be visible.")

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
                self.btn_host.setText("Stop Hosting (H)")
                self.status.setText(
                    f"Status: HOSTING{mode} — peers connect to {st.host_ip}:{st.port}{ctrl}"
                )
                try:
                    self.discovery.force_broadcast()
                except Exception:
                    pass
                # Re-broadcast a few times so peers pick it up quickly
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
            self.btn_host.setText("Start Hosting (H)")
            self.status.setText(
                f"Status: Hosting stopped (sender died){': ' + err[:120] if err else ''}"
            )
            try:
                self.discovery.force_broadcast()
            except Exception:
                pass

        # Detect viewer death
        if self.viewer.state.proc is not None and self.viewer.state.proc.poll() is not None:
            self.viewer.state.proc = None
            if "Viewing" in (self.status.text() or ""):
                self.status.setText("Status: Viewer process ended (ffplay closed)")

    def closeEvent(self, event):
        try:
            self.discovery.stop()
        except Exception:
            pass
        try:
            self.viewer.stop()
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
