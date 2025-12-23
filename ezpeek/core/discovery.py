import socket
import threading
import time

from ezpeek.utils import get_local_ip


BROADCAST_PORT = 52525
MAGIC = "EZPEEK_HELLO"


class DiscoveryService:
    def __init__(self, on_peer_found=None, get_advertisement=None):
        """
        on_peer_found: callback(name, ip, port|None)
        get_advertisement: callable returning dict-like info to broadcast (e.g. {"port": 17000})
        """
        self.on_peer_found = on_peer_found
        self.get_advertisement = get_advertisement
        self.running = False
        self._seen = set()  # (ip, port)

        self._my_ip = get_local_ip()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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

    def _broadcaster(self):
        hostname = socket.gethostname()

        while self.running:
            adv = {}
            try:
                if self.get_advertisement:
                    adv = dict(self.get_advertisement() or {})
            except Exception:
                adv = {}

            port = adv.get("port", "")
            # MAGIC|hostname|ip|port
            message = f"{MAGIC}|{hostname}|{self._my_ip}|{port}"
            try:
                self.sock.sendto(message.encode(), ("<broadcast>", BROADCAST_PORT))
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
                # Old format: MAGIC|hostname
                # New format: MAGIC|hostname|ip|port
                name = parts[1] if len(parts) > 1 else "Unknown"
                ip = parts[2] if len(parts) > 2 else addr[0]
                port_str = parts[3] if len(parts) > 3 else ""
                port = int(port_str) if port_str.isdigit() else None

                # Skip self
                if ip == self._my_ip:
                    continue

                key = (ip, port)
                if key in self._seen:
                    continue
                self._seen.add(key)

                if self.on_peer_found:
                    self.on_peer_found(name, ip, port)

            except:
                break
