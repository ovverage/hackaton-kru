"""Independent face orientation observations, never a measurement of gaze."""

from __future__ import annotations

import math

import numpy as np

from .gaze_v2 import (
    HEAD_DOWN_AWAY_DEGREES,
    HEAD_YAW_AWAY_DEGREES,
)

HEAD_DISPLAY_DEGREES = 9.0


def rotation_from_matrix(matrix):
    """Read a face transform without depending on visible irises or open eyes.

    MediaPipe may include a uniform scale and translation in its 4x4 face
    transform. Normalize the scale, but reject reflections, skew and unequal
    axis scaling before removing small floating point drift with SVD.
    """
    try:
        values = np.asarray(matrix, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if values.shape not in ((3, 3), (4, 4)) or not np.isfinite(values).all():
        return None
    if values.shape == (4, 4) and not np.allclose(
        values[3], [0., 0., 0., 1.], rtol=0, atol=1e-6
    ):
        return None
    rotation = values[:3, :3]
    norms = np.linalg.norm(rotation, axis=1)
    if not np.isfinite(norms).all() or np.min(norms) < 1e-8:
        return None
    if np.max(norms) / np.min(norms) > 1.05:
        return None
    rotation = rotation / norms[:, None]
    if (np.max(np.abs(rotation @ rotation.T - np.eye(3))) > .05
            or not .9 <= np.linalg.det(rotation) <= 1.1):
        return None
    try:
        left, _, right = np.linalg.svd(rotation)
    except np.linalg.LinAlgError:
        return None
    normalized = left @ right
    return normalized if np.linalg.det(normalized) > 0 else None


def rotation_from_features(features):
    """Compatibility adapter for the legacy full eye/head feature vector."""
    try:
        values = np.asarray(features, dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if values.shape != (33,) or not np.isfinite(values).all():
        return None
    return rotation_from_matrix(values[12:21].reshape(3, 3))


def rotation_distance(a, b):
    cosine = np.clip((np.trace(a @ b.T) - 1) / 2, -1, 1)
    return math.degrees(math.acos(float(cosine)))


class HeadPoseObserver:
    """A fixed, explicitly captured centre pose with a separate signal contract."""

    def __init__(self):
        self.clear()

    def clear(self):
        self.reference = None
        self.reference_error = None

    def set_reference(self, samples):
        return self.set_reference_rotations(
            [rotation_from_features(sample) for sample in samples]
        )

    def set_reference_rotations(self, samples, *, required_samples=25):
        """Capture stable requested poses; screen calibration may supply >=3."""
        self.clear()
        rotations = [rotation_from_matrix(sample) for sample in samples]
        if (not isinstance(required_samples, int) or required_samples < 3
                or len(rotations) != required_samples
                or any(rotation is None for rotation in rotations)):
            self.reference_error = "HEAD_REFERENCE_INSUFFICIENT"
            return False
        distances = np.asarray([
            [rotation_distance(a, b) for b in rotations] for a in rotations
        ])
        # Use an actual captured orientation, not an elementwise matrix average.
        index = int(np.argmin(np.median(distances, axis=1)))
        if float(np.percentile(distances[index], 90)) > 8:
            self.reference_error = "HEAD_REFERENCE_UNSTABLE"
            return False
        self.reference = rotations[index].copy()
        return True

    def observe(self, features, *, faces=1):
        return self.observe_rotation(rotation_from_features(features), faces=faces)

    def observe_rotation(self, matrix, *, faces=1):
        """Report visible face orientation; never infer angles from a lost face."""
        result = {
            "head_yaw": None,
            "head_pitch": None,
            "head_direction": "UNKNOWN",
            "head_reference_ready": self.reference is not None,
            "head_reference_error": self.reference_error,
            "head_away": False,
            "head_warning": False,
            "head_extreme": False,
            "head_tracking_status": "unavailable",
        }
        if faces != 1:
            if faces > 1:
                result["head_tracking_status"] = "ambiguous_faces"
            return result
        rotation = rotation_from_matrix(matrix)
        if rotation is None:
            return result
        if self.reference is None:
            result["head_tracking_status"] = "not_calibrated"
            return result
        forward = (rotation @ self.reference.T)[:, 2]
        yaw = math.degrees(math.atan2(float(forward[0]), float(forward[2])))
        pitch = math.degrees(math.atan2(-float(forward[1]), math.hypot(float(forward[0]), float(forward[2]))))
        # Work at the displayed precision to keep the exact 9-degree boundary
        # stable against trig/SVD round-off. Display sensitivity is independent
        # of the stronger thresholds used for a review event.
        yaw, pitch = round(yaw, 2), round(pitch, 2)
        result.update(head_yaw=yaw, head_pitch=pitch, head_tracking_status="tracked")
        if abs(yaw) >= HEAD_DISPLAY_DEGREES and abs(yaw) >= abs(pitch):
            result["head_direction"] = "RIGHT" if yaw > 0 else "LEFT"
        elif abs(pitch) >= HEAD_DISPLAY_DEGREES:
            result["head_direction"] = "DOWN" if pitch > 0 else "UP"
        else:
            result["head_direction"] = "SCREEN"
        result["head_away"] = (
            abs(yaw) >= HEAD_YAW_AWAY_DEGREES or abs(pitch) >= HEAD_DOWN_AWAY_DEGREES
        )
        result["head_warning"] = (
            abs(yaw) >= HEAD_DISPLAY_DEGREES or abs(pitch) >= HEAD_DISPLAY_DEGREES
        )
        result["head_extreme"] = abs(yaw) >= 45 or abs(pitch) >= 35
        return result
