from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from .capture import CaptureSpec, build_capture_input_args
from .encoder import EncodeSpec, build_video_encode_args


Transport = Literal["srt"]

# LAN-friendly defaults. 20ms was too aggressive and caused flaky connects.
DEFAULT_SRT_LATENCY_MS = 120


@dataclass(frozen=True)
class TransportSpec:
    transport: Transport = "srt"
    # For the sender/listener this is the *bind* address (use 0.0.0.0).
    # For the receiver/caller this is the peer address to dial.
    host: str = "0.0.0.0"
    port: int = 2734
    latency_ms: int = DEFAULT_SRT_LATENCY_MS


def srt_url(
    host: str,
    port: int,
    mode: Literal["caller", "listener"],
    *,
    latency_ms: int = DEFAULT_SRT_LATENCY_MS,
    extra: Optional[str] = None,
) -> str:
    """
    Build SRT URL with live streaming tuning suitable for remoting.
    """
    params = [
        f"mode={mode}",
        f"latency={latency_ms}",
        "transtype=live",
    ]
    if extra:
        # Allow callers to pass additional params without duplicating mode/latency.
        for part in extra.lstrip("&").split("&"):
            if not part:
                continue
            key = part.split("=", 1)[0].lower()
            if key in ("mode", "latency", "transtype"):
                continue
            params.append(part)
    return f"srt://{host}:{port}?{'&'.join(params)}"


_FFMPEG_CACHE: Optional[str] = None
_FFPLAY_CACHE: Optional[str] = None


def _get_ffmpeg_dir() -> Path:
    """Returns a user-writable directory for a portable FFmpeg installation."""
    if platform.system().lower() == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    return base / "ezpeek" / "ffmpeg"


def clear_ffmpeg_cache() -> None:
    global _FFMPEG_CACHE, _FFPLAY_CACHE
    _FFMPEG_CACHE = None
    _FFPLAY_CACHE = None


def _find_ffmpeg_executables() -> tuple[str, str]:
    """Returns (ffmpeg_path, ffplay_path). Downloads a portable build if necessary."""
    global _FFMPEG_CACHE, _FFPLAY_CACHE

    if _FFMPEG_CACHE and _FFPLAY_CACHE:
        return _FFMPEG_CACHE, _FFPLAY_CACHE

    # 1. Check system PATH first (preferred)
    ffmpeg = shutil.which("ffmpeg")
    ffplay = shutil.which("ffplay")
    if ffmpeg and ffplay:
        print(f"[ezpeek ffmpeg] Found in PATH: ffmpeg={ffmpeg}, ffplay={ffplay}")
        _FFMPEG_CACHE = ffmpeg
        _FFPLAY_CACHE = ffplay
        return ffmpeg, ffplay

    # 2. Check our local portable installation
    ffmpeg_dir = _get_ffmpeg_dir()
    print(f"[ezpeek ffmpeg] No PATH pair, checking portable dir: {ffmpeg_dir}")
    if platform.system().lower() == "windows":
        candidates = list(ffmpeg_dir.rglob("ffmpeg.exe"))
        ffplay_candidates = list(ffmpeg_dir.rglob("ffplay.exe"))
    else:
        candidates = list(ffmpeg_dir.rglob("ffmpeg"))
        ffplay_candidates = list(ffmpeg_dir.rglob("ffplay"))

    if candidates and ffplay_candidates:
        _FFMPEG_CACHE = str(candidates[0])
        _FFPLAY_CACHE = str(ffplay_candidates[0])
        print(f"[ezpeek ffmpeg] Using portable: {_FFMPEG_CACHE}, {_FFPLAY_CACHE}")
        return _FFMPEG_CACHE, _FFPLAY_CACHE

    # 3. Not found anywhere -> download portable build
    print("[ezpeek] FFmpeg not found on PATH. Downloading a portable build (one-time)...")
    ffmpeg_dir.mkdir(parents=True, exist_ok=True)

    sys_name = platform.system().lower()

    if sys_name == "windows":
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        archive = ffmpeg_dir / "ffmpeg.zip"
        print(f"  Downloading from {url} ...")
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(ffmpeg_dir)
        archive.unlink(missing_ok=True)
        candidates = list(ffmpeg_dir.rglob("ffmpeg.exe"))
        ffplay_candidates = list(ffmpeg_dir.rglob("ffplay.exe"))
    else:
        # Linux amd64 static build (works on most x86_64)
        url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
        archive = ffmpeg_dir / "ffmpeg.tar.xz"
        print(f"  Downloading from {url} ...")
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive, "r:xz") as tf:
            tf.extractall(ffmpeg_dir)
        archive.unlink(missing_ok=True)
        candidates = list(ffmpeg_dir.rglob("ffmpeg"))
        ffplay_candidates = list(ffmpeg_dir.rglob("ffplay"))

    if not candidates or not ffplay_candidates:
        print("[ezpeek ffmpeg] Download succeeded but no ffmpeg/ffplay binaries found in archive!")
        raise RuntimeError("Failed to download FFmpeg. Please install it manually.")

    # Make Linux binaries executable
    if sys_name != "windows":
        candidates[0].chmod(0o755)
        ffplay_candidates[0].chmod(0o755)

    _FFMPEG_CACHE = str(candidates[0])
    _FFPLAY_CACHE = str(ffplay_candidates[0])
    print(f"[ezpeek] Portable FFmpeg installed to {_FFMPEG_CACHE} and {_FFPLAY_CACHE}")
    return _FFMPEG_CACHE, _FFPLAY_CACHE


def ensure_ffmpeg_tools() -> tuple[str, str]:
    """Ensure ffmpeg + ffplay are available (PATH or portable). Returns their paths."""
    print("[ezpeek ffmpeg] ensure_ffmpeg_tools() called")
    try:
        ffmpeg, ffplay = _find_ffmpeg_executables()
        print(f"[ezpeek ffmpeg] ensure_ffmpeg_tools success -> ffmpeg={ffmpeg}, ffplay={ffplay}")
        return ffmpeg, ffplay
    except Exception as e:
        print(f"[ezpeek ffmpeg] ensure_ffmpeg_tools EXCEPTION: {repr(e)}")
        system = platform.system().lower()
        if system == "windows":
            msg = (
                "Failed to find or auto-download FFmpeg.\n\n"
                "You can try manually:\n"
                "  winget install Gyan.FFmpeg\n"
                "or download from https://www.gyan.dev/ffmpeg/builds/"
            )
        elif system == "linux":
            msg = (
                "Failed to find or auto-download FFmpeg.\n\n"
                "Install with your package manager, e.g.:\n"
                "  Ubuntu/Debian: sudo apt install ffmpeg\n"
                "  Arch: sudo pacman -S ffmpeg\n"
                "  Fedora: sudo dnf install ffmpeg"
            )
        else:
            msg = "FFmpeg not found. Please install the full FFmpeg package (includes ffplay)."
        raise RuntimeError(msg) from e


def has_srt_support() -> bool:
    """Check whether the selected ffmpeg build supports the srt protocol."""
    try:
        ffmpeg, _ = _find_ffmpeg_executables()
        p = subprocess.run(
            [ffmpeg, "-hide_banner", "-protocols"],
            capture_output=True,
            text=True,
            check=False,
        )
        txt = ((p.stdout or "") + (p.stderr or "")).lower()
        return "srt" in txt
    except Exception:
        return False


def build_sender_cmd(
    capture: CaptureSpec,
    encode: EncodeSpec,
    tx: TransportSpec,
    *,
    test_pattern: bool = False,
    pipewire_node_id: Optional[int] = None,
    gst_log_path: Optional[str] = None,
) -> list[str]:
    ensure_ffmpeg_tools()
    ffmpeg_exe, _ = _find_ffmpeg_executables()

    if tx.transport != "srt":
        raise RuntimeError(f"Unsupported transport: {tx.transport}")

    if not has_srt_support():
        raise RuntimeError(
            "This FFmpeg build has no SRT support. Install a full build with libsrt "
            "(e.g. package manager ffmpeg, or Gyan full builds on Windows)."
        )

    # Bind on all interfaces so LAN peers can connect regardless of which IP we advertise.
    bind_host = tx.host if tx.host not in ("", None) else "0.0.0.0"
    out_url = srt_url(
        bind_host,
        tx.port,
        mode="listener",
        latency_ms=tx.latency_ms,
        extra="snddropdelay=0",
    )

    from .encoder import pick_encoder, mux_format_for_family

    _enc_name, family = pick_encoder(encode.codec)
    mux_fmt = mux_format_for_family(family)
    print(f"[ezpeek] Stream mux format: {mux_fmt} (codec family={family}, encoder={_enc_name})")

    # Synthetic pattern for self-test / debugging without screen capture permissions.
    if test_pattern:
        fr = str(encode.fps)
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel", "warning",
            "-re",
            "-f", "lavfi",
            "-i", f"testsrc=size=1280x720:rate={fr},format=yuv420p",
        ]
        cmd += build_video_encode_args(encode)
        cmd += ["-f", mux_fmt, out_url]
        return cmd

    # Pure Wayland support when FFmpeg lacks native -f pipewire.
    # Prefer: wl-screenrec → GStreamer pipewiresrc (quiet y4m pipe) → fall through.
    if platform.system().lower() == "linux":
        try:
            from .capture import (
                _is_wayland,
                _has_wl_screenrec,
                _check_ffmpeg_pipewire_support,
                _has_gstreamer_pipewire,
            )

            if _is_wayland() and not _check_ffmpeg_pipewire_support():
                fr = str(capture.fps)
                encode_part = " ".join(build_video_encode_args(encode))
                if _has_wl_screenrec():
                    wl_part = f"wl-screenrec --fps {fr} -c h264 --ffmpeg-muxer mpegts -o -"
                    pipeline = (
                        f"{wl_part} | {ffmpeg_exe} -hide_banner -loglevel warning "
                        f"-fflags nobuffer -flags low_delay -i - {encode_part} "
                        f"-f {mux_fmt} '{out_url}'"
                    )
                    return ["sh", "-c", pipeline]
                elif _has_gstreamer_pipewire() and pipewire_node_id is not None:
                    # IMPORTANT:
                    # 1) gst-launch must be quiet (-q). Its status lines on stdout
                    #    corrupt the Y4M stream → "Invalid magic number for yuv4mpeg".
                    # 2) pipewire_node_id must come from a still-open ScreenCastSession
                    #    held by HostService (do not request portal here and drop it).
                    log = gst_log_path or "/tmp/ezpeek_gst.log"
                    src = f"pipewiresrc path={int(pipewire_node_id)} do-timestamp=true"
                    gst = (
                        f"gst-launch-1.0 -q "
                        f"{src} ! "
                        f"queue max-size-buffers=8 leaky=downstream ! "
                        f"videoconvert ! video/x-raw,format=I420 ! "
                        f"videorate ! video/x-raw,framerate={fr}/1 ! "
                        f"y4menc ! fdsink fd=1"
                    )
                    pipeline = (
                        f"{gst} 2>'{log}' | "
                        f"{ffmpeg_exe} -hide_banner -loglevel warning "
                        f"-fflags nobuffer -flags low_delay "
                        f"-f yuv4mpegpipe -i - "
                        f"{encode_part} -f {mux_fmt} '{out_url}'"
                    )
                    print(f"[ezpeek] Wayland capture via GStreamer pipewire node={pipewire_node_id}")
                    print(f"[ezpeek] GStreamer log: {log}")
                    return ["sh", "-c", pipeline]
                elif _has_gstreamer_pipewire() and pipewire_node_id is None:
                    print(
                        "[ezpeek] GStreamer available but no PipeWire node id "
                        "(ScreenCast session not started). Falling through..."
                    )
        except Exception as e:
            print(f"[ezpeek] Wayland external capture path failed: {e}")

    input_args = build_capture_input_args(capture)

    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-probesize", "32",
        "-analyzeduration", "0",
        "-thread_queue_size", "512",
    ]
    cmd += input_args
    cmd += build_video_encode_args(encode)
    cmd += ["-f", mux_fmt, out_url]
    return cmd


def _get_supported_hwaccels() -> str:
    """Query ffmpeg for supported hardware accelerators (used for decode)."""
    try:
        ffmpeg, _ = _find_ffmpeg_executables()
        p = subprocess.run(
            [ffmpeg, "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            check=False,
        )
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def _get_hwaccel_arg() -> list[str]:
    """Best-effort hwaccel flags for decode (AV1/H.264). Empty = software."""
    txt = _get_supported_hwaccels().lower()
    sys_name = platform.system().lower()

    if sys_name == "windows":
        if "d3d11va" in txt:
            return ["-hwaccel", "d3d11va"]
        if "cuda" in txt:
            return ["-hwaccel", "cuda"]
        if "auto" in txt:
            return ["-hwaccel", "auto"]
        return []

    if sys_name == "linux":
        # Prefer CUDA/NVDEC when present (great for AV1), then VAAPI, then auto.
        if "cuda" in txt:
            return ["-hwaccel", "cuda"]
        if "vaapi" in txt:
            return ["-hwaccel", "vaapi"]
        if "vdpau" in txt:
            return ["-hwaccel", "vdpau"]
        if "auto" in txt:
            return ["-hwaccel", "auto"]
        return []

    if "auto" in txt:
        return ["-hwaccel", "auto"]
    return []


def build_receiver_cmd(
    host: str,
    port: int,
    transport: Transport = "srt",
    *,
    latency_ms: int = DEFAULT_SRT_LATENCY_MS,
) -> list[str]:
    """Legacy external-ffplay command (debug only)."""
    print(f"[ezpeek viewer] build_receiver_cmd (legacy ffplay) for {host}:{port}")
    ensure_ffmpeg_tools()
    _, ffplay_exe = _find_ffmpeg_executables()
    if transport != "srt":
        raise RuntimeError(f"Unsupported transport: {transport}")
    if not has_srt_support():
        raise RuntimeError("This FFmpeg build has no SRT support (needed for video).")

    hw = _get_hwaccel_arg()
    cmd = [
        ffplay_exe,
        "-hide_banner",
        "-loglevel", "warning",
        "-fflags", "nobuffer",
        "-flags", "low_delay",
        "-framedrop",
        "-sync", "ext",
        "-window_title", f"EzPeek Video - {host}:{port}",
    ]
    cmd += hw
    srt = srt_url(host, port, mode="caller", latency_ms=latency_ms, extra="rcvlatency=0")
    cmd += [srt]
    return cmd
