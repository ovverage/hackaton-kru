"""Reversible Windows user-session guard. Not an OS security boundary.

Hooks run on the Qt GUI thread, which owns a Windows message loop. No
processes are killed, policies changed, or keyboard content stored.
"""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import time
from pathlib import Path
from dataclasses import dataclass, asdict

from .guard_policy import blocked_key


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    pid: int
    title: str
    executable: str
    started: int

    def public(self):
        return asdict(self)


class WindowsGuard:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Ограничение окон доступно только на Windows")
        self.u = C.WinDLL("user32", use_last_error=True)
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.callback_type = C.WINFUNCTYPE(C.c_ssize_t, C.c_int, W.WPARAM, W.LPARAM)
        signatures = {
            "GetForegroundWindow": ([], W.HWND),
            "GetAncestor": ([W.HWND, W.UINT], W.HWND),
            "GetWindowThreadProcessId": ([W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            "GetWindowTextW": ([W.HWND, W.LPWSTR, C.c_int], C.c_int),
            "IsWindowVisible": ([W.HWND], W.BOOL),
            "IsWindow": ([W.HWND], W.BOOL),
            "SetForegroundWindow": ([W.HWND], W.BOOL),
            "ShowWindow": ([W.HWND, C.c_int], W.BOOL),
            "SetWindowPos": (
                [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT],
                W.BOOL,
            ),
            "SetWindowsHookExW": (
                [C.c_int, self.callback_type, W.HINSTANCE, W.DWORD],
                W.HHOOK,
            ),
            "UnhookWindowsHookEx": ([W.HHOOK], W.BOOL),
            "CallNextHookEx": ([W.HHOOK, C.c_int, W.WPARAM, W.LPARAM], C.c_ssize_t),
            "WindowFromPoint": ([W.POINT], W.HWND),
            "GetAsyncKeyState": ([C.c_int], C.c_short),
            "GetWindowRect": ([W.HWND, C.POINTER(W.RECT)], W.BOOL),
            "GetSystemMetrics": ([C.c_int], C.c_int),
            "MonitorFromWindow": ([W.HWND, W.DWORD], W.HANDLE),
            "PostMessageW": ([W.HWND, W.UINT, W.WPARAM, W.LPARAM], W.BOOL),
            "GetWindowLongW": ([W.HWND, C.c_int], W.LONG),
            "SetWindowLongW": ([W.HWND, C.c_int, W.LONG], W.LONG),
            "OpenClipboard": ([W.HWND], W.BOOL),
            "EmptyClipboard": ([], W.BOOL),
            "CloseClipboard": ([], W.BOOL),
        }
        for name, (args, restype) in signatures.items():
            f = getattr(self.u, name)
            f.argtypes, f.restype = args, restype
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.QueryFullProcessImageNameW.argtypes = [
            W.HANDLE,
            W.DWORD,
            W.LPWSTR,
            C.POINTER(W.DWORD),
        ]
        self.k.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
        self.k.GetModuleHandleW.argtypes = [W.LPCWSTR]
        self.k.GetModuleHandleW.restype = W.HMODULE
        self.hooks = []
        self.target = None
        self.desktop = False
        self.overlay_handles = set()
        self.locked = False
        self.attempted = False
        self._callbacks = []
        self.original_rect = None
        self.original_style = None
        self.browser_fullscreen_changed = False
        self.fullscreen_pending_until = None

    def monitor_rect(self, hwnd):
        class MonitorInfo(C.Structure):
            _fields_ = [('size', W.DWORD), ('monitor', W.RECT), ('work', W.RECT), ('flags', W.DWORD)]
        info = MonitorInfo()
        info.size = C.sizeof(info)
        self.u.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
        if not self.u.GetMonitorInfoW(self.u.MonitorFromWindow(hwnd, 2), C.byref(info)):
            raise OSError('Не удалось определить экран теста')
        return info.monitor

    def toggle_browser_fullscreen(self, hwnd):
        # Send F11 only to the selected browser window, never to the active desktop.
        if not self.u.PostMessageW(hwnd, 0x100, 0x7A, 0x00570001):
            raise OSError('Не удалось развернуть браузер. Запустите Qorgau и браузер с одинаковыми правами.')
        if not self.u.PostMessageW(hwnd, 0x101, 0x7A, 0xC0570001):
            raise OSError('Не удалось завершить переключение браузера в полный экран')

    def is_fullscreen(self, hwnd):
        current = W.RECT()
        if not self.u.GetWindowRect(hwnd, C.byref(current)):
            return False
        screen = self.monitor_rect(hwnd)
        return all(abs(getattr(current, edge) - getattr(screen, edge)) <= 2
                   for edge in ('left', 'top', 'right', 'bottom'))

    def fullscreen(self, target):
        screen = self.monitor_rect(target.hwnd)
        browser = Path(target.executable).name.casefold() in {
            'msedge.exe', 'chrome.exe', 'firefox.exe', 'opera.exe', 'brave.exe', 'vivaldi.exe',
        }
        already_full = self.is_fullscreen(target.hwnd)
        if not already_full:
            self.u.ShowWindow(target.hwnd, 9)
            already_full = self.is_fullscreen(target.hwnd)
        if browser:
            if not already_full:
                self.toggle_browser_fullscreen(target.hwnd)
                # Preserve the original fullscreen state even after repairing
                # a later attempt to leave fullscreen using browser chrome.
                was_full = self.original_rect is not None and all(
                    abs(getattr(self.original_rect, edge) - getattr(screen, edge)) <= 2
                    for edge in ('left', 'top', 'right', 'bottom'))
                self.browser_fullscreen_changed = not was_full
        else:
            if self.original_style is None:
                self.original_style = self.u.GetWindowLongW(target.hwnd, -16)
            self.u.SetWindowLongW(target.hwnd, -16, self.original_style & ~0x00CF0000)
            self.u.SetWindowPos(target.hwnd, -1, screen.left, screen.top,
                                screen.right - screen.left, screen.bottom - screen.top, 0x20)
        self.u.SetWindowPos(target.hwnd, -1, 0, 0, 0, 0, 0x3)
        self.u.SetForegroundWindow(target.hwnd)
        # F11 is asynchronous. Verify in the GUI timer without blocking the
        # Windows message loop that must keep the keyboard/mouse hooks alive.
        self.fullscreen_pending_until = time.monotonic() + 3

    def info(self, hwnd):
        pid = W.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, C.byref(pid))
        handle = self.k.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return None
        try:
            path, size = C.create_unicode_buffer(32768), W.DWORD(32768)
            times = [W.FILETIME() for _ in range(4)]
            if not self.k.QueryFullProcessImageNameW(handle, 0, path, C.byref(size)):
                return None
            if not self.k.GetProcessTimes(handle, *(C.byref(t) for t in times)):
                return None
            title = C.create_unicode_buffer(1024)
            self.u.GetWindowTextW(hwnd, title, 1024)
            started = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
            return WindowTarget(int(hwnd), pid.value, title.value, path.value, started)
        finally:
            self.k.CloseHandle(handle)

    def windows(self):
        found = []
        enum_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

        @enum_type
        def callback(hwnd, _):
            if self.u.IsWindowVisible(hwnd):
                target = self.info(hwnd)
                if target and target.pid != os.getpid() and target.title:
                    found.append(target)
            return True

        self.u.EnumWindows.argtypes = [enum_type, W.LPARAM]
        self.u.EnumWindows(callback, 0)
        return found

    def valid(self, target):
        actual = self.info(target.hwnd) if self.u.IsWindow(target.hwnd) else None
        return bool(
            actual
            and (actual.pid, actual.started, actual.executable.casefold())
            == (target.pid, target.started, target.executable.casefold())
        )

    def allowed(self, hwnd):
        root = self.u.GetAncestor(
            hwnd, 2
        )  # GA_ROOT: other windows of same app are NOT allowed
        return (
            root in self.overlay_handles
            if self.locked
            else self.desktop or bool(self.target and root == self.target.hwnd)
        )

    def start(self, target=None, *, desktop=False):
        try:
            self._start(target, desktop=desktop)
        except (OSError, ValueError):
            # A failed fullscreen request must not leave a target looking
            # active on the next UI tick while neither input hook exists.
            self.stop()
            raise

    def _start(self, target=None, *, desktop=False):
        if not desktop and (target is None or not self.valid(target)):
            raise OSError("Выбранное окно закрыто. Выберите его заново.")
        self.stop()
        self.target = target
        self.desktop = desktop
        self.locked = False
        self.attempted = False
        self.teacher_requested = False
        if target:
            self.original_rect = W.RECT()
            if not self.u.GetWindowRect(target.hwnd, C.byref(self.original_rect)):
                raise OSError("Не удалось прочитать положение выбранного окна")
            self.fullscreen(target)

        class Keyboard(C.Structure):
            _fields_ = [
                ("vk", W.DWORD),
                ("scan", W.DWORD),
                ("flags", W.DWORD),
                ("time", W.DWORD),
                ("extra", C.c_size_t),
            ]

        class Mouse(C.Structure):
            _fields_ = [
                ("point", W.POINT),
                ("data", W.DWORD),
                ("flags", W.DWORD),
                ("time", W.DWORD),
                ("extra", C.c_size_t),
            ]

        @self.callback_type
        def keyboard(code, message, data):
            if code >= 0:
                vk = C.cast(data, C.POINTER(Keyboard)).contents.vk

                def down(key):
                    return bool(self.u.GetAsyncKeyState(key) & 0x8000)

                if vk == 0x51 and down(0x11) and down(0x12):  # Ctrl+Alt+Q
                    self.teacher_requested = True
                    return 1
                if self.desktop and not self.locked:
                    return self.u.CallNextHookEx(None, code, message, data)
                if not self.allowed(self.u.GetForegroundWindow()) or blocked_key(
                    vk,
                    ctrl=down(0x11),
                    alt=down(0x12),
                    shift=down(0x10),
                    locked=self.locked,
                ):
                    self.attempted = True
                    return 1
            return self.u.CallNextHookEx(None, code, message, data)

        @self.callback_type
        def mouse(code, message, data):
            if code >= 0 and message != 0x200:  # never stop pointer movement
                point = C.cast(data, C.POINTER(Mouse)).contents.point
                if not self.allowed(self.u.WindowFromPoint(point)) or (
                    not self.locked and not self.desktop
                    and message in (0x204, 0x205, 0x207, 0x208, 0x20B, 0x20C)
                ):
                    self.attempted = True
                    return 1
            return self.u.CallNextHookEx(None, code, message, data)

        self._callbacks = [keyboard, mouse]
        for kind, callback in ((13, keyboard), (14, mouse)):
            hook = self.u.SetWindowsHookExW(
                kind, callback, self.k.GetModuleHandleW(None), 0
            )
            if not hook:
                self.stop()
                raise OSError("Windows не разрешила включить защиту ввода")
            self.hooks.append(hook)
        if target:
            self.u.SetWindowPos(target.hwnd, -1, 0, 0, 0, 0, 0x3)
            self.u.SetForegroundWindow(target.hwnd)

    def tick(self, *, locked=False, overlays=()):
        previous = self.locked
        self.locked = locked
        self.overlay_handles = set(overlays)
        if self.target and self.valid(self.target) and previous != locked:
            self.u.SetWindowPos(self.target.hwnd, -2 if locked else -1, 0, 0, 0, 0, 0x13)
            if not locked:
                self.u.SetForegroundWindow(self.target.hwnd)
        if getattr(self, "teacher_requested", False):
            self.teacher_requested = False
            return "TEACHER_REQUEST"
        if len(self.hooks) != 2:
            return "GUARD_UNAVAILABLE"
        target_closed = not self.desktop and (not self.target or not self.valid(self.target))
        if target_closed and not locked:
            return "TARGET_CLOSED"
        if self.u.GetSystemMetrics(0x1000):  # SM_REMOTESESSION
            return "REMOTE_SESSION"
        if not locked and self.target and not target_closed:
            if self.is_fullscreen(self.target.hwnd):
                self.fullscreen_pending_until = None
            elif self.fullscreen_pending_until is None:
                self.fullscreen(self.target)
                self.attempted = True
            elif time.monotonic() >= self.fullscreen_pending_until:
                return "GUARD_UNAVAILABLE"
        if (locked or not self.desktop) and self.u.OpenClipboard(None):
            try:
                self.u.EmptyClipboard()
            finally:
                self.u.CloseClipboard()
        foreground = self.u.GetForegroundWindow()
        if not self.allowed(foreground):
            self.attempted = True
            focus = (
                next(iter(self.overlay_handles), None) if locked else self.target.hwnd
            )
            if focus:
                self.u.SetForegroundWindow(focus)
        if target_closed:
            return "TARGET_CLOSED"
        if self.attempted:
            self.attempted = False
            return "ENVIRONMENT_ATTEMPT"
        return None

    def stop(self):
        for hook in self.hooks:
            self.u.UnhookWindowsHookEx(hook)
        self.hooks.clear()
        self._callbacks.clear()
        if self.target and self.valid(self.target):
            if self.browser_fullscreen_changed:
                try:
                    if self.is_fullscreen(self.target.hwnd):
                        self.toggle_browser_fullscreen(self.target.hwnd)
                except OSError:
                    pass  # Input hooks must still be released if the browser stops responding.
            if self.original_style is not None:
                self.u.SetWindowLongW(self.target.hwnd, -16, self.original_style)
            self.u.SetWindowPos(self.target.hwnd, -2, 0, 0, 0, 0, 0x13)
            if self.original_rect:
                r = self.original_rect
                self.u.ShowWindow(self.target.hwnd, 9)
                self.u.SetWindowPos(
                    self.target.hwnd,
                    None,
                    r.left,
                    r.top,
                    r.right - r.left,
                    r.bottom - r.top,
                    0x34,
                )
        self.target = None
        self.desktop = False
        self.locked = False
        self.attempted = False
        self.original_rect = None
        self.original_style = None
        self.browser_fullscreen_changed = False
        self.fullscreen_pending_until = None
