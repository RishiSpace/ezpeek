from __future__ import annotations

import os
import platform
import shutil
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
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


_FFMPEG_CACHE: Optional[str] = None
_FFPLAY_CACHE: Optional[str] = None


def _get_ffmpeg_dir() -> Path:
    """Returns a user-writable directory for a portable FFmpeg installation."""
    if platform.system().lower() == "windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".local" / "share"
    return base / "ezpeek" / "ffmpeg"


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
    print(f"[ezpeek ffmpeg] No PATH ffplay, checking portable dir: {ffmpeg_dir}")
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
    machine = platform.machine().lower()

    if sys_name == "windows":
        url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        archive = ffmpeg_dir / "ffmpeg.zip"
        print(f"  Downloading from {url} ...")
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(ffmpeg_dir)
        archive.unlink()
        # Find inside the extracted folder
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
        archive.unlink()
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


def ensure_ffmpeg_tools() -> None:
    print("[ezpeek ffmpeg] ensure_ffmpeg_tools() called")
    try:
        ffmpeg, ffplay = _find_ffmpeg_executables()
        print(f"[ezpeek ffmpeg] ensure_ffmpeg_tools success -> ffmpeg={ffmpeg}, ffplay={ffplay}")
    except Exception as e:
        print(f"[ezpeek ffmpeg] ensure_ffmpeg_tools EXCEPTION: {repr(e)}")
        # Fallback to helpful error if auto-download also fails
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

    if shutil.which("ffmpeg") is None or shutil.which("ffplay") is None:
        system = platform.system().lower()
        if system == "windows":
            msg = (
                "ffmpeg / ffplay not found on PATH.\n\n"
                "On Windows:\n"
                "Recommended:\n"
                "1. winget install Gyan.FFmpeg   (or 'winget install ffmpeg')\n"
                "2. Close and reopen your terminal / PowerShell / the app\n\n"
                "Manual (guaranteed full build with ffplay):\n"
                "1. Go to https://www.gyan.dev/ffmpeg/builds/\n"
                "2. Download the latest 'full' or 'essentials' zip\n"
                "3. Extract it (example: C:\\ffmpeg)\n"
                "4. Add C:\\ffmpeg\\bin to your System PATH (search 'Edit the system environment variables')\n"
                "5. Restart your terminal and the ezpeek app\n\n"
                "Verify: open a new cmd and type `ffmpeg -version` and `ffplay -version`"
            )
        elif system == "linux":
            msg = (
                "ffmpeg / ffplay not found on PATH.\n\n"
                "Install it with your package manager, e.g.:\n"
                "  Ubuntu/Debian: sudo apt install ffmpeg\n"
                "  Arch: sudo pacman -S ffmpeg\n"
                "  Fedora: sudo dnf install ffmpeg"
            )
        else:
            msg = "ffmpeg / ffplay not found on PATH. Please install the full FFmpeg package."

        raise RuntimeError(msg)


def build_sender_cmd(capture: CaptureSpec, encode: EncodeSpec, tx: TransportSpec) -> list[str]:
    ensure_ffmpeg_tools()
    ffmpeg_exe, _ = _find_ffmpeg_executables()

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
                    pipeline = f"{wl_part} | {ffmpeg_exe} -hide_banner -loglevel warning -fflags nobuffer -flags low_delay -i - {encode_part} -f mpegts '{srt_url_str}'"
                    return ["sh", "-c", pipeline]
                elif _has_gstreamer_pipewire():
                    # Trigger portal grant and get node for specific capture.
                    node_id = None
                    try:
                        from .capture import request_pipewire_node_id
                        node_id = request_pipewire_node_id(app_id="ezpeek")
                    except Exception:
                        pass
                    if node_id:
                        src = f"pipewiresrc path={node_id} do-timestamp=true"
                    else:
                        src = "pipewiresrc do-timestamp=true"
                    # More robust pipeline for pipewiresrc negotiation:
                    # - videorate before forcing framerate caps
                    # - extra queue + explicit negotiation order
                    # - avoid strict framerate on the raw caps before rate conversion
                    gst = (
                        f"gst-launch-1.0 "
                        f"{src} ! queue max-size-buffers=4 leaky=downstream ! "
                        f"videoconvert ! "
                        f"videorate ! video/x-raw,format=I420,framerate={fr}/1 ! "
                        f"y4menc ! queue ! fdsink fd=1"
                    )
                    pipeline = f"{gst} | {ffmpeg_exe} -hide_banner -loglevel warning -fflags nobuffer -flags low_delay -f yuv4mpegpipe -i - {encode_part} -f mpegts '{srt_url_str}'"
                    return ["sh", "-c", pipeline]
        except Exception:
            pass  # fall through to build_capture (which may use kmsgrab or raise)

    input_args = build_capture_input_args(capture)

    cmd = [
        ffmpeg_exe,
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


def _get_supported_hwaccels() -> str:
    """Query ffplay for list of supported hardware accelerators."""
    try:
        _, ffplay = _find_ffmpeg_executables()
        p = subprocess.run([ffplay, "-hide_banner", "-hwaccels"],
                           capture_output=True, text=True, check=False)
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def _get_hwaccel_arg() -> list[str]:
    """Return best-effort hwaccel flags for ffplay decode.
    Falls back to software (no flag) if no supported HW accel is found.
    """
    txt = _get_supported_hwaccels().lower()
    sys_name = platform.system().lower()

    if sys_name == "windows":
        # Prefer d3d11va, then cuda, else software
        if "d3d11va" in txt:
            return ["-hwaccel", "d3d11va", "-hwaccel_output_format", "d3d11"]
        if "cuda" in txt:
            return ["-hwaccel", "cuda"]
        return []  # software decode

    elif sys_name == "linux":
        # vaapi first for intel/amd, cuda for nvidia, else software
        if "vaapi" in txt:
            return ["-hwaccel", "vaapi", "-hwaccel_output_format", "vaapi"]
        if "cuda" in txt:
            return ["-hwaccel", "cuda"]
        if "vdpau" in txt:
            return ["-hwaccel", "vdpau"]
        return []  # software decode

    # other platforms: try auto, which will fallback to software
    if "auto" in txt or "none" in txt:
        return ["-hwaccel", "auto"]
    return []  # software


def build_receiver_cmd(host: str, port: int, transport: Transport = "srt") -> list[str]:
    print(f"[ezpeek viewer] build_receiver_cmd called for {host}:{port}")
    ensure_ffmpeg_tools()
    _, ffplay_exe = _find_ffmpeg_executables()
    print(f"[ezpeek viewer] Using ffplay executable: {ffplay_exe}")
    if transport != "srt":
        raise RuntimeError(f"Unsupported transport: {transport}")

    hw = _get_hwaccel_arg()
    cmd = [
        ffplay_exe,
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
    srt = srt_url(host, port, mode="caller", latency_ms=20, extra="rcvlatency=10")
    cmd += [srt]
    print(f"[ezpeek viewer] Full ffplay command: {cmd}")
    print(f"[ezpeek viewer] SRT URL: {srt}")
    return cmd
