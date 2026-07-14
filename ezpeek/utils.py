import ipaddress
import os
import platform
import re
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


# Stream quality bounds (kbps). Encoders use VBR/CBR-with-cap inside this range.
BITRATE_MIN_KBPS = 20000
BITRATE_MAX_KBPS = 30000
BITRATE_TARGET_KBPS = 25000  # preferred average; clamped into [min, max]

# Practical stream FPS clamps (some panels report odd floats)
FPS_MIN = 24
FPS_MAX = 240


def get_log_dir() -> Path:
    """Cross-platform directory for ezpeek runtime logs."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    path = base / "ezpeek" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_temp_dir() -> Path:
    """Writable temp dir (works on Windows and Linux)."""
    path = Path(tempfile.gettempdir()) / "ezpeek"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _iter_local_ipv4_candidates():
    """
    Yield IPv4 addresses from the local host without requiring internet access.
    Uses getaddrinfo(hostname) which is imperfect, but works on both Linux/Windows.
    """
    hostname = socket.gethostname()
    seen = set()

    # Try hostname resolution
    try:
        for fam, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_DGRAM):
            if fam != socket.AF_INET:
                continue
            ip = sockaddr[0]
            if ip not in seen:
                seen.add(ip)
                yield ip
    except OSError:
        pass

    # Try common hostnames
    for name in (hostname, "localhost"):
        try:
            ip = socket.gethostbyname(name)
            if ip not in seen:
                seen.add(ip)
                yield ip
        except OSError:
            pass

    # As a last local-only fallback, this does not send packets, but may pick a default route IP.
    # (Still better than hard-coding 8.8.8.8.)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET-1; no need for real connectivity
        ip = s.getsockname()[0]
        s.close()
        if ip not in seen:
            seen.add(ip)
            yield ip
    except OSError:
        pass


def _is_virtualish_ip(ip: str) -> bool:
    """Heuristic: docker / libvirt / common hypervisor host-only ranges."""
    return ip.startswith(
        (
            "172.17.",
            "172.18.",
            "172.19.",
            "172.20.",
            "172.21.",
            "172.22.",
            "172.23.",
            "172.24.",
            "172.25.",
            "172.26.",
            "172.27.",
            "172.28.",
            "172.29.",
            "172.30.",
            "172.31.",
            "192.168.122.",  # libvirt default
            "192.168.56.",  # VirtualBox host-only common
            "16.32.",  # VMware vmnet (seen on this machine)
            "169.254.",
        )
    )


def get_local_ip(prefer_private: bool = True) -> str:
    """
    Return the best local IPv4 for LAN discovery/hosting.

    Prefers the default-route interface (UDP connect trick) so we advertise an
    address peers can actually route to, not a random virtual NIC.
    """
    # 1) Default-route source address (most reliable for "how do I leave this host")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # TEST-NET-1; no packets need to arrive
        route_ip = s.getsockname()[0]
        s.close()
        try:
            addr = ipaddress.ip_address(route_ip)
            if (
                addr.version == 4
                and not addr.is_loopback
                and not addr.is_link_local
                and not _is_virtualish_ip(route_ip)
            ):
                return route_ip
        except ValueError:
            pass
    except OSError:
        route_ip = None

    best_private = None
    best_virtual = None
    best_non_loopback = None

    for ip in _iter_local_ipv4_candidates():
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue

        if addr.version != 4:
            continue
        if addr.is_loopback or addr.is_link_local:
            continue

        if addr.is_private:
            if _is_virtualish_ip(ip):
                if best_virtual is None:
                    best_virtual = ip
            else:
                # Prefer 10.x / typical LAN over first-seen random private
                if best_private is None or ip.startswith("10."):
                    best_private = ip
                    if ip.startswith("10."):
                        return ip
        else:
            if best_non_loopback is None:
                best_non_loopback = ip

    if prefer_private and best_private:
        return best_private
    if route_ip and route_ip != "0.0.0.0":
        return route_ip
    if best_non_loopback:
        return best_non_loopback
    if best_virtual:
        return best_virtual
    return "0.0.0.0"


def get_display_refresh_hz() -> float:
    """
    Best-effort primary display refresh rate (Hz).

    Order: Qt (if a QGuiApplication exists) → platform APIs → 60 Hz default.
    """
    hz = _refresh_hz_qt()
    if hz:
        return _clamp_hz(hz)

    sys_name = platform.system().lower()
    if sys_name == "windows":
        hz = _refresh_hz_windows()
    elif sys_name == "linux":
        hz = _refresh_hz_linux()
    else:
        hz = None

    return _clamp_hz(hz or 60.0)


def _clamp_hz(hz: float) -> float:
    try:
        v = float(hz)
    except (TypeError, ValueError):
        return 60.0
    if v < 20 or v > 500:
        return 60.0
    return v


def _refresh_hz_qt() -> Optional[float]:
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return None
        screen = app.primaryScreen()
        if screen is None:
            return None
        rate = float(screen.refreshRate())
        return rate if rate >= 20 else None
    except Exception:
        return None


def _refresh_hz_windows() -> Optional[float]:
    try:
        import ctypes
        from ctypes import wintypes

        class DEVMODE(ctypes.Structure):
            _fields_ = [
                ("dmDeviceName", wintypes.WCHAR * 32),
                ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD),
                ("dmFields", wintypes.DWORD),
                ("dmPositionX", ctypes.c_long),
                ("dmPositionY", ctypes.c_long),
                ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", wintypes.SHORT),
                ("dmDuplex", wintypes.SHORT),
                ("dmYResolution", wintypes.SHORT),
                ("dmTTOption", wintypes.SHORT),
                ("dmCollate", wintypes.SHORT),
                ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD),
                ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD),
                ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD),
                ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD),
                ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD),
                ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD),
                ("dmPanningHeight", wintypes.DWORD),
            ]

        dm = DEVMODE()
        dm.dmSize = ctypes.sizeof(DEVMODE)
        # ENUM_CURRENT_SETTINGS = -1
        if ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm)):
            freq = int(dm.dmDisplayFrequency)
            if freq > 1:
                return float(freq)
    except Exception:
        pass
    return None


def _refresh_hz_linux() -> Optional[float]:
    # xrandr (X11 / XWayland)
    try:
        p = subprocess.run(
            ["xrandr"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        txt = p.stdout or ""
        # lines like: "   2560x1600    165.00*+  60.00"
        best = None
        for line in txt.splitlines():
            if "*" in line:
                nums = re.findall(r"(\d+\.?\d*)\*", line)
                if nums:
                    best = float(nums[0])
                    break
                nums = re.findall(r"(\d+\.?\d*)", line)
                # pick number nearest a '*' marker
                m = re.search(r"(\d+\.?\d*)\s*\*", line)
                if m:
                    best = float(m.group(1))
                    break
        if best:
            return best
    except Exception:
        pass

    # wlr-randr (some wlroots compositors)
    try:
        p = subprocess.run(
            ["wlr-randr"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
        for line in (p.stdout or "").splitlines():
            if "current" in line.lower() and "Hz" in line:
                m = re.search(r"(\d+\.?\d*)\s*Hz", line)
                if m:
                    return float(m.group(1))
    except Exception:
        pass

    return None


def effective_stream_fps(host_hz: float, peer_hz: Optional[float] = None) -> int:
    """
    Stream FPS = floor(min(host, peer)) clamped to [FPS_MIN, FPS_MAX].
    If peer unknown, use host rate alone.
    """
    rates = [float(host_hz)]
    if peer_hz is not None and peer_hz > 0:
        rates.append(float(peer_hz))
    fps = int(min(rates))
    return max(FPS_MIN, min(FPS_MAX, fps))
