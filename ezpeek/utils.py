import ipaddress
import os
import socket
import tempfile
from pathlib import Path


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


def get_local_ip(prefer_private: bool = True) -> str:
    """
    Return the best local IPv4 for LAN discovery/hosting.

    prefer_private:
      - True: prefer RFC1918 private addresses (192.168/10/172.16-31)
      - False: return first valid non-loopback address
    """
    best_private = None
    best_non_loopback = None

    for ip in _iter_local_ipv4_candidates():
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue

        if addr.version != 4:
            continue
        if addr.is_loopback:
            continue
        if addr.is_link_local:  # 169.254.x.x
            continue

        if addr.is_private:
            # Try to avoid common container/virtual networks unless that's all we have
            # (heuristic only)
            if ip.startswith(("172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                              "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")) is False:
                return ip
            if best_private is None:
                best_private = ip
        else:
            if best_non_loopback is None:
                best_non_loopback = ip

    if prefer_private and best_private:
        return best_private
    if best_non_loopback:
        return best_non_loopback
    return "0.0.0.0"
