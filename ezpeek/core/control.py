"""
Lightweight TCP control channel for input remoting.

Protocol (simple, human readable, newline delimited):
  MOUSE_MOVE <x> <y>
  MOUSE_CLICK <button> [down|up]
  MOUSE_WHEEL <delta>
  KEY <key> [down|up]
  PING
  QUIT

This allows the viewer side (or any client) to forward input events to the host machine.
Designed to be low overhead and easy to extend.
"""

from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

from .input_controller import InputController


class ControlServer:
    """
    Runs on the HOST side. Listens for control connections and applies input locally.
    Always prefer binding host="0.0.0.0" so LAN peers can connect.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 0,
        on_event: Optional[Callable[[str], None]] = None,
    ):
        self.host = host
        self.port = port or 0
        self.on_event = on_event
        self._sock: Optional[socket.socket] = None
        self._clients: list[socket.socket] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.input = InputController()

    def start(self) -> int:
        if self._running:
            return self.port

        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # On Windows, dual-stack quirks are fine; keep IPv4 simple for Phase 1.
        self._sock.bind((self.host, self.port))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="ezpeek-ctrl-accept")
        self._thread.start()
        print(f"[ezpeek-control] Server listening on {self.host}:{self.port}")
        return self.port

    def stop(self):
        self._running = False
        for c in list(self._clients):
            try:
                c.close()
            except Exception:
                pass
        self._clients.clear()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _accept_loop(self):
        while self._running and self._sock:
            try:
                self._sock.settimeout(1.0)
                client, addr = self._sock.accept()
                client.settimeout(300.0)
                self._clients.append(client)
                print(f"[ezpeek-control] Client connected from {addr}")
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client, addr),
                    daemon=True,
                    name=f"ezpeek-ctrl-{addr[0]}",
                )
                t.start()
            except socket.timeout:
                continue
            except TimeoutError:
                continue
            except Exception as e:
                if not self._running:
                    break
                print(f"[ezpeek-control] accept error: {e}")

    def _handle_client(self, client: socket.socket, addr):
        buf = b""
        try:
            while self._running:
                try:
                    data = client.recv(1024)
                    if not data:
                        break
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = line.decode("utf-8", errors="ignore").strip()
                        if msg:
                            self._dispatch(msg)
                            if self.on_event:
                                try:
                                    self.on_event(msg)
                                except Exception:
                                    pass
                except (socket.timeout, TimeoutError):
                    continue
                except Exception as e:
                    print(f"[ezpeek-control] client {addr} error: {e}")
                    break
        finally:
            print(f"[ezpeek-control] Client disconnected {addr}")
            try:
                client.close()
            except Exception:
                pass
            if client in self._clients:
                self._clients.remove(client)

    def _dispatch(self, msg: str):
        parts = msg.split()
        if not parts:
            return
        cmd = parts[0].upper()

        try:
            if cmd == "MOUSE_MOVE" and len(parts) >= 3:
                x, y = int(parts[1]), int(parts[2])
                self.input.send_mouse_move(x, y, absolute=True)
            elif cmd == "MOUSE_CLICK" and len(parts) >= 2:
                btn = int(parts[1])
                down = parts[2].lower() != "up" if len(parts) > 2 else True
                self.input.send_click(btn, down=down)
            elif cmd == "MOUSE_WHEEL" and len(parts) >= 2:
                delta = int(parts[1])
                self.input.send_mouse_wheel(delta)
            elif cmd == "KEY" and len(parts) >= 2:
                key = parts[1]
                down = parts[2].lower() != "up" if len(parts) > 2 else True
                self.input.send_key(key, down=down)
            elif cmd == "PING":
                try:
                    client_reply = None  # fire-and-forget for Phase 1
                    _ = client_reply
                except Exception:
                    pass
            elif cmd == "QUIT":
                pass
        except Exception as e:
            print(f"[ezpeek-control] dispatch error: {e}")


class ControlClient:
    """
    Used on VIEWER side to forward input events to a remote host's ControlServer.
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._host: str = ""
        self._port: int = 0

    @property
    def connected(self) -> bool:
        return self._connected and self._sock is not None

    def connect(self, host: str, port: int, timeout: float = 5.0, retries: int = 3) -> bool:
        """Connect with short retries (host control may come up slightly after video)."""
        self.close()
        self._host = host
        self._port = port
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect((host, port))
                # Keepalive-ish: longer idle timeout for remoting sessions
                sock.settimeout(None)
                self._sock = sock
                self._connected = True
                print(f"[ezpeek-control] Connected to {host}:{port} (attempt {attempt})")
                return True
            except Exception as e:
                last_err = e
                print(f"[ezpeek-control] Connect attempt {attempt}/{retries} to {host}:{port} failed: {e}")
                try:
                    sock.close()  # type: ignore[name-defined]
                except Exception:
                    pass
                self._sock = None
                self._connected = False
                if attempt < retries:
                    import time

                    time.sleep(0.4 * attempt)
        print(f"[ezpeek-control] All connect attempts failed: {last_err}")
        return False

    def send(self, msg: str) -> bool:
        if not self._connected or not self._sock:
            return False
        try:
            data = (msg.strip() + "\n").encode("utf-8")
            self._sock.sendall(data)
            return True
        except Exception as e:
            print(f"[ezpeek-control] send failed: {e}")
            self._connected = False
            return False

    def mouse_move(self, x: int, y: int) -> bool:
        return self.send(f"MOUSE_MOVE {int(x)} {int(y)}")

    def mouse_click(self, button: int = 1, down: bool = True) -> bool:
        state = "down" if down else "up"
        return self.send(f"MOUSE_CLICK {int(button)} {state}")

    def mouse_wheel(self, delta: int) -> bool:
        return self.send(f"MOUSE_WHEEL {int(delta)}")

    def key(self, key: str, down: bool = True) -> bool:
        state = "down" if down else "up"
        return self.send(f"KEY {key} {state}")

    def close(self):
        if self._sock:
            try:
                try:
                    self.send("QUIT")
                except Exception:
                    pass
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._connected = False


def test_control_roundtrip():
    """For manual verification."""
    srv = ControlServer(port=0)
    port = srv.start()
    cli = ControlClient()
    ok = cli.connect("127.0.0.1", port)
    cli.mouse_move(100, 100)
    cli.mouse_click(1)
    cli.key("a")
    cli.close()
    srv.stop()
    return ok, port
