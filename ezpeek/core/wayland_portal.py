from __future__ import annotations

import os
from typing import Any, Optional

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAP_IFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_IFACE = "org.freedesktop.portal.Request"


class WaylandPortalError(RuntimeError):
    pass


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _require_dbus_and_glib():
    try:
        import dbus  # type: ignore
        from dbus.mainloop.glib import DBusGMainLoop  # type: ignore
        from gi.repository import GLib  # type: ignore
    except ModuleNotFoundError as e:
        raise WaylandPortalError(
            "Missing Wayland portal dependencies. On Arch install: "
            "`python-dbus python-gobject glib2` (and a portal backend like xdg-desktop-portal-gnome)."
        ) from e
    return dbus, DBusGMainLoop, GLib


def _call_and_wait(bus, request_path, timeout_ms: int = 30_000) -> dict:
    """
    Wait for org.freedesktop.portal.Request::Response using a GLib main loop.
    """
    dbus, _DBusGMainLoop, GLib = _require_dbus_and_glib()

    loop = GLib.MainLoop()
    out: dict[str, Any] = {"done": False}

    def on_response(response: int, results: dict):
        out["response"] = int(response)
        out["results"] = dict(results)
        out["done"] = True
        try:
            loop.quit()
        except Exception:
            pass

    obj = bus.get_object(PORTAL_BUS, request_path)
    obj.connect_to_signal("Response", on_response, dbus_interface=REQUEST_IFACE)

    # timeout protection
    def on_timeout():
        if not out.get("done"):
            out["response"] = 2
            out["results"] = {}
            out["error"] = "Timed out waiting for portal Response"
            try:
                loop.quit()
            except Exception:
                pass
        return False  # don't repeat

    GLib.timeout_add(timeout_ms, on_timeout)
    loop.run()

    if out.get("response") != 0:
        reason = out.get("error") or f"Portal response={out.get('response')}"
        raise WaylandPortalError(str(reason))

    return {"response": out["response"], "results": out.get("results", {})}


def request_pipewire_node_id(app_id: str = "ezpeek") -> int:
    if not _is_wayland():
        raise WaylandPortalError("Not a Wayland session")

    dbus, DBusGMainLoop, _GLib = _require_dbus_and_glib()

    # Attach glib main loop so dbus signals work
    DBusGMainLoop(set_as_default=True)

    bus = dbus.SessionBus()
    portal = bus.get_object(PORTAL_BUS, PORTAL_PATH)
    sc = dbus.Interface(portal, SCREENCAP_IFACE)

    # 1) CreateSession
    create_req = sc.CreateSession(
        {"session_handle_token": f"{app_id}_session"},
        dbus_interface=SCREENCAP_IFACE,
    )
    create = _call_and_wait(bus, create_req)
    session_handle = create["results"].get("session_handle")
    if not session_handle:
        raise WaylandPortalError("Portal did not return a session_handle")

    # 2) SelectSources
    select_req = sc.SelectSources(
        session_handle,
        {
            "types": dbus.UInt32(1),  # monitor
            "multiple": False,
            "cursor_mode": dbus.UInt32(2),  # embedded cursor
            "handle_token": f"{app_id}_select",
        },
        dbus_interface=SCREENCAP_IFACE,
    )
    _call_and_wait(bus, select_req)

    # 3) Start
    start_req = sc.Start(
        session_handle,
        "",
        {"handle_token": f"{app_id}_start"},
        dbus_interface=SCREENCAP_IFACE,
    )
    started = _call_and_wait(bus, start_req)

    streams = started["results"].get("streams")
    if not streams:
        raise WaylandPortalError("Portal returned no streams")

    node_id = int(streams[0][0])
    return node_id