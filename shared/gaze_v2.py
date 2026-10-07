"""Aspect-aware eye and head features for a calibration-free gaze classifier.

The output is an observation for review, not a measurement of a screen point.
Feature order is a versioned model contract; changing it requires retraining.
"""

from __future__ import annotations
import math
import json
from array import array
from pathlib import Path

BLEND_NAMES = (
    "eyeLookDownLeft",
    "eyeLookDownRight",
    "eyeLookInLeft",
    "eyeLookInRight",
    "eyeLookOutLeft",
    "eyeLookOutRight",
    "eyeLookUpLeft",
    "eyeLookUpRight",
    "eyeBlinkLeft",
    "eyeBlinkRight",
    "eyeSquintLeft",
    "eyeSquintRight",
)
FEATURE_VERSION = "iris-head-blend-v2"
HEAD_YAW_AWAY_DEGREES = 22.0
HEAD_DOWN_AWAY_DEGREES = 18.0
HEAD_YAW_WARNING_DEGREES = 10.0
HEAD_PITCH_WARNING_DEGREES = 8.0


def relative_head_angles(vector, reference):
    """Forward-axis yaw/pitch after removing the fixed camera-start pose."""
    # R_current * R_reference.T: only its forward column is needed.
    forward = [sum(vector[12 + row*3 + col] * reference[18 + col]
                   for col in range(3)) for row in range(3)]
    if math.sqrt(sum(value*value for value in forward)) < .5:
        return None
    yaw = math.degrees(math.atan2(forward[0], forward[2]))
    pitch = math.degrees(math.atan2(-forward[1], math.hypot(forward[0], forward[2])))
    return yaw, pitch


def extract_features(points, matrix, blendshapes, width, height):
    if len(points) < 478 or width <= 0 or height <= 0:
        return None
    result = []
    for a, b, top, bottom, iris in (
        (33, 133, 159, 145, 468),
        (362, 263, 386, 374, 473),
    ):
        ax, ay = points[a].x * width, points[a].y * height
        dx = (points[b].x - points[a].x) * width
        dy = (points[b].y - points[a].y) * height
        length = math.hypot(dx, dy)
        if length < 8:
            return None
        # Rotate into the eye's own coordinates. Divide by width, not eyelid
        # opening: blinking/downward gaze must not create unstable ratios.
        for idx in (iris, top, bottom):
            px, py = points[idx].x * width - ax, points[idx].y * height - ay
            result.extend(
                (
                    (px * dx + py * dy) / (length * length),
                    (py * dx - px * dy) / (length * length),
                )
            )
    for r in range(3):
        norm = math.sqrt(sum(float(matrix[r][c]) ** 2 for c in range(3)))
        if norm < 1e-8:
            return None
        result.extend(float(matrix[r][c]) / norm for c in range(3))
    result.extend(float(blendshapes.get(name, 0.0)) for name in BLEND_NAMES)
    return result if all(math.isfinite(x) for x in result) else None


class GazeClassifier:
    """Read-only JSON forest with a fixed automatic session reference.

    No fitted identity data is persisted. Closed eyes cannot establish the
    reference; missing observations return UNKNOWN. A new camera gets a new
    reference; looking away is never used to continuously move that reference.
    """

    def __init__(self, path: Path):
        if path.stat().st_size > 15_000_000:
            raise ValueError("GAZE_MODEL_TOO_LARGE")
        self.model = json.loads(path.read_text(encoding="utf-8"))
        m = self.model
        if (
            m.get("schema") != 3
            or m.get("feature_version") != FEATURE_VERSION
            or m.get("features") != 33
            or m.get("classes") != ["SCREEN", "DOWN", "LEFT", "RIGHT", "UP"]
            or not 0.5 <= m.get("threshold", 0) <= 1
            or not 0.5 <= m.get("direction_threshold", 0) <= 1
            or not 1 <= len(m.get("trees", [])) <= 512
        ):
            raise ValueError("INVALID_GAZE_MODEL")
        for tree in m["trees"]:
            n = len(tree.get("left", []))
            if not 1 <= n <= 10000 or any(
                len(tree.get(k, [])) != n
                for k in ("right", "feature", "threshold", "probabilities")
            ):
                raise ValueError("INVALID_GAZE_TREE")
            seen, stack = set(), [0]
            while stack:
                node = stack.pop()
                if node in seen or not 0 <= node < n:
                    raise ValueError("INVALID_GAZE_TREE")
                seen.add(node)
                left, right = tree["left"][node], tree["right"][node]
                probs = tree["probabilities"][node]
                if (
                    len(probs) != 5
                    or not all(
                        isinstance(v, (float, int)) and math.isfinite(v) and 0 <= v <= 1
                        for v in probs
                    )
                    or abs(sum(probs) - 1) > 1e-5
                ):
                    raise ValueError("INVALID_GAZE_TREE")
                if left == right == -1:
                    continue
                if (
                    not isinstance(left, int)
                    or not isinstance(right, int)
                    or not 0 <= tree["feature"][node] < 33
                    or not math.isfinite(tree["threshold"][node])
                ):
                    raise ValueError("INVALID_GAZE_TREE")
                stack.extend((left, right))
            if len(seen) != n:
                raise ValueError("INVALID_GAZE_TREE")
        self.reference = None

    def probabilities(self, relative):
        if len(relative) != 33 or not all(math.isfinite(v) for v in relative):
            raise ValueError("INVALID_GAZE_FEATURES")
        # sklearn trees compare float32 inputs even when training used float64.
        # Keep exported inference identical at split thresholds.
        relative = array("f", relative)
        result = [0.0] * 5
        for tree in self.model["trees"]:
            node = 0
            for _ in range(64):
                if tree["left"][node] == -1:
                    for i, p in enumerate(tree["probabilities"][node]):
                        result[i] += p
                    break
                node = (
                    tree["left"][node]
                    if relative[tree["feature"][node]] <= tree["threshold"][node]
                    else tree["right"][node]
                )
            else:
                raise ValueError("GAZE_TREE_DEPTH")
        return [v / len(self.model["trees"]) for v in result]

    def observe(self, vector):
        if (
            vector is None
            or len(vector) != 33
            or not all(math.isfinite(v) for v in vector)
        ):
            return {
                "direction": "UNKNOWN",
                "offscreen_probability": None,
                "reference_ready": self.reference is not None,
                "attention_away": False,
                "attention_direction": None,
            }
        # Do not establish the initial reference on closed eyes. Once referenced,
        # the trained model can still use the visible head pose during a blink.
        if self.reference is None and (vector[29] > 0.65 or vector[30] > 0.65):
            return {
                "direction": "UNKNOWN",
                "offscreen_probability": None,
                "reference_ready": self.reference is not None,
                "attention_away": False,
                "attention_direction": None,
            }
        if self.reference is None:
            self.reference = list(vector)
        probs = self.probabilities([v - r for v, r in zip(vector, self.reference)])
        off = 1 - probs[0]
        strongest = max(range(1, 5), key=lambda i: probs[i])
        attention_direction = self.model["classes"][strongest]
        attention_away = off >= self.model["threshold"] - 0.15
        direction = "UNKNOWN"
        if (
            off >= self.model["threshold"]
            and probs[strongest] / max(off, 1e-9) >= self.model["direction_threshold"]
        ):
            direction = self.model["classes"][strongest]
        elif off < self.model["threshold"] - 0.15:
            direction = "SCREEN"
        source = "model"
        angles = relative_head_angles(vector, self.reference)
        if angles:
            yaw, pitch = angles
            if abs(yaw) >= HEAD_YAW_WARNING_DEGREES:
                attention_away = True
                attention_direction = "RIGHT" if yaw > 0 else "LEFT"
            elif abs(pitch) >= HEAD_PITCH_WARNING_DEGREES:
                attention_away = True
                attention_direction = "DOWN" if pitch > 0 else "UP"
            # A clear physical turn must not be vetoed by a forest learned on
            # only two people. Eye-only departures still use the trained model.
            if abs(yaw) >= HEAD_YAW_AWAY_DEGREES:
                direction = "RIGHT" if yaw > 0 else "LEFT"
                source = "head_pose"
            elif pitch >= HEAD_DOWN_AWAY_DEGREES:
                direction, source = "DOWN", "head_pose"
        return {
            "direction": direction,
            "offscreen_probability": off,
            "reference_ready": True,
            "attention_away": attention_away,
            "attention_direction": attention_direction if attention_away else None,
            "source": source,
            "head_yaw": round(angles[0], 2) if angles else None,
            "head_pitch": round(angles[1], 2) if angles else None,
        }
