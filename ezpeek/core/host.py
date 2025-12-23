from __future__ import annotations

import random
import subprocess
import time
from dataclasses import dataclass

from ezpeek.utils import get_local_ip
from .capture import CaptureSpec
from .encoder import EncodeSpec
from .transport import TransportSpec, build_sender_cmd


@dataclass
class HostState:
    host_ip: str
    port: int
    proc: subprocess.Popen | None = None
    last_error: str = ""


class HostService:
    def __init__(self, fps: int = 60, bitrate_kbps: int = 12000, codec: str = "h264", port: int | None = None):
        self.fps = fps
        self.bitrate_kbps = bitrate_kbps
        self.codec = codec
        self.port = port or random.randint(15000, 25000)
        self.state = HostState(host_ip=get_local_ip(), port=self.port)

    def start(self) -> HostState:
        if self.state.proc and self.state.proc.poll() is None:
            return self.state

        self.state.last_error = ""

        capture = CaptureSpec(fps=self.fps)
        encode = EncodeSpec(codec=self.codec, fps=self.fps, bitrate_kbps=self.bitrate_kbps, gop=self.fps)  # type: ignore[arg-type]
        tx = TransportSpec(transport="srt", host=self.state.host_ip, port=self.state.port)

        cmd = build_sender_cmd(capture, encode, tx)
        self.state.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        # Give ffmpeg a moment; if it exits immediately, grab output
        time.sleep(0.25)
        if self.state.proc.poll() is not None:
            out = ""
            try:
                if self.state.proc.stdout:
                    out = self.state.proc.stdout.read() or ""
            except Exception:
                out = ""
            self.state.last_error = out.strip()[-1200:] if out else "ffmpeg exited immediately (no output captured)"
            raise RuntimeError(self.state.last_error)

        return self.state

    def stop(self) -> None:
        p = self.state.proc
        if not p:
            return
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=3.0)
        self.state.proc = None
