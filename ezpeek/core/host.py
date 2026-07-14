from __future__ import annotations

import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ezpeek.utils import (
    BITRATE_MAX_KBPS,
    BITRATE_MIN_KBPS,
    BITRATE_TARGET_KBPS,
    effective_stream_fps,
    get_display_refresh_hz,
    get_local_ip,
    get_log_dir,
)
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
    host_hz: float = 60.0
    peer_hz: Optional[float] = None
    stream_fps: int = 60
    bitrate_min_kbps: int = BITRATE_MIN_KBPS
    bitrate_max_kbps: int = BITRATE_MAX_KBPS
    bitrate_target_kbps: int = BITRATE_TARGET_KBPS


class HostService:
    """Hosts a LAN remote-desktop session (video SRT listener + control TCP)."""

    DEFAULT_PORT = 2734
    DEFAULT_CONTROL_PORT = 2735
    BIND_HOST = "0.0.0.0"

    def __init__(
        self,
        fps: int | None = None,
        bitrate_kbps: int = BITRATE_TARGET_KBPS,
        bitrate_min_kbps: int = BITRATE_MIN_KBPS,
        bitrate_max_kbps: int = BITRATE_MAX_KBPS,
        codec: str = "auto",
        port: int | None = None,
        enable_control: bool = True,
        use_nat: bool = False,
        test_pattern: bool = False,
        host_hz: float | None = None,
    ):
        self.host_hz = float(host_hz) if host_hz else get_display_refresh_hz()
        # Initial stream FPS = host panel rate until a client reports a lower rate.
        self.fps = int(fps) if fps else effective_stream_fps(self.host_hz)
        self.bitrate_kbps = bitrate_kbps
        self.bitrate_min_kbps = bitrate_min_kbps
        self.bitrate_max_kbps = bitrate_max_kbps
        self.codec = codec
        self.port = port or self.DEFAULT_PORT
        self.enable_control = enable_control
        self.use_nat = use_nat
        self.test_pattern = test_pattern
        self.peer_hz: Optional[float] = None

        self._control_server: ControlServer | None = None
        self._log_file = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._screencast = None
        self._pipewire_node_id: Optional[int] = None
        self._gst_log = ""
        self._restart_lock = threading.Lock()
        self.state = HostState(
            host_ip=get_local_ip(),
            port=self.port,
            host_hz=self.host_hz,
            stream_fps=self.fps,
            bitrate_min_kbps=self.bitrate_min_kbps,
            bitrate_max_kbps=self.bitrate_max_kbps,
            bitrate_target_kbps=self.bitrate_kbps,
        )
        print(
            f"[ezpeek host] Local display refresh ≈ {self.host_hz:.2f} Hz → "
            f"initial stream {self.fps} fps, VBR {self.bitrate_min_kbps}-{self.bitrate_max_kbps} kbps"
        )

    def _start_screencast_if_needed(self) -> Optional[int]:
        if self.test_pattern:
            return None
        if platform.system().lower() != "linux":
            return None
        if not _is_wayland():
            return None
        if _check_ffmpeg_pipewire_support():
            return None
        if not _has_gstreamer_pipewire():
            return None
        if self._screencast is not None and self._pipewire_node_id is not None:
            return self._pipewire_node_id

        from .wayland_portal import ScreenCastSession, WaylandPortalError

        try:
            self._screencast = ScreenCastSession()
            node_id = self._screencast.start(app_id="ezpeek")
            self._pipewire_node_id = node_id
            return node_id
        except WaylandPortalError as e:
            self._screencast = None
            self._pipewire_node_id = None
            raise RuntimeError(
                f"Wayland screen share failed: {e}\n"
                "Accept the portal prompt, or install a portal backend "
                "(xdg-desktop-portal-gnome / kde / wlr)."
            ) from e
        except Exception as e:
            self._screencast = None
            self._pipewire_node_id = None
            raise RuntimeError(f"Wayland screen share failed: {e}") from e

    def _stop_screencast(self) -> None:
        if self._screencast is not None:
            try:
                self._screencast.close()
            except Exception:
                pass
            self._screencast = None
            self._pipewire_node_id = None

    def _on_control_event(self, msg: str) -> None:
        """Handle control-plane messages that affect streaming (e.g. client refresh rate)."""
        parts = msg.split()
        if not parts:
            return
        cmd = parts[0].upper()
        if cmd == "CLIENT_CAPS":
            # CLIENT_CAPS refresh=144
            refresh = None
            for tok in parts[1:]:
                if tok.lower().startswith("refresh="):
                    try:
                        refresh = float(tok.split("=", 1)[1])
                    except ValueError:
                        pass
            if refresh and refresh > 0:
                self.set_peer_refresh(refresh)

    def set_peer_refresh(self, peer_hz: float) -> None:
        """Viewer reported its display Hz — recompute min FPS and restart sender if needed."""
        peer_hz = float(peer_hz)
        new_fps = effective_stream_fps(self.host_hz, peer_hz)
        old_fps = self.fps
        self.peer_hz = peer_hz
        self.state.peer_hz = peer_hz
        self.state.stream_fps = new_fps
        print(
            f"[ezpeek host] Peer display {peer_hz:.2f} Hz · host {self.host_hz:.2f} Hz "
            f"→ stream {new_fps} fps (was {old_fps})"
        )
        if abs(new_fps - old_fps) >= 1 and self.state.proc and self.state.proc.poll() is None:
            self.fps = new_fps
            threading.Thread(target=self._restart_sender, daemon=True, name="ezpeek-fps-restart").start()
        else:
            self.fps = new_fps

    def _build_encode_spec(self) -> EncodeSpec:
        return EncodeSpec(
            codec=self.codec,  # type: ignore[arg-type]
            fps=self.fps,
            bitrate_kbps=self.bitrate_kbps,
            bitrate_min_kbps=self.bitrate_min_kbps,
            bitrate_max_kbps=self.bitrate_max_kbps,
            gop=max(self.fps, 15),
        )

    def _launch_sender(self, pipewire_node_id: Optional[int]) -> None:
        capture = CaptureSpec(fps=self.fps)
        encode = self._build_encode_spec()
        tx = TransportSpec(transport="srt", host=self.BIND_HOST, port=self.port)
        cmd = build_sender_cmd(
            capture,
            encode,
            tx,
            test_pattern=self.test_pattern,
            pipewire_node_id=pipewire_node_id,
            gst_log_path=self._gst_log,
        )
        print("[ezpeek host] Launching sender:", cmd)

        log_path = Path(self.state.log_path) if self.state.log_path else get_log_dir() / f"host_sender_{self.port}.log"
        self.state.log_path = str(log_path)
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
        try:
            self._log_file = open(log_path, "a" if log_path.exists() else "w", buffering=1)
            self._log_file.write(f"\n--- sender start fps={self.fps} ---\n")
        except Exception:
            self._log_file = None

        popen_kwargs: dict = {
            "stdout": self._log_file if self._log_file else subprocess.DEVNULL,
            "stderr": subprocess.STDOUT,
            "text": True,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        self.state.proc = subprocess.Popen(cmd, **popen_kwargs)
        self.state.stream_fps = self.fps
        print(f"[ezpeek host] Sender pid={self.state.proc.pid} fps={self.fps}")

    def _stop_sender_only(self) -> None:
        p = self.state.proc
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.0)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self.state.proc = None

    def _restart_sender(self) -> None:
        with self._restart_lock:
            if not self.state.control_port and not (self.state.proc):
                return
            print(f"[ezpeek host] Restarting sender at {self.fps} fps…")
            self._stop_sender_only()
            time.sleep(0.3)
            try:
                self._launch_sender(self._pipewire_node_id)
                time.sleep(0.8)
                if self.state.proc and self.state.proc.poll() is not None:
                    self.state.last_error = self._read_log_tail(self.state.log_path)
                    print("[ezpeek host] Restart failed:", self.state.last_error[:300])
            except Exception as e:
                print(f"[ezpeek host] Restart error: {e}")

    def start(self) -> HostState:
        if self.state.proc and self.state.proc.poll() is None:
            return self.state

        self.state.last_error = ""
        self.state.port = self.port
        self.state.host_ip = get_local_ip()
        self.state.host_hz = self.host_hz
        self.state.stream_fps = self.fps

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
        self.state.log_path = str(log_dir / f"host_sender_{self.port}.log")
        self._gst_log = str(log_dir / f"host_gst_{self.port}.log")

        pipewire_node_id = None
        try:
            pipewire_node_id = self._start_screencast_if_needed()
        except RuntimeError:
            print("[ezpeek host] ScreenCast portal unavailable; trying alternate capture...")
            pipewire_node_id = None

        self._launch_sender(pipewire_node_id)

        if self.enable_control and not self._control_server:
            try:
                self._control_server = ControlServer(
                    host=self.BIND_HOST,
                    port=self.DEFAULT_CONTROL_PORT,
                    on_event=self._on_control_event,
                )
                ctrl_port = self._control_server.start()
                self.state.control_port = ctrl_port
                print(f"[ezpeek host] Control server on {self.BIND_HOST}:{ctrl_port}")
            except Exception as e:
                print(f"[ezpeek] Warning: control server: {e}")
                self.state.control_port = None

        time.sleep(1.2)
        if self.state.proc and self.state.proc.poll() is not None:
            out = self._read_log_tail(self.state.log_path)
            gst_tail = self._read_log_tail(self._gst_log)
            parts = [out]
            if gst_tail:
                parts.append(f"\n--- gstreamer log ---\n{gst_tail}")
            self.state.last_error = "\n".join(p for p in parts if p).strip() or (
                "ffmpeg exited immediately"
            )
            print(f"[ezpeek host] Sender died early!\n{self.state.last_error[:800]}")
            self._stop_control()
            self._stop_screencast()
            self.state.proc = None
            raise RuntimeError(self.state.last_error)

        if not getattr(self, "_monitor_thread", None) or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(target=self._monitor_proc, daemon=True)
            self._monitor_thread.start()

        return self.state

    @staticmethod
    def _read_log_tail(log_path: Path | str, n: int = 1500) -> str:
        try:
            return Path(log_path).read_text(errors="ignore").strip()[-n:]
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
        while True:
            p = self.state.proc
            if p is None:
                time.sleep(0.5)
                # keep thread alive while hosting control exists? stop when fully stopped
                if self._control_server is None and self._screencast is None:
                    break
                continue
            if p.poll() is not None:
                # Ignore exit during intentional restart (brief None/new proc)
                time.sleep(0.2)
                if self.state.proc is p:
                    if not self.state.last_error:
                        self.state.last_error = (
                            self._read_log_tail(self.state.log_path)
                            or "Sender process exited unexpectedly"
                        )
                    print(f"[ezpeek host] Sender exited: {self.state.last_error[:300]}")
                    self.state.proc = None
                    self._stop_control()
                    self._stop_screencast()
                    break
            time.sleep(1)

    def stop(self) -> None:
        self._stop_sender_only()
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
        self._stop_control()
        self._stop_screencast()
