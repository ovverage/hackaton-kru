"""Closed-demo operational sensitivity, independent of model accuracy claims."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from agent.vision import Camera
from shared.public_gaze import PublicGazeEstimator
from shared.rules import RuleEngine
from test_head_review_agent import connected_agent as connected_agent


def estimate(yaw=0., pitch=0., error=16.):
    y, p = math.radians(yaw), math.radians(pitch)
    return {
        'vector': [math.sin(y) * math.cos(p), math.sin(p), -math.cos(y) * math.cos(p)],
        'yaw_degrees': yaw, 'pitch_degrees': pitch, 'error90_degrees': error,
        'source': 'public_gaze_model',
    }


def model(yaw=0., pitch=0., *, error=16., ready=True):
    estimator = PublicGazeEstimator.__new__(PublicGazeEstimator)
    estimator.np = np
    estimator.reference = [0., 0., -1.] if ready else None
    estimator.reference_error = 16. if ready else None
    estimator.estimate = Mock(return_value=estimate(yaw, pitch, error))
    return estimator


def camera(yaw=0., pitch=0., *, error=16., ready=True):
    result = Camera.__new__(Camera)
    result.public_gaze = model(yaw, pitch, error=error, ready=ready)
    result.cancel_gaze_reference()
    result.public_gaze.reference = [0., 0., -1.] if ready else None
    result.public_gaze.reference_error = 16. if ready else None
    result.gaze_vector = None  # Eyes-only decisions cannot require legacy iris features.
    result.gaze_blinks = [.05, .05]
    result.gaze_landmarks = [SimpleNamespace(x=.5, y=.5)] * 478
    result.head_rotation = np.eye(3)
    if ready:
        assert result.head_pose.set_reference_rotations([np.eye(3)] * 25)
    return result


def observe(instance, at, *, faces=1, mesh_faces=1):
    return instance._observe_public_gaze(np.zeros((40, 40, 3), np.uint8), faces, mesh_faces, at)


@pytest.mark.parametrize('magnitude', [8.9, 9., 10., 25.])
@pytest.mark.parametrize('axis,sign,direction', [
    ('yaw', 1, 'LEFT'), ('yaw', -1, 'RIGHT'), ('pitch', 1, 'UP'), ('pitch', -1, 'DOWN'),
])
def test_demo_policy_inclusive_boundaries_in_each_direction(magnitude, axis, sign, direction):
    estimator = model(**{axis: magnitude * sign})
    result = estimator.observe(None, [], eye_blink=[.05, .05], demo_sensitivity=True)
    assert result['direction'] == ('CENTER' if magnitude < 9 else direction)
    assert result['gaze_decision_policy'] == 'demo_sensitivity'
    assert result['gaze_decision_threshold_degrees'] == 9
    assert result['error90_degrees'] == 16  # Display sensitivity cannot rewrite model uncertainty.
    assert estimator.reference_error == 16


@pytest.mark.parametrize('yaw,pitch', [(25, 0), (-25, 0), (0, 25), (0, -25)])
def test_default_estimator_keeps_conservative_uncertainty_margin(yaw, pitch):
    result = model(yaw, pitch).observe(None, [], eye_blink=[.05, .05])
    assert result['direction'] == 'UNKNOWN'
    assert result['gaze_decision_policy'] == 'uncertainty_guard'
    assert result['gaze_decision_reason'] == 'uncertainty_margin'


@pytest.mark.parametrize('yaw,pitch,direction', [(10, 0, 'LEFT'), (-10, 0, 'RIGHT'), (0, -10, 'DOWN')])
def test_real_camera_eyes_only_reaches_rules_after_five_seconds_not_in_ready(yaw, pitch, direction):
    instance = camera(yaw, pitch)
    engine = RuleEngine()
    for index in range(31):
        gaze = observe(instance, index * .2)
        assert gaze['direction'] == direction
        assert gaze['head_direction'] == 'SCREEN' and not gaze['head_away']
        assert engine.observe(index * .2, direction=gaze['direction'], faces=1) == []
    assert engine.seconds == 0 and engine.candidate is None
    assert not engine.state.strikes
    engine.start()
    events = []
    for index in range(26):
        at = 10 + index * .2
        gaze = observe(instance, at)
        events.extend(engine.observe(at, direction=gaze['direction'], faces=1))
        if index < 25:
            assert not engine.state.strikes
    strike, = [event for event in events if event.get('category') == 'GAZE_STRIKE']
    assert strike['type'] == 'GAZE_' + direction and strike['duration'] == 5.
    assert strike['at'] == 15 and strike['start'] == 10


@pytest.mark.parametrize('condition', [
    'low_quality', 'blink', 'eye_state_missing', 'no_reference', 'no_face', 'multiple_faces', 'no_mesh',
])
def test_demo_sensitivity_never_bypasses_visibility_reference_or_quality_gates(condition):
    instance = camera(yaw=25, error=20.01 if condition == 'low_quality' else 16,
                      ready=condition != 'no_reference')
    faces, mesh_faces = 1, 1
    if condition == 'blink':
        instance.gaze_blinks = [.9, .9]
    elif condition == 'eye_state_missing':
        instance.gaze_blinks = None
    elif condition == 'no_face':
        faces = mesh_faces = 0
    elif condition == 'multiple_faces':
        faces = mesh_faces = 2
    elif condition == 'no_mesh':
        mesh_faces = 0
        instance.gaze_landmarks = None
        instance.head_rotation = None
    engine = RuleEngine()
    engine.start()
    events = []
    for index in range(36):
        at = index * .2
        gaze = observe(instance, at, faces=faces, mesh_faces=mesh_faces)
        assert gaze['direction'] == 'UNKNOWN' and not gaze['attention_away']
        events.extend(engine.observe(at, direction=gaze['direction'], faces=faces))
    assert not engine.state.strikes
    assert not any(event.get('category') == 'GAZE_STRIKE' for event in events)


def test_measured_quality_boundary_twenty_degrees_is_still_inclusive():
    accepted = model(yaw=10, error=20).observe(None, [], eye_blink=[.05, .05], demo_sensitivity=True)
    rejected = model(yaw=10, error=20.001).observe(None, [], eye_blink=[.05, .05], demo_sensitivity=True)
    assert accepted['direction'] == 'LEFT'
    assert rejected['direction'] == 'UNKNOWN' and rejected['gaze_decision_reason'] == 'model_uncertain'


def test_blink_display_hold_does_not_add_time_to_an_eyes_only_strike():
    instance = camera(yaw=10)
    engine = RuleEngine()
    engine.start()
    for index in range(21):
        gaze = observe(instance, index * .2)
        engine.observe(index * .2, direction=gaze['direction'], faces=1)
    assert engine.seconds == pytest.approx(4)
    instance.gaze_blinks = [.9, .9]
    blink = observe(instance, 4.15)
    assert blink['direction'] == 'UNKNOWN'
    assert blink['gaze_display_stale'] and blink['gaze_observed_direction'] == 'LEFT'
    engine.observe(4.15, direction=blink['direction'], faces=1)
    assert engine.seconds == pytest.approx(4) and not engine.state.strikes
    instance.gaze_blinks = [.05, .05]
    for at in [4.3, 4.5, 4.7, 4.9, 5.1]:
        gaze = observe(instance, at)
        engine.observe(at, direction=gaze['direction'], faces=1)
    assert not engine.state.strikes  # Missing intervals contribute no duration.
    gaze = observe(instance, 5.3)
    engine.observe(5.3, direction=gaze['direction'], faces=1)
    assert len(engine.state.strikes) == 1


def test_eyes_only_demo_strike_reaches_actual_agent_and_server_with_policy_diagnostics(connected_agent):
    agent, clock, server = connected_agent
    instance = camera(yaw=-10)
    for index in range(26):
        gaze = observe(instance, clock[0])
        assert gaze['head_direction'] == 'SCREEN'
        agent.observe(direction=gaze['direction'], faces=1, captured_at=clock[0], gaze_diagnostics=gaze)
        if index < 25:
            assert not agent.engine.state.strikes
        clock[0] += .2
    event, = [event for event in agent.journal['events'] if event.get('category') == 'GAZE_STRIKE']
    assert event['type'] == 'GAZE_RIGHT' and event['duration'] == 5
    assert agent.gaze_diagnostics['gaze_decision_policy'] == 'demo_sensitivity'
    assert agent.gaze_diagnostics['gaze_decision_threshold_degrees'] == 9
    assert agent.gaze_diagnostics['gaze_display_yaw_degrees'] == pytest.approx(-10)
    assert agent.gaze_diagnostics['head_direction'] == 'SCREEN'
    assert agent.engine.state.access == 'OPEN'
    agent.sync()
    saved, = [event for event in server.get('/api/snapshot').json()['events']
              if event.get('category') == 'GAZE_STRIKE']
    assert saved['type'] == 'GAZE_RIGHT' and saved['direction'] == 'RIGHT'
    assert saved['decision'] == 'PENDING'
    assert saved['gaze_decision_policy'] == 'demo_sensitivity'
    assert saved['gaze_threshold_degrees'] == 9
    assert saved['gaze_relative_yaw'] == pytest.approx(-10)
    assert saved['gaze_error90_degrees'] == 16
