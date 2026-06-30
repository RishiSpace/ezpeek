from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass
from typing import Literal, Optional

from .capture import CaptureSpec, build_capture_input_args
from .encoder import EncodeSpec, build_video_encode_args


Transport = Literal["srt"]


@dataclass(frozen=True)
class TransportSpec:
    transport: Transport = "srt"
    host: str = "0.0.0.0"
    port: int = 2734


def srt_url(host: str, port: int, mode: Literal["caller", "listener"], *, latency_ms: int = 20, extra: Optional[str] = None) -> str:
    """
    Build SRT URL with low-latency tuning suitable for remoting.
    latency_ms: end-to-end target latency.
    extra: additional query params e.g. "rcvlatency=10&snddropdelay=0"
    """
    base = f"srt://{host}:{port}?mode={mode}&latency={latency_ms}"
    if extra:
        base += "&" + extra.lstrip("&")
    return base


def ensure_ffmpeg_tools() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH")
    if shutil.which("ffplay") is None:
        # receiver uses ffplay for now
        raise RuntimeError("ffplay not found on PATH (install ffmpeg tools)")


def build_sender_cmd(capture: CaptureSpec, encode: EncodeSpec, tx: TransportSpec) -> list[str]:
    ensure_ffmpeg_tools()
    if tx.transport != "srt":
        raise RuntimeError(f"Unsupported transport: {tx.transport}")

    # Pure Wayland support (no X11). Use external capture tools + pipe to ffmpeg when
    # FFmpeg lacks native -f pipewire. Keeps support for Ubuntu 26.04+ etc. Wayland-only.
    if platform.system().lower() == "linux":
        try:
            from .capture import (_is_wayland, _has_wl_screenrec,
                                  _check_ffmpeg_pipewire_support, _has_gstreamer_pipewire)
            if _is_wayland() and not _check_ffmpeg_pipewire_support():
                fr = str(capture.fps)
                encode_part = " ".join(build_video_encode_args(encode))
                srt_url_str = srt_url(tx.host, tx.port, mode="listener", latency_ms=20, extra="snddropdelay=0")
                if _has_wl_screenrec():
                    wl_part = f"wl-screenrec --fps {fr} -c h264 --ffmpeg-muxer mpegts -o -"
                    pipeline = f"{wl_part} | ffmpeg -hide_banner -loglevel warning -fflags nobuffer -flags low_delay -i - {encode_part} -f mpegts '{srt_url_str}'"
                    return ["sh", "-c", pipeline]
                elif _has_gstreamer_pipewire():
                    # Trigger portal grant (using our ScreenCast flow) so gst pipewiresrc can access the screen.
                    # Then use gstreamer for capture (y4m carries resolution) piped to ffmpeg for (hw) encode + SRT.
                    try:
                        from .capture import request_pipewire_node_id
                        request_pipewire_node_id(app_id="ezpeek")  # may show permission dialog
                    except Exception:
                        pass  # proceed anyway; gst may still work or prompt
                    gst = f"gst-launch-1.0 --quiet pipewiresrc do-timestamp=true ! videoconvert ! video/x-raw,framerate={fr}/1 ! y4menc ! fdsink fd=1"
                    pipeline = f"{gst} | ffmpeg -hide_banner -loglevel warning -fflags nobuffer -flags low_delay -f yuv4mpegpipe -i - {encode_part} -f mpegts '{srt_url_str}'"
                    return ["sh", "-c", pipeline]
        except Exception:
            pass  # fall through to build_capture (which may use kmsgrab or raise)

    input_args = build_capture_input_args(capture)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",   # less noisy for production feel
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-probesize", "32",
        "-analyzeduration", "0",
        "-thread_queue_size", "512",
    ]
    cmd += input_args
    cmd += build_video_encode_args(encode)
    # Strong low latency SRT tuning
    cmd += ["-f", "mpegts", srt_url(tx.host, tx.port, mode="listener", latency_ms=20, extra="snddropdelay=0")]
    return cmd


def _get_hwaccel_arg() -> list[str]:
    """Return best-effort hwaccel flags for ffplay decode on current platform."""
    sys_name = platform.system().lower()
    if sys_name == "windows":
        # d3d11va or cuda if available; auto is safe
        return ["-hwaccel", "d3d11va", "-hwaccel_output_format", "d3d11"]
    elif sys_name == "linux":
        # vaapi is excellent on Linux (intel/amd), cuda for nvidia
        return ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]
    return ["-hwaccel", "auto"]


def build_receiver_cmd(host: str, port: int, transport: Transport = "srt") -> list[str]:
    ensure_ffmpeg_tools()
    if transport != "srt":
        raise RuntimeError(f"Unsupported transport: {transport}")

    hw = _get_hwaccel_arg()
    cmd = [
        "ffplay",
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-framedrop",
        "-sync", "video",
        "-infbuf",
        "-fast",
    ]
    cmd += hw
    cmd += [srt_url(host, port, mode="caller", latency_ms=20, extra="rcvlatency=10")]
    return cmd
