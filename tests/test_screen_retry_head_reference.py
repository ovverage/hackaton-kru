"""A late eye-target retry must not rebase or erase the fixed head reference."""

import math

import numpy as np
import pytest

from shared.head_pose import HeadPoseObserver, rotation_distance
from test_screen_capture import CaptureHarness


def rotation(yaw):
    angle = math.radians(yaw)
    return np.array([[math.cos(angle), 0, math.sin(angle)],
                     [0, 1, 0], [-math.sin(angle), 0, math.cos(angle)]])


@pytest.mark.parametrize("new_center", [rotation(11), None], ids=["shifted_pose", "missing_pose"])
def test_late_center_gaze_retry_preserves_original_known_head_reference(monkeypatch, new_center):
    harness = CaptureHarness(monkeypatch)

    def center_wrong_once(row):
        token = harness.current
        row['gaze']['presentation'] = token
        row['gaze']['pitch_degrees'] = -3 + (harness.point[1] - .5) * -20
        if token == 0:
            # Correct median and 3.9-degree spread pass the local four-degree
            # gate. The final normalized spread is .195 > .18 and identifies
            # this particular capture for replacement without blaming a fit.
            row['gaze']['pitch_degrees'] += (-1, 0, 1)[harness.local_sample % 3] * 3.9
            row['rotation'] = rotation(0)
        elif token == 9:
            row['rotation'] = new_center
        else:
            row['rotation'] = rotation(-11)
        return row

    harness.sample_policy = center_wrong_once
    result = harness.run()
    assert result['ready']
    assert [token for token, *_ in harness.targets] == [*range(9), 9]
    profile, center, head_samples, _ = harness.installs[0]
    assert profile.ready and all(row['presentation'] == 9 for row in center)
    assert all(sample is not None for sample in head_samples)
    observer = HeadPoseObserver()
    assert observer.set_reference_rotations(head_samples, required_samples=len(head_samples))
    assert rotation_distance(observer.reference, rotation(0)) == pytest.approx(0)
    assert rotation_distance(observer.reference, rotation(-11)) <= 12
    assert result['capture']['targets']['fit_center']['attempts'] == 2
    assert result['capture']['targets']['fit_center']['last_error'] == 'SCREEN_SAMPLES_UNSTABLE_IN_SCREEN_SPACE'
    assert all(stats['attempts'] == 1 for key, stats in result['capture']['targets'].items()
               if key != 'fit_center')


def test_late_available_pose_does_not_create_reference_after_original_optional_pose(monkeypatch):
    harness = CaptureHarness(monkeypatch)

    def initially_optional_pose(row):
        token = harness.current
        row['gaze']['pitch_degrees'] = -3 + (harness.point[1] - .5) * -20
        if token == 0:
            row['gaze']['pitch_degrees'] += (-1, 0, 1)[harness.local_sample % 3] * 3.9
            row['rotation'] = None
        else:
            row['rotation'] = rotation(11 if token == 9 else -11)
        return row

    harness.sample_policy = initially_optional_pose
    result = harness.run()
    assert result['ready']
    assert [token for token, *_ in harness.targets] == [*range(9), 9]
    assert result['capture']['targets']['fit_center']['last_error'] == 'SCREEN_SAMPLES_UNSTABLE_IN_SCREEN_SPACE'
    _, _, head_samples, _ = harness.installs[0]
    assert head_samples and all(sample is None for sample in head_samples)
    observer = HeadPoseObserver()
    assert not observer.set_reference_rotations([], required_samples=3)
    assert observer.reference is None
