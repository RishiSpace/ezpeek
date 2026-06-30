"""
Cross-platform input controller for remote control.

Production-oriented:
- Windows: pure ctypes SendInput (no extra deps)
- Linux: prefers xdotool (works on X11 and many Wayland sessions via compat layer). Falls back gracefully.
- Provides normalized mouse (absolute screen or relative) and key events.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from typing import Optional

from .wayland_portal import WaylandRemoteInput, WaylandPortalError, _is_wayland


class InputController:
    """Sends mouse and keyboard input to the local machine (used by host when receiving remote control)."""

    def __init__(self):
        self.sys = platform.system().lower()
        self._has_xdotool = shutil.which("xdotool") is not None if self.sys == "linux" else False
        self._screen_w: Optional[int] = None
        self._screen_h: Optional[int] = None
        self._wl_input: Optional["WaylandRemoteInput"] = None
        self._last_x = 0
        self._last_y = 0
        self._is_wl = self.sys == "linux" and _is_wayland()

    # ---------- Mouse ----------
    def send_mouse_move(self, x: int, y: int, absolute: bool = True) -> bool:
        """Move mouse. On Windows absolute is 0-65535 normalized; we handle internally."""
        if self.sys == "windows":
            return self._win_mouse_move(x, y, absolute)
        elif self.sys == "linux":
            return self._linux_mouse_move(x, y, absolute)
        # darwin stub
        return False

    def send_click(self, button: int = 1, down: bool = True, double: bool = False) -> bool:
        """Click. button: 1=left, 2=middle, 3=right, 4/5 = wheel."""
        if self.sys == "windows":
            return self._win_click(button, down)
        elif self.sys == "linux":
            return self._linux_click(button, down, double)
        return False

    def send_mouse_wheel(self, delta: int) -> bool:
        """Scroll wheel. Positive = up."""
        if self.sys == "windows":
            return self._win_wheel(delta)
        elif self.sys == "linux":
            btn = 4 if delta > 0 else 5
            return self._linux_click(btn, down=True) and self._linux_click(btn, down=False)
        return False

    # ---------- Keyboard ----------
    def send_key(self, key: str, down: bool = True) -> bool:
        """
        Send key event.
        key: keysym like 'a', 'Return', 'Shift_L', 'Control_L', 'F1', or single char.
        For modifiers use down/up pairs when needed.
        """
        if self.sys == "windows":
            return self._win_key(key, down)
        elif self.sys == "linux":
            return self._linux_key(key, down)
        return False

    def send_text(self, text: str) -> bool:
        """Type a string (best effort)."""
        for ch in text:
            if not self.send_key(ch):
                return False
            time.sleep(0.005)
        return True

    # ---------- Internals: Windows (ctypes) ----------
    def _win_mouse_move(self, x: int, y: int, absolute: bool) -> bool:
        import ctypes
        from ctypes import wintypes

        # Normalize to 0..65535 for absolute
        if absolute:
            # Try to get real screen res for accurate mapping
            if self._screen_w is None:
                try:
                    user32 = ctypes.windll.user32
                    self._screen_w = user32.GetSystemMetrics(0)
                    self._screen_h = user32.GetSystemMetrics(1)
                except Exception:
                    self._screen_w, self._screen_h = 1920, 1080

            w = self._screen_w or 1920
            h = self._screen_h or 1080
            nx = int(x * 65535 / max(1, w))
            ny = int(y * 65535 / max(1, h))
        else:
            nx, ny = x, y

        MOUSEEVENTF_MOVE = 0x0001
        MOUSEEVENTF_ABSOLUTE = 0x8000 if absolute else 0

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [
                ("type", wintypes.DWORD),
                ("mi", MOUSEINPUT),
            ]

        user32 = ctypes.windll.user32
        inp = INPUT()
        inp.type = 0  # INPUT_MOUSE
        inp.mi = MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        return True

    def _win_click(self, button: int, down: bool) -> bool:
        import ctypes
        from ctypes import wintypes

        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        MOUSEEVENTF_RIGHTDOWN = 0x0008
        MOUSEEVENTF_RIGHTUP = 0x0010
        MOUSEEVENTF_MIDDLEDOWN = 0x0020
        MOUSEEVENTF_MIDDLEUP = 0x0040

        flags = 0
        if button == 1:
            flags = MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
        elif button == 3:
            flags = MOUSEEVENTF_RIGHTDOWN if down else MOUSEEVENTF_RIGHTUP
        elif button == 2:
            flags = MOUSEEVENTF_MIDDLEDOWN if down else MOUSEEVENTF_MIDDLEUP
        else:
            return False

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

        user32 = ctypes.windll.user32
        inp = INPUT(type=0)
        inp.mi = MOUSEINPUT(0, 0, 0, flags, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        return True

    def _win_wheel(self, delta: int) -> bool:
        import ctypes
        from ctypes import wintypes
        MOUSEEVENTF_WHEEL = 0x0800
        WHEEL_DELTA = 120

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

        user32 = ctypes.windll.user32
        inp = INPUT(type=0)
        inp.mi = MOUSEINPUT(0, 0, int(delta * WHEEL_DELTA), MOUSEEVENTF_WHEEL, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        return True

    _WIN_VK = {
        "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
        "return": 0x0D, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
        "backspace": 0x08, "space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
        "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    }

    def _win_key(self, key: str, down: bool) -> bool:
        import ctypes
        from ctypes import wintypes

        KEYEVENTF_KEYDOWN = 0
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004

        vk = 0
        uni = 0
        k = key.lower() if len(key) > 1 else key

        if k in self._WIN_VK:
            vk = self._WIN_VK[k]
        elif len(k) == 1:
            # unicode path
            uni = ord(k)
        else:
            # try common names
            vk = self._WIN_VK.get(k, 0)

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("ki", KEYBDINPUT)]

        user32 = ctypes.windll.user32
        inp = INPUT(type=1)  # INPUT_KEYBOARD
        if uni:
            inp.ki = KEYBDINPUT(0, uni, KEYEVENTF_UNICODE | (0 if down else KEYEVENTF_KEYUP), 0, None)
        else:
            inp.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYDOWN if down else KEYEVENTF_KEYUP, 0, None)
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        return True

    # ---------- Internals: Linux (Wayland portal preferred, xdotool for X11) ----------
    def _init_wayland_input(self) -> bool:
        if self._wl_input:
            return self._wl_input._active
        if not self._is_wl:
            return False
        try:
            self._wl_input = WaylandRemoteInput()
            self._wl_input.start_session()
            return True
        except (WaylandPortalError, Exception) as e:
            # Permission denied or portal not available → fall back (or fail loudly)
            print(f"[ezpeek] Wayland remote input portal failed to start: {e}")
            self._wl_input = None
            return False

    def _linux_mouse_move(self, x: int, y: int, absolute: bool = True) -> bool:
        if self._is_wl and self._init_wayland_input():
            # Send delta. For absolute from remote viewer we compute simple delta.
            dx = x - self._last_x if absolute else x
            dy = y - self._last_y if absolute else y
            self._wl_input.send_mouse_move(dx, dy)
            if absolute:
                self._last_x, self._last_y = x, y
            return True

        if self._has_xdotool:
            cmd = ["xdotool", "mousemove" if absolute else "mousemove_relative", str(x), str(y)]
            try:
                subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
                return True
            except Exception:
                pass
        return False

    def _linux_click(self, button: int, down: bool, double: bool = False) -> bool:
        if self._is_wl and self._init_wayland_input():
            self._wl_input.send_pointer_button(button, down)
            if double and down:
                time.sleep(0.05)
                self._wl_input.send_pointer_button(button, False)
                time.sleep(0.05)
                self._wl_input.send_pointer_button(button, True)
                time.sleep(0.05)
                self._wl_input.send_pointer_button(button, False)
            return True

        if not self._has_xdotool:
            return False
        action = "mousedown" if down else "mouseup"
        try:
            subprocess.run(["xdotool", action, str(button)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            if double and down:
                subprocess.run(["xdotool", "click", "--repeat", "2", str(button)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            return True
        except Exception:
            return False

    def _linux_key(self, key: str, down: bool) -> bool:
        if self._is_wl and self._init_wayland_input():
            # Map common symbolic keys to evdev keycodes (Linux input-event-codes.h)
            keycode = self._key_to_evdev(key)
            self._wl_input.send_key(keycode, down)
            return True

        if not self._has_xdotool:
            return False
        # xdotool keydown / keyup or just "key" for tap
        try:
            if down:
                subprocess.run(["xdotool", "keydown", key], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            else:
                subprocess.run(["xdotool", "keyup", key], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)
            return True
        except Exception:
            return False

    def _key_to_evdev(self, key: str) -> int:
        """Very basic map for common keys. Expand as needed."""
        k = key.lower().strip()
        mapping = {
            "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
            "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
            "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
            "y": 21, "z": 44, "0": 11, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6,
            "6": 7, "7": 8, "8": 9, "9": 10,
            "return": 28, "enter": 28, "tab": 15, "esc": 1, "escape": 1,
            "backspace": 14, "space": 57, "left": 105, "right": 106, "up": 103, "down": 108,
            "shift_l": 42, "shift": 42, "control_l": 29, "control": 29, "alt_l": 56, "alt": 56,
            "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63,
        }
        return mapping.get(k, 57)  # default to space if unknown

    # Convenience combined
    def click_at(self, x: int, y: int, button: int = 1) -> bool:
        ok = self.send_mouse_move(x, y, absolute=True)
        time.sleep(0.02)
        ok = ok and self.send_click(button, down=True)
        time.sleep(0.02)
        ok = ok and self.send_click(button, down=False)
        return ok

    def close(self) -> None:
        if self._wl_input:
            try:
                self._wl_input.close()
            except Exception:
                pass
        self._wl_input = None
