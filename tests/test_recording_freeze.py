import json
from pathlib import Path
from threading import Event
import time

import cv2
import numpy as np
import pytest

from agent.recording import ClipRecorder


def push_frames(recorder, until, *, after=None):
    for step in range(round(until * 10) + 1):
        at = step / 10
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        if after is not None and at > after:
            frame[:, :, 2] = 255
        recorder.push(at, frame)


def assert_no_post_lock_pixels(path):
    capture = cv2.VideoCapture(str(path))
    decoded = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            assert float(frame[:, :, 2].mean()) < 10
            decoded += 1
    finally:
        capture.release()
    assert decoded > 0


def test_lock_caps_non_phone_frames_and_metadata_but_preserves_phone_context(tmp_path):
    recorder = ClipRecorder(tmp_path)
    try:
        push_frames(recorder, 16, after=12)
        recorder.mark({"id": "gaze", "type": "GAZE_AWAY", "at": 10})
        for kind in ("PHONE_DETECTED", "PHONE_AIM_REVIEW", "PHONE_LOCKED_REVIEW"):
            recorder.mark({"id": kind.lower(), "type": kind, "at": 10})
        assert recorder.freeze_at(12) == ["gaze"]
        assert recorder.freeze_at(20) == []  # Another lock cannot extend old evidence.
        parts = recorder.completed(float("inf"))
        gaze = next(part for part in parts if part["event_id"] == "gaze")
        assert gaze["end"] == gaze["requested_end"] == gaze["capture_end"] == 12
        assert gaze["complete"]
        assert_no_post_lock_pixels(gaze["path"])
        phone = [part for part in parts if part["event_id"] != "gaze"]
        assert len(phone) == 3
        assert all(part["requested_end"] == 15 and part["end"] == 15 for part in phone)
        assert all("capture_end" not in part for part in phone)
    finally:
        recorder.close()


def test_frozen_ongoing_clip_is_due_at_lock_and_survives_restart_and_late_update(tmp_path):
    recorder = ClipRecorder(tmp_path)
    try:
        push_frames(recorder, 12)
        # A legacy manifest/event without type must not inherit phone privileges.
        recorder.mark({"id": "legacy", "at": 10, "ongoing": True})
        assert recorder.freeze_at(12) == ["legacy"]
        state = json.loads((tmp_path / "recording.json").read_text())
        assert state["pending"]["legacy"]["capture_end"] == 12
    finally:
        recorder.close()
    recorder = ClipRecorder(tmp_path)
    try:
        recorder.mark({"id": "legacy", "update": True, "end": 100})
        assert recorder.pending["legacy"]["end"] == 12
        deadline = time.monotonic() + 15
        parts = recorder.completed(12)
        assert recorder.encoding or parts  # No five-second post-lock wait.
        while not parts and time.monotonic() < deadline:
            time.sleep(.01)
            parts = recorder.completed(12)
        assert len(parts) == 1
        assert parts[0]["end"] == parts[0]["requested_end"] == 12
        assert parts[0]["complete"]
    finally:
        recorder.close()


def test_freeze_during_encoding_reencodes_before_publishing_any_uncapped_result(tmp_path, monkeypatch):
    recorder = ClipRecorder(tmp_path)
    entered, release = Event(), Event()
    snapshots = []
    original = recorder.encode

    def blocked_encode(incident, selected):
        snapshots.append(dict(incident))
        if len(snapshots) == 1:
            entered.set()
            assert release.wait(10)
        return original(incident, selected)

    monkeypatch.setattr(recorder, "encode", blocked_encode)
    try:
        push_frames(recorder, 16, after=12)
        recorder.mark({"id": "racing", "type": "HEAD_TURN_REVIEW", "at": 10})
        assert recorder.completed(16) == []
        assert entered.wait(5)
        recorder.freeze_at(12)
        release.set()
        parts = recorder.completed(float("inf"))
        assert len(snapshots) == 2
        assert "capture_end" not in snapshots[0]
        assert snapshots[1]["capture_end"] == 12
        assert len(parts) == 1
        assert parts[0]["requested_end"] == parts[0]["end"] == 12
        assert_no_post_lock_pixels(parts[0]["path"])
        assert not recorder.pending and not recorder.encoding
        assert list(tmp_path.glob("*.mp4")) == [Path(parts[0]["path"])]
    finally:
        release.set()
        recorder.close()


def test_multipart_incident_ends_at_cap_even_if_more_raw_frames_exist(tmp_path):
    recorder = ClipRecorder(tmp_path)
    try:
        push_frames(recorder, 62, after=42)
        recorder.mark({"id": "long", "type": "GAZE_AWAY", "start": 5, "at": 10, "ongoing": True})
        recorder.freeze_at(42)
        parts = recorder.completed(float("inf"))
        assert len(parts) == 2
        assert [part["requested_end"] for part in parts] == [30, 42]
        assert all(part["capture_end"] == 42 and part["complete"] for part in parts)
        assert_no_post_lock_pixels(parts[-1]["path"])
    finally:
        recorder.close()


def test_lock_on_first_frame_retains_that_frame_without_post_roll(tmp_path):
    recorder = ClipRecorder(tmp_path)
    try:
        push_frames(recorder, 1, after=0)
        recorder.mark({"id": "immediate", "at": 0})
        recorder.freeze_at(0)
        parts = recorder.completed(float("inf"))
        assert len(parts) == 1
        assert parts[0]["start"] == parts[0]["end"] == parts[0]["requested_end"] == 0
        assert parts[0]["complete"]
        assert_no_post_lock_pixels(parts[0]["path"])
    finally:
        recorder.close()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, "12"])
def test_freeze_rejects_invalid_clock_values_without_mutating_manifest(tmp_path, invalid):
    recorder = ClipRecorder(tmp_path)
    try:
        recorder.mark({"id": "pending", "at": 10})
        before = recorder.manifest.read_bytes()
        with pytest.raises(ValueError, match="INVALID_RECORDING_CAPTURE_END"):
            recorder.freeze_at(invalid)
        assert recorder.manifest.read_bytes() == before
    finally:
        recorder.close()
