"""Short personal screen calibration from measured gaze angles, without images.

Five labelled fit targets define an angular screen polygon. Four other targets
only check that mapping; they never change its coefficients or polygon. The
quality limits below are configurable engineering heuristics for a short demo,
not validated statistical confidence intervals or a guarantee of eye accuracy.
Head stability, freshness and the current monitor are the caller's obligations.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
from numbers import Real

import numpy as np


FIT_TARGETS = (
    ("fit_center", .5, .5),
    ("fit_top_left", .05, .05),
    ("fit_top_right", .95, .05),
    ("fit_bottom_right", .95, .95),
    ("fit_bottom_left", .05, .95),
)
VALIDATION_TARGETS = (
    ("validation_top", .5, .05),
    ("validation_right", .95, .5),
    ("validation_bottom", .5, .95),
    ("validation_left", .05, .5),
)


@dataclass(frozen=True)
class ScreenGazeConfig:
    """Bounded demo heuristics; the angular margin is deliberately 5–7 degrees."""

    min_samples: int = 3
    margin_degrees: float = 6.0
    max_model_error_degrees: float = 20.0
    max_sample_spread_degrees: float = 4.0
    min_yaw_span_degrees: float = 4.0
    min_pitch_span_degrees: float = 3.0
    max_condition_number: float = 30.0
    max_fit_error: float = .18
    max_validation_median_error: float = .12
    max_validation_error: float = .22
    max_sample_screen_spread: float = .18

    def __post_init__(self):
        bounds = {
            "min_samples": (3, 120),
            "margin_degrees": (5, 7),
            "max_model_error_degrees": (5, 20),
            "max_sample_spread_degrees": (.5, 8),
            "min_yaw_span_degrees": (1, 30),
            "min_pitch_span_degrees": (1, 30),
            "max_condition_number": (2, 100),
            "max_fit_error": (.03, .25),
            "max_validation_median_error": (.02, .20),
            "max_validation_error": (.04, .30),
            "max_sample_screen_spread": (.03, .25),
        }
        for field in fields(self):
            value = getattr(self, field.name)
            low, high = bounds[field.name]
            if (isinstance(value, bool) or not isinstance(value, Real)
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError(f"INVALID_SCREEN_CONFIG:{field.name}")
        if not isinstance(self.min_samples, int):
            raise ValueError("INVALID_SCREEN_CONFIG:min_samples")
        if self.max_validation_median_error > self.max_validation_error:
            raise ValueError("INVALID_SCREEN_CONFIG:validation_limits")


def _wrap(value):
    return (value + 180.0) % 360.0 - 180.0


def _cross(a, b, c):
    ab, ac = b - a, c - a
    return float(ab[0] * ac[1] - ab[1] * ac[0])


def _convex_hull(points):
    ordered = [np.asarray(point, dtype=float) for point in sorted(set(map(tuple, points)))]
    lower, upper = [], []
    for point in ordered:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], point) <= 1e-9:
            lower.pop()
        lower.append(point)
    for point in reversed(ordered):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], point) <= 1e-9:
            upper.pop()
        upper.append(point)
    return np.asarray(lower[:-1] + upper[:-1], dtype=float)


def _polygon_distance(point, polygon):
    """Return zero inside/on the convex polygon, else distance to its boundary."""
    edges = tuple(zip(polygon, np.roll(polygon, -1, axis=0)))
    if all(_cross(a, b, point) >= -1e-8 for a, b in edges):
        return 0.0
    distances = []
    for start, end in edges:
        edge = end - start
        projection = np.clip(np.dot(point - start, edge) / np.dot(edge, edge), 0, 1)
        distances.append(float(np.linalg.norm(point - (start + projection * edge))))
    return min(distances)


class ScreenGazeCalibration:
    """An explicit session-local fit. Failed recalibration disables old decisions."""

    def __init__(self, config=None):
        self.config = config if config is not None else ScreenGazeConfig()
        if not isinstance(self.config, ScreenGazeConfig):
            raise TypeError("config must be ScreenGazeConfig")
        self.reset()

    def reset(self):
        self.ready = False
        self.error = "SCREEN_CALIBRATION_REQUIRED"
        self.quality = {}
        self.reference_yaw_degrees = None
        self.reference_pitch_degrees = None
        self._coefficients = None
        self._polygon = None

    def report(self):
        # All fields are compact, JSON-compatible measurements, without images.
        return {"ready": self.ready, "error": self.error, "quality": dict(self.quality)}

    def _fail(self, code, **quality):
        self.ready = False
        self.error = code
        self._coefficients = None
        self._polygon = None
        self.quality.update(quality)
        return self.report()

    def _read(self, observation, *, allow_uncertain=False):
        if not isinstance(observation, dict):
            return None
        if observation.get("gaze_tracking_status") not in (None, "tracked", "not_calibrated"):
            return None
        values = [observation.get(key) for key in (
            "yaw_degrees", "pitch_degrees", "error90_degrees"
        )]
        if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v)
               for v in values):
            return None
        yaw, pitch, error = map(float, values)
        maximum_error = 180 if allow_uncertain else self.config.max_model_error_degrees
        if not (-180 <= yaw <= 180 and -90 <= pitch <= 90
                and 0 <= error <= maximum_error):
            return None
        return np.asarray([yaw, pitch, error])

    def check_target(self, samples):
        """Check one capture before moving on, without fitting or changing state.

        This label-free check uses the existing minimum-count, circular-yaw and
        angular-spread heuristics. A successful capture still needs the complete
        screen geometry and separate validation checks in ``fit``. Fresh frames,
        target presentation and head stability remain the caller's obligations.
        """
        try:
            rows = [self._read(sample) for sample in samples]
        except TypeError:
            rows = []
        rows = [row for row in rows if row is not None]
        quality = {"sample_count": len(rows), "sample_spread_degrees": None}
        circular_strength = None
        if rows:
            values = np.asarray(rows)
            radians = np.radians(values[:, 0])
            circular = np.mean(np.column_stack([np.cos(radians), np.sin(radians)]), axis=0)
            circular_strength = float(np.linalg.norm(circular))
            anchor = math.degrees(math.atan2(float(circular[1]), float(circular[0])))
            reference = _wrap(anchor + float(np.median([_wrap(row[0] - anchor) for row in rows])))
            relative = values[:, :2].copy()
            relative[:, 0] = (relative[:, 0] - reference + 180) % 360 - 180
            median = np.median(relative, axis=0)
            quality["sample_spread_degrees"] = float(
                np.percentile(np.linalg.norm(relative - median, axis=1), 90))
        error = None
        if len(rows) < self.config.min_samples:
            error = "SCREEN_SAMPLES_INSUFFICIENT"
        elif (circular_strength < .5
              or quality["sample_spread_degrees"] > self.config.max_sample_spread_degrees):
            error = "SCREEN_SAMPLES_UNSTABLE"
        return {"ready": error is None, "error": error, "quality": quality}

    def _record_sample_metrics(self, target_id, rows, reference=None):
        """Keep measured diagnostics even when the next acceptance gate fails."""
        self.quality["sample_counts"][target_id] = len(rows)
        spread = None
        if rows:
            relative = np.asarray(rows)[:, :2].copy()
            if reference is None:
                radians = np.radians(relative[:, 0])
                reference = math.degrees(math.atan2(float(np.sin(radians).mean()),
                                                   float(np.cos(radians).mean())))
            relative[:, 0] = (relative[:, 0] - reference + 180) % 360 - 180
            median = np.median(relative, axis=0)
            spread = float(np.percentile(np.linalg.norm(relative - median, axis=1), 90))
        self.quality["sample_spreads_degrees"][target_id] = spread

    def _summaries(self, supplied, targets, reference):
        if not isinstance(supplied, dict) or set(supplied) != {row[0] for row in targets}:
            return None, "SCREEN_TARGETS_MISSING", None
        summaries = []
        for target_id, x, y in targets:
            try:
                rows = [self._read(observation) for observation in supplied[target_id]]
            except TypeError:
                self._record_sample_metrics(target_id, [])
                return None, "SCREEN_SAMPLES_INSUFFICIENT", target_id
            rows = [row for row in rows if row is not None]
            self._record_sample_metrics(target_id, rows, reference)
            if len(rows) < self.config.min_samples:
                return None, "SCREEN_SAMPLES_INSUFFICIENT", target_id
            values = np.asarray(rows)
            relative = values[:, :2].copy()
            relative[:, 0] = (relative[:, 0] - reference + 180) % 360 - 180
            median = np.median(relative, axis=0)
            spread = float(np.percentile(np.linalg.norm(relative - median, axis=1), 90))
            if spread > self.config.max_sample_spread_degrees:
                return None, "SCREEN_SAMPLES_UNSTABLE", target_id
            summaries.append({
                "id": target_id, "target": [x, y], "median": median,
                "samples": relative, "count": len(rows), "spread": spread,
                "error": float(np.median(values[:, 2])),
            })
        return summaries, None, None

    def fit(self, fit_samples, validation_samples):
        """Fit five medians, then gate on four independently captured medians.

        Each argument maps the corresponding target ID to raw gaze dictionaries
        with yaw_degrees, pitch_degrees and error90_degrees. Runtime must collect
        distinct fresh frames, exclude blinks and keep head/monitor position
        stable. Validation samples are used only for acceptance, never fitting.
        """
        self.reset()
        self.quality = {
            "policy": "personal_screen_heuristic_v1",
            "statistical_accuracy_guarantee": False,
            "fit_target_count": len(FIT_TARGETS),
            "validation_target_count": len(VALIDATION_TARGETS),
            "margin_degrees": float(self.config.margin_degrees),
            "sample_counts": {},
            "sample_spreads_degrees": {},
        }
        if not isinstance(fit_samples, dict) or "fit_center" not in fit_samples:
            return self._fail("SCREEN_TARGETS_MISSING")
        try:
            center = [self._read(row) for row in fit_samples["fit_center"]]
        except TypeError:
            self._record_sample_metrics("fit_center", [])
            return self._fail("SCREEN_SAMPLES_INSUFFICIENT", failed_target="fit_center")
        center = [row for row in center if row is not None]
        self._record_sample_metrics("fit_center", center)
        if len(center) < self.config.min_samples:
            return self._fail("SCREEN_SAMPLES_INSUFFICIENT", failed_target="fit_center")
        yaw_radians = np.radians([row[0] for row in center])
        circular = np.mean(np.column_stack([np.cos(yaw_radians), np.sin(yaw_radians)]), axis=0)
        if float(np.linalg.norm(circular)) < .5:
            return self._fail("SCREEN_SAMPLES_UNSTABLE", failed_target="fit_center")
        anchor = math.degrees(math.atan2(float(circular[1]), float(circular[0])))
        reference = _wrap(anchor + float(np.median([_wrap(row[0] - anchor) for row in center])))
        fits, error, failed = self._summaries(fit_samples, FIT_TARGETS, reference)
        if error:
            return self._fail(error, failed_target=failed)
        self.reference_yaw_degrees = reference
        self.reference_pitch_degrees = float(fits[0]["median"][1])
        self.quality.update(
            reference_yaw_degrees=reference,
            reference_pitch_degrees=self.reference_pitch_degrees,
            center_sample_count=fits[0]["count"],
            center_observation={"yaw_degrees": reference,
                                "pitch_degrees": self.reference_pitch_degrees,
                                "error90_degrees": fits[0]["error"]},
        )
        points = np.asarray([row["median"] for row in fits])
        targets = np.asarray([row["target"] for row in fits])
        span = np.ptp(points[1:], axis=0)
        self.quality.update(yaw_span_degrees=float(span[0]), pitch_span_degrees=float(span[1]))
        if span[0] < self.config.min_yaw_span_degrees or span[1] < self.config.min_pitch_span_degrees:
            return self._fail("SCREEN_ANGULAR_SPAN_TOO_SMALL")
        polygon = _convex_hull(points[1:])
        if len(polygon) != 4 or _polygon_distance(points[0], polygon) > 1e-8:
            return self._fail("SCREEN_CORNER_GEOMETRY_INVALID")
        area = abs(sum(_cross(np.zeros(2), a, b)
                       for a, b in zip(polygon, np.roll(polygon, -1, axis=0)))) / 2
        if area / float(span[0] * span[1]) < .12:
            return self._fail("SCREEN_CORNER_GEOMETRY_INVALID")
        location, scale = points.mean(axis=0), points.std(axis=0)
        design = np.column_stack([(points - location) / scale, np.ones(len(points))])
        try:
            normalized, _, rank, singular = np.linalg.lstsq(design, targets, rcond=None)
            condition = float(singular[0] / singular[-1]) if singular[-1] > 1e-12 else math.inf
            coefficients = np.vstack([normalized[:2] / scale[:, None],
                                      normalized[2] - (location / scale) @ normalized[:2]])
            transform_condition = float(np.linalg.cond(coefficients[:2]))
        except np.linalg.LinAlgError:
            return self._fail("SCREEN_TRANSFORM_DEGENERATE")
        if (rank != 3 or not np.isfinite(coefficients).all()
                or max(condition, transform_condition) > self.config.max_condition_number):
            return self._fail("SCREEN_TRANSFORM_DEGENERATE")
        predicted = np.column_stack([points, np.ones(len(points))]) @ coefficients
        fit_errors = np.linalg.norm(predicted - targets, axis=1)
        self.quality.update(fit_max_error=float(max(fit_errors)),
                            fit_condition_number=condition,
                            transform_condition_number=transform_condition,
                            angular_polygon=polygon.tolist())
        if float(max(fit_errors)) > self.config.max_fit_error:
            return self._fail("SCREEN_FIT_ERROR_TOO_HIGH",
                              failed_target=fits[int(np.argmax(fit_errors))]["id"])
        # Freeze the only fitted parameters before opening validation values.
        validations, error, failed = self._summaries(validation_samples, VALIDATION_TARGETS, reference)
        if error:
            return self._fail(error, failed_target=failed)
        validation_points = np.asarray([row["median"] for row in validations])
        validation_targets = np.asarray([row["target"] for row in validations])
        validation_predictions = np.column_stack([validation_points, np.ones(len(validations))]) @ coefficients
        validation_errors = np.linalg.norm(validation_predictions - validation_targets, axis=1)
        sample_spreads = []
        for row in fits + validations:
            offsets = (row["samples"] - row["median"]) @ coefficients[:2]
            sample_spreads.append(float(np.percentile(np.linalg.norm(offsets, axis=1), 90)))
        self.quality.update(
            validation_median_error=float(np.median(validation_errors)),
            validation_max_error=float(max(validation_errors)),
            validation_errors={row["id"]: float(error)
                               for row, error in zip(validations, validation_errors)},
            maximum_sample_screen_spread=float(max(sample_spreads)),
        )
        if (float(np.median(validation_errors)) > self.config.max_validation_median_error
                or float(max(validation_errors)) > self.config.max_validation_error):
            return self._fail("SCREEN_VALIDATION_ERROR_TOO_HIGH",
                              failed_target=validations[int(np.argmax(validation_errors))]["id"])
        if float(max(sample_spreads)) > self.config.max_sample_screen_spread:
            return self._fail("SCREEN_SAMPLES_UNSTABLE_IN_SCREEN_SPACE",
                              failed_target=(fits + validations)[int(np.argmax(sample_spreads))]["id"])
        self._coefficients = coefficients
        self._polygon = polygon
        self.error = None
        self.ready = True
        return self.report()

    def observe(self, gaze):
        """Classify against the measured polygon plus an angular 6-degree guard.

        screen_side follows the physical target layout. screen_direction retains
        the existing camera-image gaze convention: positive yaw LEFT, positive
        pitch UP. The affine inverse connects the measured boundary to that
        convention even if the display is mirrored. This never infers head pose.
        """
        result = {
            "screen_ready": self.ready,
            "screen_x": None,
            "screen_y": None,
            "screen_direction": "UNKNOWN",
            "screen_observed_direction": "UNKNOWN",
            "screen_side": "UNKNOWN",
            "screen_reason": "calibration_required" if not self.ready else "observation_invalid",
            "screen_margin_degrees": float(self.config.margin_degrees),
            "screen_distance_degrees": None,
            "screen_quality": dict(self.quality),
        }
        if not self.ready:
            return result
        # Large finite model uncertainty still allows a preliminary display,
        # but must not feed the independent violation decision below.
        observation = self._read(gaze, allow_uncertain=True)
        if observation is None:
            return result
        point = np.asarray([_wrap(observation[0] - self.reference_yaw_degrees), observation[1]])
        mapped = np.append(point, 1.0) @ self._coefficients
        distance = _polygon_distance(point, self._polygon)
        result.update(screen_x=float(mapped[0]), screen_y=float(mapped[1]),
                      screen_distance_degrees=distance)
        if distance <= 1e-8:
            result.update(screen_direction="SCREEN", screen_side="SCREEN", screen_reason="inside_screen")
        elif distance <= self.config.margin_degrees + 1e-8:
            result["screen_reason"] = "boundary_margin"
        else:
            # Coordinates need not be clamped: off-screen distance is useful to
            # display. Decisions use angular polygon distance, not these units.
            outside = mapped - np.clip(mapped, .05, .95)
            if float(np.linalg.norm(outside)) < 1e-10:
                # A mildly non-affine fit can disagree with the angular
                # polygon locally. Do not invent a direction from a zero ray.
                result["screen_reason"] = "mapping_ambiguous"
            else:
                if abs(outside[0]) > abs(outside[1]):
                    side = "LEFT" if outside[0] < 0 else "RIGHT"
                else:
                    side = "UP" if outside[1] < 0 else "DOWN"
                camera_delta = outside @ np.linalg.inv(self._coefficients[:2])
                if abs(camera_delta[0]) > abs(camera_delta[1]):
                    direction = "LEFT" if camera_delta[0] > 0 else "RIGHT"
                else:
                    direction = "UP" if camera_delta[1] > 0 else "DOWN"
                result.update(screen_direction=direction, screen_side=side, screen_reason="outside_screen")
        result["screen_observed_direction"] = result["screen_direction"]
        if observation[2] > self.config.max_model_error_degrees:
            result.update(screen_direction="UNKNOWN", screen_reason="model_uncertain")
        return result
