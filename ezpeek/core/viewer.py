from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from .transport import build_receiver_cmd


@dataclass
class ViewerState:
    proc: subprocess.Popen | None = None


class ViewerService:
    def __init__(self):
        self.state = ViewerState()

    def start(self, host_ip: str, port: int) -> None:
        print(f"[ezpeek viewer] ViewerService.start(host={host_ip}, port={port})")
        if self.state.proc and self.state.proc.poll() is None:
            print("[ezpeek viewer] Viewer already running, skipping start")
            return
        cmd = build_receiver_cmd(host_ip, port)

        # Add a nice window title so user can identify it
        cmd = cmd[:1] + ["-window_title", f"EzPeek Video - {host_ip}:{port}"] + cmd[1:]

        print(f"[ezpeek viewer] Launching external viewer process...")
        # Use env explicitly. We want the GUI ffplay window to appear in the user's desktop session.
        env = os.environ.copy()

        # Redirect ffplay's output (status, errors, connection info) to a dedicated log file.
        # This prevents any pipe buffering issues and gives full debug output for ffplay/SDL/SRT.
        log_path = f"/tmp/ezpeek_ffplay_{host_ip.replace('.', '_')}_{port}.log"
        try:
            log_file = open(log_path, "w", buffering=1)
            print(f"[ezpeek viewer] ffplay stderr/stdout redirected to: {log_path}")
            print("[ezpeek viewer] If no video window appears, tail -f that file for errors.")
        except Exception as e:
            print(f"[ezpeek viewer] Could not open log file {log_path}: {e}")
            log_file = None

        popen_kwargs = {
            "stdout": log_file if log_file else subprocess.DEVNULL,
            "stderr": subprocess.STDOUT,
            "env": env,
            "text": True,
        }

        self.state.proc = subprocess.Popen(cmd, **popen_kwargs)
        print(f"[ezpeek viewer] Popen returned, pid={self.state.proc.pid if self.state.proc else None}")
        print(f"[ezpeek viewer] Command was: {cmd}")

        # Give ffplay a moment to start or fail (GUI apps + window manager can take time)
        time.sleep(1.5)

        if self.state.proc.poll() is not None:
            code = self.state.proc.returncode
            print(f"[ezpeek viewer] !!! ffplay exited immediately with code {code}")
            if log_file:
                try:
                    log_file.flush()
                    with open(log_path, "r") as f:
                        out = f.read()
                    print(f"[ezpeek viewer] ffplay log content:\n{out[:3000]}")
                except Exception:
                    pass
                try:
                    log_file.close()
                except Exception:
                    pass
            else:
                print("[ezpeek viewer] (check terminal or system logs)")
        else:
            print(f"[ezpeek viewer] ffplay appears to be running (no early exit). Look for window titled 'EzPeek Video - {host_ip}:{port}'")
            # Keep log file open while proc lives (store handle on state for later close)
            if not hasattr(self.state, "log_file"):
                self.state.log_file = None
            self.state.log_file = log_file  # type: ignore[attr-defined]

    def stop(self) -> None:
        p = self.state.proc
        if not p:
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
        # Close the debug log file if we opened one
        if hasattr(self.state, "log_file") and getattr(self.state, "log_file", None):
            try:
                self.state.log_file.close()
            except Exception:
                pass
            self.state.log_file = None
        self.state.proc = None
