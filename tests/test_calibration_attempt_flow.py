"""Real Qt attempt results reach specific feedback and aggregate local reports."""

import json
from unittest.mock import Mock

import pytest

from test_screen_calibration_flow import (
    app as app, begin_targets, calibration_state, setup as setup, until,
)


def saved_attempts(agent):
    return [json.loads(path.read_text('utf-8'))
            for path in sorted((agent.folder / 'calibration-diagnostics').glob('attempt-*.json'))]


@pytest.mark.parametrize('slow_second_attempt', [False, True])
def test_nine_constant_targets_preserve_span_failure_then_allow_fresh_retry(app, setup, slow_second_attempt):
    agent, camera, dialog = setup
    original_sample = camera.screen_calibration_sample
    results = []
    dialog.worker.screen_result.connect(results.append)

    def constant_sample():
        sample = original_sample()
        sample['gaze'].update(yaw_degrees=7., pitch_degrees=-3.)
        return sample

    camera.screen_calibration_sample = constant_sample
    overlay = begin_targets(app, dialog)
    until(app, lambda: bool(results) and overlay.start_button.text() == 'Повторить настройку')
    first = results[0]
    assert first['error'] == 'SCREEN_ANGULAR_SPAN_TOO_SMALL' and not first['ready']
    assert first['capture']['completed_targets'] == 9 and len(camera.target_sequence) == 9
    assert first['quality']['yaw_span_degrees'] == 0
    assert first['quality']['pitch_span_degrees'] == 0
    assert all(row['accepted_samples'] >= 3 for row in first['capture']['targets'].values())
    assert 'почти не различила' in overlay.error_label.text()
    assert 'горизонтали 0.0°' in overlay.error_label.text()
    assert dialog.worker.isRunning() and dialog.worker.result is None
    assert camera.install_calls == 0 and agent.camera is None
    assert not agent.capabilities['gaze'] and not camera.gaze_enabled
    saved, = saved_attempts(agent)
    assert saved['error'] == first['error'] and not saved['ready']
    assert saved['quality']['yaw_span_degrees'] == 0
    assert saved['capture']['completed_targets'] == 9
    assert saved['elapsed_seconds'] > 0
    encoded = json.dumps(saved)
    assert 'center_observation' not in encoded and 'angular_polygon' not in encoded
    assert 'reference_yaw_degrees' not in encoded and 'frame_id' not in encoded
    # A failed fit never hands the camera to Agent. A completely fresh attempt
    # may then succeed and leaves both separate, immutable aggregate reports.
    camera.screen_calibration_sample = original_sample
    delayed_reads = []
    if slow_second_attempt:
        def scheduling_delay():
            if camera.attempt == 2 and camera.target_sequence[-1][1] == 1:
                delayed_reads.append(camera.frame_id)
                # A real-time .12-second fixture window would accept at most
                # one frame, repeatedly rejecting this otherwise valid point.
                return .15
            return .004

        camera.read_delay_seconds = scheduling_delay
    overlay.start_button.click()
    until(app, lambda: agent.camera is camera,
          diagnostics=lambda: calibration_state(camera, dialog, results))
    assert camera.attempt == 2 and camera.install_calls == 1
    assert len(camera.target_sequence) == 18 and len(results) == 2
    assert results[1]['ready'] and camera.profile.ready
    assert results[1]['capture']['retry_count'] == 0
    assert all(row['accepted_samples'] >= 3 for row in results[1]['capture']['targets'].values())
    if slow_second_attempt:
        assert len(delayed_reads) >= 3
    attempts = saved_attempts(agent)
    assert len(attempts) == 2 and [row['ready'] for row in attempts] == [False, True]
    assert attempts[0]['quality']['yaw_span_degrees'] == 0


def test_failed_validation_point_repeats_automatically_and_preserves_other_points(app, setup):
    agent, camera, dialog = setup
    original_sample = camera.screen_calibration_sample
    results = []
    dialog.worker.screen_result.connect(results.append)
    retry_seen = []

    def mismatched_validation():
        sample = original_sample()
        token = camera.target_sequence[-1][1]
        if token == 6:
            sample['gaze']['yaw_degrees'] += 12.
        if token == 15:
            retry_seen.append((camera.install_calls, agent.camera, dialog.worker.result))
        return sample

    camera.screen_calibration_sample = mismatched_validation
    begin_targets(app, dialog)
    until(app, lambda: agent.camera is camera)
    result = results[0]
    assert [row[1] for row in camera.target_sequence] == [*range(9), 15]
    assert result['capture']['completed_targets'] == 9
    assert result['ready'] and result['error'] is None
    assert camera.attempt == 1 and camera.install_calls == 1
    assert retry_seen and all(row == (0, None, None) for row in retry_seen)
    assert result['quality']['yaw_span_degrees'] > 4
    assert result['quality']['pitch_span_degrees'] > 3
    assert result['quality']['validation_max_error'] < 1e-10
    assert result['quality']['adaptive_target_retries'] == result['capture']['retry_count'] == 1
    counts = {key: value['attempts'] for key, value in result['capture']['targets'].items()}
    assert counts.pop('validation_right') == 2 and set(counts.values()) == {1}
    assert result['capture']['targets']['validation_right']['last_error'] == 'SCREEN_VALIDATION_ERROR_TOO_HIGH'
    saved, = saved_attempts(agent)
    assert saved['ready'] and saved['error'] is None
    assert saved['quality']['validation_errors'] == result['quality']['validation_errors']
    assert saved['quality']['validation_max_error'] == result['quality']['validation_max_error']
    assert saved['capture']['completed_targets'] == 9
    assert saved['capture']['retry_count'] == saved['quality']['adaptive_target_retries'] == 1
    assert saved['capture']['targets']['validation_right']['attempts'] == 2


def test_diagnostic_write_error_does_not_block_valid_calibration_handoff(app, setup, monkeypatch):
    agent, camera, dialog = setup
    save = Mock(side_effect=PermissionError('synthetic diagnostic folder read-only'))
    monkeypatch.setattr('agent.calibration_diagnostics.save_calibration_report', save)
    messages, results = [], []
    dialog.worker.message.connect(messages.append)
    dialog.worker.screen_result.connect(results.append)
    begin_targets(app, dialog)
    until(app, lambda: agent.camera is camera)
    assert camera.profile.ready and camera.install_calls == 1
    assert camera.attempt == 1 and len(camera.target_sequence) == 9
    assert results[0]['ready'] and results[0]['error'] is None
    assert not camera.closed and not agent.camera_preparing
    assert agent.capabilities['camera'] and agent.capabilities['gaze']
    assert 'Не удалось сохранить диагностику настройки.' in messages
    save.assert_called_once()
    assert save.call_args.args[0] == agent.folder
    assert save.call_args.args[1]['ready'] is True
    assert not saved_attempts(agent)
