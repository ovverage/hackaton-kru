"""Adaptive screen boundaries replace the fixed demo threshold in live cameras."""

import math
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from agent.vision import Camera
from shared.head_pose import HeadPoseObserver
from shared.public_gaze import PublicGazeEstimator
from shared.rules import RuleEngine
from shared.screen_gaze import FIT_TARGETS, VALIDATION_TARGETS, ScreenGazeCalibration


def raw_gaze(yaw=0., pitch=0., error=16.):
    y, p = math.radians(yaw), math.radians(pitch)
    return {
        'vector': [math.sin(y) * math.cos(p), math.sin(p), -math.cos(y) * math.cos(p)],
        'yaw_degrees': yaw, 'pitch_degrees': pitch, 'error90_degrees': error,
        'source': 'public_gaze_model',
    }


def fitted_profile():
    def samples(targets):
        return {name: [raw_gaze((.5 - x) * 40 + noise, (.5 - y) * 24)
                       for noise in (-.05, 0., .05)] for name, x, y in targets}
    profile = ScreenGazeCalibration()
    assert profile.fit(samples(FIT_TARGETS), samples(VALIDATION_TARGETS))['ready']
    return profile


@pytest.fixture
def camera():
    camera = Camera.__new__(Camera)
    camera.np = np
    camera.gaze_vector = None
    camera.gaze_landmarks = [SimpleNamespace(x=.5, y=.5)] * 468
    camera.gaze_blinks = [.01, .01]
    camera.head_rotation = np.eye(3)
    model = PublicGazeEstimator.__new__(PublicGazeEstimator)
    model.np = np
    model.reference = model.reference_error = None
    model.estimate = Mock(return_value=raw_gaze())
    camera.public_gaze = model
    camera.screen_calibration_required = True
    camera.screen_calibration_signature = None
    camera._screen_invalidation_requested = None
    camera.cancel_gaze_reference()
    return camera


def observe(camera, index=0, *, at=None, faces=1, mesh_faces=1):
    frame = np.full((20, 20, 3), index % 256, np.uint8)
    return camera._observe_public_gaze(frame, faces, mesh_faces, index * .1 if at is None else at)


def install(camera, signature=('monitor', 1920, 1080, 1.)):
    camera.begin_screen_calibration(signature)
    captures = []
    for index in range(3):
        observe(camera, index)
        captures.append(camera.screen_calibration_sample())
    assert all(captures)
    assert camera.install_screen_calibration(
        fitted_profile(), [row['gaze'] for row in captures],
        [row['rotation'] for row in captures], signature,
    )


def test_live_camera_requires_full_screen_calibration_even_with_old_centre_reference(camera):
    camera.public_gaze.reference = [0., 0., -1.]
    camera.public_gaze.reference_error = 16.
    camera.public_gaze.estimate.return_value = raw_gaze(45)
    result = observe(camera)
    assert result['direction'] == result['gaze_observed_direction'] == 'UNKNOWN'
    assert not result['reference_ready'] and camera.requires_gaze_reference
    assert result['gaze_decision_policy'] == 'adaptive_screen'
    assert result['screen_reason'] == 'calibration_required'
    with pytest.raises(ValueError, match='SCREEN_CALIBRATION_REQUIRED'):
        camera.begin_gaze_reference()


def test_screen_capture_consumes_only_fresh_visible_frames(camera):
    camera.begin_screen_calibration(('monitor', 1))
    assert camera.screen_calibration_sample() is None
    observe(camera, 0)
    first = camera.screen_calibration_sample()
    assert first['at'] == 0 and len(first['frame_id']) == 64
    assert first['gaze']['error90_degrees'] == 16
    assert first['rotation'] is not camera.head_rotation
    assert camera.screen_calibration_sample() is None
    observe(camera, 0, at=.1)
    assert camera.screen_calibration_sample() is None  # Frozen pixels cannot add a sample.
    observe(camera, 1, at=.2)
    assert camera.screen_calibration_sample()['frame_id'] != first['frame_id']
    camera.gaze_blinks = [.95, .95]
    observe(camera, 2)
    assert camera.screen_calibration_sample() is None
    camera.gaze_blinks = [.01, .01]
    camera.public_gaze.estimate.return_value = raw_gaze(error=25)
    observe(camera, 3)
    assert camera.screen_calibration_sample() is None
    camera.public_gaze.estimate.return_value = raw_gaze()
    observe(camera, 4, faces=2)
    assert camera.screen_calibration_sample() is None


def test_screen_reference_uses_three_real_frames_and_separate_head_reference(camera):
    install(camera)
    assert not camera.requires_gaze_reference
    assert camera.gaze_reference_progress['ready'] and camera.gaze_enabled
    assert len(camera._gaze_reference_samples) == 3
    assert camera.head_pose.reference is not None
    assert camera.public_gaze.reference == pytest.approx([0, 0, -1])
    assert camera.screen_signature == ('monitor', 1920, 1080, 1.)


@pytest.mark.parametrize('yaw,expected,reason', [
    (0., 'SCREEN', 'inside_screen'),
    (17., 'SCREEN', 'inside_screen'),
    (18., 'SCREEN', 'inside_screen'),
    (22., 'UNKNOWN', 'boundary_margin'),
    (24., 'UNKNOWN', 'boundary_margin'),
    (25., 'LEFT', 'outside_screen'),
    (-25., 'RIGHT', 'outside_screen'),
])
def test_personal_monitor_boundary_and_six_degree_margin_replace_nine_degree_strikes(
    camera, yaw, expected, reason,
):
    install(camera)
    camera.public_gaze.estimate.return_value = raw_gaze(yaw)
    result = observe(camera, 4)
    assert result['direction'] == result['gaze_observed_direction'] == expected
    assert result['screen_reason'] == reason
    assert result['gaze_display_yaw_degrees'] == pytest.approx(yaw)
    assert result['gaze_decision_threshold_degrees'] == 6
    assert result['attention_away'] == (expected in ('LEFT', 'RIGHT'))


def test_five_second_timer_only_runs_beyond_measured_screen_and_guard(camera):
    install(camera)
    engine = RuleEngine()
    engine.start()
    for yaw, offset in ((17, 0), (22, 7)):
        camera.public_gaze.estimate.return_value = raw_gaze(yaw)
        for index in range(65):
            at = offset + index * .1
            result = observe(camera, index + 3, at=at)
            engine.observe(at, direction=result['direction'])
        assert not any(engine.state.counts().values())
    camera.public_gaze.estimate.return_value = raw_gaze(25)
    for index in range(60):
        at = 14 + index * .1
        result = observe(camera, index + 3, at=at)
        engine.observe(at, direction=result['direction'])
    assert engine.state.counts()['LEFT'] == 1


@pytest.mark.parametrize('condition', ['blink', 'high_error', 'two_faces', 'lost_mesh'])
def test_validated_profile_cannot_override_current_visibility_or_quality_gate(camera, condition):
    install(camera)
    camera.public_gaze.estimate.return_value = raw_gaze(40)
    if condition == 'blink':
        camera.gaze_blinks = [.9, .9]
    elif condition == 'high_error':
        camera.public_gaze.estimate.return_value = raw_gaze(40, error=21)
    result = observe(camera, 4, faces=2 if condition == 'two_faces' else 1,
                     mesh_faces=0 if condition == 'lost_mesh' else 1)
    assert result['direction'] == 'UNKNOWN'
    assert not result['attention_away']
    if condition == 'high_error':
        assert result['gaze_observed_direction'] == 'LEFT'
        assert result['gaze_observation_uncertain']
        assert result['gaze_display_yaw_degrees'] == pytest.approx(40)
    else:
        assert result['gaze_observed_direction'] == 'UNKNOWN'


def test_head_and_eye_angles_are_not_added_or_used_interchangeably(camera):
    install(camera)
    angle = math.radians(60)
    camera.head_rotation = np.array([[math.cos(angle), 0, math.sin(angle)],
                                    [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]])
    camera.public_gaze.estimate.return_value = raw_gaze(17)
    result = observe(camera, 4)
    assert result['direction'] == result['gaze_observed_direction'] == 'SCREEN'
    assert result['head_yaw'] == pytest.approx(60) and result['head_extreme']


def test_monitor_invalidation_is_queued_and_never_falls_back_to_demo_threshold(camera):
    install(camera)
    reference = camera.public_gaze.reference
    camera.request_screen_invalidation('SCREEN_CHANGED')
    assert camera.public_gaze.reference is reference  # GUI does not mutate inference state.
    assert camera.requires_gaze_reference
    camera.public_gaze.estimate.return_value = raw_gaze(45)
    result = observe(camera, 4)
    assert camera.public_gaze.reference is None and camera.head_pose.reference is None
    assert camera.gaze_reference_progress['error'] == 'SCREEN_CHANGED'
    assert result['direction'] == 'UNKNOWN' and not camera.gaze_enabled
    assert result['gaze_observed_direction'] == 'UNKNOWN'


@pytest.mark.parametrize('failure', ['bad_profile', 'changed_monitor', 'few_centres',
                                     'unstable_centres', 'uncertain_centres', 'pending_change'])
def test_failed_install_disables_every_prior_gaze_reference(camera, failure):
    install(camera)
    signature = ('monitor', 1920, 1080, 1.)
    camera.begin_screen_calibration(signature)
    profile = fitted_profile() if failure != 'bad_profile' else ScreenGazeCalibration()
    centres = [raw_gaze()] * 3
    if failure == 'changed_monitor':
        signature = ('other monitor', 1920, 1080, 1.)
    elif failure == 'few_centres':
        centres = centres[:2]
    elif failure == 'unstable_centres':
        centres = [raw_gaze(-30), raw_gaze(), raw_gaze(30)]
    elif failure == 'uncertain_centres':
        centres[-1] = raw_gaze(error=25)
    elif failure == 'pending_change':
        camera.request_screen_invalidation('SCREEN_CHANGED')
    assert not camera.install_screen_calibration(profile, centres, [np.eye(3)] * 3, signature)
    assert camera.requires_gaze_reference and not camera.gaze_enabled
    assert camera.public_gaze.reference is None
    camera.public_gaze.estimate.return_value = raw_gaze(50)
    assert observe(camera, 4)['direction'] == 'UNKNOWN'


def test_head_reference_still_requires_twenty_five_unless_screen_caller_explicit(camera):
    observer = HeadPoseObserver()
    assert not observer.set_reference_rotations([np.eye(3)] * 3)
    assert observer.set_reference_rotations([np.eye(3)] * 3, required_samples=3)
    assert not observer.set_reference_rotations([np.eye(3)] * 2, required_samples=2)


def test_missing_head_matrices_do_not_invent_head_reference_or_reject_valid_screen(camera):
    camera.begin_screen_calibration('screen')
    assert camera.install_screen_calibration(fitted_profile(), [raw_gaze()] * 3,
                                             [None] * 3, 'screen')
    assert camera.gaze_enabled and camera.head_pose.reference is None
    result = observe(camera, 4)
    assert result['direction'] == 'SCREEN'
    assert result['head_direction'] == 'UNKNOWN'
