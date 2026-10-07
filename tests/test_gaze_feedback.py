"""Visible gaze/head motion must not turn uncertain predictions into evidence."""

import copy
import math

import pytest

from shared.gaze_feedback import public_gaze_feedback


def observation(yaw=0, pitch=0, error=17, direction="UNKNOWN"):
    return dict(yaw_degrees=yaw, pitch_degrees=pitch, error90_degrees=error,
                direction=direction, source="public_gaze_model", attention_away=False,
                reference_ready=True)


@pytest.mark.parametrize("yaw,pitch,direction", [
    (0, -22, "DOWN"), (25, 0, "LEFT"), (-25, 0, "RIGHT"), (0, 22, "UP"),
    (8.9, 7, "SCREEN"), (20, -20, "DOWN"), (30, -20, "LEFT"),
])
def test_motion_is_visible_without_changing_strict_decision(yaw, pitch, direction):
    gaze = observation(yaw, pitch)
    before = copy.deepcopy(gaze)
    feedback = public_gaze_feedback(gaze, [0, 0, -1])
    assert gaze == before
    assert feedback == {
        "gaze_observed_direction": direction,
        "gaze_observation_uncertain": True,
        "gaze_feedback_reason": "strict_unknown",
        "gaze_display_yaw_degrees": pytest.approx(yaw),
        "gaze_display_pitch_degrees": pytest.approx(pitch),
        "gaze_display_threshold_degrees": 9.0,
    }
    assert gaze["direction"] == "UNKNOWN" and not gaze["attention_away"]


def test_reference_is_required_and_angles_are_relative_with_wrapped_yaw():
    assert public_gaze_feedback(observation(30), None)["gaze_observed_direction"] == "UNKNOWN"
    reference = [math.sin(math.radians(175)), 0, -math.cos(math.radians(175))]
    feedback = public_gaze_feedback(observation(-160), reference)
    assert feedback["gaze_observed_direction"] == "LEFT"  # +25 degrees, not -335.
    assert feedback["gaze_display_yaw_degrees"] == pytest.approx(25)
    pitch_reference = [0, math.sin(math.radians(20)), -math.cos(math.radians(20))]
    assert public_gaze_feedback(observation(0, 0), pitch_reference)["gaze_observed_direction"] == "DOWN"


@pytest.mark.parametrize("gaze", [None, {}, {"direction": "UNKNOWN"}, observation(error=None)])
def test_no_observation_never_looks_like_screen(gaze):
    feedback = public_gaze_feedback(gaze, [0, 0, -1])
    assert feedback["gaze_observed_direction"] == "UNKNOWN"
    assert feedback["gaze_feedback_reason"] == "observation_missing"
    assert feedback["gaze_display_yaw_degrees"] is None
    assert feedback["gaze_display_pitch_degrees"] is None


@pytest.mark.parametrize("field,value", [
    ("yaw_degrees", float("nan")), ("pitch_degrees", float("inf")),
    ("error90_degrees", -1), ("error90_degrees", "invalid"),
])
def test_invalid_values_are_not_visible_directions(field, value):
    gaze = observation(30)
    gaze[field] = value
    feedback = public_gaze_feedback(gaze, [0, 0, -1])
    assert feedback["gaze_observed_direction"] == "UNKNOWN"
    assert feedback["gaze_feedback_reason"] == "invalid_observation"


@pytest.mark.parametrize("reference", [[0, 0, 0], [0, 0], [float("nan"), 0, -1]])
def test_invalid_reference_does_not_make_an_observation(reference):
    feedback = public_gaze_feedback(observation(30), reference)
    assert feedback["gaze_observed_direction"] == "UNKNOWN"
    assert feedback["gaze_feedback_reason"] == "reference_missing"


def test_high_uncertainty_keeps_motion_visible_but_explicitly_uncertain():
    gaze = observation(0, -25, error=25)
    feedback = public_gaze_feedback(gaze, [0, 0, -1])
    assert feedback["gaze_observed_direction"] == "DOWN"
    assert feedback["gaze_observation_uncertain"]
    assert feedback["gaze_feedback_reason"] == "uncertain_observation"


@pytest.mark.parametrize("error", [15, 20])
def test_strict_direction_remains_the_only_confident_decision(error):
    gaze = observation(40, error=error, direction="LEFT")
    feedback = public_gaze_feedback(gaze, [0, 0, -1])
    assert feedback["gaze_observed_direction"] == "LEFT"
    assert feedback["gaze_observation_uncertain"] is False


@pytest.mark.parametrize("magnitude", [8.9, 9.0, 9.1])
@pytest.mark.parametrize("axis,sign,away", [
    ("yaw", 1, "LEFT"), ("yaw", -1, "RIGHT"),
    ("pitch", 1, "UP"), ("pitch", -1, "DOWN"),
])
def test_nine_degree_display_boundary_is_inclusive_for_both_axes(magnitude, axis, sign, away):
    gaze = observation(**{axis: magnitude * sign}, direction="CENTER")
    before = copy.deepcopy(gaze)
    feedback = public_gaze_feedback(gaze, [0, 0, -1])
    assert feedback["gaze_observed_direction"] == ("SCREEN" if magnitude < 9 else away)
    assert feedback["gaze_observation_uncertain"] is (magnitude >= 9)
    assert feedback["gaze_feedback_reason"] == ("observed" if magnitude < 9 else "strict_mismatch")
    assert feedback[f"gaze_display_{axis}_degrees"] == pytest.approx(magnitude * sign)
    assert gaze == before


@pytest.mark.parametrize("error", [15, 20, 25])
@pytest.mark.parametrize("strict", ["UNKNOWN", "CENTER", "LEFT"])
def test_neutral_point_estimate_stays_visible_despite_uncertainty(error, strict):
    gaze = observation(yaw=5, pitch=-8.9, error=error, direction=strict)
    feedback = public_gaze_feedback(gaze, [0, 0, -1])
    assert feedback["gaze_observed_direction"] == "SCREEN"
    assert feedback["gaze_observation_uncertain"] is (error > 20 or strict != "CENTER")
    assert gaze["direction"] == strict


@pytest.mark.parametrize("sign,away", [(1, "LEFT"), (-1, "RIGHT")])
def test_nine_degree_boundary_works_across_yaw_wraparound(sign, away):
    reference_yaw = sign * 175
    reference = [math.sin(math.radians(reference_yaw)), 0, -math.cos(math.radians(reference_yaw))]
    observed_yaw = -sign * 176
    feedback = public_gaze_feedback(observation(yaw=observed_yaw), reference)
    assert feedback["gaze_observed_direction"] == away
    assert feedback["gaze_display_yaw_degrees"] == pytest.approx(sign * 9)


def test_status_separates_head_pose_from_uncertain_eye_gaze():
    from agent.desktop import public_gaze_status_text

    gaze = observation(0, -22)
    gaze.update(public_gaze_feedback(gaze, [0, 0, -1]))
    gaze.update(head_direction="RIGHT", head_reference_ready=True)
    text = public_gaze_status_text(gaze, active=True, gaze_seconds=4)
    assert "Взгляд (по изображению камеры): вниз" in text
    assert "направление оценено, но не подтверждено для замечания" in text
    assert "Положение головы: вправо (по изображению камеры)" in text
    assert "4.0 / 5" not in text
    assert gaze["direction"] == "UNKNOWN"


def test_status_distinguishes_missing_calibration_and_missing_observation():
    from agent.desktop import public_gaze_status_text

    text = public_gaze_status_text({})
    assert "Взгляд (по изображению камеры): не настроен" in text
    assert "Положение головы: не настроено" in text
    text = public_gaze_status_text(dict(reference_ready=True, head_reference_ready=True))
    assert "Взгляд (по изображению камеры): не отслеживается" in text
    assert "Положение головы: не отслеживается" in text


def test_small_measured_head_turn_is_not_misreported_as_missing_face():
    from agent.desktop import public_gaze_status_text
    text = public_gaze_status_text(dict(reference_ready=True, head_reference_ready=True,
                                        head_direction='UNKNOWN', head_warning=True,
                                        head_yaw=-15., head_pitch=0.))
    assert 'небольшой поворот влево, 15°' in text
    assert 'лицо должно быть видно целиком' not in text


def test_status_timer_requires_confident_observation_and_stale_frames_are_visible():
    from agent.desktop import public_gaze_status_text

    gaze = observation(40, direction="LEFT")
    gaze.update(public_gaze_feedback(gaze, [0, 0, -1]))
    gaze.update(interval_ms=800, head_reference_ready=True, head_direction="SCREEN")
    text = public_gaze_status_text(gaze, active=True, gaze_seconds=3.5)
    assert "3.5 / 5 с" in text and "таймер отвлечения сброшен" in text
    assert "Положение головы: прямо" in text
