"""Full-face CNN and head rotation must not require legacy iris features."""
from types import SimpleNamespace
from unittest.mock import Mock
import math

import cv2
import numpy as np
import pytest

from agent.vision import Camera
from shared.public_gaze import PublicGazeEstimator


def camera(yaw=0, *, count=478, blends=True, matrix=True):
    instance = Camera.__new__(Camera)
    instance.cv2, instance.np, instance.timestamp = cv2, np, 0
    instance.mp = SimpleNamespace(Image=lambda **kwargs: kwargs, ImageFormat=SimpleNamespace(SRGB=1))
    angle = math.radians(yaw)
    rotation = np.array([[math.cos(angle), 0, math.sin(angle)], [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]])
    points = [SimpleNamespace(x=.5, y=.5)] * count  # Iris width is zero, legacy extractor rejects it.
    result = SimpleNamespace(face_landmarks=[points],
        facial_transformation_matrixes=[rotation] if matrix else [],
        face_blendshapes=[[SimpleNamespace(category_name=name, score=.05)
                         for name in ('eyeBlinkLeft', 'eyeBlinkRight')]] if blends else [])
    instance.face = SimpleNamespace(detect_for_video=Mock(return_value=result))
    model = PublicGazeEstimator.__new__(PublicGazeEstimator)
    model.np = np
    model.reference = None
    model.reference_error = None
    model.estimate = Mock(return_value=dict(vector=[0., 0., -1.], yaw_degrees=0., pitch_degrees=0.,
                                            error90_degrees=16., source='public_gaze_model'))
    instance.public_gaze = model
    instance.cancel_gaze_reference()
    return instance


@pytest.mark.parametrize('count,matrix', [(468, True), (478, True), (478, False)])
def test_visible_full_face_cnn_does_not_require_irises_or_pose(count, matrix):
    instance = camera(count=count, matrix=matrix)
    frame = np.zeros((100, 100, 3), np.uint8)
    instance.face_features(frame)
    assert instance.gaze_vector is None
    assert instance.gaze_landmarks is not None and instance.gaze_blinks == [.05, .05]
    instance.public_gaze.reference = [0., 0., -1.]
    instance.public_gaze.reference_error = 16.
    result = instance._observe_public_gaze(frame, 1, 1, 1.)
    assert result['direction'] == 'SCREEN'
    assert result['gaze_observed_direction'] == 'SCREEN'
    instance.public_gaze.estimate.assert_called_once()


def test_direct_head_matrix_still_tracks_strong_turn_without_eye_state():
    instance = camera(yaw=75, blends=False)
    assert instance.head_pose.set_reference_rotations([np.eye(3)] * 25)
    frame = np.zeros((100, 100, 3), np.uint8)
    instance.face_features(frame)
    assert instance.gaze_vector is None and instance.gaze_blinks is None
    result = instance._observe_public_gaze(frame, 1, 1, 1.)
    assert result['head_yaw'] == pytest.approx(75)
    assert result['head_extreme'] and result['head_direction'] == 'RIGHT'
    assert result['direction'] == 'UNKNOWN'
    instance.public_gaze.estimate.assert_not_called()


def test_explicit_calibration_can_complete_without_legacy_iris_features():
    instance = camera()
    instance.begin_gaze_reference()
    for index in range(25):
        frame = np.full((100, 100, 3), index, np.uint8)
        instance.face_features(frame)
        assert instance.gaze_vector is None
        instance._observe_public_gaze(frame, 1, 1, index * .1)
    assert instance.gaze_reference_progress['ready']
    assert instance.head_pose.reference is not None


def test_blink_holds_display_briefly_but_never_strict_gaze_or_rules():
    instance = camera()
    frame = np.zeros((100, 100, 3), np.uint8)
    instance.face_features(frame)
    instance.public_gaze.reference = [0., 0., -1.]
    instance.public_gaze.reference_error = 16.
    assert instance._observe_public_gaze(frame, 1, 1, 1.)['direction'] == 'SCREEN'
    instance.gaze_blinks = [.9, .9]
    result = instance._observe_public_gaze(frame, 1, 1, 1.15)
    assert result['direction'] == 'UNKNOWN' and not result['attention_away']
    assert result['gaze_display_stale'] and result['gaze_observed_direction'] == 'SCREEN'
    result = instance._observe_public_gaze(frame, 1, 1, 1.5)
    assert not result['gaze_display_stale'] and result['gaze_observed_direction'] == 'UNKNOWN'
    assert result['gaze_tracking_status'] == 'blink'


@pytest.mark.parametrize('mesh_faces', [0, 2])
def test_missing_or_second_face_clears_direct_measurements_and_display(mesh_faces):
    instance = camera()
    frame = np.zeros((100, 100, 3), np.uint8)
    instance.face_features(frame)
    points = instance.face.detect_for_video.return_value.face_landmarks[0]
    instance.face.detect_for_video.return_value.face_landmarks = [points] * mesh_faces
    instance.face_features(frame)
    assert instance.gaze_blinks is None and instance.gaze_landmarks is None and instance.head_rotation is None
    result = instance._observe_public_gaze(frame, max(1, mesh_faces), mesh_faces, 1.)
    assert result['direction'] == result['head_direction'] == 'UNKNOWN'
    assert not result['gaze_display_stale']
    instance.public_gaze.estimate.assert_not_called()


@pytest.mark.parametrize('blink', [None, [float('nan'), .1], [-.1, .1], [1.1, .1]])
def test_missing_or_invalid_measured_blinks_never_borrow_legacy_open_eyes(blink):
    instance = camera()
    frame = np.zeros((100, 100, 3), np.uint8)
    instance.face_features(frame)
    instance.gaze_blinks = blink
    instance.gaze_vector = [0.] * 33  # Old feature data must not rescue a missing live signal.
    result = instance._observe_public_gaze(frame, 1, 1, 1.)
    assert result['gaze_tracking_status'] == 'eye_state_missing'
    assert result['direction'] == result['gaze_observed_direction'] == 'UNKNOWN'
    instance.public_gaze.estimate.assert_not_called()
