from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional

from ezpeek.utils import get_local_ip


def _get_subnet_broadcast(ip: str) -> Optional[str]:
    """Best-effort /24 broadcast for the given IP (common for home/LAN routers)."""
    if not ip or ip == "0.0.0.0":
        return None
    try:
        parts = ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    except Exception:
        pass
    return None


BROADCAST_PORT = 27787
MAGIC = "EZPEEK_HELLO"


class DiscoveryService:
    """
    LAN peer discovery via UDP broadcast.

    Packet format:
      EZPEEK_HELLO|<hostname>|<ip>|<video_port>|<ctrl_port>|<refresh_hz>

    Empty video/ctrl fields mean the peer is online but not hosting.
    refresh_hz is the peer's primary display refresh rate (for FPS negotiation).
    """

    def __init__(
        self,
        on_peer_found: Optional[Callable] = None,
        get_advertisement: Optional[Callable] = None,
    ):
        """
        on_peer_found: callback(name, ip, port|None, ctrl_port=None, refresh_hz=None)
        get_advertisement: callable returning dict e.g. {"port": 2734, "ctrl": 2735, "hz": 144}
        """
        self.on_peer_found = on_peer_found
        self.get_advertisement = get_advertisement
        self.running = False
        # ip -> last advertised (port, ctrl, hz)
        self._last: dict[str, tuple[Optional[int], Optional[int], Optional[float]]] = {}
        self._my_ip = get_local_ip()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self.sock.bind(("", BROADCAST_PORT))
        self.sock.settimeout(1.0)

    def start(self):
        self.running = True
        threading.Thread(target=self._listener, daemon=True, name="ezpeek-discovery-listen").start()
        threading.Thread(target=self._broadcaster, daemon=True, name="ezpeek-discovery-bcast").start()
        print(f"[ezpeek] Discovery started on UDP {BROADCAST_PORT} (my_ip={self._my_ip})")

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass

    def _build_message(self) -> str:
        # Refresh local IP occasionally (DHCP / interface changes).
        try:
            self._my_ip = get_local_ip()
        except Exception:
            pass
        adv = {}
        try:
            if self.get_advertisement:
                adv = dict(self.get_advertisement() or {})
        except Exception:
            adv = {}
        video_port = adv.get("port") or adv.get("video_port") or ""
        ctrl = adv.get("ctrl") or adv.get("control_port") or ""
        hz = adv.get("hz") or adv.get("refresh") or ""
        return f"{MAGIC}|{socket.gethostname()}|{self._my_ip}|{video_port}|{ctrl}|{hz}"

    def _send_message(self, message: str) -> None:
        msg = message.encode()
        dests = ["<broadcast>", "255.255.255.255"]
        bcast = _get_subnet_broadcast(self._my_ip)
        if bcast:
            dests.append(bcast)
        for bcast_addr in set(dests):
            try:
                self.sock.sendto(msg, (bcast_addr, BROADCAST_PORT))
            except Exception:
                pass

    def force_broadcast(self):
        """Immediately send one discovery packet (e.g. after hosting starts)."""
        if not self.running:
            return
        try:
            message = self._build_message()
            self._send_message(message)
            print(f"[ezpeek] Discovery force broadcast: {message}")
        except Exception:
            pass

    def _broadcaster(self):
        while self.running:
            try:
                message = self._build_message()
                self._send_message(message)
                print(f"[ezpeek] Discovery broadcast sent: {message}")
            except Exception:
                pass
            # Faster when hosting so peers pick up ports quickly
            time.sleep(1.5)

    def _emit_peer(
        self,
        name: str,
        ip: str,
        port: Optional[int],
        ctrl_port: Optional[int],
        refresh_hz: Optional[float] = None,
    ):
        if not self.on_peer_found:
            return
        try:
            self.on_peer_found(name, ip, port, ctrl_port=ctrl_port, refresh_hz=refresh_hz)
        except TypeError:
            try:
                self.on_peer_found(name, ip, port, ctrl_port=ctrl_port)
            except TypeError:
                self.on_peer_found(name, ip, port)

    def _listener(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                message = data.decode(errors="ignore").strip()

                if not message.startswith(MAGIC):
                    continue

                print(f"[ezpeek] Received discovery packet: {message} from {addr}")

                parts = message.split("|")
                name = parts[1] if len(parts) > 1 else "Unknown"
                ip = parts[2] if len(parts) > 2 else addr[0]
                if ip in ("0.0.0.0", "", None):
                    ip = addr[0]
                port_str = parts[3] if len(parts) > 3 else ""
                port = int(port_str) if port_str.isdigit() else None

                ctrl_port = None
                if len(parts) > 4:
                    cstr = parts[4]
                    ctrl_port = int(cstr) if cstr.isdigit() else None

                refresh_hz = None
                if len(parts) > 5 and parts[5]:
                    try:
                        refresh_hz = float(parts[5])
                    except ValueError:
                        refresh_hz = None

                # Skip self (compare both advertised IP and packet source)
                if ip == self._my_ip or addr[0] == self._my_ip:
                    continue

                key = (port, ctrl_port, refresh_hz)
                prev = self._last.get(ip)
                if prev == key:
                    continue
                self._last[ip] = key

                self._emit_peer(name, ip, port, ctrl_port, refresh_hz)
                print(
                    f"[ezpeek] Discovered peer: {name} @ {ip} "
                    f"video={port} ctrl={ctrl_port} hz={refresh_hz}"
                )

            except socket.timeout:
                continue
            except Exception:
                if not self.running:
                    break
                time.sleep(0.1)
                continue
