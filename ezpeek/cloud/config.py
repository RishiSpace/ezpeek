from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

# Relay TCP port on the cloud host (paired with API). Not a public host default.
DEFAULT_RELAY_PORT = 8788


def _config_dir() -> Path:
    base = Path.home() / ".config" / "ezpeek"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _session_path() -> Path:
    return _config_dir() / "session.json"


def _settings_path() -> Path:
    return _config_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict[str, Any]) -> None:
    """Merge-update persistent settings (server URL, etc.). Survives logout."""
    cur = load_settings()
    cur.update(data)
    p = _settings_path()
    p.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)
    except Exception:
        pass


def get_saved_server_url() -> Optional[str]:
    """Last server URL the user entered (settings, then session fallback)."""
    s = load_settings().get("server_url")
    if s and str(s).strip():
        return str(s).strip().rstrip("/")
    sess = load_session()
    if sess and sess.get("server_url"):
        return str(sess["server_url"]).strip().rstrip("/")
    return None


def save_server_url(url: str) -> None:
    url = (url or "").strip().rstrip("/")
    if not url:
        return
    save_settings({"server_url": url})


def relay_endpoint_from_server_url(server_url: str) -> tuple[str, int]:
    """
    Derive reverse-proxy host from the API URL the user configured.
    e.g. http://host:8787 → (host, 8788)
    """
    p = urlparse(server_url if "://" in server_url else f"http://{server_url}")
    host = p.hostname or ""
    port = DEFAULT_RELAY_PORT
    # Optional override in settings
    settings = load_settings()
    if settings.get("relay_host"):
        host = str(settings["relay_host"])
    if settings.get("relay_port"):
        try:
            port = int(settings["relay_port"])
        except (TypeError, ValueError):
            pass
    return host, port


def save_session(data: dict[str, Any]) -> None:
    p = _session_path()
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)
    except Exception:
        pass
    # Keep server URL across future logins
    if data.get("server_url"):
        save_server_url(str(data["server_url"]))


def load_session() -> Optional[dict[str, Any]]:
    p = _session_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_session() -> None:
    """Clear auth token/session only — server URL stays in settings.json."""
    p = _session_path()
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass
