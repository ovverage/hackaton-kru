"""Display-only gaze feedback; never changes a model or violation decision."""

from __future__ import annotations

import math

from shared.public_gaze import angles


DISPLAY_THRESHOLD_DEGREES = 9.0


def public_gaze_feedback(gaze, reference):
    """Expose observed motion while preserving the strict estimator result.

    LEFT/RIGHT use the camera-image axes used by PublicGazeEstimator.
    The display marks either axis away at 9 degrees. This is a point estimate;
    it does not change the estimator's thresholds or provide violation evidence.
    """
    result = {
        "gaze_observed_direction": "UNKNOWN",
        "gaze_observation_uncertain": True,
        "gaze_feedback_reason": "reference_missing",
        "gaze_display_yaw_degrees": None,
        "gaze_display_pitch_degrees": None,
        "gaze_display_threshold_degrees": DISPLAY_THRESHOLD_DEGREES,
    }
    if reference is None:
        return result
    try:
        yaw0, pitch0 = angles(reference)
    except (TypeError, ValueError, OverflowError):
        return result
    if not isinstance(gaze, dict) or any(
        gaze.get(key) is None
        for key in ("yaw_degrees", "pitch_degrees", "error90_degrees")
    ):
        result["gaze_feedback_reason"] = "observation_missing"
        return result
    try:
        yaw, pitch, error90 = (
            float(gaze[key])
            for key in ("yaw_degrees", "pitch_degrees", "error90_degrees")
        )
        if not all(math.isfinite(value) for value in (yaw, pitch, error90)) or error90 < 0:
            raise ValueError("invalid gaze observation")
    except (TypeError, ValueError, OverflowError):
        result["gaze_feedback_reason"] = "invalid_observation"
        return result

    yaw = (yaw - yaw0 + 180) % 360 - 180
    pitch -= pitch0
    if abs(yaw) < DISPLAY_THRESHOLD_DEGREES and abs(pitch) < DISPLAY_THRESHOLD_DEGREES:
        direction = "SCREEN"
    elif abs(yaw) > abs(pitch):
        direction = "LEFT" if yaw > 0 else "RIGHT"
    else:
        direction = "UP" if pitch > 0 else "DOWN"

    strict_direction = gaze.get("direction")
    if strict_direction == "CENTER":
        strict_direction = "SCREEN"
    strict_known = strict_direction in {"SCREEN", "LEFT", "RIGHT", "UP", "DOWN"}
    strict_matches = strict_direction == direction
    result.update(
        gaze_observed_direction=direction,
        gaze_observation_uncertain=error90 > 20 or not strict_matches,
        gaze_display_yaw_degrees=yaw,
        gaze_display_pitch_degrees=pitch,
        gaze_feedback_reason=(
            "uncertain_observation" if error90 > 20
            else "strict_unknown" if not strict_known
            else "strict_mismatch" if not strict_matches
            else "observed"
        ),
    )
    return result
