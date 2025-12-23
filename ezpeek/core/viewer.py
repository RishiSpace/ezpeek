from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .transport import build_receiver_cmd


@dataclass
class ViewerState:
    proc: subprocess.Popen | None = None


class ViewerService:
    def __init__(self):
        self.state = ViewerState()

    def start(self, host_ip: str, port: int) -> None:
        if self.state.proc and self.state.proc.poll() is None:
            return
        cmd = build_receiver_cmd(host_ip, port)
        self.state.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

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
