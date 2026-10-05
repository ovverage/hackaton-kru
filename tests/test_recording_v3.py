import numpy as np
import cv2
import time
import pytest
from pathlib import Path
from agent.recording import ClipRecorder


@pytest.mark.parametrize("force", [False, True])
def test_burst_larger_than_encoder_queue_drains_without_losing_evidence(tmp_path, force):
    recorder = ClipRecorder(tmp_path)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    recorder.push(0, frame)
    recorder.push(.1, frame)
    for i in range(10):
        recorder.mark({"id": f"burst-{i}", "at": .1})
    try:
        deadline = time.monotonic() + 30
        results = recorder.completed(float("inf") if force else 6)
        if force:
            assert len(results) == 10
        while len(results) < 10 and time.monotonic() < deadline:
            time.sleep(.02)
            results = recorder.completed(6)
        assert {item['event_id'] for item in results} == {f'burst-{i}' for i in range(10)}
        assert not recorder.pending and not recorder.encoding
        recorder.reset_session('next-exam')
        # Reset may discard old raw segments only after all incidents are encoded.
        assert len(recorder.ready) == 10
        assert all(Path(item['path']).is_file() for item in results)
    finally:
        recorder.close()


def test_long_incident_survives_restart_without_ram_frame_queue(tmp_path):
    recorder = ClipRecorder(tmp_path)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    for i in range(81):
        recorder.push(i / 5, frame)
        if i == 50:
            recorder.mark({"id": "long", "at": 10, "start": 5, "ongoing": True})
    assert not recorder.completed(16)
    assert not hasattr(recorder, "frames")
    recorder.close()
    recorder = ClipRecorder(tmp_path)
    for i in range(81, 501):
        recorder.push(i / 5, frame)
    # The incident lasts exactly 90 seconds (5..95), across an agent restart.
    recorder.mark({"id": "long", "update": True, "end": 95})
    results = recorder.completed(float("inf"))
    assert len(results) == 4 and results[0]["start"] == 0 and results[-1]["end"] == 100
    assert all(result["complete"] and result["gaps"] == [] for result in results)
    capture = cv2.VideoCapture(results[-1]["path"])
    capture.set(cv2.CAP_PROP_POS_MSEC, 1000)
    ok, decoded = capture.read()
    capture.release()
    assert ok and decoded.shape == frame.shape
    assert len(recorder.completed(float("inf"))) == 4
    recorder.acknowledge("long", results[0]["path"])
    assert len(recorder.completed(float("inf"))) == 3
    recorder.acknowledge("long")
    assert not recorder.completed(float("inf"))
    recorder.close()


def test_recording_reports_missing_interval_and_torn_tail(tmp_path):
    recorder = ClipRecorder(tmp_path)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    for at in [0, .1, .2, 2, 2.1, 2.2]:
        recorder.push(at, frame)
    recorder.mark({"id": "gap", "at": 2})
    recorder.close()
    segment = next((tmp_path / "segments").glob("*.seg"))
    with segment.open("ab") as stream:
        stream.write(b'torn-record')
    recovered = ClipRecorder(tmp_path)
    result = recovered.completed(float("inf"))[0]
    assert result["gaps"] == [[.2, 2]] and not result["complete"]
    assert result["end"] == 2.2
    recovered.close()


def test_completed_clip_expiry_removes_only_expired_media_and_records_reason(tmp_path):
    recorder = ClipRecorder(tmp_path)
    old, fresh = tmp_path / 'old.mp4', tmp_path / 'fresh.mp4'
    old.write_bytes(b'old fixture')
    fresh.write_bytes(b'fresh fixture')
    recorder.ready = {'old': [{'event_id': 'old', 'path': str(old), 'created_at': 1}],
                      'fresh': [{'event_id': 'fresh', 'path': str(fresh), 'created_at': 700000}]}
    recorder.save()
    expired = recorder.expire_completed(now=700001)
    assert [x['event_id'] for x in expired] == ['old']
    assert not old.exists() and fresh.exists()
    assert 'old' not in recorder.ready
    recorder.close()
