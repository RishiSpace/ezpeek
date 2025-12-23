from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Literal

from .capture import CaptureSpec, build_capture_input_args
from .encoder import EncodeSpec, build_video_encode_args


Transport = Literal["srt"]


@dataclass(frozen=True)
class TransportSpec:
    transport: Transport = "srt"
    host: str = "0.0.0.0"
    port: int = 17000


def srt_url(host: str, port: int, mode: Literal["caller", "listener"]) -> str:
    # low latency tuning; can be exposed in GUI later
    return f"srt://{host}:{port}?mode={mode}&latency=20"


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

    input_args = build_capture_input_args(capture)
    # Debug/diagnostic
    print("[ezpeek] capture input args:", " ".join(input_args))

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-probesize", "32",
        "-analyzeduration", "0",
    ]
    cmd += input_args
    cmd += build_video_encode_args(encode)
    cmd += ["-f", "mpegts", srt_url(tx.host, tx.port, mode="listener")]
    return cmd


def build_receiver_cmd(host: str, port: int, transport: Transport = "srt") -> list[str]:
    ensure_ffmpeg_tools()
    if transport != "srt":
        raise RuntimeError(f"Unsupported transport: {transport}")
    return [
        "ffplay",
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-framedrop",
        "-sync", "video",
        srt_url(host, port, mode="caller"),
    ]
