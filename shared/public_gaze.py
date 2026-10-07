"""CPU inference contract for the public-dataset gaze experiment.

Axes follow Gaze360: x camera-left, y up, z away. These are directions,
not screen coordinates. A neutral reference must be explicitly established
while the user is instructed to look at the screen centre.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

SCHEMA = "qorgau-public-gaze-v1"
INPUT_SIZE = 224


def normalize(vector):
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values))
    if len(values) != 3 or not math.isfinite(norm) or norm < 1e-8:
        raise ValueError("INVALID_GAZE_VECTOR")
    return [v / norm for v in values]


def angles(vector):
    x, y, z = normalize(vector)
    return math.degrees(math.atan2(x, -z)), math.degrees(math.asin(max(-1, min(1, y))))


def angular_error(prediction, target):
    p, t = normalize(prediction), normalize(target)
    return math.degrees(math.acos(max(-1, min(1, sum(a*b for a, b in zip(p, t))))))


def mpii_direction(face_center, target):
    """Camera coordinates -> eye-to-camera ray basis used by Gaze360.

    MPII camera x is right, y down. Rotation removes the off-axis position
    of the participant; this is not merely flipping two signs.
    """
    import numpy as np
    z = np.asarray(normalize(face_center))
    right = np.cross([0., 1., 0.], z)
    right /= np.linalg.norm(right)
    down = np.cross(z, right)
    ray = np.asarray(target, dtype=float) - np.asarray(face_center, dtype=float)
    return normalize(np.stack([-right, -down, z]) @ ray)


def face_crop(frame, landmarks, size=INPUT_SIZE):
    """Identical tight square RGB crop for training and live inference.

    Input frame is BGR; landmarks expose normalized x/y. Padding is reflected.
    Perspective normalization is intentionally absent: both datasets and the
    live camera use face appearance in a tight image crop. This leaves a
    documented approximation for faces far from the camera optical axis.
    """
    import cv2
    import numpy as np
    h, w = frame.shape[:2]
    if len(landmarks) < 468:
        return None
    xy = np.asarray([(p.x*w, p.y*h) for p in landmarks[:468]], dtype=float)
    if not np.isfinite(xy).all():
        return None
    lo, hi = xy.min(0), xy.max(0)
    span = max(hi-lo)*1.2
    if span < 40 or span > max(w, h)*2:
        return None
    centre = (lo+hi)/2
    # Affine sampling keeps the same crop geometry at every image boundary.
    scale = size/span
    matrix = np.array([[scale, 0, size/2-centre[0]*scale],
                       [0, scale, size/2-centre[1]*scale]], dtype=np.float32)
    crop = cv2.warpAffine(frame, matrix, (size, size), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REFLECT_101)
    return cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)


class PublicGazeEstimator:
    """Optional ONNX gaze model; Face Mesh remains required at every frame."""
    def __init__(self, path: Path, metadata: Path | None = None, threads=2, *, allow_unvalidated=False):
        import onnxruntime as ort
        import numpy as np
        self.np = np
        self.metadata = json.loads((metadata or path.with_suffix(".json")).read_text("utf-8"))
        if (self.metadata.get("schema") != SCHEMA or
                self.metadata.get("input_size") != INPUT_SIZE or
                not self.metadata.get("validation_calibration")):
            raise ValueError("INVALID_PUBLIC_GAZE_METADATA")
        if not allow_unvalidated and not self.metadata.get("deployment_eligible"):
            raise ValueError("PUBLIC_GAZE_VALIDATION_GATE_FAILED")
        import hashlib
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.metadata.get("onnx_sha256"):
            raise ValueError("PUBLIC_GAZE_HASH_MISMATCH")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        inputs, outputs = self.session.get_inputs(), self.session.get_outputs()
        if (len(inputs) != 1 or inputs[0].name != "face" or inputs[0].type != "tensor(float)"
                or len(inputs[0].shape) != 4 or inputs[0].shape[1:] != [3, INPUT_SIZE, INPUT_SIZE]
                or len(outputs) != 1 or outputs[0].name != "gaze" or outputs[0].type != "tensor(float)"
                or len(outputs[0].shape) != 2 or outputs[0].shape[1] != 4):
            raise ValueError("PUBLIC_GAZE_ONNX_CONTRACT_MISMATCH")
        self.reference = None
        self.reference_error = None

    def estimate(self, frame, landmarks):
        crop = face_crop(frame, landmarks)
        if crop is None:
            return None
        np = self.np
        tensor = crop.astype(np.float32).transpose(2, 0, 1)[None]/255
        tensor = (tensor-np.asarray([.485, .456, .406], np.float32)[None,:,None,None]) / np.asarray([.229, .224, .225], np.float32)[None,:,None,None]
        result = self.session.run(None, {"face": tensor})[0][0]
        if result.shape != (4,) or not np.isfinite(result).all():
            return None
        vector = normalize(result[:3])
        concentration = max(1., min(200., float(result[3])))
        scale = math.degrees(math.sqrt(2/concentration))
        correction = self.metadata["validation_calibration"]["q90_scale_multiplier"]
        if not isinstance(correction, (int,float)) or not math.isfinite(correction) or correction <= 0:
            return None
        error90 = min(180., scale*float(correction))
        yaw, pitch = angles(vector)
        return {"vector": vector, "yaw_degrees": yaw, "pitch_degrees": pitch,
                "error90_degrees": error90, "source": "public_gaze_model"}

    def set_reference(self, observations):
        """Explicit neutral calibration; rejects inconsistent/uncertain input."""
        valid = [o for o in observations if o and o["error90_degrees"] <= 20]
        if len(valid) < 15:
            raise ValueError("GAZE_REFERENCE_INSUFFICIENT")
        reference = normalize(self.np.mean([o["vector"] for o in valid], axis=0))
        if self.np.percentile([angular_error(o["vector"], reference) for o in valid], 90) > 8:
            raise ValueError("GAZE_REFERENCE_UNSTABLE")
        self.reference = reference
        self.reference_error = float(self.np.median([o["error90_degrees"] for o in valid]))

    def observe(self, frame, landmarks, mesh_features=None):
        result = {"direction": "UNKNOWN", "offscreen_probability": None,
                  "reference_ready": self.reference is not None,
                  "source": "public_gaze_model"}
        if mesh_features is None or len(mesh_features) != 33:
            return result
        # Blinks, severe eye occlusion, missing/ambiguous faces do not produce
        # apparent confidence. Caller must pass landmarks of exactly one face.
        if mesh_features[29] > .65 or mesh_features[30] > .65:
            return result
        observation = self.estimate(frame, landmarks)
        if not observation:
            return result
        result.update(observation)
        if self.reference is None or observation["error90_degrees"] > 20:
            return result
        yaw0, pitch0 = angles(self.reference)
        yaw = (observation["yaw_degrees"]-yaw0+180)%360-180
        pitch = observation["pitch_degrees"]-pitch0
        result.update(relative_yaw_degrees=yaw, relative_pitch_degrees=pitch)
        # Operational thresholds are deliberately distinct from measured model
        # uncertainty; neither is calibrated against a monitor boundary.
        uncertainty = observation["error90_degrees"]+(self.reference_error or 0)
        horizontal = max(22., uncertainty)
        vertical = max(18., uncertainty)
        if abs(yaw) >= horizontal and abs(yaw)/horizontal > abs(pitch)/vertical:
            result["direction"] = "LEFT" if yaw > 0 else "RIGHT"
        elif abs(pitch) >= vertical:
            result["direction"] = "UP" if pitch > 0 else "DOWN"
        elif abs(yaw) < 10 and abs(pitch) < 8:
            result["direction"] = "CENTER"
        return result
