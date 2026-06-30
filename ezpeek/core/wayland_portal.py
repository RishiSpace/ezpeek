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


# ==================== Remote Desktop (for input injection on pure Wayland) ====================
# This allows emulating mouse/keyboard without X11 or root privileges.
# User will see a permission dialog when starting hosting on Wayland.

REMOTE_DESKTOP_IFACE = "org.freedesktop.portal.RemoteDesktop"

# Simple evdev button codes (common)
EVDEV_BTN_LEFT = 0x110
EVDEV_BTN_RIGHT = 0x111
EVDEV_BTN_MIDDLE = 0x112


class WaylandRemoteInput:
    """
    Manages a RemoteDesktop portal session for sending input events on Wayland.
    Must be started once per hosting session.
    """

    def __init__(self):
        self.dbus_mod = None
        self.bus = None
        self.session_handle = None
        self.rd_iface = None
        self._active = False

    def start_session(self, app_id: str = "ezpeek") -> None:
        if self._active:
            return
        if not _is_wayland():
            raise WaylandPortalError("Remote input only supported on native Wayland")

        dbus_mod, DBusGMainLoop, GLib = _require_dbus_and_glib()
        self.dbus_mod = dbus_mod
        DBusGMainLoop(set_as_default=True)

        self.bus = self.dbus_mod.SessionBus()
        portal = self.bus.get_object(PORTAL_BUS, PORTAL_PATH)
        rd = self.dbus_mod.Interface(portal, REMOTE_DESKTOP_IFACE)

        # Create session
        create_req = rd.CreateSession(
            {"session_handle_token": f"{app_id}_rd_session"},
            dbus_interface=REMOTE_DESKTOP_IFACE,
        )
        create = _call_and_wait(self.bus, create_req)
        self.session_handle = create["results"].get("session_handle")
        if not self.session_handle:
            raise WaylandPortalError("No session_handle from RemoteDesktop")

        d = self.dbus_mod
        # Select keyboard + pointer
        select_req = rd.SelectDevices(
            self.session_handle,
            {
                "types": d.UInt32(1 | 2),  # keyboard + pointer
                "handle_token": f"{app_id}_rd_devices",
            },
            dbus_interface=REMOTE_DESKTOP_IFACE,
        )
        _call_and_wait(self.bus, select_req)

        # Start the session (this triggers the permission prompt for "remote control")
        start_req = rd.Start(
            self.session_handle,
            "",
            {"handle_token": f"{app_id}_rd_start"},
            dbus_interface=REMOTE_DESKTOP_IFACE,
        )
        started = _call_and_wait(self.bus, start_req)

        # Get the actual session object path if provided, or use handle
        self.rd_iface = d.Interface(
            self.bus.get_object(PORTAL_BUS, self.session_handle),
            REMOTE_DESKTOP_IFACE,
        )
        self._active = True

    def _notify(self, method: str, options: dict, arg: dict) -> None:
        if not self._active or not self.rd_iface:
            return
        d = self.dbus_mod
        try:
            # Use typed dicts so dbus can guess signature
            opts = d.Dictionary(options or {}, signature="sv")
            data = d.Dictionary(arg or {}, signature="sv")
            getattr(self.rd_iface, method)(self.session_handle, opts, data, dbus_interface=REMOTE_DESKTOP_IFACE)
        except Exception as e:
            # Debug once if needed
            if os.environ.get("EZPEEK_DEBUG"):
                print("[ezpeek] portal notify error:", e)
            pass  # best effort

    def send_mouse_move(self, x: int, y: int, absolute: bool = False) -> None:
        # Relative motion for now. For remote control, the caller (control protocol + viewer)
        # should send deltas or we track state. Absolute support can be added with screen size.
        d = self.dbus_mod
        self._notify("NotifyPointerMotion", {}, {"dx": d.Double(x), "dy": d.Double(y)})

    def send_pointer_button(self, button: int, state: bool) -> None:
        d = self.dbus_mod
        evdev_btn = {1: EVDEV_BTN_LEFT, 2: EVDEV_BTN_MIDDLE, 3: EVDEV_BTN_RIGHT}.get(button, EVDEV_BTN_LEFT)
        self._notify("NotifyPointerButton", {}, {"button": d.UInt32(evdev_btn), "state": d.Boolean(state)})

    def send_key(self, keycode: int, state: bool) -> None:
        d = self.dbus_mod
        # keycode = Linux evdev (from mapping in InputController)
        self._notify("NotifyKeyboardKeycode", {}, {"keycode": d.UInt32(keycode), "state": d.Boolean(state)})

    def close(self) -> None:
        self._active = False
        # Portal sessions are cleaned by the portal when app exits or we can stop.
        self.rd_iface = None
        self.session_handle = None