"""Start/unlock require the current monitor profile, including queued changes."""

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.client import Agent, atomic_json
from agent.vision import Camera


def adaptive_camera(status='valid'):
    camera = Camera.__new__(Camera)
    camera.public_gaze = SimpleNamespace(reference=[0., 0., -1.], reference_error=16.)
    camera.screen_calibration_required = True
    camera.screen_calibration = SimpleNamespace(ready=True)
    camera._screen_invalidation_requested = None
    if status == 'pending_monitor_change':
        camera.request_screen_invalidation('SCREEN_CHANGED')
    elif status == 'missing_profile':
        camera.screen_calibration = None
    elif status == 'failed_profile':
        camera.screen_calibration.ready = False
    elif status == 'missing_centre':
        camera.public_gaze.reference = None
    return camera


@pytest.fixture
def agent(tmp_path, monkeypatch):
    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'test'})
    instance = Agent(tmp_path)
    instance.journal['exam_id'] = 'exam'
    instance.camera = adaptive_camera()
    instance.recorder = Mock()
    instance.last_observation = {'faces': 1, 'phone_confidence': 0., 'direction': 'SCREEN'}
    instance.last_observation_at = time.monotonic()
    instance.launch_environment = Mock()
    monkeypatch.setattr('agent.client.shutil.disk_usage', lambda _: SimpleNamespace(free=2 * 1024**3))
    yield instance
    instance.http.close()


def command(agent, kind, *, ident='command', require_camera=True):
    return {
        'id': ident, 'exam_id': 'exam', 'expires_at': 100, 'type': kind,
        'expected_version': agent.engine.state.version,
        'lock_id': agent.engine.state.lock_id, 'require_camera': require_camera,
    }


@pytest.mark.parametrize('kind', ['START', 'UNLOCK'])
@pytest.mark.parametrize('status', ['pending_monitor_change', 'missing_profile', 'failed_profile', 'missing_centre'])
def test_incomplete_adaptive_calibration_blocks_start_and_teacher_unlock(agent, kind, status):
    agent.camera = adaptive_camera(status)
    if kind == 'UNLOCK':
        agent.engine.start()
        agent.engine.lock('DISPLAY_CHANGED')
    before = agent.engine.state.public()
    agent.apply(command(agent, kind), now=1)
    assert agent.journal['acks'][-1] == {
        'id': 'command', 'ok': False, 'error': 'SCREEN_CALIBRATION_REQUIRED',
    }
    assert agent.engine.state.public() == before
    agent.launch_environment.assert_not_called()
    if status == 'pending_monitor_change':
        # The old profile still exists: the queued GUI request alone must close
        # this race before the capture thread processes its next frame.
        assert agent.camera.screen_calibration.ready
        assert agent.camera.public_gaze.reference is not None


@pytest.mark.parametrize('kind', ['START', 'UNLOCK'])
def test_validated_profile_allows_the_existing_command_flow(agent, kind):
    if kind == 'UNLOCK':
        agent.engine.start()
        agent.engine.lock('DISPLAY_CHANGED')
    agent.apply(command(agent, kind), now=1)
    assert agent.journal['acks'][-1]['ok']
    assert agent.engine.state.lifecycle == 'RUNNING'
    assert agent.engine.state.access == 'OPEN'
    assert agent.launch_environment.call_count == (1 if kind == 'START' else 0)


def test_new_teacher_unlock_is_required_after_successful_recalibration(agent):
    agent.engine.start()
    agent.engine.lock('DISPLAY_CHANGED')
    lock = agent.engine.state.lock_id
    agent.camera = adaptive_camera('pending_monitor_change')
    rejected = command(agent, 'UNLOCK', ident='before-calibration')
    agent.apply(rejected, now=1)
    assert not agent.journal['acks'][-1]['ok']
    agent.camera = adaptive_camera('valid')  # A newly installed, verified camera profile.
    assert agent.engine.state.access == 'LOCKED' and agent.engine.state.lock_id == lock
    agent.apply(rejected, now=2)
    assert not agent.journal['acks'][-1]['ok']  # Rejected command IDs remain idempotent.
    assert agent.engine.state.access == 'LOCKED'
    agent.apply(command(agent, 'UNLOCK', ident='after-calibration'), now=3)
    assert agent.journal['acks'][-1]['ok']
    assert agent.engine.state.access == 'OPEN'


@pytest.mark.parametrize('kind', ['START', 'UNLOCK'])
def test_require_camera_false_cannot_bypass_invalid_present_adaptive_camera(agent, kind):
    agent.camera = adaptive_camera('missing_profile')
    if kind == 'UNLOCK':
        agent.engine.start()
        agent.engine.lock('DISPLAY_CHANGED')
    agent.apply(command(agent, kind, require_camera=False), now=1)
    assert agent.journal['acks'][-1]['error'] == 'SCREEN_CALIBRATION_REQUIRED'


@pytest.mark.parametrize('kind', ['START', 'UNLOCK'])
@pytest.mark.parametrize('camera_kind', ['none', 'legacy', 'legacy_mock'])
def test_nonadaptive_and_camera_optional_commands_keep_legacy_behavior(agent, kind, camera_kind):
    agent.camera = {
        'none': None,
        'legacy': SimpleNamespace(screen_calibration_required=False, requires_gaze_reference=True),
        'legacy_mock': Mock(),
    }[camera_kind]
    if kind == 'UNLOCK':
        agent.engine.start()
        agent.engine.lock('TEACHER_LOCK')
    agent.apply(command(agent, kind, require_camera=False), now=1)
    assert agent.journal['acks'][-1]['ok']
    assert agent.engine.state.lifecycle == 'RUNNING' and agent.engine.state.access == 'OPEN'


def test_ending_exam_remains_available_when_calibration_is_invalid(agent):
    agent.camera = adaptive_camera('missing_profile')
    agent.engine.start()
    agent.engine.lock('DISPLAY_CHANGED')
    agent.apply(command(agent, 'END_AND_RELEASE'), now=1)
    assert agent.journal['acks'][-1]['ok']
    assert agent.engine.state.lifecycle == 'COMPLETED' and agent.engine.state.access == 'OPEN'


def test_valid_calibration_does_not_bypass_camera_freshness_requirement(agent):
    agent.last_observation_at = None
    agent.apply(command(agent, 'START'), now=1)
    assert agent.journal['acks'][-1]['error'] == 'CAMERA_FRAME_STALE'
    assert agent.engine.state.lifecycle == 'READY'
