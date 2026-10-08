"""Switch input language on one permitted window, without opening Windows UI."""

import ctypes as C
from ctypes import wintypes as W
import os


def next_layout(layouts, current):
    layouts = list(dict.fromkeys(layouts))
    if not layouts:
        return None
    return layouts[(layouts.index(current) + 1) % len(layouts)] if current in layouts else layouts[0]


def switch_layout(hwnd):
    if os.name != 'nt':
        return False
    user = C.WinDLL('user32', use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    user.GetWindowThreadProcessId.restype = W.DWORD
    user.GetKeyboardLayout.argtypes = [W.DWORD]
    user.GetKeyboardLayout.restype = W.HANDLE
    user.GetKeyboardLayoutList.argtypes = [C.c_int, C.POINTER(W.HANDLE)]
    user.GetKeyboardLayoutList.restype = C.c_int
    user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user.PostMessageW.restype = W.BOOL
    thread = user.GetWindowThreadProcessId(hwnd, None)
    if not thread:
        return False
    count = user.GetKeyboardLayoutList(0, None)
    layouts = (W.HANDLE * count)()
    count = user.GetKeyboardLayoutList(count, layouts)
    selected = next_layout(list(layouts)[:count], user.GetKeyboardLayout(thread))
    return bool(selected and user.PostMessageW(hwnd, 0x50, 0, selected))


class LayoutShortcut:
    """Consume Win+Space ourselves; never expose Win to the shell or escape keys."""

    def __init__(self):
        self.windows = set()
        self.space_down = False

    def handle(self, vk, pressed):
        if vk in (0x5B, 0x5C):
            if pressed:
                self.windows.add(vk)
            else:
                self.windows.discard(vk)
            return 'consume'
        if vk == 0x20 and (self.windows or self.space_down):
            switch = pressed and not self.space_down and bool(self.windows)
            self.space_down = pressed
            return 'switch' if switch else 'consume'
        return 'blocked' if self.windows else None
