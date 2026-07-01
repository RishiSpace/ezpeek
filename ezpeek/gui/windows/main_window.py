from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QLabel, QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut  # <-- add

from ...core.control import ControlClient
from ...core.discovery import DiscoveryService
from ...core.host import HostService
from ...core.viewer import ViewerService
from .viewer_window import ViewerWindow


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("EzPeek")
        self.setMinimumSize(900, 600)

        # Services
        self.host = HostService()          # not started by default
        self.viewer = ViewerService()      # started on-demand
        self.control = ControlClient()     # for sending input to remote host

        self._current_peer: dict = {}      # {ip, port, ctrl}
        self.viewer_win: ViewerWindow | None = None

        self.setup_ui()

        # Make 'H' work regardless of which widget has focus
        self._host_shortcut = QShortcut(QKeySequence("H"), self)
        self._host_shortcut.activated.connect(self.toggle_hosting)

        self.discovery = DiscoveryService(
            on_peer_found=self.add_peer,
            get_advertisement=self._my_advertisement,
        )
        self.discovery.start()

        # Poll to detect if host proc died unexpectedly
        self._hosting_poll = QTimer(self)
        self._hosting_poll.timeout.connect(self._poll_hosting)
        self._hosting_poll.start(2000)

    def setup_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        title = QLabel("EzPeek — Devices on your Network")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 24px; color: white;")

        hint = QLabel("Tip: Press 'H' to toggle hosting. Double-click a device to view (video + control if advertised). Enable Grab Input in the viewer window.")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("color: #bbbbbb;")

        self.status = QLabel("Status: Not hosting")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet("color: #bbbbbb;")

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background: #111111;
                color: #ffffff;
                border: 1px solid #333;
            }
            QListWidget::item:selected {
                background: #444;
            }
        """)
        self.list_widget.itemDoubleClicked.connect(self._connect_to_selected)

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.status)
        layout.addWidget(self.list_widget)

        self.setCentralWidget(central)

        self.setStyleSheet("""
            QMainWindow {
                background-color: #0b0b0b;
            }
        """)

    def _my_advertisement(self):
        # If hosting, advertise video port + control port for full remoting
        if self.host.state.proc and self.host.state.proc.poll() is None:
            adv = {"port": self.host.state.port}
            if self.host.state.control_port:
                adv["ctrl"] = self.host.state.control_port
            return adv
        return {}

    def add_peer(self, name, ip, port, ctrl_port=None):
        # Store connection metadata (video + optional control)
        label = f"{name}  —  {ip}"
        if port:
            label += f"  (video {port})"
        if ctrl_port:
            label += f" +ctrl"

        data = {"ip": ip, "port": port, "ctrl": ctrl_port}

        # Update existing item for this IP if present (allows port to appear when hosting starts)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            existing = item.data(Qt.UserRole) or {}
            if existing.get("ip") == ip:
                item.setText(label)
                item.setData(Qt.UserRole, data)
                return

        # New peer
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, data)
        self.list_widget.addItem(item)

    def _connect_to_selected(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        ip = data.get("ip")
        port = data.get("port")
        ctrl = data.get("ctrl")

        if not ip or not port:
            self.status.setText("Status: Peer did not advertise a port yet (start hosting on the other device and wait a few seconds).")
            return

        # Stop previous
        try:
            self.viewer.stop()
        except Exception:
            pass
        try:
            self.control.close()
        except Exception:
            pass

        self._current_peer = {"ip": ip, "port": port, "ctrl": ctrl}

        print(f"[ezpeek] _connect_to_selected -> ip={ip} port={port} ctrl={ctrl}")
        try:
            self.viewer.start(ip, int(port))
            # Connect control channel if peer advertised one (enables input remoting)
            if ctrl:
                if self.control.connect(ip, int(ctrl)):
                    self.status.setText(f"Status: Viewing {ip}:{port} (control ready)")
                else:
                    self.status.setText(f"Status: Viewing {ip}:{port} (no control)")
            else:
                self.status.setText(f"Status: Viewing {ip}:{port} (no ctrl advertised)")

            # Open integrated viewer window (for input grabbing + status)
            if self.viewer_win:
                try:
                    self.viewer_win.close()
                except Exception:
                    pass
            self.viewer_win = ViewerWindow(ip, int(port), int(ctrl) if ctrl else None)
            self.viewer_win.show()
            self.viewer_win.raise_()
            print(f"[ezpeek] ViewerWindow shown for {ip}:{port}. External ffplay should have been launched by ViewerService.")
        except Exception as e:
            import traceback
            print("[ezpeek] Exception in _connect_to_selected / viewer.start:")
            traceback.print_exc()
            self.status.setText(f"Status: Failed to start viewer: {e}")

    def toggle_hosting(self) -> None:
        try:
            if self.host.state.proc and self.host.state.proc.poll() is None:
                self.host.stop()
                self.status.setText("Status: Not hosting")
                if hasattr(self, 'discovery') and self.discovery:
                    try:
                        self.discovery.force_broadcast()
                    except Exception:
                        pass
            else:
                st = self.host.start()
                ctrl = f" +ctrl:{st.control_port}" if getattr(st, "control_port", None) else ""
                self.status.setText(f"Status: Hosting on {st.host_ip}:{st.port}{ctrl}")
                # Immediately announce the new port to peers
                if hasattr(self, 'discovery') and self.discovery:
                    try:
                        self.discovery.force_broadcast()
                    except Exception:
                        pass
        except Exception as e:
            import traceback
            # Always log full error to terminal for debugging (esp. Wayland setup)
            print("Host start failed:")
            traceback.print_exc()
            # Show concise in UI
            msg = str(e).strip()
            first = msg.splitlines()[0] if msg else repr(e)
            self.status.setText(f"Status: Host failed: {first}. Check terminal output.")

    def _poll_hosting(self):
        if (self.host.state.proc is not None and
                self.host.state.proc.poll() is not None):
            self.host.state.proc = None
            try:
                self.host._stop_control()
            except Exception:
                pass
            self.status.setText("Status: Hosting stopped (sender process died)")

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
        try:
            self.control.close()
        except Exception:
            pass
        if self.viewer_win:
            try:
                self.viewer_win.close()
            except Exception:
                pass
        super().closeEvent(event)
