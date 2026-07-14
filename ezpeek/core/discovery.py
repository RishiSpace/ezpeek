from __future__ import annotations

import socket
import threading
import time
from typing import Callable, Optional, Set

from ezpeek.utils import get_local_ip


BROADCAST_PORT = 27787
MAGIC = "EZPEEK_HELLO"


def _broadcast_targets(ip: str) -> list[str]:
    """
    Destinations for discovery UDP.

    Important: we used to only add a /24 directed broadcast (x.y.z.255).
    On a /16 LAN (e.g. Linux 10.0.0.3/16 and Windows 10.0.7.x) that NEVER
    reaches the other host. Include /16 and /8 candidates + limited broadcast.
    """
    dests = ["<broadcast>", "255.255.255.255"]
    if not ip or ip == "0.0.0.0":
        return dests
    parts = ip.split(".")
    if len(parts) != 4:
        return dests
    try:
        a, b, c, _d = (int(x) for x in parts)
    except ValueError:
        return dests
    # /24, /16, /8 directed broadcasts (harmless if unused)
    dests.append(f"{a}.{b}.{c}.255")
    dests.append(f"{a}.{b}.255.255")
    dests.append(f"{a}.255.255.255")
    return dests


class DiscoveryService:
    """
    LAN peer discovery via UDP broadcast + unicast replies.

    Packet format:
      EZPEEK_HELLO|<hostname>|<ip>|<video_port>|<ctrl_port>|<refresh_hz>

    When we *receive* a peer hello, we unicast our hello back to the sender.
    That fixes asymmetric cases (Windows receives limited broadcasts from Linux
    but Linux never sees Windows' /24-only directed broadcasts).
    """

    def __init__(
        self,
        on_peer_found: Optional[Callable] = None,
        get_advertisement: Optional[Callable] = None,
    ):
        self.on_peer_found = on_peer_found
        self.get_advertisement = get_advertisement
        self.running = False
        # ip -> last advertised (port, ctrl, hz)
        self._last: dict[str, tuple[Optional[int], Optional[int], Optional[float]]] = {}
        # Peers we unicast to on each tick (source IPs that talked to us)
        self._known_peers: Set[str] = set()
        self._my_ip = get_local_ip()
        self._lock = threading.Lock()

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
        print(
            f"[ezpeek] Discovery started on UDP {BROADCAST_PORT} "
            f"(my_ip={self._my_ip}, bcast targets={_broadcast_targets(self._my_ip)})"
        )

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass

    def _build_message(self) -> str:
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

    def _send_message(self, message: str, extra_unicast: Optional[list[str]] = None) -> None:
        msg = message.encode()
        dests = set(_broadcast_targets(self._my_ip))
        with self._lock:
            peers = list(self._known_peers)
        for peer in peers:
            dests.add(peer)
        if extra_unicast:
            dests.update(extra_unicast)

        for dest in dests:
            try:
                self.sock.sendto(msg, (dest, BROADCAST_PORT))
            except Exception:
                pass

    def force_broadcast(self):
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
                advertised_ip = parts[2] if len(parts) > 2 else addr[0]
                if advertised_ip in ("0.0.0.0", "", None):
                    advertised_ip = addr[0]

                # CRITICAL: connect using the UDP *source* address, not the
                # self-reported IP in the payload. Hosts often advertise a
                # virtual/VPN NIC (e.g. 192.168.206.x) that peers cannot reach,
                # while packets actually arrive from a reachable LAN IP.
                src_ip = addr[0]
                connect_ip = src_ip or advertised_ip

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

                # Skip self
                if connect_ip == self._my_ip or src_ip == self._my_ip:
                    continue
                if advertised_ip == self._my_ip and src_ip == self._my_ip:
                    continue

                with self._lock:
                    self._known_peers.add(connect_ip)
                    if advertised_ip and advertised_ip != self._my_ip:
                        self._known_peers.add(advertised_ip)

                # Unicast our hello back (source first — known reachable path)
                try:
                    reply = self._build_message()
                    self.sock.sendto(reply.encode(), (src_ip, BROADCAST_PORT))
                    if advertised_ip and advertised_ip != src_ip:
                        try:
                            self.sock.sendto(reply.encode(), (advertised_ip, BROADCAST_PORT))
                        except Exception:
                            pass
                except Exception:
                    pass

                key = (port, ctrl_port, refresh_hz, advertised_ip)
                prev = self._last.get(connect_ip)
                if prev == key:
                    continue
                self._last[connect_ip] = key

                if advertised_ip and advertised_ip != connect_ip:
                    print(
                        f"[ezpeek] Peer {name} advertised {advertised_ip} but "
                        f"packets come from {connect_ip} — using {connect_ip} for connections"
                    )

                self._emit_peer(name, connect_ip, port, ctrl_port, refresh_hz)
                print(
                    f"[ezpeek] Discovered peer: {name} @ {connect_ip} "
                    f"video={port} ctrl={ctrl_port} hz={refresh_hz} "
                    f"(advertised={advertised_ip}, src={src_ip})"
                )

            except socket.timeout:
                continue
            except Exception:
                if not self.running:
                    break
                time.sleep(0.1)
                continue
