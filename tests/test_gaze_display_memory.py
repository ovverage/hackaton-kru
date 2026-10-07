"""Blink continuity must stay separate from strict gaze and attention state."""

import copy

import pytest

from shared.gaze_display_memory import GazeDisplayMemory


def feedback(direction="SCREEN"):
    observed = direction != "UNKNOWN"
    return {
        "gaze_observed_direction": direction,
        "gaze_display_yaw_degrees": 3.0 if observed else None,
        "gaze_display_pitch_degrees": -2.0 if observed else None,
        "gaze_display_threshold_degrees": 9.0,
        "gaze_observation_uncertain": not observed,
        "gaze_feedback_reason": "observed" if observed else "observation_missing",
        "direction": "CENTER" if observed else "UNKNOWN",
        "attention_away": False,
    }


def update(memory, value, at, **overrides):
    args = dict(blink=False, single_face=True, reference_ready=True)
    args.update(overrides)
    return memory.update(value, at=at, **args)


@pytest.mark.parametrize("direction", ["SCREEN", "LEFT", "RIGHT", "UP", "DOWN"])
def test_brief_blink_holds_only_display_and_keeps_strict_unknown(direction):
    memory = GazeDisplayMemory()
    previous = feedback(direction)
    fresh = update(memory, previous, 10)
    assert not fresh["gaze_display_stale"] and fresh["gaze_display_age_ms"] == 0
    missing = feedback("UNKNOWN")
    before = copy.deepcopy(missing)
    held = update(memory, missing, 10.15, blink=True)
    assert missing == before
    assert held["gaze_observed_direction"] == direction
    assert held["gaze_display_yaw_degrees"] == 3.0
    assert held["gaze_display_pitch_degrees"] == -2.0
    assert held["gaze_display_stale"]
    assert held["gaze_display_age_ms"] == pytest.approx(150)
    assert held["gaze_observation_uncertain"]
    assert held["gaze_feedback_reason"] == "blink_hold"
    assert held["direction"] == "UNKNOWN" and not held["attention_away"]


def test_blink_does_not_extend_its_own_expiry_or_invent_current_angles():
    memory = GazeDisplayMemory()
    update(memory, feedback(), 10)
    assert update(memory, feedback("UNKNOWN"), 10.2, blink=True)["gaze_display_stale"]
    expired = update(memory, feedback("UNKNOWN"), 10.351, blink=True)
    assert expired["gaze_observed_direction"] == "UNKNOWN"
    assert expired["gaze_display_yaw_degrees"] is None
    assert expired["gaze_display_pitch_degrees"] is None
    assert expired["gaze_display_age_ms"] is None
    assert not expired["gaze_display_stale"]
    assert not update(memory, feedback("UNKNOWN"), 10.5, blink=True)["gaze_display_stale"]


@pytest.mark.parametrize("blink", [False, None, 1, "true"])
def test_no_hold_without_explicit_blink(blink):
    memory = GazeDisplayMemory()
    update(memory, feedback(), 10)
    result = update(memory, feedback("UNKNOWN"), 10.15, blink=blink)
    assert result["gaze_observed_direction"] == "UNKNOWN"
    assert not result["gaze_display_stale"]


@pytest.mark.parametrize("invalid_context", [
    {"single_face": False}, {"reference_ready": False}, {"head_away": True},
])
def test_invalid_tracking_context_clears_memory_until_new_observation(invalid_context):
    memory = GazeDisplayMemory()
    update(memory, feedback(), 10)
    invalid = update(memory, feedback("UNKNOWN"), 10.1, blink=True, **invalid_context)
    assert not invalid["gaze_display_stale"]
    recovered_context = update(memory, feedback("UNKNOWN"), 10.2, blink=True)
    assert recovered_context["gaze_observed_direction"] == "UNKNOWN"
    update(memory, feedback("LEFT"), 10.3)
    assert update(memory, feedback("UNKNOWN"), 10.4, blink=True)["gaze_observed_direction"] == "LEFT"


@pytest.mark.parametrize("interrupted_at", [9.95, 10.751, float("nan"), None])
def test_clock_rollback_gap_or_invalid_time_resets_memory(interrupted_at):
    memory = GazeDisplayMemory()
    update(memory, feedback(), 10)
    result = update(memory, feedback("UNKNOWN"), interrupted_at, blink=True)
    assert result["gaze_observed_direction"] == "UNKNOWN"
    assert not result["gaze_display_stale"]
    assert not update(memory, feedback("UNKNOWN"), 10.2, blink=True)["gaze_display_stale"]


def test_reset_requires_a_new_observation():
    memory = GazeDisplayMemory()
    update(memory, feedback(), 10)
    memory.reset()
    result = update(memory, feedback("UNKNOWN"), 10.1, blink=True)
    assert result["gaze_observed_direction"] == "UNKNOWN"


def test_unknown_initial_feedback_does_not_invent_screen():
    result = update(GazeDisplayMemory(), feedback("UNKNOWN"), 10, blink=True)
    assert result["gaze_observed_direction"] == "UNKNOWN"
    assert result["gaze_display_yaw_degrees"] is None


def test_display_history_cannot_mutate_input_or_restore_attention_decision():
    memory = GazeDisplayMemory()
    away = feedback("LEFT")
    away.update(direction="LEFT", attention_away=True)
    before = copy.deepcopy(away)
    update(memory, away, 10)
    assert away == before
    away["gaze_observed_direction"] = "RIGHT"
    held = update(memory, feedback("UNKNOWN"), 10.15, blink=True)
    assert held["gaze_observed_direction"] == "LEFT"
    assert held["direction"] == "UNKNOWN" and held["attention_away"] is False


def test_stale_feedback_never_refreshes_memory():
    memory = GazeDisplayMemory()
    update(memory, feedback(), 10)
    held = update(memory, feedback("UNKNOWN"), 10.2, blink=True)
    update(memory, held, 10.3)
    expired = update(memory, feedback("UNKNOWN"), 10.4, blink=True)
    assert expired["gaze_observed_direction"] == "UNKNOWN"


@pytest.mark.parametrize("missing", [feedback("UNKNOWN"), {}])
def test_non_blink_missing_observation_prevents_later_blink_resurrection(missing):
    memory = GazeDisplayMemory()
    update(memory, feedback("LEFT"), 10)
    update(memory, missing, 10.1)
    result = update(memory, feedback("UNKNOWN"), 10.2, blink=True)
    assert result["gaze_observed_direction"] == "UNKNOWN"
    assert result["gaze_display_yaw_degrees"] is None
    assert result["gaze_display_pitch_degrees"] is None
    assert not result["gaze_display_stale"]
    assert result["gaze_display_age_ms"] is None
