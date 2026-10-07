import os
from pathlib import Path

import pytest

from agent.client import Agent, atomic_json


@pytest.fixture(scope="module")
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase

    app = QApplication.instance() or QApplication([])
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    yield app


def test_lock_screen_renders_evidence_and_masks_password(app, tmp_path):
    from PySide6.QtWidgets import QLineEdit
    from agent.exam_ui import LockScreen

    atomic_json(
        tmp_path / "config.json",
        {"server": "http://localhost:8000", "token": "fixture"},
    )
    agent = Agent(tmp_path)
    agent.engine.start()
    agent.engine.lock("PHONE_DETECTED")
    agent.remember(
        [
            {"id": "a", "type": "GAZE_DOWN", "at": 40, "epoch": 1, "duration": 5.2},
            {"id": "b", "type": "PHONE_DETECTED", "at": 74, "epoch": 1},
        ]
    )
    screen = LockScreen(agent)
    screen.resize(1366, 768)
    screen.update_state(agent.snapshot())
    screen.show()
    app.processEvents()
    assert screen.heading.text() == "Позовите преподавателя"
    assert screen.password.echoMode() == QLineEdit.EchoMode.Password
    assert screen.evidence_layout.count() >= 4
    screen.close()
    assert screen.isVisible()  # Alt+F4/window close is not an unlock
    destination = os.getenv("QORGAU_SCREENSHOT_DIR")
    if destination:
        Path(destination).mkdir(parents=True, exist_ok=True)
        assert screen.grab().save(str(Path(destination) / "lock-screen.png"))
    screen.hide()
    agent.http.close()


def test_native_browser_navigation_uses_exact_origin():
    from PySide6.QtCore import QUrl
    from agent.exam_browser import ExamPage, origin
    from types import SimpleNamespace

    reports = []
    page = SimpleNamespace(
        allowed_origin=origin("https://test.example"), on_attempt=reports.append
    )
    assert ExamPage.acceptNavigationRequest(
        page, QUrl("https://test.example/next"), None, True
    )
    for url in (
        "https://test.example.evil.test",
        "https://other.example",
        "file:///C:/",
        "https://test.example:8443",
    ):
        assert not ExamPage.acceptNavigationRequest(page, QUrl(url), None, True)
        assert not ExamPage.acceptNavigationRequest(page, QUrl(url), None, False)
    assert len(reports) == 8


def test_gaze_warning_is_also_visible_in_observe_mode(app, tmp_path):
    from PySide6.QtWidgets import QWidget
    from agent.exam_ui import ExamController

    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture'})
    agent = Agent(tmp_path)
    agent.environment = {'kind': 'DESKTOP', 'guarded': False}
    agent.journal['exam_id'] = 'observe-test'
    agent.capabilities.update(camera=True, gaze=True)
    agent.gaze_diagnostics = {
        'direction': 'SCREEN', 'reference_ready': True,
        'attention_away': True, 'attention_direction': 'LEFT',
    }
    parent = QWidget()
    controller = ExamController(agent, parent)
    controller.timer.stop()
    agent.engine.start()
    controller.tick()
    assert controller.gaze_warnings and all(w.isVisible() for w in controller.gaze_warnings)
    agent.gaze_diagnostics.update(attention_away=False, attention_direction=None)
    controller.tick()
    assert not any(w.isVisible() for w in controller.gaze_warnings)
    controller.release()
    agent.http.close()


def test_desktop_overlay_only_during_lock_and_release_after_end(app, tmp_path):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QWidget
    from agent.exam_ui import ExamController

    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture'})
    agent = Agent(tmp_path)
    agent.environment = {'kind': 'DESKTOP', 'guarded': True}
    agent.journal['exam_id'] = 'test-desktop'
    agent.launch_environment()
    parent = QWidget()
    controller = ExamController(agent, parent)
    controller.timer.stop()
    # Never install global input hooks on the developer's desktop.
    guard = Mock(hooks=[], locked=False, target=None)
    guard.tick.return_value = None
    def start(**kwargs):
        assert kwargs == {'desktop': True}
        guard.hooks = [1, 2]
    guard.start.side_effect = start
    controller.guard = guard
    agent.engine.start()
    agent.last_synced_at = __import__('time').monotonic()
    agent.capabilities.update(camera=True, gaze=True)
    agent.gaze_diagnostics = {
        'direction': 'SCREEN', 'reference_ready': True,
        'attention_away': True, 'attention_direction': 'RIGHT',
    }
    controller.tick()
    assert controller.surfaces and not any(s.isVisible() for s in controller.surfaces)
    assert controller.gaze_warnings and all(w.isVisible() for w in controller.gaze_warnings)
    assert all(w.message.text() == 'Верните взгляд на монитор' for w in controller.gaze_warnings)
    agent.gaze_diagnostics.update(attention_away=False, attention_direction=None)
    controller.tick()
    assert not any(w.isVisible() for w in controller.gaze_warnings)
    agent.engine.lock('PHONE_DETECTED')
    controller.tick()
    app.processEvents()
    assert all(s.isVisible() for s in controller.surfaces)
    assert not any(w.isVisible() for w in controller.gaze_warnings)
    assert all(s.isFullScreen() for s in controller.surfaces)
    assert guard.tick.call_args.kwargs['locked'] is True
    agent.engine.unlock(agent.engine.state.lock_id, agent.engine.state.version)
    controller.tick()
    assert not any(s.isVisible() for s in controller.surfaces)
    agent.engine.end()
    controller.tick()
    guard.stop.assert_called_once()
    assert not any(s.isVisible() for s in controller.surfaces)
    controller.release()
    agent.http.close()


def test_selected_window_overlay_hides_and_focus_returns_after_remote_unlock(app, tmp_path):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QWidget
    from agent.exam_ui import ExamController
    from agent.windows_guard import WindowTarget
    import time

    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture'})
    agent = Agent(tmp_path)
    agent.environment = {'kind': 'DESKTOP', 'guarded': True}
    agent.journal['exam_id'] = 'selected-test'
    target = WindowTarget(123, 234, 'Exam', 'browser.exe', 1)
    agent.guard_target = target.public()
    parent = QWidget()
    controller = ExamController(agent, parent)
    controller.timer.stop()
    guard = Mock(hooks=[1, 2], locked=False, target=target)
    def tick(**kwargs):
        guard.locked = kwargs['locked']
    guard.tick.side_effect = tick
    controller.guard = guard
    agent.engine.start()
    agent.last_synced_at = time.monotonic()
    controller.tick()
    assert not any(s.isVisible() for s in controller.surfaces)
    agent.engine.lock('PHONE_DETECTED')
    controller.tick()
    app.processEvents()
    assert all(s.isVisible() and s.isFullScreen() for s in controller.surfaces)
    agent.apply({'id': 'remote-unlock', 'type': 'UNLOCK', 'exam_id': 'selected-test',
                 'expires_at': time.time() + 30, 'expected_version': agent.engine.state.version,
                 'lock_id': agent.engine.state.lock_id, 'require_camera': False})
    controller.tick()
    assert agent.engine.state.access == 'OPEN'
    assert not any(s.isVisible() for s in controller.surfaces)
    guard.u.SetForegroundWindow.assert_called_with(target.hwnd)
    controller.release()
    agent.http.close()


def test_lock_without_target_still_installs_input_guard(app, tmp_path):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QWidget
    from agent.exam_ui import ExamController
    import time

    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture'})
    agent = Agent(tmp_path)
    agent.environment = {'kind': 'DESKTOP', 'guarded': True}
    agent.journal['exam_id'] = 'closed-test'
    parent = QWidget()
    controller = ExamController(agent, parent)
    controller.timer.stop()
    guard = Mock(hooks=[], locked=False, target=None)
    guard.windows.return_value = []
    guard.tick.return_value = None
    controller.guard = guard
    agent.engine.start()
    agent.engine.lock('TARGET_CLOSED')
    agent.last_synced_at = time.monotonic()
    controller.tick()
    app.processEvents()
    guard.start.assert_called_once_with(desktop=True)
    assert guard.tick.call_args.kwargs['locked'] is True
    assert all(s.isVisible() and s.isFullScreen() for s in controller.surfaces)
    assert agent.capabilities['guard_fault'] == 'TARGET_CLOSED'
    agent.apply({'id': 'remote-unlock', 'type': 'UNLOCK', 'exam_id': 'closed-test',
                 'expires_at': time.time() + 30, 'expected_version': agent.engine.state.version,
                 'lock_id': agent.engine.state.lock_id, 'require_camera': False})
    assert agent.engine.state.access == 'LOCKED'
    controller.release()
    agent.http.close()
