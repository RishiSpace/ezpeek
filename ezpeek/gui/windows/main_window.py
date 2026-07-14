from __future__ import annotations

import json
from typing import Optional

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
    QLineEdit,
    QSplitter,
    QInputDialog,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut

from ...cloud import CloudClient, CloudError, clear_session
from ...cloud.client import RelayHostAgent
from ...cloud.config import relay_endpoint_from_server_url
from ...core.discovery import DiscoveryService
from ...core.encoder import describe_encode_choice, EncodeSpec
from ...core.host import HostService
from ...utils import (
    BITRATE_MAX_KBPS,
    BITRATE_MIN_KBPS,
    BITRATE_TARGET_KBPS,
    get_display_refresh_hz,
    get_local_ip,
)
from .viewer_window import ViewerWindow


class MainWindow(QMainWindow):
    def __init__(self, test_pattern: bool = False, cloud: Optional[CloudClient] = None):
        super().__init__()

        self.cloud = cloud
        uname = (cloud.user or {}).get("username", "?") if cloud else "?"
        self.setWindowTitle(f"EzPeek — @{uname}")
        self.setMinimumSize(1000, 640)

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
        self._peer_hz: dict[str, float] = {}
        self._relay_agent: Optional[RelayHostAgent] = None

        print(f"[ezpeek] Local display refresh ≈ {self.local_hz:.2f} Hz user={uname}")
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

        self._friends_timer = QTimer(self)
        self._friends_timer.timeout.connect(self.refresh_friends)
        self._friends_timer.start(5000)

        self._presence_timer = QTimer(self)
        self._presence_timer.timeout.connect(self._push_presence)
        self._presence_timer.start(8000)

        self._push_presence()
        self.refresh_friends()

        if test_pattern:
            self.status.setText("Status: Test-pattern mode. Press H to host.")

    def setup_ui(self):
        central = QWidget()
        layout = QVBoxLayout(central)

        uname = (self.cloud.user or {}).get("username", "") if self.cloud else ""
        title = QLabel(f"EzPeek — @{uname}" if uname else "EzPeek")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 22px; color: white;")

        hint = QLabel(
            "LAN devices (left) · Friends via greenbird (right) · "
            "H = host · Double-click LAN peer or use Connect on a hosting friend · "
            f"CBR {BITRATE_TARGET_KBPS // 1000} Mbps · panel ≈ {self.local_hz:.0f} Hz"
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
        self.btn_logout = QPushButton("Log out")
        self.btn_logout.clicked.connect(self._logout)
        btn_row.addWidget(self.btn_host)
        btn_row.addWidget(self.btn_refresh)
        btn_row.addWidget(self.btn_logout)

        splitter = QSplitter(Qt.Horizontal)

        # LAN list
        lan_box = QWidget()
        lan_l = QVBoxLayout(lan_box)
        lan_l.addWidget(QLabel("LAN devices"))
        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._connect_to_selected)
        lan_l.addWidget(self.list_widget)
        splitter.addWidget(lan_box)

        # Friends
        fr_box = QWidget()
        fr_l = QVBoxLayout(fr_box)
        fr_l.addWidget(QLabel("Friends (greenbird)"))
        self.friends_list = QListWidget()
        fr_l.addWidget(self.friends_list)
        fr_btns = QHBoxLayout()
        self.friend_input = QLineEdit()
        self.friend_input.setPlaceholderText("Add by username")
        btn_add = QPushButton("Add")
        btn_add.clicked.connect(self._add_friend)
        btn_accept = QPushButton("Accept selected")
        btn_accept.clicked.connect(self._accept_friend)
        btn_connect = QPushButton("Connect")
        btn_connect.clicked.connect(self._connect_friend)
        btn_fr_refresh = QPushButton("Refresh")
        btn_fr_refresh.clicked.connect(self.refresh_friends)
        fr_btns.addWidget(self.friend_input)
        fr_btns.addWidget(btn_add)
        fr_btns.addWidget(btn_accept)
        fr_btns.addWidget(btn_connect)
        fr_btns.addWidget(btn_fr_refresh)
        fr_l.addLayout(fr_btns)
        splitter.addWidget(fr_box)
        splitter.setSizes([500, 500])

        for lw in (self.list_widget, self.friends_list):
            lw.setStyleSheet(
                """
                QListWidget {
                    background: #111111; color: #ffffff; border: 1px solid #333;
                }
                QListWidget::item:selected { background: #444; }
                """
            )

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.status)
        layout.addLayout(btn_row)
        layout.addWidget(splitter)
        self.setCentralWidget(central)
        self.setStyleSheet(
            """
            QMainWindow { background-color: #0b0b0b; }
            QLabel { color: #ddd; }
            QLineEdit {
                background: #1a1a1a; color: white; border: 1px solid #333; padding: 6px;
            }
            QPushButton {
                background: #2a2a2a; color: white; padding: 8px 12px;
                border: 1px solid #444; border-radius: 4px;
            }
            QPushButton:hover { background: #3a3a3a; }
            """
        )

    # ----- presence / friends -----
    def _lan_ips(self) -> list[str]:
        ips = []
        try:
            ip = get_local_ip()
            if ip and ip != "0.0.0.0":
                ips.append(ip)
        except Exception:
            pass
        return ips

    def _push_presence(self):
        if not self.cloud or not self.cloud.token:
            return
        hosting = bool(self.host.state.proc and self.host.state.proc.poll() is None)
        try:
            self.cloud.set_presence(
                online=True,
                hosting=hosting,
                lan_ips=self._lan_ips(),
                video_port=self.host.state.port if hosting else None,
                ctrl_port=self.host.state.control_port if hosting else None,
                relay_ready=bool(self._relay_agent),
            )
        except CloudError as e:
            print(f"[ezpeek] presence push failed: {e}")

    def refresh_friends(self):
        if not self.cloud:
            return
        try:
            friends = self.cloud.friends()
        except CloudError as e:
            print(f"[ezpeek] friends refresh: {e}")
            return
        self.friends_list.clear()
        for f in friends:
            flags = []
            if f.get("status") == "pending":
                flags.append(f"pending/{f.get('direction')}")
            else:
                flags.append("friend")
            if f.get("online"):
                flags.append("online")
            if f.get("hosting"):
                flags.append("HOSTING")
            if f.get("relay_ready"):
                flags.append("relay")
            label = f"@{f['username']}  —  {', '.join(flags)}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, f)
            self.friends_list.addItem(item)

    def _add_friend(self):
        if not self.cloud:
            return
        name = self.friend_input.text().strip().lstrip("@")
        if not name:
            name, ok = QInputDialog.getText(self, "Add friend", "Username:")
            if not ok or not name.strip():
                return
            name = name.strip().lstrip("@")
        try:
            out = self.cloud.add_friend(name)
            QMessageBox.information(self, "Friends", f"@{name}: {out.get('status')}")
            self.friend_input.clear()
            self.refresh_friends()
        except CloudError as e:
            QMessageBox.warning(self, "Add friend failed", str(e))

    def _accept_friend(self):
        item = self.friends_list.currentItem()
        if not item or not self.cloud:
            return
        data = item.data(Qt.UserRole) or {}
        if data.get("status") != "pending" or data.get("direction") != "incoming":
            QMessageBox.information(self, "Friends", "Select an incoming pending request.")
            return
        try:
            self.cloud.accept_friend(data["username"])
            self.refresh_friends()
        except CloudError as e:
            QMessageBox.warning(self, "Accept failed", str(e))

    def _connect_friend(self):
        item = self.friends_list.currentItem()
        if not item or not self.cloud:
            return
        data = item.data(Qt.UserRole) or {}
        if data.get("status") != "accepted" and data.get("direction") == "friend":
            pass
        if data.get("status") == "pending":
            QMessageBox.information(self, "Connect", "Friend request not accepted yet.")
            return
        username = data.get("username")
        if not username:
            return
        try:
            info = self.cloud.friend_connect(username)
        except CloudError as e:
            QMessageBox.warning(self, "Connect failed", str(e))
            return

        if not info.get("hosting"):
            QMessageBox.information(
                self, "Connect", f"@{username} is online but not hosting yet."
            )
            return

        # Prefer LAN IP that looks like our network
        my_ip = get_local_ip()
        ips = info.get("lan_ips") or []
        if isinstance(ips, str):
            try:
                ips = json.loads(ips)
            except Exception:
                ips = []
        target_ip = None
        for ip in ips:
            if my_ip.startswith("10.") and ip.startswith("10."):
                target_ip = ip
                break
        if not target_ip and ips:
            target_ip = ips[0]

        port = info.get("video_port") or 2734
        ctrl = info.get("ctrl_port") or 2735

        if target_ip:
            self.status.setText(f"Status: Connecting to @{username} via LAN {target_ip}…")
            self._open_viewer(target_ip, int(port), int(ctrl) if ctrl else None)
            return

        # Relay path for control (video still needs LAN/SRT for now)
        QMessageBox.information(
            self,
            "Remote connect",
            f"@{username} has no reachable LAN IP from here.\n"
            "Control can use greenbird reverse-proxy; for full video, use the same LAN "
            "or ensure the host publishes a reachable IP.\n\n"
            "Opening control tunnel + attempting LAN video ports if any…",
        )
        # Still try relay host from API
        relay = info.get("relay") or {}
        # Video without LAN is limited; show message
        self.status.setText(
            f"Status: @{username} remote — no LAN IP; use same network for video for now."
        )

    def _logout(self):
        try:
            if self.cloud:
                self.cloud.set_presence(online=False, hosting=False)
                self.cloud.logout()
        except Exception:
            pass
        clear_session()
        self.close()

    # ----- LAN discovery (existing) -----
    def _force_discovery(self):
        try:
            self.discovery.force_broadcast()
            self.status.setText("Status: Discovery ping sent")
        except Exception as e:
            self.status.setText(f"Status: Discovery ping failed: {e}")

    def _my_advertisement(self):
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
            label += f"  (video {port})"
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
                "On the other PC press H, wait until video ports appear."
            )
            return
        self._open_viewer(ip, int(port), int(ctrl) if ctrl else None)

    def _open_viewer(self, ip: str, port: int, ctrl: int | None):
        if self.viewer_win:
            try:
                self.viewer_win.close()
            except Exception:
                pass
            self.viewer_win = None

        self._current_peer = {"ip": ip, "port": port, "ctrl": ctrl}
        print(f"[ezpeek] Connecting → {ip} video={port} ctrl={ctrl}")
        try:
            self.viewer_win = ViewerWindow(ip, int(port), int(ctrl) if ctrl else None)
            self.viewer_win.show()
            self.viewer_win.raise_()
            self.viewer_win.activateWindow()
            self.status.setText(f"Status: Viewing {ip}:{port}")
        except Exception as e:
            import traceback

            traceback.print_exc()
            QMessageBox.warning(self, "Viewer failed", str(e))

    def toggle_hosting(self) -> None:
        try:
            if self.host.state.proc and self.host.state.proc.poll() is None:
                self.host.stop()
                self._stop_relay()
                self.btn_host.setText("Start Hosting (H)")
                self.status.setText("Status: Not hosting")
                self._push_presence()
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
                self._start_relay()
                self._push_presence()
                try:
                    self.discovery.force_broadcast()
                except Exception:
                    pass
                QTimer.singleShot(500, self._force_discovery)
                QTimer.singleShot(1500, self._force_discovery)
        except Exception as e:
            import traceback

            traceback.print_exc()
            msg = str(e).strip()
            self.status.setText(f"Status: Host failed: {msg.splitlines()[0] if msg else e}")
            QMessageBox.critical(self, "Host failed", msg[:1500])

    def _start_relay(self):
        if not self.cloud or not self.cloud.token:
            return
        self._stop_relay()
        self._relay_agent = RelayHostAgent(
            token=self.cloud.token,
            local_ctrl_port=self.host.state.control_port or 2735,
            server_url=self.cloud.base_url,
            channel="control",
        )
        self._relay_agent.start()
        print("[ezpeek] reverse-proxy host agent started → greenbird")

    def _stop_relay(self):
        if self._relay_agent:
            self._relay_agent.stop()
            self._relay_agent = None

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
            self._stop_relay()
            self.btn_host.setText("Start Hosting (H)")
            self.status.setText(
                f"Status: Hosting stopped (sender died){': ' + err[:120] if err else ''}"
            )
            self._push_presence()
            try:
                self.discovery.force_broadcast()
            except Exception:
                pass

    def closeEvent(self, event):
        try:
            if self.cloud:
                self.cloud.set_presence(online=False, hosting=False, relay_ready=False)
        except Exception:
            pass
        self._stop_relay()
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
