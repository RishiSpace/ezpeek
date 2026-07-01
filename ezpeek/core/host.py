from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass

from ezpeek.utils import get_local_ip
from .capture import CaptureSpec
from .control import ControlServer
from .encoder import EncodeSpec
from .nat_traversal import get_best_connection_address
from .transport import TransportSpec, build_sender_cmd


@dataclass
class HostState:
    host_ip: str
    port: int
    proc: subprocess.Popen | None = None
    control_port: int | None = None
    last_error: str = ""


class HostService:
    DEFAULT_PORT = 2734
    DEFAULT_CONTROL_PORT = 2735

    def __init__(self, fps: int = 60, bitrate_kbps: int = 12000, codec: str = "h264", port: int | None = None, enable_control: bool = True, use_nat: bool = False):
        self.fps = fps
        self.bitrate_kbps = bitrate_kbps
        self.codec = codec
        self.port = port or self.DEFAULT_PORT
        self.enable_control = enable_control
        self.use_nat = use_nat
        self._control_server: ControlServer | None = None
        self.state = HostState(host_ip=get_local_ip(), port=self.port)

    def start(self) -> HostState:
        if self.state.proc and self.state.proc.poll() is None:
            return self.state

        self.state.last_error = ""

        # Optional NAT: discover public address for advertisement (viewer will try public IP)
        if self.use_nat:
            try:
                ip, prt, ctype = get_best_connection_address(local_port=self.port)
                if ip and ip != "0.0.0.0":
                    self.state.host_ip = ip
                    if prt:
                        self.port = prt
                    print(f"[ezpeek] NAT: advertising {ctype} address {ip}:{self.port}")
            except Exception as e:
                print(f"[ezpeek] NAT discovery failed (using local): {e}")

        capture = CaptureSpec(fps=self.fps)
        encode = EncodeSpec(codec=self.codec, fps=self.fps, bitrate_kbps=self.bitrate_kbps, gop=self.fps)  # type: ignore[arg-type]
        tx = TransportSpec(transport="srt", host=self.state.host_ip, port=self.state.port)

        cmd = build_sender_cmd(capture, encode, tx)
        print(f"[ezpeek host] build_sender_cmd returned. Launching sender ffmpeg...")
        print(f"[ezpeek host] sender cmd (first 8 elems + last): {cmd[:8] + ['...'] + cmd[-1:]}")
        self.state.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        print(f"[ezpeek host] Sender ffmpeg Popen pid={self.state.proc.pid}")

        # Start control server (for input remoting)
        if self.enable_control:
            try:
                self._control_server = ControlServer(host=self.state.host_ip, port=self.DEFAULT_CONTROL_PORT)
                ctrl_port = self._control_server.start()
                self.state.control_port = ctrl_port
                print(f"[ezpeek host] Control server started on port {ctrl_port}")
            except Exception as e:
                print(f"[ezpeek] Warning: could not start control server: {e}")
                self.state.control_port = None

        # Give ffmpeg a moment; if it exits immediately, grab output
        time.sleep(0.3)
        if self.state.proc.poll() is not None:
            out = ""
            try:
                if self.state.proc.stdout:
                    out = self.state.proc.stdout.read() or ""
            except Exception:
                out = ""
            self.state.last_error = out.strip()[-1200:] if out else "ffmpeg exited immediately (no output captured)"
            print(f"[ezpeek host] Sender ffmpeg died early! last_error={self.state.last_error[:300]}")
            # cleanup control
            self._stop_control()
            raise RuntimeError(self.state.last_error)

        # Start monitor thread to detect if sender dies later
        if not getattr(self, '_monitor_thread', None):
            self._monitor_thread = threading.Thread(target=self._monitor_proc, daemon=True)
            self._monitor_thread.start()

        return self.state

    def _stop_control(self):
        if self._control_server:
            try:
                self._control_server.stop()
            except Exception:
                pass
            self._control_server = None
        self.state.control_port = None

    def _monitor_proc(self):
        while self.state.proc:
            if self.state.proc.poll() is not None:
                if not self.state.last_error:
                    self.state.last_error = "Sender process exited unexpectedly"
                self.state.proc = None
                self._stop_control()
                break
            time.sleep(1)

    def stop(self) -> None:
        p = self.state.proc
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.5)
            except subprocess.TimeoutExpired:
                # More forceful cross platform
                try:
                    if hasattr(p, "kill"):
                        p.kill()
                    else:
                        p.terminate()
                    p.wait(timeout=2)
                except Exception:
                    pass
            except Exception:
                pass
        self.state.proc = None

        self._stop_control()
