"""Early target quality uses raw capture stability, never screen fit feedback."""

from copy import deepcopy
import json

import numpy as np
import pytest

from shared.screen_gaze import ScreenGazeCalibration, ScreenGazeConfig
from test_screen_gaze import captures, fitted, observation


def samples(yaws, pitch=-3, error=16):
    return [dict(observation(), yaw_degrees=yaw, pitch_degrees=pitch,
                 error90_degrees=error) for yaw in yaws]


def test_three_good_raw_observations_pass_before_reference_or_screen_fit_exists():
    profile = ScreenGazeCalibration()
    result = profile.check_target(samples([7, 7.2, 6.8]))
    assert result == {"ready": True, "error": None,
                      "quality": {"sample_count": 3, "sample_spread_degrees": pytest.approx(.2)}}
    assert not profile.ready and profile.reference_yaw_degrees is None
    assert profile.quality == {}
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("rows,count", [(None, 0), ([], 0), ({}, 0),
                                       ("bad input", 0), ([None, {}], 0),
                                       (samples([7, 8]), 2)])
def test_incomplete_target_requests_retry_with_actual_valid_count(rows, count):
    result = ScreenGazeCalibration().check_target(rows)
    assert result["error"] == "SCREEN_SAMPLES_INSUFFICIENT" and not result["ready"]
    assert result["quality"]["sample_count"] == count
    if not count:
        assert result["quality"]["sample_spread_degrees"] is None


@pytest.mark.parametrize("changes", [
    {"error90_degrees": 20.1}, {"error90_degrees": -1},
    {"yaw_degrees": float("nan")}, {"pitch_degrees": float("inf")},
    {"yaw_degrees": True}, {"pitch_degrees": 91}, {"yaw_degrees": 181},
    {"gaze_tracking_status": "blink"}, {"gaze_tracking_status": "multiple_faces"},
])
def test_invalid_or_uncertain_sample_cannot_complete_target(changes):
    rows = samples([7, 7, 7])
    rows[-1].update(changes)
    result = ScreenGazeCalibration().check_target(rows)
    assert result["error"] == "SCREEN_SAMPLES_INSUFFICIENT"
    assert result["quality"]["sample_count"] == 2


def test_not_calibrated_raw_cnn_observations_are_valid_target_input():
    rows = samples([7, 7, 7])
    for row in rows:
        row["gaze_tracking_status"] = "not_calibrated"
    assert ScreenGazeCalibration().check_target(rows)["ready"]


def test_target_quality_wraps_yaw_instead_of_treating_180_boundary_as_motion():
    result = ScreenGazeCalibration().check_target(samples([179.9, -180, -179.9]))
    assert result["ready"]
    assert result["quality"]["sample_spread_degrees"] == pytest.approx(.1)


@pytest.mark.parametrize("spread,ready", [(3.999, True), (4., True), (4.001, False)])
def test_existing_four_degree_spread_gate_keeps_exact_boundary(spread, ready):
    result = ScreenGazeCalibration().check_target(samples([-spread, 0, spread]))
    assert result["ready"] is ready
    assert result["quality"]["sample_spread_degrees"] == pytest.approx(spread)
    assert result["error"] == (None if ready else "SCREEN_SAMPLES_UNSTABLE")


def test_two_axis_p90_spread_is_checked_without_axiswise_relaxation():
    rows = [dict(row, pitch_degrees=row["yaw_degrees"]) for row in samples([-3, 0, 3])]
    result = ScreenGazeCalibration().check_target(rows)
    assert result["error"] == "SCREEN_SAMPLES_UNSTABLE"
    assert result["quality"]["sample_spread_degrees"] == pytest.approx(np.sqrt(18))


def test_circularly_unstable_target_cannot_pass_early_check():
    result = ScreenGazeCalibration().check_target(samples([-120, 0, 120]))
    assert result["error"] == "SCREEN_SAMPLES_UNSTABLE"
    assert result["quality"]["sample_count"] == 3
    assert result["quality"]["sample_spread_degrees"] > 100


def test_configured_existing_count_and_spread_gates_are_used():
    profile = ScreenGazeCalibration(ScreenGazeConfig(min_samples=5, max_sample_spread_degrees=1))
    assert profile.check_target(samples([0, 0, 0]))["error"] == "SCREEN_SAMPLES_INSUFFICIENT"
    assert profile.check_target(samples([0, 0, 0, 0, 0]))["ready"]
    assert profile.check_target(samples([-2, -1, 0, 1, 2]))["error"] == "SCREEN_SAMPLES_UNSTABLE"


@pytest.mark.parametrize("rows", [samples([7, 7, 7]), samples([-7, 0, 7]), None])
def test_check_never_changes_existing_profile_or_inputs(rows):
    profile = fitted()
    before_report, before_rows = deepcopy(profile.report()), deepcopy(rows)
    coefficients, polygon = profile._coefficients.copy(), profile._polygon.copy()
    profile.check_target(rows)
    assert profile.report() == before_report
    assert np.array_equal(profile._coefficients, coefficients)
    assert np.array_equal(profile._polygon, polygon)
    assert rows == before_rows


def test_early_target_pass_is_not_a_substitute_for_global_angular_span_validation():
    fit, validation = captures()
    profile = ScreenGazeCalibration()
    for group in (fit, validation):
        for key in group:
            group[key] = samples([7, 7, 7])
            assert profile.check_target(group[key])["ready"]
    result = profile.fit(fit, validation)
    assert result["error"] == "SCREEN_ANGULAR_SPAN_TOO_SMALL"
    assert not result["ready"] and "failed_target" not in result["quality"]


def test_early_failure_has_same_spread_and_error_as_full_center_gate():
    fit, validation = captures()
    fit["fit_center"] = samples([-7, 0, 7])
    profile = ScreenGazeCalibration()
    early = profile.check_target(fit["fit_center"])
    final = profile.fit(fit, validation)
    assert early["error"] == final["error"] == "SCREEN_SAMPLES_UNSTABLE"
    assert early["quality"]["sample_spread_degrees"] == pytest.approx(
        final["quality"]["sample_spreads_degrees"]["fit_center"])
