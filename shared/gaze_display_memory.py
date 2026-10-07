"""Brief blink continuity for the display, never for gaze decisions or timers."""

from __future__ import annotations

import math


class GazeDisplayMemory:
    """Retain only display fields for at most 350 ms after a real observation.

    ``at`` is monotonic time in seconds. A held display is explicitly stale and
    uncertain; strict direction and attention fields are never copied from
    history. Face/reference loss or a head turn invalidates the history.
    """

    HOLD_SECONDS = 0.35
    MAX_UPDATE_GAP_SECONDS = 0.75
    _DIRECTIONS = {"SCREEN", "LEFT", "RIGHT", "UP", "DOWN"}
    _DISPLAY_FIELDS = (
        "gaze_observed_direction",
        "gaze_display_yaw_degrees",
        "gaze_display_pitch_degrees",
        "gaze_display_threshold_degrees",
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self._last_update_at = None
        self._last_observed_at = None
        self._display = None

    def update(self, feedback, *, at, blink, single_face, reference_ready, head_away=False):
        result = dict(feedback)
        result.update(gaze_display_stale=False, gaze_display_age_ms=None)
        try:
            at = float(at)
            if not math.isfinite(at):
                raise ValueError("invalid timestamp")
        except (TypeError, ValueError, OverflowError):
            self.reset()
            return result

        if self._last_update_at is not None and (
            at < self._last_update_at
            or at - self._last_update_at > self.MAX_UPDATE_GAP_SECONDS
        ):
            self.reset()
        self._last_update_at = at

        if not single_face or not reference_ready or head_away:
            self.reset()
            return result

        # A blink must be explicitly detected. Ordinary missing/uncertain
        # observations cannot borrow an earlier direction.
        if blink is True:
            if self._display is not None:
                age = at - self._last_observed_at
                if 0 <= age <= self.HOLD_SECONDS:
                    result.update(self._display)
                    result.update(
                        gaze_display_stale=True,
                        gaze_display_age_ms=age * 1000,
                        gaze_feedback_reason="blink_hold",
                        gaze_observation_uncertain=True,
                    )
                else:
                    self._last_observed_at = None
                    self._display = None
            return result

        if (
            feedback.get("gaze_observed_direction") in self._DIRECTIONS
            and not feedback.get("gaze_display_stale")
            and feedback.get("gaze_feedback_reason") != "blink_hold"
        ):
            self._last_observed_at = at
            self._display = {key: feedback.get(key) for key in self._DISPLAY_FIELDS}
            result["gaze_display_age_ms"] = 0.0
        else:
            # Lost eye input outside a confirmed blink breaks continuity.
            # A later blink cannot resurrect the preceding observation.
            self._last_observed_at = None
            self._display = None
        return result
