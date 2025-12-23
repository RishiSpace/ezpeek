from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QLabel, QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut  # <-- add

from ...core.discovery import DiscoveryService
from ...core.host import HostService
from ...core.viewer import ViewerService


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("EzPeek")
        self.setMinimumSize(900, 600)

        # Services
        self.host = HostService()          # not started by default
        self.viewer = ViewerService()      # started on-demand

        self.setup_ui()

        # Make 'H' work regardless of which widget has focus
        self._host_shortcut = QShortcut(QKeySequence("H"), self)
        self._host_shortcut.activated.connect(self.toggle_hosting)

        self.discovery = DiscoveryService(
            on_peer_found=self.add_peer,
            get_advertisement=self._my_advertisement,
        )
        self.discovery.start()

    def setup_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        title = QLabel("EzPeek — Devices on your Network")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 24px; color: white;")

        hint = QLabel("Tip: Press 'H' to toggle hosting. Double-click a device to view it (if it advertises a port).")
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
        # If hosting, advertise the actual port; else advertise empty
        if self.host.state.proc and self.host.state.proc.poll() is None:
            return {"port": self.host.state.port}
        return {}

    def add_peer(self, name, ip, port):
        # Store connection metadata in the item for double-click connect
        label = f"{name}  —  {ip}"
        if port:
            label += f"  (port {port})"

        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, {"ip": ip, "port": port})
        self.list_widget.addItem(item)

    def _connect_to_selected(self, item: QListWidgetItem):
        data = item.data(Qt.UserRole) or {}
        ip = data.get("ip")
        port = data.get("port")

        if not ip or not port:
            self.status.setText("Status: Peer did not advertise a port (start hosting on the other device).")
            return

        # Stop current viewer then start
        try:
            self.viewer.stop()
        except Exception:
            pass

        try:
            self.viewer.start(ip, int(port))
            self.status.setText(f"Status: Viewing {ip}:{port}")
        except Exception as e:
            self.status.setText(f"Status: Failed to start viewer: {e}")

    def toggle_hosting(self) -> None:
        try:
            if self.host.state.proc and self.host.state.proc.poll() is None:
                self.host.stop()
                self.status.setText("Status: Not hosting")
            else:
                st = self.host.start()
                self.status.setText(f"Status: Hosting on {st.host_ip}:{st.port}")
        except Exception as e:
            # show the most useful part
            msg = str(e).strip().splitlines()[-1] if str(e).strip() else repr(e)
            self.status.setText(f"Status: Host failed: {msg}")

    # Keep keyPressEvent if you want, but it's no longer required:
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_H:
            self.toggle_hosting()
            event.accept()
            return
        super().keyPressEvent(event)

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
        super().closeEvent(event)
