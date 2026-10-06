"""Keep the installer from replacing/removing a running Windows client.

This is an installation-safety marker, not an anti-tampering boundary. Windows
releases the handle when the process exits; do not close it while Qt is running.
"""

import os

INSTALL_MUTEX = r"Local\Qorgau.Student.Running"
_handle = None
_kernel = None


def hold_installation_mutex():
    global _handle, _kernel
    if os.name != "nt":
        return None
    if _handle is None:
        import ctypes
        from ctypes import wintypes

        _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        _kernel.CreateMutexW.restype = wintypes.HANDLE
        _handle = _kernel.CreateMutexW(None, False, INSTALL_MUTEX)
        if not _handle:
            raise ctypes.WinError(ctypes.get_last_error())
    return _handle
