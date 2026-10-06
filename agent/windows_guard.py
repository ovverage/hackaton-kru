"""Reversible Windows user-session guard. Not an OS security boundary.

Hooks run on the Qt GUI thread, which owns a Windows message loop. No
processes are killed, policies changed, or keyboard content stored.
"""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
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
        self.overlay_handles = set()
        self.locked = False
        self.attempted = False
        self._callbacks = []
        self.original_rect = None

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
            else bool(self.target and root == self.target.hwnd)
        )

    def start(self, target):
        if not self.valid(target):
            raise OSError("Выбранное окно закрыто. Выберите его заново.")
        self.stop()
        self.target = target
        self.original_rect = W.RECT()
        self.u.GetWindowRect(target.hwnd, C.byref(self.original_rect))

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
                    not self.locked
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
        self.u.ShowWindow(target.hwnd, 3)
        self.u.SetWindowPos(target.hwnd, -1, 0, 0, 0, 0, 0x3)
        self.u.SetForegroundWindow(target.hwnd)

    def tick(self, *, locked=False, overlays=()):
        self.locked = locked
        self.overlay_handles = set(overlays)
        if not self.target or not self.valid(self.target):
            return "TARGET_CLOSED"
        if self.u.GetSystemMetrics(0x1000):  # SM_REMOTESESSION
            return "REMOTE_SESSION"
        if self.u.OpenClipboard(None):
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
                    0x14,
                )
        self.target = None
        self.original_rect = None
