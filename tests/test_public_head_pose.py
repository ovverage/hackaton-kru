"""The private CNN's explicitly referenced head signal stays separate from gaze."""

import math

import numpy as np
import pytest

from agent.vision import Camera
from shared.head_pose import HeadPoseObserver, rotation_from_features
from shared.rules import RuleEngine


def features(yaw=0, pitch=0):
    y, p = math.radians(yaw), math.radians(pitch)
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    result = [0.] * 33
    result[12:21] = (ry @ rx).reshape(-1).tolist()
    return result


@pytest.mark.parametrize("yaw,pitch,direction", [
    (-30, 0, "LEFT"), (30, 0, "RIGHT"), (0, 25, "DOWN"), (0, -25, "UP"),
    (0, 0, "SCREEN"), (12, 0, "RIGHT"), (0, 10, "DOWN"),
])
def test_independent_orientation_axes_and_warning_zone(yaw, pitch, direction):
    model = HeadPoseObserver()
    assert model.set_reference([features()] * 25)
    result = model.observe(features(yaw, pitch))
    assert result["head_direction"] == direction
    assert result["head_yaw"] == pytest.approx(yaw)
    assert result["head_pitch"] == pytest.approx(pitch)
    assert result["head_away"] == (abs(yaw) >= 22 or abs(pitch) >= 18)
    assert result["head_warning"] == (abs(yaw) >= 9 or abs(pitch) >= 9)
    assert "direction" not in result


def test_reference_is_explicit_stable_and_fixed():
    model = HeadPoseObserver()
    assert model.observe(features(35))["head_direction"] == "UNKNOWN"
    assert model.reference is None
    assert model.set_reference([features(10)] * 25)
    fixed = model.reference.copy()
    for _ in range(60):
        assert model.observe(features(40))["head_direction"] == "RIGHT"
    np.testing.assert_array_equal(model.reference, fixed)
    assert model.observe(features(10))["head_direction"] == "SCREEN"
    assert not model.set_reference([features(-30 if i % 2 else 30) for i in range(25)])
    assert model.reference is None
    assert model.reference_error == "HEAD_REFERENCE_UNSTABLE"


@pytest.mark.parametrize("invalid", [None, [0.] * 33, [float("nan")] * 33, [0.] * 12])
def test_invalid_matrices_never_produce_head_orientation(invalid):
    model = HeadPoseObserver()
    model.set_reference([features()] * 25)
    assert rotation_from_features(invalid) is None
    assert model.observe(invalid)["head_direction"] == "UNKNOWN"


def test_reflection_and_nonorthogonal_matrix_are_rejected():
    reflected = features()
    reflected[12] = -1
    assert rotation_from_features(reflected) is None
    skewed = features()
    skewed[13] = .5
    assert rotation_from_features(skewed) is None


def test_head_pose_survives_blink_but_never_missing_or_multiple_faces():
    model = HeadPoseObserver()
    model.set_reference([features()] * 25)
    blink = features(30)
    blink[29:31] = [1., 1.]
    assert model.observe(blink)["head_direction"] == "RIGHT"
    for faces in (0, 2):
        result = model.observe(blink, faces=faces)
        assert result["head_direction"] == "UNKNOWN"
        assert result["head_yaw"] is None and not result["head_away"]


class FakePublicGaze:
    reference = None
    reference_error = None
    fail = False
    uncertain = False

    def observe(self, frame, landmarks, mesh_features):
        result = {"direction": "UNKNOWN", "reference_ready": self.reference is not None}
        if mesh_features is not None:
            result.update(vector=[0., 0., -1.], error90_degrees=16.)
            if self.reference is not None and not self.uncertain:
                result["direction"] = "CENTER"
        return result

    def set_reference(self, observations):
        assert len(observations) == 25
        if self.fail:
            raise ValueError("GAZE_REFERENCE_UNSTABLE")
        self.reference = [0., 0., -1.]


def camera():
    instance = Camera.__new__(Camera)
    instance.public_gaze = FakePublicGaze()
    instance.gaze_vector = features()
    instance.gaze_landmarks = [object()] * 478
    instance.cancel_gaze_reference()
    return instance


def observe(instance, index, *, faces=1):
    frame = np.full((2, 2, 3), index % 256, dtype=np.uint8)
    return instance._observe_public_gaze(frame, faces, faces, index * .1)


def collect(instance, start=0):
    for index in range(start, start + 25):
        observe(instance, index)


def test_camera_uses_same_explicit_samples_and_head_never_becomes_gaze_strike():
    instance = camera()
    collect(instance)
    assert instance.head_pose.reference is None
    instance.begin_gaze_reference()
    collect(instance, 25)
    assert instance.head_pose.reference is not None
    assert len(instance._head_reference_samples) == 25
    instance.gaze_vector = features(30)
    instance.gaze_vector[29:31] = [1., 1.]
    engine = RuleEngine()
    engine.start()
    for index in range(50, 120):
        result = observe(instance, index)
        assert result["head_direction"] == "RIGHT"
        assert result["direction"] == "UNKNOWN"
        engine.observe(index * .1, direction=result["direction"])
    assert not any(engine.state.counts().values())
    assert observe(instance, 120, faces=2)["head_direction"] == "UNKNOWN"


def test_cancel_and_failed_gaze_reference_clear_head_reference():
    instance = camera()
    instance.begin_gaze_reference()
    collect(instance)
    instance.cancel_gaze_reference()
    assert instance.head_pose.reference is None
    assert not instance._head_reference_samples
    instance.public_gaze.fail = True
    instance.begin_gaze_reference()
    collect(instance, 25)
    assert instance.public_gaze.reference is None
    assert instance.head_pose.reference is None


def test_invalid_or_unstable_head_reference_does_not_disable_valid_gaze_reference():
    for bad_matrix in (True, False):
        instance = camera()
        instance.begin_gaze_reference()
        for index in range(25):
            instance.gaze_vector = [0.] * 33 if bad_matrix else features(30 if index % 2 else -30)
            observe(instance, index)
        assert instance.gaze_enabled
        assert instance.public_gaze.reference is not None
        assert instance.head_pose.reference is None
        assert instance.head_pose.reference_error == (
            "HEAD_REFERENCE_INSUFFICIENT" if bad_matrix else "HEAD_REFERENCE_UNSTABLE"
        )


def test_interrupted_reference_discards_both_partial_sample_sets():
    instance = camera()
    instance.begin_gaze_reference()
    for index in range(10):
        observe(instance, index)
    assert len(instance._head_reference_samples) == 10
    observe(instance, 10, faces=2)
    assert instance._head_reference_samples == instance._gaze_reference_samples == []
    collect(instance, 11)
    assert instance.head_pose.reference is not None
