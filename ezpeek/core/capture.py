from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from .wayland_portal import request_pipewire_node_id, WaylandPortalError, ScreenCastSession


@dataclass(frozen=True)
class CaptureSpec:
    fps: int = 60
    display: Optional[str] = None  # X11 display like ":0.0" (optional)
    force_x11: bool = False  # Only for legacy X11 debugging. Not recommended on modern Wayland systems.
    draw_mouse: bool = True  # Whether to include cursor in capture
    video_size: Optional[str] = None  # Override e.g. "1920x1080" (platform dependent)


@dataclass
class CaptureMethod:
    """Describes how to capture the screen."""
    method: str  # "ffmpeg", "wl-screenrec", "gstreamer"
    args: list[str]  # Command arguments
    pipewire_node_id: Optional[int] = None


def _is_wayland() -> bool:
    """Check if running on native Wayland (primary supported path)."""
    # QT_QPA_PLATFORM=xcb forces XWayland emulation (legacy).
    qt_platform = os.environ.get("QT_QPA_PLATFORM", "").lower()
    if qt_platform == "xcb":
        return False
    
    # Check for Wayland display
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _is_x11() -> bool:
    """Check if X11 is available for capture."""
    return bool(os.environ.get("DISPLAY"))


def _x11_screen_size(display: str) -> Optional[str]:
    try:
        p = subprocess.run(
            ["xdpyinfo", "-display", display],
            capture_output=True, text=True, check=False
        )
        txt = (p.stdout or "") + (p.stderr or "")
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("dimensions:"):
                parts = line.split()
                for token in parts:
                    if "x" in token:
                        w_h = token.split("x")
                        if len(w_h) == 2 and w_h[0].isdigit() and w_h[1].isdigit():
                            return token
    except Exception:
        return None
    return None


def _check_ffmpeg_pipewire_support() -> bool:
    """Check if FFmpeg has PipeWire demuxer support."""
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-demuxers"],
            capture_output=True, text=True, check=False
        )
        output = (p.stdout or "") + (p.stderr or "")
        for line in output.splitlines():
            if "pipewire" in line.lower():
                return True
        return False
    except Exception:
        return False


def _has_wl_screenrec() -> bool:
    """Check for wl-screenrec (fast Wayland recorder)."""
    return shutil.which("wl-screenrec") is not None


def _has_gstreamer_pipewire() -> bool:
    """Check if GStreamer has pipewiresrc element and gst-launch-1.0 is available."""
    if shutil.which("gst-launch-1.0") is None:
        return False
    try:
        p = subprocess.run(
            ["gst-inspect-1.0", "pipewiresrc"],
            capture_output=True, text=True, check=False
        )
        return p.returncode == 0
    except Exception:
        return False


def _check_ffmpeg_d3d11grab_support() -> bool:
    """Check if FFmpeg has d3d11grab (or dxgi) demuxer for Windows HW capture."""
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-demuxers"],
            capture_output=True, text=True, check=False
        )
        output = (p.stdout or "") + (p.stderr or "")
        for line in output.splitlines():
            if "d3d11grab" in line.lower() or "dxgi" in line.lower():
                return True
        return False
    except Exception:
        return False


def _check_ffmpeg_kmsgrab_support() -> bool:
    """Check if FFmpeg has kmsgrab for native Wayland/DRM screen capture (no X11)."""
    try:
        p = subprocess.run(
            ["ffmpeg", "-hide_banner", "-demuxers"],
            capture_output=True, text=True, check=False
        )
        output = (p.stdout or "") + (p.stderr or "")
        for line in output.splitlines():
            if "kmsgrab" in line.lower():
                return True
        return False
    except Exception:
        return False


def _find_drm_device() -> str:
    """Find a usable /dev/dri/card* device for kmsgrab. Prefers first render-capable."""
    for dev in ["/dev/dri/card0", "/dev/dri/card1", "/dev/dri/card2"]:
        if os.path.exists(dev):
            return dev
    return "/dev/dri/card0"  # fallback, may need video group membership


def _windows_screen_size() -> Optional[str]:
    """Try to detect primary screen size on Windows using PowerShell or wmic."""
    try:
        # Modern PowerShell
        ps = [
            "powershell", "-NoProfile", "-Command",
            "(Get-WmiObject Win32_VideoController | Select-Object -First 1).CurrentHorizontalResolution,'x',"
            "(Get-WmiObject Win32_VideoController | Select-Object -First 1).CurrentVerticalResolution"
        ]
        p = subprocess.run(ps, capture_output=True, text=True, check=False, timeout=3)
        out = (p.stdout or "").strip()
        if "x" in out and out.replace("x", "").replace(" ", "").isdigit():
            return out.replace(" ", "")
    except Exception:
        pass
    try:
        # Fallback wmic (older Windows)
        p = subprocess.run(
            ["wmic", "desktopmonitor", "get", "screenwidth,screenheight"],
            capture_output=True, text=True, check=False, timeout=3
        )
        lines = [l.strip() for l in (p.stdout or "").splitlines() if l.strip() and any(c.isdigit() for c in l)]
        if len(lines) >= 2:
            dims = lines[1].split()
            if len(dims) == 2:
                return f"{dims[0]}x{dims[1]}"
    except Exception:
        pass
    return None


def build_capture_input_args(spec: CaptureSpec) -> list[str]:
    """
    Build FFmpeg input arguments for screen capture.
    Cross-platform, prioritizes hardware-accelerated / low-overhead paths where possible.
    """
    fr = str(spec.fps)
    sys_name = platform.system().lower()

    if sys_name == "windows":
        # Prefer modern low-overhead capture if available (d3d11grab / dxgi in recent FFmpeg builds)
        use_d3d = _check_ffmpeg_d3d11grab_support()
        args = ["-f", "d3d11grab" if use_d3d else "gdigrab", "-framerate", fr]

        # Size / offset (helps avoid capturing multiple monitors unintentionally)
        size = spec.video_size or _windows_screen_size()
        if size:
            args += ["-video_size", size]

        # Offset for primary monitor usually 0,0 ; can be extended later
        args += ["-offset_x", "0", "-offset_y", "0"]

        if spec.draw_mouse:
            # gdigrab supports draw_mouse; d3d11grab uses different flag sometimes, include for gdigrab
            if not use_d3d:
                args += ["-draw_mouse", "1"]
            # For d3d11 users often rely on cursor being composited or use post filter; keep simple

        args += ["-i", "desktop"]
        return args

    if sys_name == "linux":
        # Pure Wayland first (modern default and only option on many mainstream LTS releases now).
        # X11grab kept only for legacy X11 sessions or explicit force.
        is_wayland = _is_wayland()
        has_display = _is_x11()

        if is_wayland and not spec.force_x11:
            # === PURE WAYLAND PATH (no X11 / XWayland dependency whatsoever) ===
            if _check_ffmpeg_pipewire_support():
                try:
                    node_id = request_pipewire_node_id(app_id="ezpeek")
                except WaylandPortalError as e:
                    raise RuntimeError(f"Wayland portal capture failed: {e}") from e

                return [
                    "-f", "pipewire",
                    "-framerate", fr,
                    "-i", str(node_id),
                ]

            # kmsgrab is opt-in: it often fails without CAP_SYS_ADMIN / correct DRM master
            # ("No handle set on framebuffer") and previously blocked better fallbacks.
            if os.environ.get("EZPEEK_USE_KMSGRAB", "").strip().lower() in ("1", "true", "yes"):
                if _check_ffmpeg_kmsgrab_support():
                    device = _find_drm_device()
                    args = ["-f", "kmsgrab", "-framerate", fr]
                    if device:
                        args += ["-device", device]
                    args += ["-i", "-"]
                    return args

            if _has_wl_screenrec():
                # wl-screenrec is handled in build_sender_cmd (pipeline bypass)
                raise RuntimeError(
                    "wl-screenrec is installed but was not selected by the sender builder; retry hosting."
                )

            # Fall through to XWayland/x11grab if DISPLAY is available.
            print(
                "[ezpeek] No ffmpeg-native Wayland capture (no pipewire demuxer). "
                "Will try XWayland x11grab if DISPLAY is set, or GStreamer/portal via HostService."
            )

        # X11 / XWayland path.
        # On pure Wayland with no working PipeWire capture, x11grab via XWayland is
        # a last-resort fallback so hosting can still start (may miss pure-Wayland UI).
        if has_display and (
            spec.force_x11
            or not is_wayland
            or os.environ.get("EZPEEK_ALLOW_X11GRAB", "1") == "1"
        ):
            if is_wayland and not spec.force_x11:
                print(
                    "[ezpeek] Falling back to x11grab via XWayland (DISPLAY set). "
                    "Prefer PipeWire portal capture when available."
                )
            display = spec.display or os.environ.get("DISPLAY", ":0.0")
            size = spec.video_size or _x11_screen_size(display)
            args = ["-f", "x11grab", "-framerate", fr]
            if size:
                args += ["-video_size", size]
            args += ["-i", f"{display}+0,0"]
            if spec.draw_mouse:
                args += ["-draw_mouse", "1"]
            return args

    # macOS stub for future (avfoundation)
    if sys_name == "darwin":
        args = ["-f", "avfoundation", "-framerate", fr, "-i", "1"]  # 1 = screen, adjust as needed
        return args

    raise RuntimeError(
        "Unsupported capture environment.\n"
        "Linux Wayland: need PipeWire portal (xdg-desktop-portal) + either\n"
        "  ffmpeg with pipewire demuxer, wl-screenrec, or gstreamer pipewiresrc.\n"
        "Or set DISPLAY for X11/XWayland x11grab fallback."
    )


def get_wayland_capture_command(spec: CaptureSpec, output_url: str) -> Tuple[str, list[str]]:
    """
    Advanced Wayland-only capture (no X11).
    Prefer the main build_capture_input_args + PipeWire path.
    """
    fr = str(spec.fps)
    
    # Option 1: FFmpeg with PipeWire (preferred for low overhead)
    if _check_ffmpeg_pipewire_support():
        try:
            node_id = request_pipewire_node_id(app_id="ezpeek")
            cmd = [
                "ffmpeg", "-hide_banner",
                "-fflags", "nobuffer", "-flags", "low_delay",
                "-f", "pipewire",
                "-framerate", fr,
                "-i", str(node_id),
            ]
            # Note: encode args are added by caller in normal path. Here provide basic.
            cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-f", "mpegts", output_url]
            return ("ffmpeg", cmd)
        except WaylandPortalError:
            pass
    
    # Option 2: wl-screenrec (excellent on Wayland, low CPU)
    if _has_wl_screenrec():
        cmd = [
            "wl-screenrec",
            "--fps", fr,
            "-c", "h264",
            "--ffmpeg-muxer", "mpegts",
            "-o", "-",
        ]
        # The caller (if using) must pipe stdout to an ffmpeg that does the SRT mux/encode.
        return ("wl-screenrec", cmd)
    
    # Option 3: kmsgrab (built-in FFmpeg, pure DRM/Wayland, no extra tools)
    if _check_ffmpeg_kmsgrab_support():
        device = _find_drm_device()
        cmd = ["ffmpeg", "-hide_banner", "-fflags", "nobuffer", "-flags", "low_delay",
               "-f", "kmsgrab", "-framerate", fr]
        if device:
            cmd += ["-device", device]
        cmd += ["-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-f", "mpegts", output_url]
        return ("kmsgrab", cmd)
    
    raise RuntimeError(
        "No Wayland capture method available.\n"
        "Install FFmpeg with PipeWire or wl-screenrec (or ensure kmsgrab works). Pure Wayland only (no X11)."
    )
