import socket
import threading
import time

from ezpeek.utils import get_local_ip


def _get_subnet_broadcast(ip: str) -> str:
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


BROADCAST_PORT = 52525
MAGIC = "EZPEEK_HELLO"


class DiscoveryService:
    def __init__(self, on_peer_found=None, get_advertisement=None):
        """
        on_peer_found: callback(name, ip, port|None)
        get_advertisement: callable returning dict-like info to broadcast (e.g. {"port": 2734})
        """
        self.on_peer_found = on_peer_found
        self.get_advertisement = get_advertisement
        self.running = False
        self._seen = set()  # (ip, port)

        self._my_ip = get_local_ip()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self.sock.bind(("", BROADCAST_PORT))

    def start(self):
        self.running = True
        threading.Thread(target=self._listener, daemon=True).start()
        threading.Thread(target=self._broadcaster, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except:
            pass

    def force_broadcast(self):
        """Immediately send one discovery packet (useful after hosting state change)."""
        if not self.running:
            return
        adv = {}
        try:
            if self.get_advertisement:
                adv = dict(self.get_advertisement() or {})
        except Exception:
            adv = {}
        video_port = adv.get("port") or adv.get("video_port") or ""
        ctrl = adv.get("ctrl") or adv.get("control_port") or ""
        message = f"{MAGIC}|{socket.gethostname()}|{self._my_ip}|{video_port}|{ctrl}"
        try:
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
            print(f"[ezpeek] Discovery force broadcast: {message}")
        except Exception:
            pass

    def _broadcaster(self):
        hostname = socket.gethostname()

        while self.running:
            adv = {}
            try:
                if self.get_advertisement:
                    adv = dict(self.get_advertisement() or {})
            except Exception:
                adv = {}

            # Support richer advertisement: {"port": video_port, "ctrl": control_port, ...}
            video_port = adv.get("port") or adv.get("video_port") or ""
            ctrl = adv.get("ctrl") or adv.get("control_port") or ""
            # MAGIC|hostname|ip|video_port|ctrl_port
            message = f"{MAGIC}|{hostname}|{self._my_ip}|{video_port}|{ctrl}"
            try:
                msg = message.encode()
                # Try common broadcast addresses for better compatibility across OS/VMs/routers
                dests = ["<broadcast>", "255.255.255.255"]
                bcast = _get_subnet_broadcast(self._my_ip)
                if bcast:
                    dests.append(bcast)
                for bcast_addr in set(dests):
                    try:
                        self.sock.sendto(msg, (bcast_addr, BROADCAST_PORT))
                    except Exception:
                        pass
                if self.running:
                    print(f"[ezpeek] Discovery broadcast sent: {message}")
            except Exception:
                pass
            time.sleep(2)

    def _listener(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                message = data.decode(errors="ignore")

                if not message.startswith(MAGIC):
                    continue

                parts = message.split("|")
                # Formats:
                #   MAGIC|hostname|ip|video_port
                #   MAGIC|hostname|ip|video_port|ctrl_port
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

                # Skip self
                if ip == self._my_ip:
                    continue

                key = (ip, port)
                if key in self._seen:
                    continue
                self._seen.add(key)

                if self.on_peer_found:
                    # Pass extra metadata via a small wrapper or by convention (ip, video_port, ctrl_port)
                    # Keep backward compat: existing callers receive (name, ip, port)
                    # We attach ctrl via item data later in GUI
                    try:
                        self.on_peer_found(name, ip, port, ctrl_port=ctrl_port)
                    except TypeError:
                        # Older callback signature
                        self.on_peer_found(name, ip, port)
                    if self.running:
                        print(f"[ezpeek] Discovered peer from network: {name} @ {ip} port={port}")

            except:
                break
