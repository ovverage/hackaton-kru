"""Retries preserve good measurements and require a new exposure acknowledgement."""

from test_screen_capture import CaptureHarness


def test_unstable_point_repeats_only_it_before_advancing(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def noisy_once(row):
        if harness.current == 1:
            row['gaze']['yaw_degrees'] += 10 if harness.local_sample % 2 else -10
        return row

    harness.sample_policy = noisy_once
    result = harness.run()
    assert result['ready']
    assert [target[0] for target in harness.targets] == [0, 1, 10, 2, 3, 4, 5, 6, 7, 8]
    assert result['capture']['retry_count'] == 1
    assert result['capture']['targets']['fit_center']['attempts'] == 1
    assert result['capture']['targets']['fit_top_left']['attempts'] == 2
    assert result['quality']['sample_spreads_degrees']['fit_top_left'] == 0
    assert len(harness.beginnings) == len(harness.installs) == 1


def test_late_validation_failure_replaces_only_failed_point(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def wrong_right_once(row):
        if harness.current == 6:
            row['gaze']['yaw_degrees'] += 18
        return row

    harness.sample_policy = wrong_right_once
    result = harness.run()
    assert result['ready']
    assert [target[0] for target in harness.targets] == [*range(9), 15]
    assert result['capture']['completed_targets'] == 9
    assert result['capture']['targets']['validation_right']['attempts'] == 2
    assert all(item['attempts'] == 1 for key, item in result['capture']['targets'].items()
               if key != 'validation_right')
    assert result['quality']['validation_max_error'] < 1e-10
    assert result['quality']['adaptive_target_retries'] == 1


def test_repeated_failure_runs_past_old_session_timeout_and_remains_cancellable(monkeypatch):
    harness = CaptureHarness(monkeypatch)
    harness.sample_policy = lambda row: None

    def cancel_after_many_repeats():
        if len(harness.targets) == 24:
            harness.stop.set()

    harness.on_read = cancel_after_many_repeats
    result = harness.run()
    assert result['error'] == 'SCREEN_CALIBRATION_CANCELLED'
    assert result['elapsed_seconds'] > 65
    assert not harness.installs
    assert {token % 9 for token, *_ in harness.targets} == {0}
    assert result['capture']['retry_count'] == 23
    assert len(result['capture']['targets']) == 1
    assert result['capture']['targets']['fit_center']['attempts'] == 24


def test_late_ack_for_previous_exposure_cannot_authorize_retry(monkeypatch):
    harness = CaptureHarness(monkeypatch)
    harness.sample_policy = lambda row: None

    def deliver_old_ack():
        if harness.current == 9:
            harness.allow_ack = False
            # Freshly queued, but identifies the PREVIOUS presentation of point0.
            harness.session.presented(0, harness.signature)

    harness.on_read = deliver_old_ack
    result = harness.run()
    assert result['error'] == 'SCREEN_TARGET_NOT_PRESENTED'
    assert [row[0] for row in harness.targets] == [0, 9]
    assert all(token == 0 for token, _, _ in harness.sample_calls)
    assert not harness.installs


def test_retried_point_never_reuses_samples_from_failed_exposure(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def sparse_then_good(row):
        if harness.current == 0 and harness.local_sample > 2:
            return None
        row['gaze']['presentation'] = harness.current
        return row

    harness.sample_policy = sparse_then_good
    result = harness.run()
    assert result['ready']
    _, center, rotations, _ = harness.installs[0]
    assert all(row['presentation'] == 9 for row in center)
    assert len(center) == len(rotations)
    assert result['capture']['targets']['fit_center']['attempts'] == 2
