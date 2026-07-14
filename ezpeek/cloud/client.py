from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urljoin

from .config import DEFAULT_RELAY_PORT, get_saved_server_url, relay_endpoint_from_server_url


class CloudError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class CloudClient:
    def __init__(self, base_url: str = "", token: Optional[str] = None):
        url = (base_url or get_saved_server_url() or "").rstrip("/")
        self.base_url = url
        self.token = token
        self.user: Optional[dict] = None

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        auth: bool = False,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise CloudError("no server URL configured — enter one on the sign-in screen")
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if auth:
            if not self.token:
                raise CloudError("not logged in", 401)
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8")
                j = json.loads(detail)
                msg = j.get("detail") or detail
            except Exception:
                msg = str(e)
            raise CloudError(str(msg), e.code) from e
        except urllib.error.URLError as e:
            raise CloudError(f"cannot reach server {self.base_url}: {e.reason}") from e

    def health(self) -> dict:
        return self._request("GET", "/health")

    def register(self, username: str, email: str, password: str) -> dict:
        out = self._request(
            "POST",
            "/auth/register",
            {"username": username, "email": email, "password": password},
        )
        self.token = out["token"]
        self.user = out["user"]
        return out

    def login(self, login: str, password: str) -> dict:
        out = self._request("POST", "/auth/login", {"login": login, "password": password})
        self.token = out["token"]
        self.user = out["user"]
        return out

    def me(self) -> dict:
        out = self._request("GET", "/auth/me", auth=True)
        self.user = out.get("user")
        return out

    def logout(self) -> None:
        try:
            if self.token:
                self._request("POST", "/auth/logout", auth=True)
        except Exception:
            pass
        self.token = None
        self.user = None

    def friends(self) -> list[dict]:
        return self._request("GET", "/friends", auth=True).get("friends") or []

    def add_friend(self, username: str) -> dict:
        return self._request("POST", "/friends/add", {"username": username}, auth=True)

    def accept_friend(self, username: str) -> dict:
        return self._request("POST", "/friends/accept", {"username": username}, auth=True)

    def set_presence(
        self,
        *,
        online: bool = True,
        hosting: bool = False,
        lan_ips: Optional[list[str]] = None,
        video_port: Optional[int] = None,
        ctrl_port: Optional[int] = None,
        relay_ready: bool = False,
    ) -> dict:
        return self._request(
            "POST",
            "/presence",
            {
                "online": online,
                "hosting": hosting,
                "lan_ips": lan_ips or [],
                "video_port": video_port,
                "ctrl_port": ctrl_port,
                "relay_ready": relay_ready,
            },
            auth=True,
        )

    def friend_connect(self, username: str) -> dict:
        return self._request("GET", f"/friends/{username}/connect", auth=True)


class RelayHostAgent:
    """
    Maintains outbound TCP to greenbird so viewers can reverse-proxy in.
    Currently bridges the *control* channel; video still prefers LAN SRT.
    """

    def __init__(
        self,
        token: str,
        local_ctrl_port: int = 2735,
        relay_host: str = "",
        relay_port: int = DEFAULT_RELAY_PORT,
        channel: str = "control",
        server_url: str = "",
    ):
        if not relay_host and server_url:
            relay_host, relay_port = relay_endpoint_from_server_url(server_url)
        self.token = token
        self.local_ctrl_port = local_ctrl_port
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.channel = channel
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ezpeek-relay-host")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._session()
            except Exception as e:
                print(f"[ezpeek relay] host session error: {e}")
            if self._stop.is_set():
                break
            self._stop.wait(3.0)

    def _session(self):
        print(f"[ezpeek relay] connecting to {self.relay_host}:{self.relay_port} as HOST…")
        sock = socket.create_connection((self.relay_host, self.relay_port), timeout=20)
        sock.settimeout(120)
        sock.sendall(f"HOST {self.token} {self.channel}\n".encode())
        line = _recv_line(sock)
        print(f"[ezpeek relay] server: {line}")
        if not line.startswith("OK"):
            sock.close()
            raise RuntimeError(line)
        # wait for PAIRED
        while not self._stop.is_set():
            sock.settimeout(2.0)
            try:
                line = _recv_line(sock)
            except socket.timeout:
                continue
            print(f"[ezpeek relay] {line}")
            if "PAIRED" in line:
                break
            if line.startswith("ERR"):
                sock.close()
                raise RuntimeError(line)
        # Bridge to local control server
        local = socket.create_connection(("127.0.0.1", self.local_ctrl_port), timeout=5)
        _pipe_sockets(sock, local)
        try:
            sock.close()
        except Exception:
            pass
        try:
            local.close()
        except Exception:
            pass


class RelayViewerTunnel:
    """Open a reverse-proxy control tunnel to a friend's host via greenbird."""

    def __init__(
        self,
        token: str,
        friend_username: str,
        local_listen_port: int = 12735,
        relay_host: str = "",
        relay_port: int = DEFAULT_RELAY_PORT,
        channel: str = "control",
        server_url: str = "",
    ):
        if not relay_host and server_url:
            relay_host, relay_port = relay_endpoint_from_server_url(server_url)
        self.token = token
        self.friend_username = friend_username
        self.local_listen_port = local_listen_port
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.channel = channel
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[socket.socket] = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ezpeek-relay-view")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass

    def _run(self):
        # Listen locally; first connection bridges through relay
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", self.local_listen_port))
        srv.listen(1)
        srv.settimeout(1.0)
        self._server = srv
        print(f"[ezpeek relay] viewer local listen 127.0.0.1:{self.local_listen_port}")
        while not self._stop.is_set():
            try:
                client, _ = srv.accept()
            except socket.timeout:
                continue
            try:
                remote = socket.create_connection((self.relay_host, self.relay_port), timeout=20)
                remote.sendall(
                    f"VIEW {self.token} {self.friend_username} {self.channel}\n".encode()
                )
                line = _recv_line(remote)
                print(f"[ezpeek relay] view: {line}")
                if not line.startswith("OK"):
                    client.close()
                    remote.close()
                    continue
                # may get OK VIEW pairing then data — host sends OK PAIRED on its side
                _pipe_sockets(client, remote)
            except Exception as e:
                print(f"[ezpeek relay] view bridge error: {e}")
            finally:
                try:
                    client.close()
                except Exception:
                    pass


def _recv_line(sock: socket.socket) -> str:
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    return buf.decode("utf-8", errors="ignore").strip()


def _pipe_sockets(a: socket.socket, b: socket.socket):
    stop = threading.Event()

    def one_way(src, dst):
        try:
            while not stop.is_set():
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        stop.set()
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass

    t1 = threading.Thread(target=one_way, args=(a, b), daemon=True)
    t2 = threading.Thread(target=one_way, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
