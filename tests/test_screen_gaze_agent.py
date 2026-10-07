"""Personal screen decisions travel through the real Agent and server sync."""

import numpy as np
import pytest

from test_head_review_agent import connected_agent as connected_agent
from test_screen_calibration_runtime import camera as camera, fitted_profile, raw_gaze


def calibrate(camera, at):
    signature = {'name': 'test-monitor', 'geometry': [0, 0, 1920, 1080]}
    camera.begin_screen_calibration(signature)
    captured = []
    for index in range(3):
        frame = np.full((20, 20, 3), index, np.uint8)
        camera._observe_public_gaze(frame, 1, 1, at - .3 + index * .1)
        captured.append(camera.screen_calibration_sample())
    assert all(captured)
    assert camera.install_screen_calibration(
        fitted_profile(), [row['gaze'] for row in captured],
        [row['rotation'] for row in captured], signature,
    )


def observe(agent, camera, clock, index):
    frame = np.full((20, 20, 3), index % 256, np.uint8)
    diagnostics = camera._observe_public_gaze(frame, 1, 1, clock[0])
    agent.observe(direction=diagnostics['direction'], faces=1,
                  captured_at=clock[0], gaze_diagnostics=diagnostics)
    clock[0] += .2
    return diagnostics


def test_personal_screen_strike_syncs_policy_margin_distance_and_angles(camera, connected_agent):
    agent, clock, server = connected_agent
    calibrate(camera, clock[0])
    agent.camera = camera
    camera.public_gaze.estimate.return_value = raw_gaze(yaw=25, pitch=2)
    for index in range(26):
        diagnostics = observe(agent, camera, clock, index)
        assert diagnostics['direction'] == 'LEFT'
        assert diagnostics['head_direction'] == 'SCREEN'
        if index < 25:
            assert not agent.engine.state.strikes
    event, = [row for row in agent.journal['events'] if row.get('category') == 'GAZE_STRIKE']
    assert event['duration'] == 5
    assert event['gaze_decision_policy'] == 'adaptive_screen'
    assert event['gaze_threshold_degrees'] == event['screen_margin_degrees'] == 6
    assert event['screen_distance_degrees'] == pytest.approx(7)
    assert agent.gaze_diagnostics['screen_ready']
    assert agent.gaze_diagnostics['screen_reason'] == 'outside_screen'
    assert agent.gaze_diagnostics['gaze_display_yaw_degrees'] == pytest.approx(25)
    assert agent.engine.state.access == 'OPEN'
    agent.sync()
    assert not agent.journal['events']
    saved, = [row for row in server.get('/api/snapshot').json()['events']
              if row.get('category') == 'GAZE_STRIKE']
    assert saved['type'] == 'GAZE_LEFT' and saved['direction'] == 'LEFT'
    assert saved['decision'] == 'PENDING'
    assert saved['gaze_decision_policy'] == 'adaptive_screen'
    assert saved['gaze_threshold_degrees'] == saved['screen_margin_degrees'] == 6
    assert saved['screen_distance_degrees'] == pytest.approx(7)
    assert saved['gaze_relative_yaw'] == pytest.approx(25)
    assert saved['gaze_relative_pitch'] == pytest.approx(2)
    assert saved['gaze_error90_degrees'] == 16


@pytest.mark.parametrize('yaw,error,direction,observed,reason', [
    (17, 16, 'SCREEN', 'SCREEN', 'inside_screen'),
    (22, 16, 'UNKNOWN', 'UNKNOWN', 'boundary_margin'),
    (40, 25, 'UNKNOWN', 'LEFT', 'model_uncertain'),
])
def test_screen_edges_guard_and_uncertain_banner_do_not_create_backend_strikes(
    camera, connected_agent, yaw, error, direction, observed, reason,
):
    agent, clock, server = connected_agent
    calibrate(camera, clock[0])
    agent.camera = camera
    camera.public_gaze.estimate.return_value = raw_gaze(yaw=yaw, error=error)
    for index in range(81):
        diagnostics = observe(agent, camera, clock, index)
        assert diagnostics['direction'] == direction
        assert diagnostics['gaze_observed_direction'] == observed
        assert diagnostics['screen_reason'] == reason
    assert agent.gaze_diagnostics['gaze_decision_policy'] == 'adaptive_screen'
    assert agent.gaze_diagnostics['screen_margin_degrees'] == 6
    assert agent.gaze_diagnostics['gaze_display_yaw_degrees'] == pytest.approx(yaw)
    assert agent.gaze_diagnostics['gaze_observation_uncertain'] == (direction == 'UNKNOWN')
    assert not agent.engine.state.strikes
    assert agent.engine.state.access == 'OPEN'
    agent.sync()
    assert not any(row.get('category') == 'GAZE_STRIKE'
                   for row in server.get('/api/snapshot').json()['events'])
