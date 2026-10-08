"""Target acknowledgement and fresh-frame sampling with a deterministic clock."""

from copy import deepcopy
import threading

import pytest

from agent import screen_capture
from shared.screen_gaze import FIT_TARGETS, VALIDATION_TARGETS


SIGNATURE = {
    "name": "Exam monitor", "serial": "fixture", "geometry": [-1920, 0, 1920, 1080],
    "device_pixel_ratio": 1.25, "logical_dpi": 96.0,
}


class Clock:
    def __init__(self):
        self.at = 100.0

    def monotonic(self):
        return self.at


class CaptureHarness:
    """Simulate frames, not the calibrated profile or fitting calculations."""

    def __init__(self, monkeypatch):
        self.clock = Clock()
        monkeypatch.setattr(screen_capture, "time", self.clock)
        self.session = screen_capture.ScreenCaptureSession()
        self.stop = threading.Event()
        self.signature = deepcopy(SIGNATURE)
        self.targets = []
        self.target_times = []
        self.progress = []
        self.progress_times = []
        self.acknowledgements = []
        self.sample_calls = []
        self.invalidations = []
        self.installs = []
        self.beginnings = []
        self.pending = None
        self.current = None
        self.point = (.5, .5)
        self.frame_id = 0
        self.local_sample = 0
        self.attempt = 0
        self.allow_ack = True
        self.ack_signature = None
        self.sample_policy = lambda row: row
        self.on_read = None
        self.install_allowed = True
        self.screen_calibration_progress = {"error": None}

    def begin_screen_calibration(self, signature):
        self.beginnings.append(deepcopy(signature))
        self.attempt += 1

    def invalidate_screen_calibration(self, error):
        self.invalidations.append(error)

    def target(self, index, phase, point, count, total):
        self.current = index
        self.point = point
        self.local_sample = 0
        self.targets.append((index, phase, point, count, total))
        self.target_times.append(self.clock.at)
        self.pending = (index, self.clock.at + screen_capture.TARGET_SETTLE_SECONDS)

    def read(self):
        self.clock.at += .05
        self.frame_id += 1
        if self.on_read:
            self.on_read()
        if self.pending and self.allow_ack and self.clock.at >= self.pending[1]:
            index, _ = self.pending
            self.session.presented(index, self.ack_signature or self.signature)
            self.acknowledgements.append((index, self.clock.at))
            self.pending = None

    def screen_calibration_sample(self):
        self.local_sample += 1
        self.sample_calls.append((self.current, self.clock.at, self.attempt))
        x, y = self.point
        identity = f"attempt-{self.attempt}-frame-{self.frame_id}"
        return self.sample_policy({
            "at": self.clock.at,
            "frame_id": identity,
            "gaze": {
                "yaw_degrees": 7 + (x - .5) * -32,
                "pitch_degrees": -3 + (y - .5) * -24,
                "error90_degrees": 16,
                "gaze_tracking_status": "tracked",
                "frame_id": identity,
            },
            "rotation": f"rotation-{identity}",
        })

    def install_screen_calibration(self, profile, center, rotations, signature):
        if self.install_allowed:
            self.installs.append((profile, center, rotations, deepcopy(signature)))
        return self.install_allowed

    def run(self):
        def record_progress(*args):
            self.progress.append(args)
            self.progress_times.append((self.current, self.clock.at))

        return self.session.run(
            self, self.signature, self.stop, target=self.target,
            progress=record_progress, read=self.read,
        )


@pytest.fixture
def harness(monkeypatch):
    return CaptureHarness(monkeypatch)


def test_fit_then_separate_validation_installs_actual_profile(harness):
    result = harness.run()
    assert result["ready"] and result["error"] is None
    assert not harness.invalidations and len(harness.installs) == 1
    expected = [("fit", row) for row in FIT_TARGETS] + [("validation", row) for row in VALIDATION_TARGETS]
    assert harness.targets == [
        (index, phase, (row[1], row[2]), 0, 3)
        for index, (phase, row) in enumerate(expected)
    ]
    assert len(harness.acknowledgements) == 9
    for index, at, attempt in harness.sample_calls:
        assert attempt == 1 and at > harness.acknowledgements[index][1]
        assert at >= harness.target_times[index] + screen_capture.TARGET_SETTLE_SECONDS
    profile, center, rotations, signature = harness.installs[0]
    assert profile.ready and signature == SIGNATURE
    assert len(center) >= 3 and len(rotations) == len(center)
    assert all(row["frame_id"].startswith("attempt-1-") for row in center)
    assert set(result["quality"]["sample_counts"]) == {row[0] for _, row in expected}
    assert result["quality"]["validation_max_error"] < 1e-10
    assert not result["quality"]["statistical_accuracy_guarantee"]
    assert 27 <= result["elapsed_seconds"] < 29
    ends = harness.target_times[1:] + [harness.clock.at]
    assert all(3 <= end - start < 3.2 for start, end in zip(harness.target_times, ends))


@pytest.mark.parametrize("kind", ["duplicate", "old", "future", "too_close", "missing"])
def test_duplicate_or_nonfresh_frames_cannot_complete_a_target(harness, kind):
    def invalid(row):
        if kind == "duplicate":
            row["frame_id"] = "same-frame"
        elif kind == "old":
            row["at"] = harness.acknowledgements[-1][1] - .01
        elif kind == "future":
            row["at"] += 1
        elif kind == "too_close":
            row["at"] = harness.acknowledgements[-1][1] + .001
        else:
            return None
        return row

    harness.sample_policy = invalid
    harness.on_read = lambda: harness.stop.set() if harness.current >= 9 else None
    result = harness.run()
    assert not result["ready"] and result["error"] == "SCREEN_CALIBRATION_CANCELLED"
    assert not harness.installs and [row[0] for row in harness.targets] == [0, 9]
    assert result['capture']['retry_count'] == 1
    stats = result['capture']['targets']['fit_center']
    assert stats['attempts'] == 2 and stats['last_error'] == 'SCREEN_TOO_FEW_FRESH_SAMPLES'
    assert all(count <= 1 for count, _, _ in harness.progress)


def test_only_post_ack_valid_distinct_frames_reach_fit(harness):
    def some_stale(row):
        if harness.local_sample < 5:
            row["at"] = harness.acknowledgements[-1][1] - .01
        elif harness.local_sample in (5, 6):
            row["frame_id"] = "reused-across-targets"
        return row

    harness.sample_policy = some_stale
    result = harness.run()
    assert result["ready"]
    assert len(harness.progress) < sum(result["quality"]["sample_counts"].values())
    # IDs are bounded to one exposure; a duplicate within that exposure is
    # rejected, while a later exposure still needs a newer timestamp.
    for stats in result['capture']['targets'].values():
        assert stats['rejections'] == {'stale_frame': 5}
        assert stats['accepted_samples'] == stats['total_frames'] - 5


def test_minimum_frames_never_advance_a_point_before_full_three_seconds(harness):
    harness.sample_policy = lambda row: row if harness.local_sample <= 3 else None
    result = harness.run()
    assert result["ready"]
    assert set(result["quality"]["sample_counts"].values()) == {3}
    assert result["elapsed_seconds"] >= 27
    ends = harness.target_times[1:] + [harness.clock.at]
    assert all(end - start >= screen_capture.TARGET_VISIBLE_SECONDS
               for start, end in zip(harness.target_times, ends))


def test_progress_is_bounded_even_when_every_frame_is_valid(harness):
    result = harness.run()
    assert result["ready"]
    for index in range(9):
        updates = [at for point, at in harness.progress_times if point == index]
        assert 8 <= len(updates) <= 12
        assert all(b - a >= screen_capture.PROGRESS_INTERVAL_SECONDS
                   for a, b in zip(updates, updates[1:]))


def test_sparse_capture_repeats_point_without_extending_three_second_exposure(harness):
    harness.sample_policy = lambda row: row if harness.current >= 9 else None
    result = harness.run()
    assert result["ready"]
    ends = harness.target_times[1:] + [harness.clock.at]
    assert all(3 <= end - start < 3.2 for start, end in zip(harness.target_times, ends))
    assert [row[0] for row in harness.targets] == [value for index in range(9) for value in (index, index + 9)]
    assert result['capture']['retry_count'] == 9
    assert all(row['attempts'] == 2 for row in result['capture']['targets'].values())
    assert len(harness.beginnings) == 1


def test_failed_point_repeats_in_bounded_windows_until_cancelled(harness):
    harness.sample_policy = lambda row: None
    harness.on_read = lambda: harness.stop.set() if harness.current >= 18 else None
    result = harness.run()
    assert result["error"] == "SCREEN_CALIBRATION_CANCELLED"
    assert [row[0] for row in harness.targets] == [0, 9, 18]
    assert all(3 <= end - start < 3.2 for start, end in zip(harness.target_times, harness.target_times[1:]))
    assert result['capture']['retry_count'] == 2
    assert result['capture']['completed_targets'] == 0
    assert not harness.installs


def test_wrong_monitor_signature_fails_before_any_sample(harness):
    harness.ack_signature = dict(SIGNATURE, device_pixel_ratio=1.5)
    result = harness.run()
    assert result["error"] == "SCREEN_CHANGED"
    assert not harness.sample_calls and not harness.installs


def test_absent_target_acknowledgement_repeats_without_sampling_until_cancelled(harness):
    harness.allow_ack = False
    harness.on_read = lambda: harness.stop.set() if harness.current == 9 else None
    result = harness.run()
    assert result["error"] == "SCREEN_CALIBRATION_CANCELLED"
    assert result['capture']['targets']['fit_center']['last_error'] == 'SCREEN_TARGET_NOT_PRESENTED'
    assert not harness.sample_calls and not harness.installs
    assert harness.clock.at < 105


def test_stale_acknowledgement_does_not_authorize_new_attempt(harness):
    harness.session.presented(0, SIGNATURE)
    harness.allow_ack = False
    harness.on_read = lambda: harness.stop.set() if harness.current == 9 else None
    result = harness.run()
    assert result["error"] == "SCREEN_CALIBRATION_CANCELLED"
    assert result['capture']['targets']['fit_center']['last_error'] == 'SCREEN_TARGET_NOT_PRESENTED'
    assert not harness.sample_calls


@pytest.mark.parametrize("when", ["before_ack", "during_capture", "after_last_target"])
def test_cancellation_never_installs_a_profile(harness, when):
    def cancel():
        if (
            when == "before_ack"
            or (when == "during_capture" and harness.local_sample == 2)
            or (when == "after_last_target" and harness.current == 8 and harness.local_sample >= 3)
        ):
            harness.stop.set()

    harness.on_read = cancel
    result = harness.run()
    assert result["error"] == "SCREEN_CALIBRATION_CANCELLED"
    assert not harness.installs


def test_insufficient_frames_then_retry_replaces_only_that_exposure(harness):
    discarded = []

    def sparse_center(row):
        if harness.current == 0:
            if harness.local_sample <= 2:
                discarded.append(row['gaze']['frame_id'])
                return row
            return None
        return row

    harness.sample_policy = sparse_center
    result = harness.run()
    assert result["ready"] and len(harness.beginnings) == 1
    assert len(harness.installs) == 1
    _, center, rotations, _ = harness.installs[0]
    assert [row[0] for row in harness.targets] == [0, 9, *range(1, 9)]
    assert not set(discarded) & {row['frame_id'] for row in center}
    assert len(rotations) == len(center)
    assert result['capture']['targets']['fit_center']['attempts'] == 2
    assert result['capture']['targets']['fit_center']['last_error'] == 'SCREEN_TOO_FEW_FRESH_SAMPLES'
    assert result['capture']['retry_count'] == result['quality']['adaptive_target_retries'] == 1


def test_reference_quality_install_refusal_restarts_collection_automatically(harness):
    harness.install_allowed = False
    harness.screen_calibration_progress["error"] = "SCREEN_HEAD_MOVED"
    harness.on_read = lambda: harness.stop.set() if harness.current == 9 else None
    result = harness.run()
    assert result["error"] == "SCREEN_CALIBRATION_CANCELLED" and not result["ready"]
    assert len(harness.beginnings) == 2
    assert result['capture']['targets']['fit_center']['last_error'] == 'SCREEN_HEAD_MOVED'
    assert not harness.installs


def test_start_and_present_queues_copy_gui_signature(harness):
    signature = deepcopy(SIGNATURE)
    harness.session.start(signature)
    harness.session.presented(2, signature)
    signature["geometry"][0] = 0
    assert harness.session.next_start() == SIGNATURE
    assert harness.session.next_start() is None
    index, queued, _ = harness.session.presentations.get_nowait()
    assert index == 2 and queued == SIGNATURE
