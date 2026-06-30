# ezpeek

**LAN / NAT-friendly remote desktop with hardware-accelerated capture + encode + smooth low-latency streaming (FFmpeg + SRT + Qt).**

Production-oriented focus: excellent cross-platform capture (especially **Linux Wayland** ↔ **Windows**), hardware acceleration on both encode and decode, and real input remoting.

## Features
- **Capture**:
  - Linux: X11 + Wayland (PipeWire via portals) with excellent fallbacks.
  - Windows: gdigrab + d3d11grab (when available in FFmpeg) for lower overhead.
- **HW Acceleration**:
  - Auto-detects NVENC, AMF, QSV, VAAPI per platform.
  - Viewer uses ffplay with platform HW decode where possible.
- **Transport**: SRT (low latency, reliable).
- **Input Remoting**: Full mouse + keyboard forwarding (Windows native via SendInput; Linux via xdotool).
- **Discovery**: Simple LAN broadcast (works without mDNS).
- **NAT**: Optional STUN-based public address advertisement.
- **GUI**: Qt (PySide6) with integrated ViewerWindow for input grab.

## Requirements
- Python >= 3.10
- FFmpeg (with SRT support strongly recommended) + ffplay on PATH
- PySide6
- Linux only: `dbus-python`, `PyGObject` (for Wayland portal)
- (Recommended) `xdotool` on Linux for input

## Quick Start
```bash
# Install in editable mode
pip install -e .

# Run GUI
ezpeek
# or
python -m ezpeek
```

In GUI:
1. On machine A press **H** (or use UI) to start **hosting**.
2. On machine B the peer appears — double-click to view.
3. In the ViewerWindow enable **"Grab Input"** to forward mouse/keyboard.

## Configuration / Advanced
HostService supports:
- `fps`, `bitrate_kbps`, `codec`
- `enable_control=True`
- `use_nat=True` (advertises STUN public address when possible)

SRT latency is tuned low (~20ms target).

### Wayland Native Support (recommended and default)
ezpeek is designed for pure Wayland (the mainstream on modern Linux).

Research (2025-2026):
- Ubuntu 25.10 removed the X11/GNOME session entirely.
- Ubuntu 26.04 LTS ships Wayland-only.
- GNOME 49+ and recent KDE Plasma are dropping or have dropped X11 session support.
- X11 is in maintenance-only mode with known long-standing vulnerabilities.
- XWayland remains only for running old X11 apps inside Wayland.

We have removed all X11 capture/input fallbacks for Wayland hosts.

- Capture uses xdg-desktop-portal + PipeWire for native, permissioned, low-overhead screen capture via FFmpeg.
- Input uses the RemoteDesktop portal (user grants "remote control" permission once).
- No dependency on X11, x11grab, or xdotool for Wayland hosts.
- Requirements: FFmpeg with PipeWire support + xdg-desktop-portal + wireplumber.
- If your FFmpeg lacks PipeWire, install a properly built one (e.g. ffmpeg-obs on Arch) or wl-screenrec.
- The GUI (PySide6) runs natively on Wayland.
- ffplay for viewing also works on Wayland.

See capture.py and wayland_portal.py for implementation. X11 paths are retained only for legacy X11 sessions.

### Windows Notes
- Modern FFmpeg builds support d3d11grab for better capture.
- HW encode (NVENC/AMF) is auto-picked.
- Input uses native SendInput (no extra privileges usually needed for user session).

## Architecture Highlights
- Video path stays with FFmpeg (best HW accel + low latency).
- Separate lightweight TCP control channel for input.
- Modular: `core/capture`, `encoder`, `transport`, `control`, `nat_traversal`, etc.

## Production Notes
- Robust process lifecycle (terminate + kill).
- Graceful fallbacks and clear error messages.
- Version 0.2.0 — focused on compatibility and remoting completeness.
- For internet use: combine with the included STUN/TURN server or port-forward + `use_nat`.

See `server/` for the bundled STUN/TURN implementation.

## Development
```bash
pip install -e ".[dev]"
ruff check .
pytest
```

Contributions and issues welcome. The goal is reliable, smooth, hardware-accelerated remoting across Linux Wayland and Windows.
