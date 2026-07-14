from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Literal, Optional


VideoCodec = Literal["auto", "av1", "h264", "hevc"]


@dataclass(frozen=True)
class EncodeSpec:
    # "auto" = prefer working HW AV1, else working HW/soft H.264
    codec: VideoCodec = "auto"
    # Constant bitrate (kbps). CBR for stable remoting quality.
    bitrate_kbps: int = 25000
    bitrate_min_kbps: int = 25000  # kept for API compat; CBR uses bitrate_kbps
    bitrate_max_kbps: int = 25000
    fps: int = 60
    gop: int = 60
    width: Optional[int] = None
    height: Optional[int] = None


def _ffmpeg() -> str:
    from .transport import _find_ffmpeg_executables

    path, _ = _find_ffmpeg_executables()
    return path


_ENCODERS_CACHE: Optional[str] = None
_PROBE_CACHE: dict[str, bool] = {}


def _ffmpeg_encoders_text() -> str:
    global _ENCODERS_CACHE
    if _ENCODERS_CACHE is not None:
        return _ENCODERS_CACHE
    try:
        p = subprocess.run(
            [_ffmpeg(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
        )
        _ENCODERS_CACHE = (p.stdout or "") + (p.stderr or "")
    except Exception:
        _ENCODERS_CACHE = ""
    return _ENCODERS_CACHE


def _encoder_listed(name: str) -> bool:
    txt = _ffmpeg_encoders_text()
    # ffmpeg -encoders lines look like: " V....D av1_nvenc  NVIDIA NVENC av1 encoder"
    for line in txt.splitlines():
        parts = line.split()
        if name in parts:
            return True
    return name in txt


def probe_encoder(name: str) -> bool:
    """
    Actually try opening the encoder on a tiny synthetic frame.
    Listing an encoder in -encoders does NOT mean the GPU supports it
    (e.g. av1_nvenc on older cards).
    """
    if name in _PROBE_CACHE:
        return _PROBE_CACHE[name]
    # Software codecs we trust without probing (fast + always present if listed)
    if name in ("libx264", "libx265"):
        ok = _encoder_listed(name)
        _PROBE_CACHE[name] = ok
        return ok

    try:
        ffmpeg = _ffmpeg()
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", "testsrc=size=256x144:rate=10,format=yuv420p",
            "-t", "0.4",
            "-c:v", name,
            "-f", "null",
            "-",
        ]
        # Extra low-latency-ish flags for nvenc/amf so probe mirrors real use
        if name.endswith("_nvenc"):
            cmd[cmd.index(name) + 1 : cmd.index(name) + 1] = []  # no-op placeholder
            # insert after -c:v name
            i = cmd.index(name)
            cmd = cmd[: i + 1] + ["-preset", "p1", "-b:v", "500k"] + cmd[i + 1 :]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=8, check=False)
        ok = p.returncode == 0
        if not ok:
            err = ((p.stderr or "") + (p.stdout or ""))[-300:]
            print(f"[ezpeek encode] probe failed for {name}: {err.strip() or p.returncode}")
    except Exception as e:
        print(f"[ezpeek encode] probe exception for {name}: {e}")
        ok = False

    _PROBE_CACHE[name] = ok
    if ok:
        print(f"[ezpeek encode] probe OK: {name}")
    return ok


def _platform_encoder_order(family: str) -> list[str]:
    sys_name = platform.system().lower()
    if family == "av1":
        if sys_name == "windows":
            return ["av1_nvenc", "av1_amf", "av1_qsv", "av1_mf"]
        return ["av1_nvenc", "av1_amf", "av1_qsv"]
    if family == "hevc":
        if sys_name == "windows":
            return ["hevc_nvenc", "hevc_amf", "hevc_qsv"]
        return ["hevc_nvenc", "hevc_qsv", "hevc_amf"]
    # h264
    if sys_name == "windows":
        return ["h264_nvenc", "h264_amf", "h264_qsv"]
    return ["h264_nvenc", "h264_qsv", "h264_amf"]


def pick_encoder(codec: VideoCodec = "auto") -> tuple[str, str]:
    """
    Choose the best *working* encoder.

    Returns (encoder_name, family) where family is 'av1' | 'h264' | 'hevc'.

    Policy:
      - auto / av1: first working HW AV1 → else H.264 path
      - h264: first working HW H.264 → libx264
    Software AV1 is off unless EZPEEK_ALLOW_SW_AV1=1 (too slow for remoting).
    """
    allow_sw_av1 = os.environ.get("EZPEEK_ALLOW_SW_AV1", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    def first_working(names: list[str]) -> Optional[str]:
        for n in names:
            if _encoder_listed(n) and probe_encoder(n):
                return n
        return None

    if codec in ("auto", "av1"):
        av1 = first_working(_platform_encoder_order("av1"))
        if av1:
            print(f"[ezpeek encode] Using AV1 hardware encoder: {av1}")
            return av1, "av1"
        if codec == "av1" and allow_sw_av1:
            for n in ("libsvtav1", "librav1e", "libaom-av1"):
                if _encoder_listed(n) and probe_encoder(n):
                    print(f"[ezpeek encode] Using software AV1 encoder: {n}")
                    return n, "av1"
        if codec == "av1":
            print("[ezpeek encode] No working AV1 encoder; falling back to H.264")

    if codec in ("auto", "av1", "h264"):
        h264 = first_working(_platform_encoder_order("h264"))
        if h264:
            print(f"[ezpeek encode] Using H.264 hardware encoder: {h264}")
            return h264, "h264"
        print("[ezpeek encode] Using software H.264 (libx264)")
        return "libx264", "h264"

    if codec == "hevc":
        hevc = first_working(_platform_encoder_order("hevc"))
        if hevc:
            return hevc, "hevc"
        return "libx265", "hevc"

    return "libx264", "h264"


def pick_hw_encoder(codec: VideoCodec) -> Optional[str]:
    """Back-compat helper. Returns HW encoder name or None for pure software."""
    name, _family = pick_encoder(codec if codec != "auto" else "h264")
    if name in ("libx264", "libx265", "libsvtav1", "libaom-av1", "librav1e"):
        return None
    return name


def mux_format_for_family(family: str) -> str:
    """
    AV1 in MPEG-TS is poorly supported (private stream). Use Matroska for AV1.
    H.264 stays on MPEG-TS (battle-tested for our SRT path).
    """
    if family == "av1":
        return "matroska"
    return "mpegts"


def build_video_encode_args(spec: EncodeSpec) -> list[str]:
    """Build ffmpeg output-side encode args for low-latency streaming (constant CBR)."""
    rate = max(1000, int(spec.bitrate_kbps))
    bitrate = f"{rate}k"
    bufsize = f"{rate}k"
    gop = str(max(spec.gop, 1))

    args: list[str] = []
    if spec.width and spec.height:
        args += ["-vf", f"scale={spec.width}:{spec.height}"]

    enc, family = pick_encoder(spec.codec)

    if enc == "libx264":
        args += [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-g", gop,
            "-keyint_min", gop,
            "-b:v", bitrate,
            "-minrate", bitrate,
            "-maxrate", bitrate,
            "-bufsize", bufsize,
            "-pix_fmt", "yuv420p",
        ]
        return args

    if enc == "libx265":
        args += [
            "-c:v", "libx265",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-g", gop,
            "-b:v", bitrate,
            "-minrate", bitrate,
            "-maxrate", bitrate,
            "-bufsize", bufsize,
            "-pix_fmt", "yuv420p",
        ]
        return args

    if enc in ("libsvtav1", "libaom-av1", "librav1e"):
        args += [
            "-c:v", enc,
            "-g", gop,
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-pix_fmt", "yuv420p",
        ]
        if enc == "libsvtav1":
            args += ["-preset", "10", "-svtav1-params", "lp=1:fast-decode=1"]
        return args

    # Hardware encoders — constant bitrate
    args += [
        "-c:v", enc,
        "-g", gop,
        "-b:v", bitrate,
        "-maxrate", bitrate,
        "-bufsize", bufsize,
        "-pix_fmt", "yuv420p",
    ]

    if enc.endswith("_nvenc"):
        if family == "av1":
            args += ["-preset", "p1", "-tune", "ull", "-rc", "cbr"]
        else:
            args += ["-preset", "p1", "-tune", "ll", "-rc", "cbr"]
    elif enc.endswith("_qsv"):
        args += ["-preset", "veryfast", "-look_ahead", "0"]
    elif enc.endswith("_amf"):
        args += ["-usage", "lowlatency", "-rc", "cbr"]
    elif enc.endswith("_mf"):
        pass

    return args


def describe_encode_choice(spec: EncodeSpec | None = None) -> str:
    spec = spec or EncodeSpec()
    enc, family = pick_encoder(spec.codec)
    return f"{family.upper()} via {enc} · CBR {spec.bitrate_kbps} kbps · {spec.fps} fps"
