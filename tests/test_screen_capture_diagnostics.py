"""Capture transition/failure regressions with timestamped synthetic observations."""

from test_screen_capture import CaptureHarness


def test_fixed_settling_window_excludes_transition_frames_before_fitting(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def eye_movement(row):
        # A deterministic transition unrelated to desired fit or quality scores.
        if harness.clock.at - harness.target_times[-1] < .8:
            row['gaze'].update(yaw_degrees=7., pitch_degrees=-3.)
        return row

    harness.sample_policy = eye_movement
    result = harness.run()
    assert result['ready']
    assert result['capture']['settle_seconds'] == .8
    assert all(at - harness.target_times[index] >= .8
               for index, at, _ in harness.sample_calls)
    assert result['quality']['validation_max_error'] < 1e-10


def test_all_nine_points_with_unmoving_gaze_preserve_actual_final_failure(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def staring_at_center(row):
        row['gaze'].update(yaw_degrees=7., pitch_degrees=-3.)
        return row

    harness.sample_policy = staring_at_center
    result = harness.run()
    assert len(harness.targets) == result['capture']['completed_targets'] == 9
    assert result['error'] == 'SCREEN_ANGULAR_SPAN_TOO_SMALL'
    assert result['quality']['yaw_span_degrees'] == 0
    assert result['quality']['pitch_span_degrees'] == 0
    assert not result['ready'] and not harness.installs
    assert all(row['accepted_samples'] >= 3 for row in result['capture']['targets'].values())


def test_live_no_sample_feedback_explains_model_uncertainty(monkeypatch):
    harness = CaptureHarness(monkeypatch)
    original_read = harness.read

    def uncertain_read():
        original_read()
        return {'gaze_diagnostics': {'gaze_tracking_status': 'tracked', 'error90_degrees': 25.}}

    harness.read = uncertain_read
    harness.sample_policy = lambda row: None
    harness.on_read = lambda: harness.stop.set() if harness.current == 9 else None
    result = harness.run()
    point = result['capture']['targets']['fit_center']
    assert point['accepted_samples'] == 0
    assert point['rejections']['model_uncertain'] == point['total_frames']
    assert all('неуверенная' in message for _, _, message in harness.progress)
    assert result['capture']['failed_target'] == 'fit_center'
    assert point['last_error'] == 'SCREEN_TOO_FEW_FRESH_SAMPLES'
    assert result['error'] == 'SCREEN_CALIBRATION_CANCELLED'


def test_current_sample_loss_never_claims_tracking_still_active(monkeypatch):
    harness = CaptureHarness(monkeypatch)
    harness.sample_policy = lambda row: row if harness.local_sample <= 3 else None
    result = harness.run()
    assert result['ready']
    assert any('Трекинг взгляда работает' in text for _, _, text in harness.progress)
    assert any('ждём пригодной оценки' in text for _, _, text in harness.progress)
    assert result['capture']['targets']['fit_center']['accepted_samples'] == 3
