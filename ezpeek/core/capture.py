from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from typing import Optional

from .wayland_portal import request_pipewire_node_id, WaylandPortalError


@dataclass(frozen=True)
class CaptureSpec:
    fps: int = 60
    display: Optional[str] = None  # X11 display like ":0.0" (optional)


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _is_x11() -> bool:
    return bool(os.environ.get("DISPLAY")) and not _is_wayland()


def _x11_screen_size(display: str) -> Optional[str]:
    try:
        p = subprocess.run(["xdpyinfo", "-display", display], capture_output=True, text=True, check=False)
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


def build_capture_input_args(spec: CaptureSpec) -> list[str]:
    fr = str(spec.fps)
    sys = platform.system().lower()

    if sys == "windows":
        return ["-f", "gdigrab", "-framerate", fr, "-i", "desktop"]

    if sys == "linux":
        if _is_wayland():
            try:
                node_id = request_pipewire_node_id(app_id="ezpeek")
            except WaylandPortalError as e:
                raise RuntimeError(f"Wayland portal capture failed: {e}") from e

            print("[ezpeek] portal pipewire node id:", node_id)
            return ["-f", "pipewire", "-framerate", fr, "-i", f"pipewire:{node_id}"]

        if _is_x11():
            display = spec.display or os.environ.get("DISPLAY", ":0.0")
            size = _x11_screen_size(display)
            args = ["-f", "x11grab", "-framerate", fr]
            if size:
                args += ["-video_size", size]
            args += ["-i", f"{display}+0,0"]
            return args

    raise RuntimeError("Unsupported capture environment (need Windows, X11, or Wayland/PipeWire).")
