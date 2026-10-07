import json
import math
from types import SimpleNamespace

import pytest

from shared.gaze_v2 import FEATURE_VERSION, GazeClassifier, extract_features


def pose_vector(yaw=0, pitch=0):
    y, p = math.radians(yaw), math.radians(pitch)
    cy, sy, cp, sp = math.cos(y), math.sin(y), math.cos(p), math.sin(p)
    vector = [0.] * 33
    vector[12:21] = [cy, sy*sp, sy*cp, 0, cp, -sp, -sy, cy*sp, cy*cp]
    return vector


@pytest.mark.parametrize('yaw,pitch,direction', [(-30, 0, 'LEFT'), (30, 0, 'RIGHT'), (0, 25, 'DOWN')])
def test_clear_head_turn_is_detected_even_when_forest_votes_screen(tmp_path, yaw, pitch, direction):
    gaze = GazeClassifier(write_model(tmp_path))
    gaze.observe(pose_vector())
    for _ in range(30):
        observation = gaze.observe(pose_vector(yaw, pitch))
        assert observation['direction'] == direction
        assert observation['source'] == 'head_pose'
    assert gaze.observe(pose_vector())['direction'] == 'SCREEN'


def test_camera_start_pose_is_removed_from_head_angles(tmp_path):
    gaze = GazeClassifier(write_model(tmp_path))
    gaze.observe(pose_vector(yaw=12))
    assert gaze.observe(pose_vector(yaw=25))['direction'] == 'SCREEN'
    assert gaze.observe(pose_vector(yaw=40))['direction'] == 'RIGHT'


def test_small_head_turn_warns_before_it_becomes_a_counted_direction(tmp_path):
    gaze = GazeClassifier(write_model(tmp_path))
    assert not gaze.observe(pose_vector())["attention_away"]
    observation = gaze.observe(pose_vector(yaw=12))
    assert observation["direction"] == "SCREEN"
    assert observation["attention_away"]
    assert observation["attention_direction"] == "RIGHT"


def write_model(tmp_path, *, cycle=False):
    tree = {
        "left": [0 if cycle else 1, -1, -1],
        "right": [2, -1, -1],
        "feature": [0, -2, -2],
        "threshold": [0.2, -2, -2],
        "probabilities": [[0.5, 0.5, 0, 0, 0], [1, 0, 0, 0, 0], [0, 1, 0, 0, 0]],
    }
    model = {
        "schema": 3,
        "feature_version": FEATURE_VERSION,
        "features": 33,
        "classes": ["SCREEN", "DOWN", "LEFT", "RIGHT", "UP"],
        "threshold": 0.65,
        "direction_threshold": 0.55,
        "trees": [tree],
    }
    path = tmp_path / "gaze.json"
    path.write_text(json.dumps(model))
    return path


def test_automatic_reference_stays_fixed_after_initial_open_eye_frame(tmp_path):
    model = GazeClassifier(write_model(tmp_path))
    baseline = [0.0] * 33
    baseline[0] = 0.8
    assert model.observe(None)["direction"] == "UNKNOWN"
    assert model.reference is None
    initial_blink = baseline.copy()
    initial_blink[29] = 0.9
    assert model.observe(initial_blink)["direction"] == "UNKNOWN"
    assert model.reference is None
    assert model.observe(baseline)["direction"] == "SCREEN"
    away = baseline.copy()
    away[0] = 1.2
    for _ in range(100):
        assert model.observe(away)["direction"] == "DOWN"
    assert model.reference == baseline
    assert model.observe(None)["direction"] == "UNKNOWN"
    assert model.observe(baseline)["direction"] == "SCREEN"


def test_second_yolo_face_cannot_establish_the_students_gaze_reference(tmp_path):
    import cv2
    import numpy as np
    from unittest.mock import Mock
    from agent.behavior import PhoneRaising
    from agent.vision import Camera

    camera = Camera.__new__(Camera)
    camera.cv2 = cv2
    camera.centres = {}
    camera.gaze = GazeClassifier(write_model(tmp_path))
    camera.gaze_vector = [0.1] * 33
    camera.face_features = lambda *args: (1, None)
    camera.face_detector = Mock()
    camera.face_detector.detect.return_value = [{"box": [1, 1, 10, 10]}] * 2
    camera.phone = Mock()
    camera.phone.detect.return_value = []
    camera.raising = PhoneRaising()
    frame = np.zeros((360, 640, 3), dtype=np.uint8)

    observation = camera.analyze(frame, at=0)
    assert observation["faces"] == 2 and observation["direction"] == "UNKNOWN"
    assert camera.gaze.reference is None

    # Only the student remains; their different pose becomes the first reference.
    student = [0.2] * 33
    camera.gaze_vector = student
    camera.face_detector.detect.return_value = [{"box": [1, 1, 10, 10]}]
    observation = camera.analyze(frame, at=0.2)
    assert observation["faces"] == 1 and observation["direction"] == "SCREEN"
    assert camera.gaze.reference == student


def test_corrupt_or_cyclic_model_cannot_reach_inference(tmp_path):
    with pytest.raises(ValueError, match="INVALID_GAZE_TREE"):
        GazeClassifier(write_model(tmp_path, cycle=True))
    classifier = GazeClassifier(write_model(tmp_path))
    with pytest.raises(ValueError, match="INVALID_GAZE_FEATURES"):
        classifier.probabilities([float("nan")] * 33)


def test_eye_features_handle_closed_lids_and_preserve_pixel_aspect():
    points = [SimpleNamespace(x=0.5, y=0.5, z=0) for _ in range(478)]
    for a, b, top, bottom, iris in (
        (33, 133, 159, 145, 468),
        (362, 263, 386, 374, 473),
    ):
        points[a] = SimpleNamespace(x=0.3, y=0.4, z=0)
        points[b] = SimpleNamespace(x=0.4, y=0.4, z=0)
        points[iris] = SimpleNamespace(x=0.35, y=0.402, z=0)
        points[top] = SimpleNamespace(x=0.35, y=0.4, z=0)
        points[bottom] = SimpleNamespace(x=0.35, y=0.4, z=0)
    matrix = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    a = extract_features(points, matrix, {}, 1280, 720)
    b = extract_features(points, matrix, {}, 640, 360)
    assert len(a) == 33 and a == pytest.approx(b)
    assert a[0] == pytest.approx(0.5)
    assert a[1] == pytest.approx(0.002 * 720 / (0.1 * 1280))
