"""Changed monitor geometry invalidates gaze without silently unlocking an exam."""

import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agent.client import Agent, atomic_json
from agent.student_state import present
from shared.rules import RuleEngine


@pytest.fixture(scope='module')
def application():
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class Monitor:
    def __init__(self, *, name='test-monitor', geometry=(0, 0, 1920, 1080), dpi=96., ratio=1.):
        self._name, self._geometry, self._dpi, self._ratio = name, geometry, dpi, ratio

    def name(self):
        return self._name

    def serialNumber(self):
        return self._name + '-serial'

    def geometry(self):
        from PySide6.QtCore import QRect
        return QRect(*self._geometry)

    def logicalDotsPerInch(self):
        return self._dpi

    def devicePixelRatio(self):
        return self._ratio


def watcher(monitor, *, lifecycle='RUNNING', access='OPEN'):
    from agent.screen_calibration import screen_signature
    camera = SimpleNamespace(
        screen_signature=screen_signature(monitor), gaze_enabled=True,
        request_screen_invalidation=Mock(),
    )
    agent = SimpleNamespace(
        camera=camera, capabilities={'gaze': True, 'camera': True}, mutex=threading.RLock(),
        engine=SimpleNamespace(state=SimpleNamespace(lifecycle=lifecycle, access=access)),
        security_event=Mock(),
    )
    return SimpleNamespace(agent=agent)


@pytest.mark.parametrize('change', ['removed', 'resolution', 'origin', 'dpi', 'pixel_ratio', 'identity'])
def test_changed_selected_monitor_queues_reference_invalidation_and_locks_running_session(application, change):
    from agent.desktop import StudentWindow
    monitor = Monitor()
    window = watcher(monitor)
    if change == 'resolution':
        monitor._geometry = (0, 0, 2560, 1440)
    elif change == 'origin':
        monitor._geometry = (-1920, 0, 1920, 1080)
    elif change == 'dpi':
        monitor._dpi = 144.
    elif change == 'pixel_ratio':
        monitor._ratio = 1.5
    elif change == 'identity':
        monitor._name = 'replacement-monitor'
    with patch('agent.desktop.QApplication.screens', return_value=[] if change == 'removed' else [monitor]):
        StudentWindow.check_calibrated_monitor(window)
    window.agent.camera.request_screen_invalidation.assert_called_once_with('SCREEN_CHANGED')
    assert window.agent.capabilities == {'gaze': False, 'camera': True}
    # The GUI queues invalidation; it never mutates the model from its own thread.
    assert window.agent.camera.gaze_enabled
    window.agent.security_event.assert_called_once_with('DISPLAY_CHANGED')


@pytest.mark.parametrize('lifecycle,access', [
    ('READY', 'OPEN'), ('COMPLETED', 'OPEN'), ('RUNNING', 'LOCKED'),
])
def test_reference_is_invalidated_outside_active_open_exam_without_new_lock(application, lifecycle, access):
    from agent.desktop import StudentWindow
    window = watcher(Monitor(), lifecycle=lifecycle, access=access)
    with patch('agent.desktop.QApplication.screens', return_value=[]):
        StudentWindow.check_calibrated_monitor(window)
    window.agent.camera.request_screen_invalidation.assert_called_once_with('SCREEN_CHANGED')
    assert not window.agent.capabilities['gaze']
    window.agent.security_event.assert_not_called()


def test_unchanged_calibrated_secondary_monitor_does_not_depend_on_primary_monitor(application):
    from agent.desktop import StudentWindow
    selected = Monitor(name='secondary', geometry=(-1920, 0, 1920, 1080), dpi=144., ratio=1.5)
    window = watcher(selected)
    with patch('agent.desktop.QApplication.screens', return_value=[Monitor(name='primary'), selected]):
        StudentWindow.check_calibrated_monitor(window)
    window.agent.camera.request_screen_invalidation.assert_not_called()
    window.agent.security_event.assert_not_called()
    assert window.agent.capabilities['gaze']


@pytest.mark.parametrize('condition', ['no_camera', 'no_signature', 'already_disabled'])
def test_watcher_ignores_cameras_without_active_screen_profile(application, condition):
    from agent.desktop import StudentWindow
    window = watcher(Monitor())
    camera = window.agent.camera
    if condition == 'no_camera':
        window.agent.camera = None
    elif condition == 'no_signature':
        camera.screen_signature = None
    else:
        camera.gaze_enabled = False
    with patch('agent.desktop.QApplication.screens') as screens:
        StudentWindow.check_calibrated_monitor(window)
        screens.assert_not_called()
    camera.request_screen_invalidation.assert_not_called()
    window.agent.security_event.assert_not_called()


def test_display_changed_lock_allows_recalibration_but_never_exam_access():
    engine = RuleEngine()
    engine.start()
    engine.lock('DISPLAY_CHANGED')
    snapshot = {'state': engine.state.public(), 'camera': True, 'camera_preparing': False}
    presentation = present(snapshot)
    assert presentation['can_calibrate']
    assert presentation['locked'] and not presentation['can_open']
    assert 'преподавател' in presentation['message'].lower()
    snapshot['camera_preparing'] = True
    assert not present(snapshot)['can_calibrate']


@pytest.mark.parametrize('reason', ['PHONE_DETECTED', 'GAZE_LEFT', 'TEACHER_LOCK'])
def test_display_recovery_does_not_enable_recalibration_for_unrelated_locks(reason):
    engine = RuleEngine()
    engine.start()
    engine.lock(reason)
    presentation = present({'state': engine.state.public(), 'camera': True})
    assert not presentation['can_calibrate'] and not presentation['can_open']


def test_camera_setup_can_start_display_recovery_while_preserving_teacher_lock(application, tmp_path):
    from agent.camera_setup import CameraSetup
    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'test'})
    agent = Agent(tmp_path)
    agent.engine.start()
    agent.engine.lock('DISPLAY_CHANGED')
    original_lock = agent.engine.state.lock_id
    model = tmp_path / 'model'
    model.write_bytes(b'fixture')
    worker = Mock()
    worker.result, worker.error = None, ''
    worker.isRunning.return_value = False
    callbacks = []
    with (
        patch('agent.resources.verified_models', return_value=(model, model)),
        patch('agent.camera_setup.QTimer.singleShot', side_effect=lambda _, callback: callbacks.append(callback)),
    ):
        dialog = CameraSetup(agent)
    try:
        with patch('agent.camera_setup.CameraStartWorker', return_value=worker):
            callbacks[0]()
        worker.start.assert_called_once()
        assert agent.camera_preparing and dialog.preparing
        assert agent.engine.state.access == 'LOCKED'
        assert agent.engine.state.lock_id == original_lock
        assert agent.engine.state.reason == 'DISPLAY_CHANGED'
    finally:
        # Finish the fake worker through the same GUI completion boundary.
        dialog.cancelled = True
        dialog.finished_calibration()
        dialog.close()
        agent.http.close()


def test_real_monitor_security_event_preserves_recovery_controls(application, tmp_path):
    from agent.desktop import StudentWindow
    from agent.screen_calibration import screen_signature
    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'test'})
    agent = Agent(tmp_path)
    agent.engine.start()
    agent.camera = SimpleNamespace(
        screen_signature=screen_signature(Monitor()), gaze_enabled=True,
        request_screen_invalidation=Mock(),
    )
    agent.capabilities.update(camera=True, gaze=True)
    try:
        with patch('agent.desktop.QApplication.screens', return_value=[]):
            StudentWindow.check_calibrated_monitor(SimpleNamespace(agent=agent))
        assert agent.engine.state.reason == 'DISPLAY_CHANGED'
        assert agent.engine.state.access == 'LOCKED'
        assert agent.journal['events'][-1]['category'] == 'TECHNICAL'
        ui = present(agent.snapshot())
        assert ui['can_calibrate'] and not ui['can_open']
    finally:
        agent.http.close()
