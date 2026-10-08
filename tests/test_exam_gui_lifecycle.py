"""GUI-only startup, target selection, and display feedback use no real camera."""

from copy import deepcopy
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.client import Agent, atomic_json


@pytest.fixture(scope="session")
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def cleanup_created_widgets(app):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    previous = {id(widget) for widget in QApplication.topLevelWidgets()}
    yield
    for widget in QApplication.topLevelWidgets():
        if id(widget) not in previous:
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def gui(app, tmp_path):
    from agent.desktop import StudentWindow
    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "fixture"})
    agent = Agent(tmp_path)
    window = StudentWindow(tmp_path, agent=agent, run_worker=False)
    window.timer.stop()
    window.exam_controller.timer.stop()
    yield agent, window
    window.shutting_down = True
    window.timer.stop()
    window.exam_controller.timer.stop()
    window.exam_controller.release()
    for worker in (window.password_worker, window.offline_start_worker, window.environment_worker):
        if worker is not None:
            assert worker.wait(2000)
    window.hide()
    if window.tray:
        window.tray.hide()
    agent.http.close()
    for surface in window.exam_controller.surfaces + window.exam_controller.gaze_warnings:
        surface.deleteLater()
    window.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_startup_remains_fullscreen_with_previously_selected_window(gui):
    agent, window = gui
    agent.capabilities["selected_window"] = True
    window.start_visibility()
    assert window.isVisible() and window.isFullScreen()
    window.hide()
    window.show_status()
    assert window.isVisible() and window.isFullScreen()
    assert agent.capabilities["interactive_start"] is True


def test_camera_selection_before_start_never_opens_calibration(gui, monkeypatch):
    agent, window = gui
    setup = Mock(side_effect=AssertionError("No calibration before START"))
    monkeypatch.setattr("agent.camera_setup.CameraSetup", setup)
    window.camera_button.click()
    setup.assert_not_called()
    assert agent.config["camera_settings"]["index"] == window.camera_choice.currentData()
    assert window.calibration is None


def test_pending_start_calibrates_then_prepares_environment_then_completes(gui, app, monkeypatch):
    from PySide6.QtWidgets import QDialog
    agent, window = gui
    original = agent.snapshot
    pending = {"value": {"id": "authorized-start"}}
    calls = []

    def snapshot():
        result = original()
        result.update(start_pending=pending["value"], start_camera_ready=True)
        return result

    monkeypatch.setattr(agent, "snapshot", snapshot)

    class Setup(QDialog):
        def __init__(self, owner, parent, **kwargs):
            super().__init__(parent)
            assert owner.engine.state.lifecycle != "RUNNING"
            assert kwargs["screen"] is not None
            calls.append("calibration")

    monkeypatch.setattr("agent.camera_setup.CameraSetup", Setup)
    monkeypatch.setattr(agent, "prepare_start", lambda token: calls.append("prepare:" + token), raising=False)

    def prepare_environment(token, ready, failed):
        calls.append("environment:" + token)
        ready()

    def complete(token):
        calls.append("complete:" + token)
        pending["value"] = None

    monkeypatch.setattr(window.exam_controller, "prepare_environment", prepare_environment)
    monkeypatch.setattr(agent, "complete_start", complete, raising=False)
    window.refresh()
    app.processEvents()
    assert calls == ["calibration"]
    assert agent.engine.state.lifecycle != "RUNNING"
    window.calibration.accept()
    from PySide6.QtTest import QTest
    for _ in range(100):
        QTest.qWait(5)
        if pending["value"] is None:
            break
    assert calls == ["calibration", "prepare:authorized-start", "environment:authorized-start", "complete:authorized-start"]


def test_cancelled_calibration_never_launches_target_or_arms_exam(gui, app, monkeypatch):
    from PySide6.QtWidgets import QDialog
    agent, window = gui
    original = agent.snapshot
    pending = {"value": {"id": "cancelled-start"}}
    monkeypatch.setattr(agent, "snapshot", lambda: dict(original(), start_pending=pending["value"]))

    class Setup(QDialog):
        def __init__(self, owner, parent, **kwargs):
            super().__init__(parent)

    def cancel(token, message):
        assert token == "cancelled-start"
        pending["value"] = None

    monkeypatch.setattr("agent.camera_setup.CameraSetup", Setup)
    monkeypatch.setattr(agent, "cancel_start", Mock(side_effect=cancel), raising=False)
    monkeypatch.setattr(agent, "prepare_start", Mock(), raising=False)
    monkeypatch.setattr(agent, "complete_start", Mock(), raising=False)
    window.refresh()
    app.processEvents()
    window.calibration.reject()
    agent.cancel_start.assert_called_once()
    agent.prepare_start.assert_not_called()
    agent.complete_start.assert_not_called()
    assert agent.engine.state.lifecycle != "RUNNING"
    assert window.isFullScreen()


def test_first_fresh_camera_frame_wait_is_bounded_and_does_not_block(gui, monkeypatch):
    agent, window = gui
    original = agent.snapshot
    monkeypatch.setattr(agent, "snapshot", lambda: dict(original(), start_pending={"id": "p"}, start_camera_ready=False))
    monkeypatch.setattr("agent.desktop.time.monotonic", lambda: 20.0)
    failed = Mock()
    monkeypatch.setattr(window, "start_preparation_failed", failed)
    ready = Mock()
    window.wait_for_start_camera("p", ready, deadline=19.9)
    ready.assert_not_called()
    failed.assert_called_once()


def test_end_closes_only_owned_browser_and_restores_main_window(gui, app):
    from PySide6.QtWidgets import QWidget
    agent, window = gui
    controller = window.exam_controller
    controller.guard = Mock()
    browser = QWidget()
    browser.released = False
    browser.show()
    controller.browser = browser
    controller.active_exam = "owned-exam"
    window.hide()
    controller.release()
    assert browser.released and not browser.isVisible()
    assert controller.browser is None
    assert window.isVisible() and window.isFullScreen()
    controller.guard.stop.assert_called_once()


def test_unarmed_preflight_waits_for_system_dialog_then_stable_target_focus(gui, monkeypatch):
    from agent.windows_guard import WindowTarget
    agent, window = gui
    controller = window.exam_controller
    original = agent.snapshot
    monkeypatch.setattr(agent, "snapshot", lambda: dict(original(), start_pending={"id": "p"}))
    target = WindowTarget(42, 101, "Test", "exam.exe", 1234)
    agent.guard_target = target.public()
    guard = Mock()
    guard.valid.return_value = True
    foreground = {"value": 999}
    guard.u.GetForegroundWindow.side_effect = lambda: foreground["value"]
    guard.u.GetAncestor.side_effect = lambda value, flags: value
    controller.guard = guard
    clock = [100.0]
    monkeypatch.setattr("agent.exam_ui.time.monotonic", lambda: clock[0])
    ready, failed = Mock(), Mock()
    controller.prepare_environment("p", ready, failed)
    controller.environment_timer.stop()
    clock[0] += 2
    controller.poll_environment_preparation()
    ready.assert_not_called()
    guard.start.assert_not_called()
    assert agent.engine.state.lifecycle != "RUNNING"
    foreground["value"] = 42
    controller.poll_environment_preparation()
    clock[0] += .8
    controller.poll_environment_preparation()
    ready.assert_not_called()
    clock[0] += .3
    controller.poll_environment_preparation()
    ready.assert_called_once()
    failed.assert_not_called()
    guard.start.assert_not_called()
    assert not agent.journal["events"]


def test_calibration_monitor_is_resolved_from_activated_target_hwnd(gui, monkeypatch):
    agent, window = gui
    controller = window.exam_controller
    original = agent.snapshot
    monkeypatch.setattr(agent, "snapshot", lambda: dict(original(), start_pending={"id": "p", "target_hwnd": 42}))
    controller.guard = Mock()
    controller.guard.monitor_handle.return_value = 72
    first = SimpleNamespace(nativeInterface=lambda: SimpleNamespace(handle=lambda: 71))
    second = SimpleNamespace(nativeInterface=lambda: SimpleNamespace(handle=lambda: 72))
    monkeypatch.setattr("agent.exam_ui.QApplication.screens", lambda: [first, second])
    assert controller.preparation_screen() is second
    controller.guard.monitor_handle.assert_called_once_with(42)


def test_target_moved_after_calibration_cannot_arm_guard(gui, monkeypatch):
    agent, window = gui
    agent.start_pending = {'id': 'moved'}
    controller = window.exam_controller
    controller.guard = None
    ready, failed = Mock(), Mock()
    controller.prepare_environment('moved', ready, failed)
    controller.environment_timer.stop()
    controller.preparation['focused_since'] = 0
    monkeypatch.setattr(controller, 'check_exam_monitor', lambda screens: False)
    controller.poll_environment_preparation()
    ready.assert_not_called()
    failed.assert_called_once()
    assert 'другом экране' in failed.call_args.args[0]
    assert agent.engine.state.lifecycle != 'RUNNING'


def test_open_running_exam_focuses_exact_window_without_relaunch(gui, monkeypatch):
    from agent.windows_guard import WindowTarget
    agent, window = gui
    agent.engine.state.lifecycle = 'RUNNING'
    agent.engine.state.access = 'OPEN'
    target = WindowTarget(42, 101, 'Exam', 'exam.exe', 1234)
    agent.guard_target = target.public()
    guard = Mock()
    guard.valid.return_value = True
    window.exam_controller.guard = guard
    launch = Mock(side_effect=AssertionError('Active target must never be relaunched'))
    monkeypatch.setattr(agent, 'launch_environment', launch)
    window.open_exam()
    launch.assert_not_called()
    guard.valid.assert_called_once_with(target)
    guard.u.ShowWindow.assert_called_once_with(42, 9)
    guard.u.SetForegroundWindow.assert_called_once_with(42)


def test_open_running_exam_rejects_reused_window_without_focusing_it(gui, monkeypatch):
    from agent.windows_guard import WindowTarget
    agent, window = gui
    agent.engine.state.lifecycle = 'RUNNING'
    agent.engine.state.access = 'OPEN'
    agent.guard_target = WindowTarget(42, 101, 'Exam', 'exam.exe', 1234).public()
    guard = Mock()
    guard.valid.return_value = False
    window.exam_controller.guard = guard
    event, tick = Mock(), Mock()
    monkeypatch.setattr(agent, 'security_event', event)
    monkeypatch.setattr(window.exam_controller, 'tick', tick)
    window.open_exam()
    guard.u.ShowWindow.assert_not_called()
    guard.u.SetForegroundWindow.assert_not_called()
    event.assert_called_once_with('TARGET_CLOSED')
    tick.assert_called_once()


def test_tab_picker_preserves_exact_extension_tab_identity(app):
    from agent.exam_ui import BrowserTabPicker
    tabs = [dict(kind="BROWSER_TAB", id="instance:11", browser_instance="instance", window_id=5,
                 tab_id=11, title="Exam one", url="https://test.example/one"),
            dict(kind="BROWSER_TAB", id="instance:12", browser_instance="instance", window_id=5,
                 tab_id=12, title="Exam two", url="https://test.example/two")]
    agent = SimpleNamespace(available_browser_tabs=lambda: tabs)
    picker = BrowserTabPicker(agent, None)
    assert picker.items.count() == 2
    picker.items.setCurrentRow(1)
    picker.choose()
    assert picker.selected == tabs[1]
    assert picker.selected is not tabs[1]


def test_empty_tab_roster_does_not_fallback_to_browser_window_titles(app):
    from agent.exam_ui import BrowserTabPicker
    agent = SimpleNamespace(available_browser_tabs=lambda: [], targets=[{"name": "Chrome exam window"}])
    picker = BrowserTabPicker(agent, None)
    assert picker.items.count() == 0 and picker.selected is None
    assert "расширение" in picker.extension_help.text()


def test_browser_binding_replacement_is_explicit(app):
    from agent.exam_ui import BrowserBindingDialog
    agent = SimpleNamespace(bind_browser=Mock())
    dialog = BrowserBindingDialog(agent, None)
    dialog.extension_id.setText("a" * 32)
    dialog.browser_instance.setText("11111111-1111-4111-8111-111111111111")
    assert not dialog.replace.isChecked()
    dialog.bind()
    agent.bind_browser.assert_called_once_with("a" * 32, "11111111-1111-4111-8111-111111111111", "chrome", False)


def test_head_warning_is_independent_of_eye_tracking_and_hold_never_changes_rules():
    from agent.exam_ui import WarningDisplayHold
    snap = {"state": {"lifecycle": "RUNNING", "access": "OPEN"}, "camera": True,
            "gaze_seconds": 0, "gaze_diagnostics": {
                "source": "public_gaze_model", "reference_ready": False,
                "head_reference_ready": True, "head_tracking_status": "tracked",
                "head_warning": True, "head_extreme": True, "head_direction": "RIGHT"}}
    before = deepcopy(snap)
    display = WarningDisplayHold()
    assert "Сильный поворот головы вправо" in display.update(snap, 10)
    assert snap == before
    snap["gaze_diagnostics"]["head_warning"] = False
    snap["gaze_diagnostics"]["head_extreme"] = False
    assert "Повернитесь к монитору" in display.update(snap, 10.9)
    assert display.update(snap, 11.01) == ""
    assert snap["gaze_seconds"] == 0


def test_face_unlock_worker_only_requests_authorized_agent_api(app):
    from agent.exam_ui import FaceUnlockWorker
    messages, completed = [], []

    def scan(progress):
        progress("Посмотрите в камеру")

    agent = SimpleNamespace(teacher_face_unlock=Mock(side_effect=scan))
    worker = FaceUnlockWorker(agent, None)
    worker.progress.connect(messages.append)
    worker.done.connect(completed.append)
    worker.run()
    assert messages == ["Посмотрите в камеру"] and completed == [""]
    agent.teacher_face_unlock.assert_called_once()


def test_eye_and_head_banner_hold_expire_independently():
    from agent.exam_ui import WarningDisplayHold
    snap = {"state": {"lifecycle": "RUNNING", "access": "OPEN"}, "camera": True,
            "gaze_diagnostics": {"source": "public_gaze_model", "reference_ready": True,
                                 "gaze_tracking_status": "tracked", "gaze_observed_direction": "LEFT",
                                 "head_reference_ready": True, "head_tracking_status": "tracked",
                                 "head_warning": True, "head_direction": "RIGHT"}}
    display = WarningDisplayHold()
    assert "Поворот головы" in display.update(snap, 20)
    snap['gaze_diagnostics']['head_warning'] = False
    assert "Поворот головы" in display.update(snap, 20.8)
    assert "Поворот головы" not in display.update(snap, 21.01)
    assert "взгляд влево" in display.update(snap, 21.01)


def test_webengine_preflight_flags_preserve_security_and_existing_features(monkeypatch):
    from agent.student_entry import configure_webengine
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-features=FeatureOne --log-level=2")
    configure_webengine()
    configure_webengine()
    result = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"]
    assert result.count("MediaRouter") == 1 and "FeatureOne,MediaRouter" in result
    assert "--log-level=2" in result
    assert "no-sandbox" not in result and "NetworkServiceInProcess" not in result


def test_online_reserve_password_is_verified_off_gui_thread_and_cleared(gui, monkeypatch):
    from PySide6.QtCore import QThread
    from PySide6.QtTest import QTest
    agent, window = gui
    calls = []
    monkeypatch.setattr(agent, "setup_password", lambda password: calls.append((password, QThread.currentThread())), raising=False)
    assert not window.password_setup.isHidden()
    assert window.local_password_repeat.isHidden()
    assert "Существующий" in window.local_password.placeholderText()
    window.local_password.setText("fixture-password")
    window.setup_local_password()
    assert window.local_password.text() == ""
    for _ in range(100):
        QTest.qWait(5)
        if window.password_worker.isFinished():
            break
    assert calls[0][0] == "fixture-password"
    assert calls[0][1] != window.thread()
    assert window.password_worker.password == ""
    assert "сохранён" in window.password_feedback.text()


def test_offline_password_requires_repeat_and_face_unlock_remains_available(gui, monkeypatch):
    from agent.exam_ui import LockScreen
    agent, window = gui
    monkeypatch.setattr(agent, "mode", "offline", raising=False)
    setup = Mock()
    monkeypatch.setattr(agent, "setup_password", setup, raising=False)
    window.local_password.setText("first")
    window.local_password_repeat.setText("second")
    window.setup_local_password()
    setup.assert_not_called()
    assert "не совпали" in window.password_feedback.text()
    lock = LockScreen(agent)
    assert not lock.face_unlock.isHidden()
    lock.deleteLater()


def test_second_exam_preflight_survives_completed_previous_exam(gui):
    from PySide6.QtWidgets import QWidget
    agent, window = gui
    controller = window.exam_controller
    agent.engine.state.lifecycle = "COMPLETED"
    original = agent.snapshot
    agent.snapshot = lambda: dict(original(), start_pending={"id": "next-exam"})
    browser = QWidget()
    browser.released = False
    controller.browser = browser
    controller.preparation = {"token": "next-exam"}
    controller.tick()
    assert controller.browser is browser
    assert not browser.released
    assert controller.preparation is not None


def test_preflight_error_keeps_calibration_and_retries_without_camera_dialog(gui, monkeypatch):
    agent, window = gui
    token = 'calibrated-retry'
    agent.start_pending = {'id': token}
    agent.camera = SimpleNamespace(requires_gaze_reference=False)
    window.preparing_start_id = token
    window.prepared_camera_token = token
    monkeypatch.setattr('agent.camera_setup.CameraSetup', Mock(side_effect=AssertionError('must reuse calibration')))
    window.start_preparation_failed(token, 'Страница недоступна. Повторите открытие.')
    assert agent.start_pending['id'] == token
    assert window.prepared_camera_token == token
    assert not window.environment_retry.isHidden()
    wait = Mock()
    monkeypatch.setattr(window, 'wait_for_start_camera', wait)
    window.retry_pending_environment()
    assert window.environment_error is None
    wait.assert_called_once()
    assert wait.call_args.args[0] == token
    window.cancel_pending_environment()
    assert agent.start_pending is None
    assert window.environment_retry.isHidden()
