from __future__ import annotations

import platform
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ezpeek.utils import get_local_ip, get_log_dir
from .capture import CaptureSpec, _check_ffmpeg_pipewire_support, _has_gstreamer_pipewire, _is_wayland
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
    log_path: str = ""


class HostService:
    """Hosts a LAN remote-desktop session (video SRT listener + control TCP)."""

    DEFAULT_PORT = 2734
    DEFAULT_CONTROL_PORT = 2735
    # Always bind listeners on all interfaces; advertise host_ip separately.
    BIND_HOST = "0.0.0.0"

    def __init__(
        self,
        fps: int = 30,
        bitrate_kbps: int = 6000,
        codec: str = "h264",
        port: int | None = None,
        enable_control: bool = True,
        use_nat: bool = False,
        test_pattern: bool = False,
    ):
        self.fps = fps
        self.bitrate_kbps = bitrate_kbps
        self.codec = codec
        self.port = port or self.DEFAULT_PORT
        self.enable_control = enable_control
        self.use_nat = use_nat
        self.test_pattern = test_pattern
        self._control_server: ControlServer | None = None
        self._log_file = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._screencast = None  # ScreenCastSession (Wayland)
        self.state = HostState(host_ip=get_local_ip(), port=self.port)

    def _start_screencast_if_needed(self) -> Optional[int]:
        """
        On Wayland without ffmpeg pipewire demuxer, open an xdg-desktop-portal
        ScreenCast session and keep it alive for the host lifetime.
        Returns pipewire node id, or None.
        """
        if self.test_pattern:
            return None
        if platform.system().lower() != "linux":
            return None
        if not _is_wayland():
            return None
        if _check_ffmpeg_pipewire_support():
            # Native ffmpeg path will request portal itself via build_capture_input_args
            return None
        if not _has_gstreamer_pipewire():
            return None

        from .wayland_portal import ScreenCastSession, WaylandPortalError

        try:
            self._screencast = ScreenCastSession()
            node_id = self._screencast.start(app_id="ezpeek")
            return node_id
        except WaylandPortalError as e:
            self._screencast = None
            raise RuntimeError(
                f"Wayland screen share failed: {e}\n"
                "Accept the portal prompt, or install a portal backend "
                "(xdg-desktop-portal-gnome / kde / wlr)."
            ) from e
        except Exception as e:
            self._screencast = None
            raise RuntimeError(f"Wayland screen share failed: {e}") from e

    def _stop_screencast(self) -> None:
        if self._screencast is not None:
            try:
                self._screencast.close()
            except Exception:
                pass
            self._screencast = None

    def start(self) -> HostState:
        if self.state.proc and self.state.proc.poll() is None:
            return self.state

        self.state.last_error = ""
        self.state.port = self.port

        # Refresh LAN IP for advertisement (bind stays 0.0.0.0).
        self.state.host_ip = get_local_ip()

        # Optional NAT: discover public address for advertisement only.
        if self.use_nat:
            try:
                ip, prt, ctype = get_best_connection_address(local_port=self.port)
                if ip and ip != "0.0.0.0":
                    self.state.host_ip = ip
                    if prt:
                        self.port = prt
                        self.state.port = prt
                    print(f"[ezpeek] NAT: advertising {ctype} address {ip}:{self.port}")
            except Exception as e:
                print(f"[ezpeek] NAT discovery failed (using local): {e}")

        log_dir = get_log_dir()
        log_path = log_dir / f"host_sender_{self.port}.log"
        gst_log = str(log_dir / f"host_gst_{self.port}.log")
        self.state.log_path = str(log_path)

        # Open ScreenCast portal *before* building the pipeline so the PipeWire
        # node stays valid for the whole host session.
        pipewire_node_id = None
        try:
            pipewire_node_id = self._start_screencast_if_needed()
        except RuntimeError:
            # If portal fails, still try other capture methods (X11/kmsgrab).
            print("[ezpeek host] ScreenCast portal unavailable; trying alternate capture...")
            pipewire_node_id = None

        capture = CaptureSpec(fps=self.fps)
        encode = EncodeSpec(
            codec=self.codec,  # type: ignore[arg-type]
            fps=self.fps,
            bitrate_kbps=self.bitrate_kbps,
            gop=max(self.fps, 15),
        )
        tx = TransportSpec(transport="srt", host=self.BIND_HOST, port=self.port)

        cmd = build_sender_cmd(
            capture,
            encode,
            tx,
            test_pattern=self.test_pattern,
            pipewire_node_id=pipewire_node_id,
            gst_log_path=gst_log,
        )
        print("[ezpeek host] build_sender_cmd returned. Launching sender ffmpeg...")
        print(f"[ezpeek host] advertise={self.state.host_ip}:{self.port} bind={self.BIND_HOST}")
        print(f"[ezpeek host] sender cmd: {cmd}")

        try:
            if self._log_file:
                try:
                    self._log_file.close()
                except Exception:
                    pass
            self._log_file = open(log_path, "w", buffering=1)
        except Exception as e:
            print(f"[ezpeek host] Could not open log {log_path}: {e}")
            self._log_file = None

        popen_kwargs: dict = {
            "stdout": self._log_file if self._log_file else subprocess.DEVNULL,
            "stderr": subprocess.STDOUT,
            "text": True,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        self.state.proc = subprocess.Popen(cmd, **popen_kwargs)
        print(f"[ezpeek host] Sender ffmpeg Popen pid={self.state.proc.pid} log={log_path}")

        # Control server: bind all interfaces, fixed port for Phase 1.
        if self.enable_control:
            try:
                self._control_server = ControlServer(
                    host=self.BIND_HOST,
                    port=self.DEFAULT_CONTROL_PORT,
                )
                ctrl_port = self._control_server.start()
                self.state.control_port = ctrl_port
                print(f"[ezpeek host] Control server listening on {self.BIND_HOST}:{ctrl_port}")
            except OSError as e:
                print(f"[ezpeek] Warning: could not start control server: {e}")
                self.state.control_port = None
            except Exception as e:
                print(f"[ezpeek] Warning: could not start control server: {e}")
                self.state.control_port = None

        # Give capture + ffmpeg a moment (portal/gst can be slow to produce first frames).
        time.sleep(1.2)
        if self.state.proc.poll() is not None:
            out = self._read_log_tail(log_path)
            gst_tail = self._read_log_tail(gst_log)
            parts = [out]
            if gst_tail:
                parts.append(f"\n--- gstreamer log ---\n{gst_tail}")
            self.state.last_error = "\n".join(p for p in parts if p).strip() or (
                "ffmpeg exited immediately (no output captured)"
            )
            print(f"[ezpeek host] Sender died early!\n{self.state.last_error[:800]}")
            self._stop_control()
            self._stop_screencast()
            self.state.proc = None
            raise RuntimeError(self.state.last_error)

        self._monitor_thread = threading.Thread(target=self._monitor_proc, daemon=True)
        self._monitor_thread.start()

        return self.state

    @staticmethod
    def _read_log_tail(log_path: Path | str, n: int = 1500) -> str:
        try:
            text = Path(log_path).read_text(errors="ignore")
            return text.strip()[-n:]
        except Exception:
            return ""

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
                    tail = self._read_log_tail(self.state.log_path) if self.state.log_path else ""
                    self.state.last_error = tail or "Sender process exited unexpectedly"
                print(f"[ezpeek host] Sender exited: {self.state.last_error[:300]}")
                self.state.proc = None
                self._stop_control()
                self._stop_screencast()
                break
            time.sleep(1)

    def stop(self) -> None:
        p = self.state.proc
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.5)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                    p.wait(timeout=2)
                except Exception:
                    pass
            except Exception:
                pass
        self.state.proc = None

        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        self._stop_control()
        self._stop_screencast()
