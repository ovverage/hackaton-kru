"""Unavailable head pose is optional; measured unstable pose is not absence."""

import math

import numpy as np

from shared.head_pose import HeadPoseObserver
from test_screen_capture import CaptureHarness
from test_screen_calibration_runtime import camera as camera, fitted_profile, raw_gaze


def rotation(yaw):
    angle = math.radians(yaw)
    return np.array([[math.cos(angle), 0, math.sin(angle)],
                     [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]])


def test_valid_but_unstable_center_poses_have_distinct_failure_reason():
    observer = HeadPoseObserver()
    assert not observer.set_reference_rotations([rotation(yaw) for yaw in (0, 30, 60)],
                                               required_samples=3)
    assert observer.reference_error == 'HEAD_REFERENCE_UNSTABLE'
    assert observer.reference is None


def test_capture_rejects_observed_center_head_motion_instead_of_disabling_pose_gate(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def moving_head(row):
        row['rotation'] = rotation((harness.local_sample % 3) * 30 if harness.current % 9 == 0 else 0)
        return row

    harness.sample_policy = moving_head
    harness.on_read = lambda: harness.stop.set() if harness.current >= 9 else None
    result = harness.run()
    assert not result['ready']
    assert result['error'] == 'SCREEN_CALIBRATION_CANCELLED'
    assert result['capture']['targets']['fit_center']['last_error'] == 'SCREEN_HEAD_MOVED'
    assert not harness.installs
    assert [row[0] for row in harness.targets] == [0, 9]


def test_capture_rejects_later_head_motion_when_center_pose_is_available(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def moved_after_center(row):
        row['rotation'] = rotation(0 if harness.current % 9 == 0 else 35)
        return row

    harness.sample_policy = moved_after_center
    harness.on_read = lambda: harness.stop.set() if harness.current >= 9 else None
    result = harness.run()
    assert result['error'] == 'SCREEN_CALIBRATION_CANCELLED'
    assert result['capture']['targets']['fit_top_left']['last_error'] == 'SCREEN_HEAD_MOVED'
    assert result['capture']['completed_targets'] == 1
    assert not harness.installs
    assert [row[0] for row in harness.targets] == [0, 1, 10]


def test_capture_without_any_pose_can_still_validate_actual_gaze_mapping(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def unavailable(row):
        row['rotation'] = None
        return row

    harness.sample_policy = unavailable
    result = harness.run()
    assert result['ready']
    assert len(harness.installs) == 1
    assert harness.installs[0][2] and all(value is None for value in harness.installs[0][2])


def test_runtime_install_rejects_known_unstable_center_pose(camera):
    camera.begin_screen_calibration('test-screen')
    installed = camera.install_screen_calibration(
        fitted_profile(), [raw_gaze()] * 3,
        [rotation(yaw) for yaw in (0, 30, 60)], 'test-screen',
    )
    assert not installed
    assert camera.gaze_reference_progress['error'] == 'SCREEN_HEAD_MOVED'
    assert not camera.gaze_enabled
    assert camera.public_gaze.reference is None
    assert camera.screen_calibration is None


def test_runtime_install_without_pose_keeps_gaze_but_never_invents_head_reference(camera):
    camera.begin_screen_calibration('test-screen')
    assert camera.install_screen_calibration(fitted_profile(), [raw_gaze()] * 3,
                                             [None] * 3, 'test-screen')
    assert camera.gaze_enabled
    assert camera.head_pose.reference is None
    assert camera.head_pose.reference_error == 'HEAD_REFERENCE_INSUFFICIENT'
