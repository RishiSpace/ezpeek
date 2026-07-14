from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Literal, Optional


VideoCodec = Literal["h264", "hevc"]


@dataclass(frozen=True)
class EncodeSpec:
    codec: VideoCodec = "h264"
    bitrate_kbps: int = 6000
    fps: int = 30
    gop: int = 30
    width: Optional[int] = None
    height: Optional[int] = None


def _ffmpeg() -> str:
    from .transport import _find_ffmpeg_executables

    path, _ = _find_ffmpeg_executables()
    return path


def _ffmpeg_encoders_text() -> str:
    try:
        p = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        )
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def pick_hw_encoder(codec: VideoCodec) -> Optional[str]:
    """
    Pick a hardware encoder if available.

    Phase 1 default is *software* encode (libx264). Many HW paths (especially
    VAAPI/QSV) need device setup that was previously missing and caused hosts
    to die immediately. Set EZPEEK_HW_ENCODE=1 to enable auto HW pick.
    """
    if os.environ.get("EZPEEK_HW_ENCODE", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None

    txt = _ffmpeg_encoders_text()
    sys_name = platform.system().lower()

    if codec == "h264":
        base = ["h264_nvenc", "h264_amf", "h264_qsv", "h264_vaapi"]
    else:
        base = ["hevc_nvenc", "hevc_amf", "hevc_qsv", "hevc_vaapi"]

    if sys_name == "windows":
        order = ["nvenc", "amf", "qsv", "vaapi"]
    elif sys_name == "linux":
        # Prefer NVENC over VAAPI — VAAPI needs -vaapi_device + hwupload.
        order = ["nvenc", "qsv", "amf", "vaapi"]
    else:
        order = ["nvenc", "amf", "qsv", "vaapi"]

    cands: list[str] = []
    for pref in order:
        for c in base:
            if pref in c and c not in cands:
                cands.append(c)
    for c in base:
        if c not in cands:
            cands.append(c)

    for enc in cands:
        if enc in txt and not enc.endswith("_vaapi"):
            # Skip bare VAAPI until we wire hw device context.
            return enc
    return None


def build_video_encode_args(spec: EncodeSpec) -> list[str]:
    """
    Build ffmpeg args for low latency streaming.
    Returns args for the OUTPUT side (after input).
    """
    bitrate = f"{spec.bitrate_kbps}k"
    gop = str(spec.gop)

    vf = []
    if spec.width and spec.height:
        vf.append(f"scale={spec.width}:{spec.height}")

    args: list[str] = []
    if vf:
        args += ["-vf", ",".join(vf)]

    hw = pick_hw_encoder(spec.codec)
    if hw is None:
        vcodec = "libx264" if spec.codec == "h264" else "libx265"
        args += [
            "-c:v", vcodec,
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-g", gop,
            "-keyint_min", gop,
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", "2M",
            "-pix_fmt", "yuv420p",
        ]
        return args

    args += ["-c:v", hw, "-g", gop, "-b:v", bitrate]

    if hw.endswith("_nvenc"):
        args += ["-preset", "p1", "-tune", "ll", "-rc", "cbr", "-pix_fmt", "yuv420p"]
    elif hw.endswith("_qsv"):
        args += ["-preset", "veryfast", "-look_ahead", "0"]
    elif hw.endswith("_amf"):
        args += ["-usage", "lowlatency", "-rc", "cbr"]

    return args
