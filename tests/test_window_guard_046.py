"""Exercise selected-window protection without installing any real OS hooks."""

import ctypes as C
from ctypes import wintypes as W
from unittest.mock import Mock, patch

import pytest

from agent.guard_policy import blocked_key
from agent.windows_guard import WindowsGuard, WindowTarget


def fake_guard():
    guard = WindowsGuard.__new__(WindowsGuard)
    guard.u = Mock()
    guard.k = Mock()
    guard.callback_type = lambda callback: callback
    guard.hooks = []
    guard._callbacks = []
    guard.target = None
    guard.desktop = False
    guard.locked = False
    guard.attempted = False
    guard.teacher_requested = False
    guard.overlay_handles = set()
    guard.original_rect = None
    guard.original_style = None
    guard.browser_fullscreen_changed = False
    guard.fullscreen_pending_until = None
    guard.valid = Mock(return_value=True)
    guard.u.GetWindowRect.return_value = True
    guard.u.SetWindowsHookExW.side_effect = [101, 102]
    guard.u.GetForegroundWindow.return_value = 42
    guard.u.GetAncestor.side_effect = lambda hwnd, _: hwnd
    guard.u.GetSystemMetrics.return_value = 0
    guard.u.OpenClipboard.return_value = False
    guard.u.CallNextHookEx.return_value = 0
    return guard


def test_failed_fullscreen_cannot_leave_an_active_target_without_hooks():
    guard = fake_guard()
    guard.fullscreen = Mock(side_effect=OSError("F11 request failed"))
    with pytest.raises(OSError, match="F11"):
        guard.start(WindowTarget(42, 1, "Exam", "chrome.exe", 1))
    assert guard.target is None
    assert not guard.hooks
    assert not guard._callbacks
    assert guard.tick() == "GUARD_UNAVAILABLE"


def test_partial_hook_install_failure_releases_first_hook_and_target():
    guard = fake_guard()
    guard.fullscreen = Mock()
    guard.u.SetWindowsHookExW.side_effect = [101, 0]
    with pytest.raises(OSError, match="защиту ввода"):
        guard.start(WindowTarget(42, 1, "Exam", "chrome.exe", 1))
    guard.u.UnhookWindowsHookEx.assert_called_once_with(101)
    assert guard.target is None
    assert guard.hooks == []


@pytest.mark.parametrize("vk, modifiers", [
    (0x7B, {}),  # F12
    (0x49, {"ctrl": True, "shift": True}),
    (0x4A, {"ctrl": True, "shift": True}),
    (0x43, {"ctrl": True, "shift": True}),
    (0x55, {"ctrl": True}),  # View source
    (0x1B, {}), (0x7A, {}),  # Esc / F11
    (0x09, {"alt": True}), (0x73, {"alt": True}),
    (0x5B, {}), (0x5C, {}),
    (0x79, {"shift": True}),  # Keyboard context menu -> Inspect
])
def test_selected_browser_escape_and_developer_shortcuts_are_blocked(vk, modifiers):
    assert blocked_key(vk, **modifiers)


def test_installed_callback_blocks_f12_and_other_windows_but_preserves_teacher_call():
    guard = fake_guard()
    guard.fullscreen = Mock()
    guard.start(WindowTarget(42, 1, "Exam", "chrome.exe", 1))
    keyboard = guard._callbacks[0]

    class Key(C.Structure):
        _fields_ = [("vk", W.DWORD), ("scan", W.DWORD), ("flags", W.DWORD),
                    ("time", W.DWORD), ("extra", C.c_size_t)]

    held = set()
    guard.u.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key in held else 0
    assert keyboard(0, 0x100, C.addressof(Key(0x7B))) == 1
    assert guard.attempted
    guard.attempted = False
    assert keyboard(0, 0x100, C.addressof(Key(0x41))) == 0
    guard.u.GetForegroundWindow.return_value = 99
    assert keyboard(0, 0x100, C.addressof(Key(0x41))) == 1
    held.update([0x11, 0x12])
    assert keyboard(0, 0x100, C.addressof(Key(0x51))) == 1
    assert guard.teacher_requested
    assert guard.tick() == "TEACHER_REQUEST"
    guard.stop()
    assert not guard.hooks


def test_fullscreen_is_verified_after_async_f11_and_repaired_if_browser_exits():
    guard = fake_guard()
    guard.target = WindowTarget(42, 1, "Exam", "chrome.exe", 1)
    guard.hooks = [101, 102]
    guard.is_fullscreen = Mock(return_value=False)
    guard.fullscreen_pending_until = 13
    with patch("agent.windows_guard.time.monotonic", return_value=12):
        assert guard.tick() is None
    with patch("agent.windows_guard.time.monotonic", return_value=13):
        assert guard.tick() == "GUARD_UNAVAILABLE"
    guard.is_fullscreen.return_value = True
    assert guard.tick() is None
    assert guard.fullscreen_pending_until is None
    guard.is_fullscreen.return_value = False
    guard.fullscreen = Mock()
    assert guard.tick() == "ENVIRONMENT_ATTEMPT"
    guard.fullscreen.assert_called_once_with(guard.target)


def test_foreign_window_loses_focus_and_locked_screen_keeps_teacher_input():
    guard = fake_guard()
    guard.target = WindowTarget(42, 1, "Exam", "chrome.exe", 1)
    guard.hooks = [101, 102]
    guard.is_fullscreen = Mock(return_value=True)
    guard.u.GetForegroundWindow.return_value = 99
    assert guard.tick() == "ENVIRONMENT_ATTEMPT"
    guard.u.SetForegroundWindow.assert_called_with(42)
    assert guard.tick(locked=True, overlays=[77]) == "ENVIRONMENT_ATTEMPT"
    guard.u.SetForegroundWindow.assert_called_with(77)
    assert guard.allowed(77)
    assert not guard.allowed(42)


def test_stop_does_not_enter_fullscreen_when_browser_already_left_it():
    guard = fake_guard()
    guard.target = WindowTarget(42, 1, "Exam", "chrome.exe", 1)
    guard.hooks = [101, 102]
    guard.browser_fullscreen_changed = True
    guard.is_fullscreen = Mock(return_value=False)
    guard.toggle_browser_fullscreen = Mock()
    guard.stop()
    guard.toggle_browser_fullscreen.assert_not_called()
    assert guard.u.UnhookWindowsHookEx.call_count == 2
    assert guard.target is None


def test_browser_fullscreen_targets_only_selected_hwnd_and_restores_after_end():
    guard = fake_guard()
    target = WindowTarget(42, 1, "Exam", "chrome.exe", 1)
    guard.target = target
    guard.original_rect = W.RECT(100, 100, 800, 600)
    guard.monitor_rect = Mock(return_value=W.RECT(0, 0, 1920, 1080))
    bounds = [100, 100, 800, 600]

    def window_rect(hwnd, pointer):
        assert hwnd == target.hwnd
        for name, value in zip(('left', 'top', 'right', 'bottom'), bounds):
            setattr(pointer._obj, name, value)
        return True

    guard.u.GetWindowRect.side_effect = window_rect
    guard.fullscreen(target)
    assert guard.u.PostMessageW.call_args_list[0].args == (42, 0x100, 0x7A, 0x00570001)
    assert guard.u.PostMessageW.call_args_list[1].args == (42, 0x101, 0x7A, 0xC0570001)
    assert guard.browser_fullscreen_changed
    guard.u.SetForegroundWindow.assert_called_with(42)
    bounds[:] = [0, 0, 1920, 1080]  # Browser handles its queued F11.
    guard.stop()
    assert guard.u.PostMessageW.call_count == 4  # Exactly one exit F11.
    guard.u.SetWindowPos.assert_any_call(42, None, 100, 100, 700, 500, 0x34)
    assert guard.target is None


def test_controller_retries_partial_start_and_never_reports_missing_hooks_as_active(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication, QWidget
    from agent.client import Agent, atomic_json
    from agent.exam_ui import ExamController

    app = QApplication.instance() or QApplication([])
    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture'})
    agent = Agent(tmp_path)
    agent.environment = {'kind': 'DESKTOP', 'guarded': True}
    agent.journal['exam_id'] = 'fullscreen-retry'
    target = WindowTarget(42, 1, 'Exam', 'chrome.exe', 1)
    agent.guard_target = target.public()
    parent = QWidget()
    controller = ExamController(agent, parent)
    controller.timer.stop()
    guard = Mock(hooks=[], locked=False, target=target)
    guard.tick.return_value = None

    def start(selected):
        assert selected == target
        if guard.start.call_count == 1:
            raise OSError('Fullscreen unavailable')
        guard.hooks = [101, 102]

    guard.start.side_effect = start
    controller.guard = guard
    agent.engine.start()
    agent.last_synced_at = time.monotonic()
    controller.tick()
    assert not agent.capabilities['guard_active']
    assert agent.capabilities['guard_fault'] == 'GUARD_UNAVAILABLE'
    assert agent.engine.state.access == 'LOCKED'
    controller.tick()
    assert guard.start.call_count == 2
    assert agent.capabilities['guard_active']
    assert guard.tick.call_args.kwargs['locked']
    controller.release()
    agent.http.close()
    app.processEvents()
