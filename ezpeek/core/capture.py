from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from .wayland_portal import request_pipewire_node_id, WaylandPortalError


@dataclass(frozen=True)
class CaptureSpec:
    fps: int = 60
    display: Optional[str] = None  # X11 display like ":0.0" (optional)
    force_x11: bool = False  # Force X11 capture even on Wayland


@dataclass
class CaptureMethod:
    """Describes how to capture the screen."""
    method: str  # "ffmpeg", "wl-screenrec", "gstreamer"
    args: list[str]  # Command arguments
    pipewire_node_id: Optional[int] = None


def _is_wayland() -> bool:
    """Check if running on native Wayland (not XWayland)."""
    # If QT_QPA_PLATFORM is xcb, we're using XWayland
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
    """Check if GStreamer has pipewiresrc element."""
    try:
        p = subprocess.run(
            ["gst-inspect-1.0", "pipewiresrc"],
            capture_output=True, text=True, check=False
        )
        return p.returncode == 0
    except Exception:
        return False


def build_capture_input_args(spec: CaptureSpec) -> list[str]:
    """
    Build FFmpeg input arguments for screen capture.
    """
    fr = str(spec.fps)
    sys_name = platform.system().lower()

    if sys_name == "windows":
        return ["-f", "gdigrab", "-framerate", fr, "-i", "desktop"]

    if sys_name == "linux":
        # Check if we should use X11 capture
        use_x11 = spec.force_x11 or not _is_wayland()
        
        if use_x11 and _is_x11():
            # Use X11 capture (works with XWayland too)
            display = spec.display or os.environ.get("DISPLAY", ":0.0")
            size = _x11_screen_size(display)
            args = ["-f", "x11grab", "-framerate", fr]
            if size:
                args += ["-video_size", size]
            args += ["-i", f"{display}+0,0"]
            return args
        
        if _is_wayland():
            # Native Wayland - need PipeWire
            if _check_ffmpeg_pipewire_support():
                try:
                    node_id = request_pipewire_node_id(app_id="ezpeek")
                except WaylandPortalError as e:
                    raise RuntimeError(f"Wayland portal capture failed: {e}") from e
                
                print(f"[ezpeek] PipeWire node ID: {node_id}")
                return [
                    "-f", "pipewire",
                    "-framerate", fr,
                    "-i", str(node_id),
                ]
            
            # No PipeWire support - check for alternatives
            if _has_wl_screenrec():
                raise RuntimeError(
                    "FFmpeg lacks PipeWire support, but wl-screenrec is available.\n"
                    "Use get_wayland_capture_command() instead, or run with:\n"
                    "  QT_QPA_PLATFORM=xcb ezpeek"
                )
            
            raise RuntimeError(
                "Your FFmpeg is not built with PipeWire support.\n\n"
                "Solutions:\n"
                "1. Run under XWayland:\n"
                "   QT_QPA_PLATFORM=xcb ezpeek\n\n"
                "2. Install FFmpeg with PipeWire support:\n"
                "   - Arch: yay -S ffmpeg-obs\n"
                "   - Fedora: sudo dnf install ffmpeg --enablerepo=rpmfusion-free\n\n"
                "3. Install wl-screenrec: pacman -S wl-screenrec"
            )
        
        # Fallback to X11 if available
        if _is_x11():
            display = spec.display or os.environ.get("DISPLAY", ":0.0")
            size = _x11_screen_size(display)
            args = ["-f", "x11grab", "-framerate", fr]
            if size:
                args += ["-video_size", size]
            args += ["-i", f"{display}+0,0"]
            return args

    raise RuntimeError("Unsupported capture environment (need Windows, X11, or Wayland/PipeWire).")


def get_wayland_capture_command(spec: CaptureSpec, output_url: str) -> Tuple[str, list[str]]:
    """
    Get a complete capture command for Wayland.
    
    Returns (method, full_command) where method is "ffmpeg", "wl-screenrec", etc.
    """
    fr = str(spec.fps)
    
    # Option 1: FFmpeg with PipeWire
    if _check_ffmpeg_pipewire_support():
        try:
            node_id = request_pipewire_node_id(app_id="ezpeek")
            cmd = [
                "ffmpeg", "-hide_banner",
                "-f", "pipewire",
                "-framerate", fr,
                "-i", str(node_id),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "zerolatency",
                "-f", "mpegts",
                output_url,
            ]
            return ("ffmpeg", cmd)
        except WaylandPortalError:
            pass
    
    # Option 2: wl-screenrec piped to FFmpeg
    if _has_wl_screenrec():
        cmd = [
            "wl-screenrec",
            "--fps", fr,
            "-c", "h264",
            "--ffmpeg-muxer", "mpegts",
            "-o", "-",
        ]
        return ("wl-screenrec", cmd)
    
    raise RuntimeError(
        "No Wayland capture method available.\n"
        "Run with: QT_QPA_PLATFORM=xcb ezpeek"
    )
