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
import time
from typing import Callable, Optional

from .input_controller import InputController


class ControlServer:
    """
    Runs on the HOST side. Listens for control connections and applies input locally.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 0, on_event: Optional[Callable[[str], None]] = None):
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
        self._sock.bind((self.host, self.port))
        self._sock.listen(4)
        self.port = self._sock.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
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
                client.settimeout(300)  # 5 minutes idle timeout for control connection
                self._clients.append(client)
                t = threading.Thread(target=self._handle_client, args=(client, addr), daemon=True)
                t.start()
            except socket.timeout:
                continue
            except Exception:
                if not self._running:
                    break

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
                except TimeoutError:
                    continue  # no data yet, keep waiting for input
                except Exception:
                    break
        finally:
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
                # could reply but fire-and-forget is fine for now
                pass
            elif cmd == "QUIT":
                # close handled by client loop
                pass
        except Exception as e:
            # Never crash the control thread
            print(f"[ezpeek-control] dispatch error: {e}")


class ControlClient:
    """
    Used on VIEWER side to forward input events to a remote host's ControlServer.
    """

    def __init__(self):
        self._sock: Optional[socket.socket] = None
        self._connected = False

    def connect(self, host: str, port: int, timeout: float = 3.0) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(timeout)
            self._sock.connect((host, port))
            self._connected = True
            return True
        except Exception:
            self._connected = False
            self._sock = None
            return False

    def send(self, msg: str) -> bool:
        if not self._connected or not self._sock:
            return False
        try:
            data = (msg.strip() + "\n").encode("utf-8")
            self._sock.sendall(data)
            return True
        except Exception:
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
                self.send("QUIT")
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._connected = False


# Simple test helper
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