"""Exercise real browser chrome only on the disposable Windows CI desktop."""

import os
import time
from pathlib import Path

import pytest


def test_selected_edge_window_enters_and_leaves_fullscreen(tmp_path):
    if os.name != 'nt' or os.getenv('QORGAU_TEST_INPUT_HOOKS') != '1':
        pytest.skip('Real browser window test is restricted to disposable Windows CI')
    import ctypes as c
    import subprocess
    from ctypes import wintypes as w
    from agent.windows_guard import WindowsGuard

    executable = next((Path(os.environ.get(root, 'C:/')) / 'Microsoft/Edge/Application/msedge.exe'
                       for root in ('ProgramFiles(x86)', 'ProgramFiles')
                       if (Path(os.environ.get(root, 'C:/')) / 'Microsoft/Edge/Application/msedge.exe').is_file()), None)
    assert executable, 'Windows runner must provide Edge for the fullscreen acceptance test'
    title = 'Qorgau fullscreen acceptance ' + tmp_path.name
    page = tmp_path / 'exam.html'
    page.write_text(f'<title>{title}</title><h1>Disposable fullscreen test</h1>', encoding='utf-8')
    process = subprocess.Popen([str(executable), '--no-first-run', '--no-default-browser-check',
                                '--user-data-dir=' + str(tmp_path / 'profile'),
                                '--new-window', page.as_uri()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    guard = WindowsGuard()
    target = None

    def bounds(hwnd):
        rect = w.RECT()
        assert guard.u.GetWindowRect(hwnd, c.byref(rect))
        return tuple(getattr(rect, edge) for edge in ('left', 'top', 'right', 'bottom'))

    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            target = next((win for win in guard.windows() if title in win.title), None)
            if target:
                break
            time.sleep(.2)
        assert target, 'Edge did not open the test page'
        guard.target = target
        guard.original_rect = w.RECT()
        guard.u.GetWindowRect(target.hwnd, c.byref(guard.original_rect))
        original = bounds(target.hwnd)
        screen = guard.monitor_rect(target.hwnd)
        expected = tuple(getattr(screen, edge) for edge in ('left', 'top', 'right', 'bottom'))
        guard.fullscreen(target)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and bounds(target.hwnd) != expected:
            time.sleep(.1)
        assert bounds(target.hwnd) == expected
        assert guard.browser_fullscreen_changed
        guard.stop()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and bounds(target.hwnd) != original:
            time.sleep(.1)
        assert bounds(target.hwnd) == original
    finally:
        guard.stop()
        if target:
            guard.u.PostMessageW(target.hwnd, 0x10, 0, 0)  # WM_CLOSE: this test's window only.
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
