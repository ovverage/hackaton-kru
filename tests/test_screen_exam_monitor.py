"""A personal screen profile must follow the display hosting the exam."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.exam_ui import (
    ExamController,
    calibration_overlay_handles,
    calibrated_exam_screen,
    place_exam_browser,
    raise_calibration_overlays,
)
from agent.screen_calibration import screen_signature
from agent.windows_guard import WindowsGuard, WindowTarget
from test_exam_ui import app as app


def screen(name, native, *, left=0, ratio=1):
    from PySide6.QtCore import QRect
    return SimpleNamespace(
        name=lambda: name, serialNumber=lambda: name + '-serial',
        geometry=lambda: QRect(left, 0, 1920, 1080), devicePixelRatio=lambda: ratio,
        logicalDotsPerInch=lambda: 96.,
        nativeInterface=lambda: SimpleNamespace(handle=lambda: native),
    )


class Browser:
    def __init__(self, monitor):
        self.monitor = monitor
        self.calls = []

    def winId(self):
        self.calls.append('winId')
        return 101

    def windowHandle(self):
        return self

    def setScreen(self, monitor):
        self.calls.append(('screen', monitor))
        self.monitor = monitor

    def screen(self):
        return self.monitor

    def setGeometry(self, geometry):
        self.calls.append(('geometry', geometry))

    def showFullScreen(self):
        self.calls.append('fullscreen')


def controller(expected, *, current=None, locked=False):
    camera = SimpleNamespace(screen_signature=screen_signature(expected),
                             requires_gaze_reference=False,
                             request_screen_invalidation=Mock())
    agent = SimpleNamespace(
        camera=camera, capabilities={'gaze': True}, guard_target=None,
        engine=SimpleNamespace(state=SimpleNamespace(lifecycle='RUNNING', access='LOCKED' if locked else 'OPEN')),
        security_event=Mock(),
    )
    result = SimpleNamespace(agent=agent, browser=Browser(current) if current is not None else None,
                             guard=None)
    result.invalidate_exam_monitor = lambda: ExamController.invalidate_exam_monitor(result)
    return result


def test_calibrated_screen_resolution_includes_secondary_origin_and_dpi():
    primary = screen('Primary', 1)
    secondary = screen('Secondary', 2, left=-1920, ratio=1.5)
    camera = SimpleNamespace(screen_signature=screen_signature(secondary))
    assert calibrated_exam_screen(camera, [primary, secondary]) is secondary
    changed_dpi = screen('Secondary', 2, left=-1920, ratio=1.25)
    assert calibrated_exam_screen(camera, [primary, changed_dpi]) is None


def test_browser_selects_calibrated_secondary_before_becoming_fullscreen():
    primary = screen('Primary', 1)
    secondary = screen('Secondary', 2, left=-1920, ratio=1.5)
    browser = Browser(primary)
    place_exam_browser(browser, secondary)
    assert browser.calls == ['winId', ('screen', secondary),
                             ('geometry', secondary.geometry()), 'fullscreen']
    assert browser.monitor is secondary


def test_existing_browser_on_calibrated_screen_keeps_profile():
    expected = screen('Selected', 2)
    current = controller(expected, current=expected)
    assert ExamController.check_exam_monitor(current, [expected])
    current.agent.camera.request_screen_invalidation.assert_not_called()
    current.agent.security_event.assert_not_called()


def test_moving_browser_to_other_connected_monitor_invalidates_profile():
    expected, wrong = screen('Selected', 2), screen('Other', 3)
    current = controller(expected, current=wrong)
    assert not ExamController.check_exam_monitor(current, [expected, wrong])
    current.agent.camera.request_screen_invalidation.assert_called_once_with('SCREEN_CHANGED')
    current.agent.security_event.assert_called_once_with('DISPLAY_CHANGED')
    assert not current.agent.capabilities['gaze']


def test_locked_recovery_can_reposition_owned_browser_to_new_validated_screen():
    expected, old = screen('Selected', 2), screen('Old', 3)
    current = controller(expected, current=old, locked=True)
    assert ExamController.check_exam_monitor(current, [expected, old])
    assert current.browser.monitor is expected
    assert current.browser.calls[-1] == 'fullscreen'
    current.agent.security_event.assert_not_called()
    current.agent.camera.request_screen_invalidation.assert_not_called()


def test_unready_locked_camera_does_not_reposition_or_create_another_lock():
    expected, wrong = screen('Selected', 2), screen('Other', 3)
    current = controller(expected, current=wrong, locked=True)
    current.agent.camera.requires_gaze_reference = True
    assert not ExamController.check_exam_monitor(current, [expected, wrong])
    assert not current.browser.calls
    current.agent.security_event.assert_not_called()
    current.agent.camera.request_screen_invalidation.assert_called_once_with('SCREEN_CHANGED')


def test_removed_calibrated_monitor_fails_even_if_another_display_remains():
    expected, wrong = screen('Selected', 2), screen('Other', 3)
    current = controller(expected, current=wrong)
    assert not ExamController.check_exam_monitor(current, [wrong])
    current.agent.security_event.assert_called_once_with('DISPLAY_CHANGED')


@pytest.mark.parametrize('native_monitor,valid', [(12345, True), (99999, False)])
def test_foreign_window_uses_native_monitor_identity_without_name_or_pixel_guess(native_monitor, valid):
    expected = screen('User presentable name unrelated to Windows device', 12345, left=-1536, ratio=1.5)
    current = controller(expected)
    current.agent.guard_target = WindowTarget(77, 88, 'Test', 'C:/test.exe', 99).public()
    current.guard = SimpleNamespace(valid=Mock(return_value=True), monitor_handle=Mock(return_value=native_monitor))
    assert ExamController.check_exam_monitor(current, [expected]) is valid
    current.guard.monitor_handle.assert_called_once_with(77)
    assert current.agent.security_event.called is (not valid)


def test_launched_foreign_window_matches_pid_and_executable_before_monitor_check():
    expected = screen('Selected', 123)
    current = controller(expected)
    target = WindowTarget(77, 88, 'Test', 'C:/Test.exe', 99)
    current.agent.guard_target = {'launched_pid': 88, 'executable': 'c:/test.EXE'}
    current.guard = SimpleNamespace(valid=Mock(return_value=True), monitor_handle=Mock(return_value=123),
                                    windows=lambda: [target])
    assert ExamController.check_exam_monitor(current, [expected])
    current.guard.monitor_handle.assert_called_once_with(77)


def test_unknown_native_screen_identity_disables_profile_instead_of_guessing():
    expected = screen('Selected', 123)
    expected.nativeInterface = Mock(side_effect=RuntimeError('native screen unavailable'))
    current = controller(expected)
    current.agent.guard_target = WindowTarget(77, 88, 'Test', 'C:/test.exe', 99).public()
    current.guard = SimpleNamespace(valid=Mock(return_value=True), monitor_handle=Mock(return_value=123))
    assert not ExamController.check_exam_monitor(current, [expected])
    current.agent.security_event.assert_called_once_with('DISPLAY_CHANGED')


def test_legacy_camera_without_personal_screen_profile_keeps_existing_flow():
    expected = screen('Selected', 123)
    current = controller(expected)
    current.agent.camera = SimpleNamespace()
    assert ExamController.check_exam_monitor(current, [])
    current.agent.security_event.assert_not_called()


@pytest.mark.parametrize('is_window,monitor,raises', [(True, 45, False), (True, 0, True), (False, 45, True)])
def test_windows_monitor_handle_checks_window_and_never_defaults_to_primary(is_window, monitor, raises):
    guard = WindowsGuard.__new__(WindowsGuard)
    guard.u = SimpleNamespace(IsWindow=Mock(return_value=is_window), MonitorFromWindow=Mock(return_value=monitor))
    if raises:
        with pytest.raises(OSError):
            guard.monitor_handle(88)
    else:
        assert guard.monitor_handle(88) == 45
    if is_window:
        guard.u.MonitorFromWindow.assert_called_once_with(88, 0)
    else:
        guard.u.MonitorFromWindow.assert_not_called()


def test_fullscreen_targets_are_included_in_locked_guard_overlay_allowlist():
    target = SimpleNamespace(isVisible=lambda: True, winId=lambda: 300)
    setup = SimpleNamespace(isVisible=lambda: True, winId=lambda: 200, screen_dialog=target)
    hidden = SimpleNamespace(isVisible=lambda: False, winId=lambda: 400, screen_dialog=target)
    assert calibration_overlay_handles([SimpleNamespace(calibration=setup),
                                        SimpleNamespace(calibration=hidden),
                                        SimpleNamespace(calibration=None)]) == [200, 300]


def test_target_dialogs_are_raised_after_all_setup_windows_without_activation():
    calls = []
    def setup(index):
        target = SimpleNamespace(isVisible=lambda: True, raise_=lambda: calls.append(('target', index)))
        return SimpleNamespace(isVisible=lambda: True, raise_=lambda: calls.append(('setup', index)),
                               screen_dialog=target)
    raise_calibration_overlays([SimpleNamespace(calibration=setup(1)), SimpleNamespace(calibration=setup(2))])
    assert calls == [('setup', 1), ('setup', 2), ('target', 1), ('target', 2)]


def test_display_change_lock_exposes_camera_recalibration(app, tmp_path):
    from agent.client import Agent, atomic_json
    from agent.exam_ui import LockScreen
    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture'})
    agent = Agent(tmp_path)
    agent.engine.start()
    agent.engine.lock('DISPLAY_CHANGED')
    surface = LockScreen(agent)
    try:
        surface.update_state(agent.snapshot())
        assert not surface.recover.isHidden()
    finally:
        surface.hide()
        surface.deleteLater()
        agent.http.close()
