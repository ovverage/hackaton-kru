"""Read-only detection of known running remote-control executables."""
import os

NAMES = {
    'anydesk.exe': 'AnyDesk', 'rustdesk.exe': 'RustDesk',
    'teamviewer.exe': 'TeamViewer', 'teamviewer_service.exe': 'TeamViewer',
    'tv_w32.exe': 'TeamViewer', 'tv_x64.exe': 'TeamViewer',
    'aeroadmin.exe': 'AeroAdmin', 'r_server.exe': 'Radmin',
    'winvnc.exe': 'VNC', 'tvnserver.exe': 'TightVNC', 'vncserver.exe': 'VNC',
    'remoting_host.exe': 'Chrome Remote Desktop', 'dwagent.exe': 'DWService',
    'dwsession.exe': 'DWService', 'parsecd.exe': 'Parsec',
    'nxserver.exe': 'NoMachine', 'nxnode.exe': 'NoMachine',
    'quickassist.exe': 'Быстрая помощь Windows', 'meshagent.exe': 'MeshCentral',
    'screenconnect.clientservice.exe': 'ConnectWise ScreenConnect',
}


def classify(names):
    return sorted({NAMES[name.casefold()] for name in names if name.casefold() in NAMES})


def process_inventory():
    if os.name != 'nt':
        return {}
    import ctypes as c
    from ctypes import wintypes as w

    class Entry(c.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                    ('heap', c.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                    ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD),
                    ('exe', w.WCHAR * 260)]

    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == c.c_void_p(-1).value:
        raise OSError('Не удалось проверить запущенные программы.')
    found = {}
    try:
        entry = Entry()
        entry.size = c.sizeof(entry)
        present = kernel.Process32FirstW(handle, c.byref(entry))
        while present:
            found[entry.pid] = (entry.parent, entry.exe)
            present = kernel.Process32NextW(handle, c.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    return found


def running_tools():
    return classify(item[1] for item in process_inventory().values())
