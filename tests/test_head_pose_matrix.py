"""Face-transform tracking works when eye/iris features are unavailable."""

import math

import numpy as np
import pytest

from shared.head_pose import HeadPoseObserver, rotation_from_matrix


def rotation(yaw=0, pitch=0):
    y, p = math.radians(yaw), math.radians(pitch)
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    return ry @ rx


def observer():
    result = HeadPoseObserver()
    assert result.set_reference_rotations([np.eye(3)] * 25)
    return result


@pytest.mark.parametrize("yaw,pitch,direction", [
    (-70, 0, "LEFT"), (70, 0, "RIGHT"),
    (-85, 0, "LEFT"), (85, 0, "RIGHT"),
    (0, 60, "DOWN"), (0, -60, "UP"),
])
def test_profile_rotation_does_not_require_irises_or_blink_features(yaw, pitch, direction):
    model = observer()
    # No 33-value eye feature vector exists for this frame. A valid transform
    # still carries its own orientation, including near-profile poses.
    assert model.observe(None)["head_tracking_status"] == "unavailable"
    result = model.observe_rotation(rotation(yaw, pitch))
    assert result["head_tracking_status"] == "tracked"
    assert result["head_direction"] == direction
    assert result["head_yaw"] == pytest.approx(yaw)
    assert result["head_pitch"] == pytest.approx(pitch)
    assert result["head_extreme"] and result["head_away"]


@pytest.mark.parametrize("yaw,pitch,direction,away", [
    (0, 0, "SCREEN", False), (8.99, 0, "SCREEN", False),
    (0, -8.99, "SCREEN", False),
    (9, 0, "RIGHT", False), (-9, 0, "LEFT", False),
    (0, 9, "DOWN", False), (0, -9, "UP", False),
    (21.99, 0, "RIGHT", False), (22, 0, "RIGHT", True),
    (0, 17.99, "DOWN", False), (0, 18, "DOWN", True),
])
def test_sensitive_display_preserves_stronger_review_thresholds(yaw, pitch, direction, away):
    result = observer().observe_rotation(rotation(yaw, pitch))
    assert result["head_direction"] == direction
    assert result["head_warning"] == (direction != "SCREEN")
    assert result["head_away"] == away
    assert not result["head_extreme"]


@pytest.mark.parametrize("yaw,pitch,extreme", [
    (44.99, 0, False), (45, 0, True), (-45, 0, True),
    (0, 34.99, False), (0, 35, True), (0, -35, True),
])
def test_extreme_orientation_threshold(yaw, pitch, extreme):
    assert observer().observe_rotation(rotation(yaw, pitch))["head_extreme"] == extreme


@pytest.mark.parametrize("shape,scale", [(3, .2), (3, 12), (4, .2), (4, 12)])
def test_uniform_scale_and_translation_are_removed(shape, scale):
    expected = rotation(70, -10)
    matrix = np.eye(shape)
    matrix[:3, :3] = expected * scale
    if shape == 4:
        matrix[:3, 3] = [123, -20, 3]
    np.testing.assert_allclose(rotation_from_matrix(matrix), expected, atol=1e-12)


@pytest.mark.parametrize("matrix", [
    None, [], [[1, 0], [0, 1]], [[1], [1, 0]], "invalid",
    np.zeros((3, 3)), np.full((3, 3), np.nan),
    np.full((4, 4), np.inf), np.diag([-1., 1., 1.]),
    np.diag([1., 1.2, 1.]), [[1, .3, 0], [0, 1, 0], [0, 0, 1]],
    [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [.2, 0, 0, 1]],
])
def test_bad_transforms_do_not_invent_orientation(matrix):
    assert rotation_from_matrix(matrix) is None
    result = observer().observe_rotation(matrix)
    assert result["head_tracking_status"] == "unavailable"
    assert result["head_yaw"] is None and result["head_pitch"] is None
    assert result["head_direction"] == "UNKNOWN"
    assert not result["head_away"] and not result["head_extreme"]


def test_tracking_status_distinguishes_missing_ambiguous_and_uncalibrated():
    model = HeadPoseObserver()
    assert model.observe_rotation(np.eye(3))["head_tracking_status"] == "not_calibrated"
    assert model.set_reference_rotations([np.eye(3)] * 25)
    for faces, status in [(0, "unavailable"), (2, "ambiguous_faces")]:
        result = model.observe_rotation(rotation(85), faces=faces)
        assert result["head_tracking_status"] == status
        assert result["head_yaw"] is None and not result["head_extreme"]
    model.observe_rotation(rotation(85))
    lost = model.observe_rotation(None)
    assert lost["head_tracking_status"] == "unavailable"
    assert lost["head_yaw"] is None and not lost["head_away"]


def test_matrix_reference_is_explicit_stable_and_fixed():
    model = HeadPoseObserver()
    reference = rotation(10, 5)
    assert model.set_reference_rotations([reference] * 25)
    np.testing.assert_allclose(model.reference, reference, atol=1e-12)
    for _ in range(30):
        result = model.observe_rotation(rotation(70) @ reference)
        assert result["head_yaw"] == pytest.approx(70)
    np.testing.assert_allclose(model.reference, reference, atol=1e-12)
    assert not model.set_reference_rotations([reference] * 24)
    assert model.reference is None
    assert model.reference_error == "HEAD_REFERENCE_INSUFFICIENT"
    assert not model.set_reference_rotations([rotation(30 if i % 2 else -30) for i in range(25)])
    assert model.reference is None
    assert model.reference_error == "HEAD_REFERENCE_UNSTABLE"
