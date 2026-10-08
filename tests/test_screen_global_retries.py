"""Global setup failures remeasure coherent windows without lowering any gate."""

import pytest

from shared.head_pose import HeadPoseObserver, rotation_distance
from test_screen_capture import CaptureHarness
from test_screen_capture_head import rotation


def test_small_span_remeasures_corners_and_new_checks_but_keeps_center(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def collapsed_once(row):
        row['gaze']['presentation'] = harness.current
        if harness.current < 9:
            row['gaze'].update(yaw_degrees=7., pitch_degrees=-3.)
        return row

    harness.sample_policy = collapsed_once
    result = harness.run()
    assert result['ready'] and len(harness.beginnings) == 1
    assert [token for token, *_ in harness.targets] == [*range(9), *range(10, 18)]
    assert result['capture']['retry_count'] == 8
    assert result['capture']['targets']['fit_center']['attempts'] == 1
    assert all(row['presentation'] == 0 for row in harness.installs[0][1])
    assert result['quality']['yaw_span_degrees'] > 4
    assert result['quality']['pitch_span_degrees'] > 3
    assert result['quality']['validation_max_error'] < 1e-10


@pytest.mark.parametrize('failure', ['geometry', 'affine_fit'])
def test_global_inconsistent_fit_replaces_every_window_and_can_rebase_head(monkeypatch, failure):
    harness = CaptureHarness(monkeypatch)

    def inconsistent_once(row):
        token = harness.current
        row['rotation'] = rotation(0 if token < 9 else 30)
        row['gaze']['presentation'] = token
        if failure == 'geometry' and token == 1:
            row['gaze'].update(yaw_degrees=7., pitch_degrees=-3.)
        if failure == 'affine_fit' and token == 0:
            row['gaze']['yaw_degrees'] = 7 + (.85 - .5) * -32
        return row

    harness.sample_policy = inconsistent_once
    result = harness.run()
    assert result['ready'] and len(harness.beginnings) == 1
    assert [token for token, *_ in harness.targets] == list(range(18))
    assert all(stats['attempts'] == 2 for stats in result['capture']['targets'].values())
    expected = 'SCREEN_CORNER_GEOMETRY_INVALID' if failure == 'geometry' else 'SCREEN_FIT_ERROR_TOO_HIGH'
    assert result['capture']['targets']['fit_center']['last_error'] == expected
    profile, center, poses, _ = harness.installs[0]
    assert all(row['presentation'] == 9 for row in center)
    observer = HeadPoseObserver()
    assert observer.set_reference_rotations(poses, required_samples=len(poses))
    assert rotation_distance(observer.reference, rotation(30)) == pytest.approx(0)
    assert profile.ready and result['quality']['validation_max_error'] < 1e-10


def test_repeated_same_validation_disagreement_escalates_to_new_fit_and_checks(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def repeatedly_wrong_right(row):
        if harness.current in (6, 15):
            row['gaze']['yaw_degrees'] += 18
        return row

    harness.sample_policy = repeatedly_wrong_right
    result = harness.run()
    assert result['ready']
    assert [token for token, *_ in harness.targets] == [*range(9), 15, 9, 10, 11, 12, 13, 14, 24, 16, 17]
    assert result['capture']['retry_count'] == 10
    assert result['capture']['targets']['validation_right']['attempts'] == 3
    assert all(stats['attempts'] == 2 for key, stats in result['capture']['targets'].items()
               if key != 'validation_right')
    assert result['quality']['validation_max_error'] < 1e-10


def test_median_validation_failure_remeasures_all_disagreeing_checks(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def checks_wrong_once(row):
        if 5 <= harness.current <= 8:
            row['gaze']['yaw_degrees'] += 9
        return row

    harness.sample_policy = checks_wrong_once
    result = harness.run()
    assert result['ready']
    assert [token for token, *_ in harness.targets] == [*range(9), 14, 15, 16, 17]
    assert all(stats['attempts'] == (2 if key.startswith('validation_') else 1)
               for key, stats in result['capture']['targets'].items())


def test_delayed_gui_ack_retries_new_presentation_then_completes(monkeypatch):
    harness = CaptureHarness(monkeypatch)
    harness.allow_ack = False

    def gui_recovers():
        if harness.current == 9:
            harness.allow_ack = True

    harness.on_read = gui_recovers
    result = harness.run()
    assert result['ready']
    assert [token for token, *_ in harness.targets] == [0, 9, *range(1, 9)]
    assert all(token != 0 for token, *_ in harness.sample_calls)
    assert result['capture']['targets']['fit_center']['last_error'] == 'SCREEN_TARGET_NOT_PRESENTED'


def test_reference_install_failure_resumes_collection_before_automatic_new_set(monkeypatch):
    harness = CaptureHarness(monkeypatch)
    begun, install = harness.begin_screen_calibration, harness.install_screen_calibration
    active = False
    attempted = 0

    def begin(signature):
        nonlocal active
        active = True
        return begun(signature)

    def install_once_bad(*args):
        nonlocal active, attempted
        attempted += 1
        if attempted == 1:
            active = False
            harness.screen_calibration_progress['error'] = 'GAZE_REFERENCE_UNSTABLE'
            return False
        return install(*args)

    harness.begin_screen_calibration = begin
    harness.install_screen_calibration = install_once_bad
    harness.sample_policy = lambda row: row if active else None
    result = harness.run()
    assert result['ready'] and attempted == 2
    assert len(harness.beginnings) == 2
    assert [token for token, *_ in harness.targets] == list(range(18))
    assert result['capture']['targets']['fit_center']['last_error'] == 'GAZE_REFERENCE_UNSTABLE'


def test_monitor_change_during_install_still_stops_without_retry(monkeypatch):
    harness = CaptureHarness(monkeypatch)
    harness.install_allowed = False
    harness.screen_calibration_progress['error'] = 'SCREEN_CHANGED'
    result = harness.run()
    assert result['error'] == 'SCREEN_CHANGED' and not result['ready']
    assert len(harness.targets) == 9 and len(harness.beginnings) == 1
    assert not harness.installs


def test_uncertain_target_repeats_until_original_model_quality_limit_is_met(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def uncertain_once(row):
        if harness.current == 1:
            row['gaze']['error90_degrees'] = 25
        return row

    harness.sample_policy = uncertain_once
    result = harness.run()
    assert result['ready']
    assert [token for token, *_ in harness.targets] == [0, 1, 10, *range(2, 9)]
    assert result['capture']['targets']['fit_top_left']['last_error'] == 'SCREEN_SAMPLES_INSUFFICIENT'
    assert harness.installs[0][0].config.max_model_error_degrees == 20
