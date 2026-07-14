from __future__ import annotations

"""
Viewer-side stream receive helpers.

Video is displayed inside the Qt ViewerWindow (integrated surface).
This module builds the ffmpeg decode command and optional legacy ffplay path.
"""

import platform
import subprocess
from dataclasses import dataclass
from typing import Optional

from .transport import (
    DEFAULT_SRT_LATENCY_MS,
    ensure_ffmpeg_tools,
    has_srt_support,
    srt_url,
    _find_ffmpeg_executables,
    _get_hwaccel_arg,
)


@dataclass
class ViewerState:
    proc: subprocess.Popen | None = None
    log_path: str = ""
    last_error: str = ""


def build_integrated_decode_cmd(
    host: str,
    port: int,
    *,
    latency_ms: int = DEFAULT_SRT_LATENCY_MS,
    max_width: int = 1920,
    use_hwaccel: bool = False,
) -> list[str]:
    """
    Decode SRT stream to an MJPEG pipe for the integrated Qt viewer.

    Default is *software* decode (libdav1d / native). HW accel is optional because
    d3d11va/cuda often hang or fail on mixed AV1/H.264 LAN streams and leave the
    UI stuck on "Connecting…".
    """
    ensure_ffmpeg_tools()
    ffmpeg_exe, _ = _find_ffmpeg_executables()
    if not has_srt_support():
        raise RuntimeError("FFmpeg has no SRT support")

    srt = srt_url(host, port, mode="caller", latency_ms=latency_ms, extra="rcvlatency=0")

    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "nobuffer+discardcorrupt",
        "-flags", "low_delay",
        "-probesize", "1000000",
        "-analyzeduration", "1000000",
        "-rw_timeout", "5000000",  # 5s I/O timeout (microseconds) where supported
    ]
    if use_hwaccel:
        cmd += _get_hwaccel_arg()
    cmd += [
        "-i", srt,
        "-vf", f"scale='min({max_width},iw)':-2",
        "-an",
        "-f", "mjpeg",
        "-q:v", "5",
        "pipe:1",
    ]
    print(f"[ezpeek viewer] integrated decode cmd (hw={use_hwaccel}): {cmd}")
    return cmd


def build_receiver_cmd(
    host: str,
    port: int,
    transport: str = "srt",
    *,
    latency_ms: int = DEFAULT_SRT_LATENCY_MS,
) -> list[str]:
    """Legacy external-ffplay receiver (kept for debugging / fallback)."""
    from .transport import build_receiver_cmd as _tx_build

    return _tx_build(host, port, transport=transport, latency_ms=latency_ms)  # type: ignore[arg-type]


class ViewerService:
    """
    Optional external-ffplay launcher (legacy).

    Preferred path is ViewerWindow integrated decode via build_integrated_decode_cmd.
    """

    def __init__(self):
        self.state = ViewerState()

    def start(self, host_ip: str, port: int) -> None:
        raise RuntimeError(
            "External ffplay viewer is disabled. Use the integrated ViewerWindow."
        )

    def stop(self) -> None:
        pass
