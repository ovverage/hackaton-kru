"""Private CNN camera integration: explicit reference, identity and blink gates."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from agent.behavior import PhoneRaising
from agent.vision import Camera
from shared.public_gaze import PublicGazeEstimator
from shared.rules import RuleEngine


def estimate(yaw=0, error=5):
    radians = math.radians(yaw)
    return {
        'vector': [math.sin(radians), 0., -math.cos(radians)],
        'yaw_degrees': yaw, 'pitch_degrees': 0., 'error90_degrees': error,
        'source': 'public_gaze_model',
    }


@pytest.fixture
def camera():
    camera = Camera.__new__(Camera)
    camera.cv2 = cv2
    camera.np = np
    camera.centres = {'SCREEN': [0., 0., 0., 0.]}
    camera.gaze = Mock()
    camera.gaze.observe.return_value = {'direction': 'LEFT'}
    camera.gaze_landmarks = [SimpleNamespace(x=.5, y=.5)] * 478
    camera.gaze_vector = [0.] * 33
    camera.face_features = Mock(return_value=(1, np.zeros(4)))
    camera.face_detector = Mock()
    camera.face_detector.detect.return_value = []
    camera.phone = Mock()
    camera.phone.detect.return_value = []
    camera.raising = PhoneRaising()
    model = PublicGazeEstimator.__new__(PublicGazeEstimator)
    model.np = np
    model.reference = None
    model.reference_error = None
    model.estimate = Mock(return_value=estimate())
    camera.public_gaze = model
    camera.cancel_gaze_reference()
    return camera


def frame(index=0):
    image = np.zeros((360, 640, 3), np.uint8)
    image[0, 0] = index % 256
    return image


def collect(camera, start=0):
    for index in range(25):
        camera.analyze(frame(index), at=start + index * .1)


def test_preview_never_chooses_arbitrary_neutral_or_produces_gaze_strikes(camera):
    camera.public_gaze.estimate.return_value = estimate(45)
    engine = RuleEngine()
    engine.start()
    for index in range(100):
        observation = camera.analyze(frame(index), at=index * .1)
        assert observation['direction'] == 'UNKNOWN'
        engine.observe(index * .1, **observation)
    assert camera.requires_gaze_reference
    assert not camera.gaze_enabled
    assert camera.gaze_reference_progress['collected'] == 0
    assert not any(engine.state.counts().values())
    camera.gaze.observe.assert_not_called()


def test_explicit_centre_reference_enables_existing_rule_directions_and_stays_fixed(camera):
    camera.begin_gaze_reference()
    collect(camera)
    assert camera.gaze_reference_progress == {
        'collecting': False, 'collected': 25, 'required': 25,
        'ready': True, 'error': None,
    }
    assert camera.gaze_enabled and not camera.requires_gaze_reference
    assert camera.analyze(frame(26), at=3)['direction'] == 'SCREEN'
    reference = list(camera.public_gaze.reference)
    camera.public_gaze.estimate.return_value = estimate(45)
    for index in range(25):
        assert camera.analyze(frame(index), at=4 + index * .1)['direction'] == 'LEFT'
    assert camera.public_gaze.reference == reference
    assert camera.gaze_diagnostics['attention_away']
    assert camera.gaze_diagnostics['attention_direction'] == 'LEFT'
    assert camera.gaze_diagnostics['source'] == 'public_gaze_model'
    camera.gaze.observe.assert_not_called()


@pytest.mark.parametrize('condition', ['mesh_missing', 'mesh_multiple', 'yolo_multiple', 'blink', 'invalid_vector'])
def test_ambiguous_faces_or_closed_eyes_never_calibrate_or_emit_directions(camera, condition):
    camera.begin_gaze_reference()
    collect(camera)
    camera.public_gaze.estimate.return_value = estimate(45)
    if condition == 'mesh_missing':
        camera.face_features.return_value = (0, None)
    elif condition == 'mesh_multiple':
        camera.face_features.return_value = (2, None)
    elif condition == 'yolo_multiple':
        camera.face_detector.detect.return_value = [{}, {}]
    elif condition == 'blink':
        camera.gaze_vector[30] = .9
    else:
        camera.gaze_vector[0] = float('nan')
    camera.public_gaze.estimate.reset_mock()
    assert camera.analyze(frame(26), at=3)['direction'] == 'UNKNOWN'
    assert not camera.gaze_diagnostics['attention_away']
    camera.public_gaze.estimate.assert_not_called()
    camera.begin_gaze_reference()
    collect(camera, start=4)
    assert not camera.gaze_reference_progress['ready']
    assert camera.gaze_reference_progress['collected'] == 0


def test_uncertain_prediction_never_falls_back_to_legacy_gaze(camera):
    camera.begin_gaze_reference()
    collect(camera)
    camera.public_gaze.estimate.return_value = estimate(45, error=25)
    assert camera.analyze(frame(26), at=3)['direction'] == 'UNKNOWN'
    camera.gaze.observe.assert_not_called()


def test_frozen_image_does_not_count_as_25_reference_samples(camera):
    camera.begin_gaze_reference()
    for index in range(25):
        camera.analyze(frame(), at=index * .1)
    assert camera.gaze_reference_progress['collected'] < 25
    assert not camera.gaze_reference_progress['ready']


def test_second_person_or_long_capture_gap_discards_partial_reference(camera):
    camera.begin_gaze_reference()
    for index in range(10):
        camera.analyze(frame(index), at=index * .1)
    assert camera.gaze_reference_progress['collected'] == 10
    camera.face_detector.detect.return_value = [{}, {}]
    camera.analyze(frame(11), at=1.1)
    assert camera.gaze_reference_progress['collected'] == 0
    camera.face_detector.detect.return_value = []
    camera.analyze(frame(12), at=1.2)
    camera.analyze(frame(13), at=5)
    assert camera.gaze_reference_progress['collected'] == 1


def test_unstable_reference_requires_explicit_retry(camera):
    camera.begin_gaze_reference()
    for index in range(25):
        camera.public_gaze.estimate.return_value = estimate(-30 if index % 2 else 30)
        camera.analyze(frame(index), at=index * .1)
    progress = camera.gaze_reference_progress
    assert progress['error'] == 'GAZE_REFERENCE_UNSTABLE'
    assert not progress['collecting'] and not progress['ready']
    assert not camera.gaze_enabled
    camera.public_gaze.estimate.return_value = estimate()
    collect(camera, start=3)
    assert not camera.gaze_reference_progress['ready']
    camera.begin_gaze_reference()
    collect(camera, start=6)
    camera.validate_calibration()
    assert camera.gaze_reference_progress['ready']


def test_cancel_discards_reference_and_cannot_implicitly_rearm(camera):
    camera.begin_gaze_reference()
    collect(camera)
    camera.cancel_gaze_reference()
    collect(camera, start=3)
    assert camera.requires_gaze_reference
    assert not camera.gaze_reference_progress['collecting']
    assert not camera.gaze_reference_progress['ready']
    with pytest.raises(ValueError, match='GAZE_REFERENCE_INSUFFICIENT'):
        camera.validate_calibration()


def test_cnn_uses_the_same_mesh_frame_as_its_landmarks(camera):
    image = np.zeros((720, 1280, 3), np.uint8)
    camera.analyze(image, at=0)
    mesh_frame = camera.face_features.call_args.args[0]
    crop_frame, crop_landmarks = camera.public_gaze.estimate.call_args.args
    assert mesh_frame.shape == (540, 960, 3)
    assert crop_frame is mesh_frame
    assert crop_landmarks is camera.gaze_landmarks


def test_phone_detection_remains_active_before_reference(camera):
    camera.phone.detect.return_value = [{'confidence': .95, 'box': [20, 20, 50, 90]}]
    engine = RuleEngine()
    engine.start()
    for index in range(3):
        observation = camera.analyze(frame(index), at=index * .2)
        assert observation['direction'] == 'UNKNOWN'
        engine.observe(index * .2, **observation)
    assert engine.state.reason == 'PHONE_DETECTED'
