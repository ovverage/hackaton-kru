"""Behavioral checks for a short, personal angular screen calibration."""

import copy
import json

import numpy as np
import pytest

from shared.screen_gaze import (
    FIT_TARGETS,
    VALIDATION_TARGETS,
    ScreenGazeCalibration,
    ScreenGazeConfig,
)


def observation(x=.5, y=.5, *, yaw0=7, pitch0=-3, horizontal=-32, vertical=-24,
                error=16, jitter=0):
    return {
        "yaw_degrees": (yaw0 + (x - .5) * horizontal + jitter + 180) % 360 - 180,
        "pitch_degrees": pitch0 + (y - .5) * vertical + jitter / 2,
        "error90_degrees": error,
        "gaze_tracking_status": "tracked",
    }


def captures(**kwargs):
    return tuple({
        target_id: [observation(x, y, jitter=jitter, **kwargs) for jitter in (-.2, 0, .2)]
        for target_id, x, y in targets
    } for targets in (FIT_TARGETS, VALIDATION_TARGETS))


def fitted(**kwargs):
    profile = ScreenGazeCalibration()
    assert profile.fit(*captures(**kwargs))["ready"]
    return profile


def test_nine_targets_fit_center_and_corners_check_edges_on_separate_samples():
    assert len(FIT_TARGETS) == 5 and len(VALIDATION_TARGETS) == 4
    assert not ({row[0] for row in FIT_TARGETS} & {row[0] for row in VALIDATION_TARGETS})
    profile = fitted()
    report = profile.report()
    assert report["ready"] and report["error"] is None
    assert not report["quality"]["statistical_accuracy_guarantee"]
    assert report["quality"]["center_sample_count"] == 3
    assert report["quality"]["reference_yaw_degrees"] == pytest.approx(7)
    assert report["quality"]["reference_pitch_degrees"] == pytest.approx(-3)
    assert report["quality"]["validation_max_error"] < 1e-10
    assert json.loads(json.dumps(report)) == report


@pytest.mark.parametrize("x,y", [(.5, .5), (.05, .05), (.95, .05), (.95, .95),
                                    (.05, .95), (.05, .5), (.5, .95), (.3, .8)])
def test_calibrated_screen_and_measured_edges_remain_screen(x, y):
    result = fitted().observe(observation(x, y))
    assert result["screen_direction"] == "SCREEN"
    assert result["screen_side"] == "SCREEN"
    assert result["screen_reason"] == "inside_screen"
    assert result["screen_distance_degrees"] == 0
    assert result["screen_x"] == pytest.approx(x)
    assert result["screen_y"] == pytest.approx(y)


@pytest.mark.parametrize("x,y,side", [(-.3, .5, "LEFT"), (1.3, .5, "RIGHT"),
                                        (.5, -.3, "UP"), (.5, 1.3, "DOWN")])
def test_sustained_decision_input_is_only_beyond_angular_margin(x, y, side):
    result = fitted().observe(observation(x, y))
    assert result["screen_direction"] == side
    assert result["screen_side"] == side
    assert result["screen_reason"] == "outside_screen"
    assert result["screen_distance_degrees"] > 6


@pytest.mark.parametrize("distance,expected", [(0, "SCREEN"), (.001, "UNKNOWN"),
                                                (5.999, "UNKNOWN"), (6, "UNKNOWN"),
                                                (6.001, "LEFT")])
def test_six_degree_guard_is_angular_and_includes_exact_boundary(distance, expected):
    result = fitted().observe(observation(.05 - distance / 32, .5))
    assert result["screen_distance_degrees"] == pytest.approx(distance)
    assert result["screen_direction"] == expected


def test_guard_is_euclidean_distance_to_polygon_at_a_corner_not_axis_padding():
    profile = fitted()
    within = profile.observe(observation(.05 - 4 / 32, .05 - 4 / 24))
    beyond = profile.observe(observation(.05 - 4.5 / 32, .05 - 4.5 / 24))
    assert within["screen_distance_degrees"] == pytest.approx(np.sqrt(32))
    assert within["screen_direction"] == "UNKNOWN"
    assert beyond["screen_distance_degrees"] > 6
    assert beyond["screen_reason"] == "outside_screen"


def test_different_monitor_angular_widths_keep_same_six_degree_margin():
    narrow = fitted(horizontal=-12)
    wide = fitted(horizontal=-60)
    # At the same normalized distance, different monitors cover different angles.
    assert narrow.observe(observation(-.1, .5, horizontal=-12))["screen_direction"] == "UNKNOWN"
    assert wide.observe(observation(-.1, .5, horizontal=-60))["screen_direction"] == "LEFT"
    assert narrow.observe(observation(.05 - 6.01 / 12, .5, horizontal=-12))["screen_direction"] == "LEFT"


def test_mirrored_horizontal_axis_retains_camera_image_direction_and_physical_side():
    result = fitted(horizontal=32).observe(observation(-.3, .5, horizontal=32))
    assert result["screen_side"] == "LEFT"
    assert result["screen_direction"] == "RIGHT"  # negative raw yaw, despite physical left.


def test_yaw_wraparound_uses_personal_center_without_359_degree_jumps():
    profile = fitted(yaw0=179)
    assert profile.observe(observation(.5, .5, yaw0=179))["screen_direction"] == "SCREEN"
    assert profile.observe(observation(.95, .5, yaw0=179))["screen_direction"] == "SCREEN"
    assert profile.observe(observation(-.3, .5, yaw0=179))["screen_direction"] == "LEFT"
    assert profile.quality["yaw_span_degrees"] == pytest.approx(28.8)


def test_center_circular_median_handles_samples_on_both_sides_of_180():
    profile = fitted(yaw0=180)
    assert abs(profile.reference_yaw_degrees) == pytest.approx(180)
    assert profile.observe(observation(.5, .5, yaw0=180))["screen_direction"] == "SCREEN"


def test_validation_never_changes_fitted_mapping_or_polygon():
    fit, validation = captures()
    altered_validation = copy.deepcopy(validation)
    for rows in altered_validation.values():
        for row in rows:
            row["yaw_degrees"] += 1
    first, second = ScreenGazeCalibration(), ScreenGazeCalibration()
    assert first.fit(fit, validation)["ready"]
    assert second.fit(fit, altered_validation)["ready"]
    for point in [(.5, .5), (.1, .9), (-.3, .5), (.5, 1.4)]:
        left, right = first.observe(observation(*point)), second.observe(observation(*point))
        for key in ("screen_x", "screen_y", "screen_distance_degrees", "screen_direction"):
            assert left[key] == right[key]
    assert second.quality["validation_max_error"] > first.quality["validation_max_error"]


def test_bad_heldout_capture_rejects_perfect_fit_and_does_not_keep_old_calibration():
    profile = fitted()
    fit, validation = captures()
    validation["validation_top"] = [observation(.5, .9) for _ in range(3)]
    result = profile.fit(fit, validation)
    assert not result["ready"]
    assert result["error"] == "SCREEN_VALIDATION_ERROR_TOO_HIGH"
    assert result["quality"]["fit_max_error"] < 1e-10
    after = profile.observe(observation(-.3, .5))
    assert after["screen_direction"] == "UNKNOWN"
    assert after["screen_x"] is None and not after["screen_ready"]


@pytest.mark.parametrize("target_id", ["fit_center", "fit_top_left", "validation_bottom"])
def test_three_valid_observations_required_at_every_target(target_id):
    fit, validation = captures()
    group = fit if target_id in fit else validation
    group[target_id] = group[target_id][:2]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_INSUFFICIENT"
    assert result["quality"]["failed_target"] == target_id


@pytest.mark.parametrize("values", [None, {}, [], {"wrong": []}])
def test_missing_or_wrong_targets_do_not_silently_fit_partial_screen(values):
    fit, validation = captures()
    profile = ScreenGazeCalibration()
    assert not profile.fit(values, validation)["ready"]
    assert not profile.fit(fit, values)["ready"]


def test_invalid_samples_are_ignored_instead_of_fabricating_visibility():
    fit, validation = captures()
    fit["fit_top_left"] = [observation(.05, .05, error=21), None, observation(.05, .05)]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_INSUFFICIENT"
    assert result["quality"]["failed_target"] == "fit_top_left"


def test_unstable_three_sample_capture_fails_even_when_median_matches():
    fit, validation = captures()
    fit["fit_center"] = [observation(jitter=jitter) for jitter in (-7, 0, 7)]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_UNSTABLE"


def test_staring_at_center_for_all_targets_is_rejected():
    fit, validation = captures()
    for group in (fit, validation):
        for target_id in group:
            group[target_id] = [observation() for _ in range(3)]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_ANGULAR_SPAN_TOO_SMALL"


def test_collinear_outputs_are_not_a_two_dimensional_screen():
    fit, validation = captures()
    for group in (fit, validation):
        for rows in group.values():
            for row in rows:
                row["pitch_degrees"] = row["yaw_degrees"] / 2
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_CORNER_GEOMETRY_INVALID"


def test_mislabelled_corners_cannot_be_rescued_using_validation():
    fit, validation = captures()
    fit["fit_top_left"], fit["fit_top_right"] = fit["fit_top_right"], fit["fit_top_left"]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert not result["ready"]
    assert result["error"] in {"SCREEN_FIT_ERROR_TOO_HIGH", "SCREEN_TRANSFORM_DEGENERATE"}


def test_small_compressed_screen_rejects_noise_that_would_cross_large_screen_fraction():
    fit, validation = captures(horizontal=-6, vertical=-6)
    fit["fit_top_left"] = [observation(.05, .05, horizontal=-6, vertical=-6, jitter=j)
                           for j in (-1.5, 0, 1.5)]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_UNSTABLE_IN_SCREEN_SPACE"


@pytest.mark.parametrize("changes", [
    {"yaw_degrees": float("nan")}, {"pitch_degrees": float("inf")},
    {"yaw_degrees": "17"}, {"pitch_degrees": True}, {"error90_degrees": -1},
    {"error90_degrees": 180.01}, {"yaw_degrees": 181}, {"pitch_degrees": 91},
    {"gaze_tracking_status": "blink"}, {"gaze_tracking_status": "multiple_faces"},
    {"gaze_tracking_status": "face_landmarks_missing"},
])
def test_invalid_runtime_observation_cannot_become_a_violation(changes):
    gaze = observation(-.5, .5)
    gaze.update(changes)
    result = fitted().observe(gaze)
    assert result["screen_direction"] == "UNKNOWN"
    assert result["screen_reason"] == "observation_invalid"
    assert result["screen_x"] is None


@pytest.mark.parametrize("error", [20.01, 25, 180])
@pytest.mark.parametrize("x,y,expected", [(-.4, .5, "LEFT"), (1.4, .5, "RIGHT"),
                                          (.5, -.4, "UP"), (.5, 1.4, "DOWN"),
                                          (.5, .5, "SCREEN"), (0, .5, "UNKNOWN")])
def test_uncertain_gaze_still_has_preliminary_display_but_never_a_strict_decision(error, x, y, expected):
    result = fitted().observe(observation(x, y, error=error))
    assert result["screen_observed_direction"] == expected
    assert result["screen_direction"] == "UNKNOWN"
    assert result["screen_reason"] == "model_uncertain"
    assert result["screen_x"] == pytest.approx(x)
    assert result["screen_y"] == pytest.approx(y)


def test_finite_high_uncertainty_remains_ineligible_for_calibration():
    fit, validation = captures(error=25)
    result = ScreenGazeCalibration().fit(fit, validation)
    assert not result["ready"]
    assert result["error"] == "SCREEN_SAMPLES_INSUFFICIENT"


def test_good_quality_observed_direction_matches_strict_personal_decision():
    for x, y in [(.5, .5), (-.4, .5), (0, .5)]:
        result = fitted().observe(observation(x, y))
        assert result["screen_observed_direction"] == result["screen_direction"]


def test_old_model_direction_does_not_override_personal_screen_boundaries():
    gaze = observation(.05, .5)
    gaze["direction"] = "LEFT"
    before = copy.deepcopy(gaze)
    assert fitted().observe(gaze)["screen_direction"] == "SCREEN"
    assert gaze == before


def test_reset_disables_decisions_without_a_nine_degree_fallback():
    profile = fitted()
    profile.reset()
    result = profile.observe(observation(-.3, .5))
    assert not profile.ready and not result["screen_ready"]
    assert result["screen_direction"] == "UNKNOWN"
    assert result["screen_reason"] == "calibration_required"


@pytest.mark.parametrize("kwargs", [
    {"min_samples": 2}, {"min_samples": 3.0}, {"min_samples": True},
    {"margin_degrees": 4.99}, {"margin_degrees": 7.01},
    {"max_model_error_degrees": 21}, {"max_model_error_degrees": float("nan")},
    {"max_condition_number": 101}, {"max_validation_error": .31},
    {"max_validation_median_error": .19, "max_validation_error": .15},
])
def test_heuristic_configuration_is_bounded(kwargs):
    with pytest.raises(ValueError, match="INVALID_SCREEN_CONFIG"):
        ScreenGazeConfig(**kwargs)


@pytest.mark.parametrize("margin", [5, 6, 7])
def test_user_requested_five_to_seven_degree_margin_is_configurable(margin):
    profile = ScreenGazeCalibration(ScreenGazeConfig(margin_degrees=margin))
    assert profile.fit(*captures())["ready"]
    assert profile.observe(observation(.05 - margin / 32, .5))["screen_direction"] == "UNKNOWN"
    assert profile.observe(observation(.05 - (margin + .01) / 32, .5))["screen_direction"] == "LEFT"


def test_failed_unstable_target_retains_its_spread_and_preceding_target_metrics():
    fit, validation = captures()
    fit["fit_top_right"] = [observation(.95, .05, jitter=j) for j in (-7, 0, 7)]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_UNSTABLE"
    quality = result["quality"]
    assert quality["failed_target"] == "fit_top_right"
    assert quality["sample_counts"] == {"fit_center": 3, "fit_top_left": 3, "fit_top_right": 3}
    assert quality["sample_spreads_degrees"]["fit_top_right"] == pytest.approx(np.hypot(7, 3.5))
    assert quality["sample_spreads_degrees"]["fit_center"] < 1
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("rows,count,spread", [
    (None, 0, None), ([], 0, None),
    ([observation(), observation(error=21), None], 1, 0),
    ([observation(jitter=-.2), observation(jitter=.2)], 2, np.hypot(.2, .1)),
])
def test_insufficient_center_records_valid_count_and_available_spread(rows, count, spread):
    fit, validation = captures()
    fit["fit_center"] = rows
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_INSUFFICIENT"
    quality = result["quality"]
    assert quality["sample_counts"] == {"fit_center": count}
    measured = quality["sample_spreads_degrees"]["fit_center"]
    assert measured is None if spread is None else measured == pytest.approx(spread)


def test_circularly_unstable_center_keeps_diagnostic_spread_before_reference_exists():
    fit, validation = captures()
    fit["fit_center"] = [dict(observation(), yaw_degrees=yaw) for yaw in (-120, 0, 120)]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_UNSTABLE"
    assert result["quality"]["failed_target"] == "fit_center"
    assert result["quality"]["sample_counts"] == {"fit_center": 3}
    assert result["quality"]["sample_spreads_degrees"]["fit_center"] > 100


def test_failed_validation_sample_retains_fit_and_processed_validation_metrics():
    fit, validation = captures()
    validation["validation_right"] = [observation(.95, .5)]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_INSUFFICIENT"
    quality = result["quality"]
    assert quality["failed_target"] == "validation_right"
    assert quality["sample_counts"] == {
        **{target_id: 3 for target_id, _, _ in FIT_TARGETS},
        "validation_top": 3, "validation_right": 1,
    }
    assert set(quality["sample_spreads_degrees"]) == set(quality["sample_counts"])
    assert quality["sample_spreads_degrees"]["validation_right"] == 0


def test_validation_error_identifies_worst_target_without_discarding_capture_metrics():
    fit, validation = captures()
    validation["validation_bottom"] = [observation(.5, .05)] * 3
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_VALIDATION_ERROR_TOO_HIGH"
    quality = result["quality"]
    assert quality["failed_target"] == "validation_bottom"
    assert len(quality["sample_counts"]) == len(quality["sample_spreads_degrees"]) == 9
    assert quality["validation_errors"]["validation_bottom"] == pytest.approx(.9)


def test_screen_space_spread_failure_identifies_measured_target():
    fit, validation = captures(horizontal=-6, vertical=-6)
    fit["fit_top_left"] = [observation(.05, .05, horizontal=-6, vertical=-6, jitter=j)
                           for j in (-1.5, 0, 1.5)]
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_SAMPLES_UNSTABLE_IN_SCREEN_SPACE"
    quality = result["quality"]
    assert quality["failed_target"] == "fit_top_left"
    assert quality["sample_spreads_degrees"]["fit_top_left"] == pytest.approx(np.hypot(1.5, .75))
    assert len(quality["sample_counts"]) == 9


def test_bad_fit_identifies_largest_residual_target_and_preserves_fit_metrics():
    fit, validation = captures()
    fit["fit_center"] = [observation(.85, .5)] * 3
    result = ScreenGazeCalibration().fit(fit, validation)
    assert result["error"] == "SCREEN_FIT_ERROR_TOO_HIGH"
    quality = result["quality"]
    assert quality["failed_target"] == "fit_center"
    assert len(quality["sample_counts"]) == len(quality["sample_spreads_degrees"]) == 5
    assert quality["fit_max_error"] > ScreenGazeConfig().max_fit_error
