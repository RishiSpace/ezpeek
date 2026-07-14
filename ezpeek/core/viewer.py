from __future__ import annotations

import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ezpeek.utils import get_log_dir
from .transport import build_receiver_cmd


@dataclass
class ViewerState:
    proc: subprocess.Popen | None = None
    log_path: str = ""
    last_error: str = ""


class ViewerService:
    """Launches external ffplay to receive an SRT stream from a LAN host."""

    def __init__(self):
        self.state = ViewerState()
        self._log_file = None

    def start(self, host_ip: str, port: int) -> None:
        print(f"[ezpeek viewer] ViewerService.start(host={host_ip}, port={port})")
        if self.state.proc and self.state.proc.poll() is None:
            print("[ezpeek viewer] Viewer already running, stopping previous instance first")
            self.stop()

        self.state.last_error = ""
        cmd = build_receiver_cmd(host_ip, port)

        env = os.environ.copy()
        # Ensure GUI backends can find a display when launched from a desktop session.
        if platform.system().lower() == "linux":
            if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
                print("[ezpeek viewer] WARNING: No DISPLAY/WAYLAND_DISPLAY — ffplay window may not appear")

        log_path = get_log_dir() / f"ffplay_{host_ip.replace('.', '_').replace(':', '_')}_{port}.log"
        self.state.log_path = str(log_path)
        try:
            if self._log_file:
                try:
                    self._log_file.close()
                except Exception:
                    pass
            self._log_file = open(log_path, "w", buffering=1)
            print(f"[ezpeek viewer] ffplay log: {log_path}")
        except Exception as e:
            print(f"[ezpeek viewer] Could not open log file {log_path}: {e}")
            self._log_file = None

        popen_kwargs: dict = {
            "stdout": self._log_file if self._log_file else subprocess.DEVNULL,
            "stderr": subprocess.STDOUT,
            "env": env,
        }

        # Windows: do NOT use CREATE_NO_WINDOW — ffplay needs a visible SDL window.
        # start_new_session helps avoid signals/parent tty quirks on Unix.
        if platform.system().lower() != "windows":
            popen_kwargs["start_new_session"] = True

        print(f"[ezpeek viewer] Launching: {cmd}")
        self.state.proc = subprocess.Popen(cmd, **popen_kwargs)
        print(f"[ezpeek viewer] Popen pid={self.state.proc.pid}")

        # Give ffplay time to connect or fail fast.
        time.sleep(1.2)

        if self.state.proc.poll() is not None:
            code = self.state.proc.returncode
            out = ""
            try:
                out = Path(log_path).read_text(errors="ignore")
            except Exception:
                pass
            self.state.last_error = (out or f"ffplay exited with code {code}").strip()[-2000:]
            print(f"[ezpeek viewer] !!! ffplay exited immediately with code {code}")
            print(f"[ezpeek viewer] log:\n{self.state.last_error[:2000]}")
            self.state.proc = None
            raise RuntimeError(
                f"ffplay failed to start (exit {code}). See log: {log_path}\n{self.state.last_error[:400]}"
            )

        print(
            f"[ezpeek viewer] ffplay running. Look for window 'EzPeek Video - {host_ip}:{port}'. "
            f"Log: {log_path}"
        )

    def stop(self) -> None:
        p = self.state.proc
        if not p:
            if self._log_file:
                try:
                    self._log_file.close()
                except Exception:
                    pass
                self._log_file = None
            return

        print("[ezpeek viewer] Stopping viewer process")
        if p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                    p.wait(timeout=2.0)
                except Exception:
                    pass
            except Exception:
                pass

        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        self.state.proc = None
