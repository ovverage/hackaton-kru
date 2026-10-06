"""No camera/hook access: verify inference gating and durable evidence semantics."""
import queue
import time
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from agent.capture import CapturePump
from agent.client import Agent, atomic_json
from agent.vision import Camera
from shared.rules import RuleEngine


class ControlledCamera:
    def __init__(self):
        self.calls = queue.Queue()
        self.frames = queue.Queue()

    def read(self, *, analyze=True):
        self.calls.put(analyze)
        frame, observation = self.frames.get(timeout=5)
        return frame, observation if analyze else None


def test_capture_pump_discards_in_flight_result_across_pause_and_resume():
    camera = ControlledCamera()
    pump = CapturePump(camera)
    try:
        assert camera.calls.get(timeout=2) is True
        pump.set_recognition(False)
        pump.set_recognition(True)
        camera.frames.put(("old-frame", {"phone_confidence": .99}))
        assert camera.calls.get(timeout=2) is True
        assert pump.poll() == ("old-frame", None)
        camera.frames.put(("fresh-frame", {"phone_confidence": .81}))
        assert camera.calls.get(timeout=2) is True
        assert pump.poll() == ("fresh-frame", {"phone_confidence": .81})
    finally:
        pump.stop.set()
        camera.frames.put((None, None))
        pump.close()


def test_paused_capture_continues_without_requesting_inference():
    camera = ControlledCamera()
    pump = CapturePump(camera, recognize=False)
    try:
        assert camera.calls.get(timeout=2) is False
        camera.frames.put(("raw-one", {"faces": 0}))
        assert camera.calls.get(timeout=2) is False
        assert pump.poll() == ("raw-one", None)
        camera.frames.put(("raw-two", {"phone_confidence": .99}))
        assert camera.calls.get(timeout=2) is False
        assert pump.poll() == ("raw-two", None)
    finally:
        pump.stop.set()
        camera.frames.put((None, None))
        pump.close()


def test_camera_raw_capture_does_not_call_any_model():
    camera = Camera.__new__(Camera)
    frame = np.zeros((12, 16, 3), dtype=np.uint8)
    camera.capture = SimpleNamespace(read=lambda: (True, frame))
    camera.last_frame = time.monotonic() - 1
    camera.frame_digest = None
    camera.frame_changed_at = time.monotonic()
    camera.analyze = Mock(side_effect=AssertionError("Recognition must remain paused"))
    received, observation = camera.read(analyze=False)
    assert received is frame and observation is None
    camera.analyze.assert_not_called()


class Recorder:
    def __init__(self):
        self.last_t = None
        self.frames = []
        self.events = []
        self.collected = []

    def push(self, t, frame):
        assert self.last_t is None or t >= self.last_t
        self.last_t = t
        self.frames.append((t, frame))

    def mark(self, event):
        self.events.append(dict(event))

    def completed(self, t):
        self.collected.append(t)
        return []


@pytest.fixture
def running_agent(tmp_path, monkeypatch):
    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "fixture"})
    agent = Agent(tmp_path)
    agent.journal["exam_id"] = "exam"
    agent.engine.start()
    agent.camera = SimpleNamespace()
    agent.recorder = Recorder()
    agent.capabilities.update(camera=True, gaze=True)
    clock = [time.monotonic() + .1]
    monkeypatch.setattr("agent.client.time.monotonic", lambda: clock[0])
    yield agent, clock
    agent.http.close()


def command(agent, kind, ident):
    state = agent.engine.state
    return {"id": ident, "type": kind, "exam_id": "exam", "expires_at": 100,
            "expected_version": state.version, "lock_id": state.lock_id, "require_camera": True}


def test_phone_lock_stops_events_preserves_video_and_unlock_requires_new_frames(running_agent):
    agent, clock = running_agent
    frame = np.zeros((12, 16, 3), dtype=np.uint8)
    agent.observe(phone_confidence=.9, frame=frame, captured_at=clock[0])
    clock[0] += .2
    agent.observe(phone_confidence=.9, frame=frame, captured_at=clock[0])
    assert agent.engine.state.access == "LOCKED"
    assert agent.snapshot()["recognition_paused"]
    incident = next(e for e in agent.recorder.events if e.get("type") == "PHONE_DETECTED")
    updates = [e for e in agent.recorder.events if e.get("update")]
    assert updates == [{"id": incident["id"], "update": True, "end": clock[0] - agent.origin}]
    assert not agent.engine.phone_present
    locked_events = len(agent.journal["events"])
    clock[0] += 5
    old_sample = clock[0]
    agent.observe(phone_confidence=.99, faces=0, frame=frame, captured_at=old_sample)
    assert len(agent.journal["events"]) == locked_events
    assert len(agent.recorder.frames) == 3
    assert agent.recorder.collected[-1] == clock[0] - agent.origin
    clock[0] += .1
    agent.apply(command(agent, "UNLOCK", "unlock"), now=1)
    assert agent.journal["acks"][-1]["ok"]
    assert agent.engine.state.access == "OPEN"
    assert not agent.snapshot()["recognition_paused"]
    agent.observe(phone_confidence=.99, frame=frame, captured_at=old_sample)
    assert agent.engine.state.access == "OPEN" and not agent.engine.phone_samples
    clock[0] += .2
    agent.observe(phone_confidence=.9, frame=frame, captured_at=clock[0])
    assert agent.engine.state.access == "OPEN"
    clock[0] += .2
    agent.observe(phone_confidence=.9, frame=frame, captured_at=clock[0])
    assert agent.engine.state.access == "LOCKED"


def test_absence_pause_does_not_deadlock_unlock_but_camera_fault_still_blocks(running_agent):
    agent, clock = running_agent
    for _ in range(52):
        clock[0] += .2
        agent.observe(faces=0, captured_at=clock[0])
    assert agent.engine.state.reason == "FACE_ABSENCE_TECHNICAL"
    assert agent.engine.absent_start is None
    agent.camera_fault = True
    agent.apply(command(agent, "UNLOCK", "camera-fault"), now=1)
    assert agent.journal["acks"][-1]["error"] == "CAMERA_UNAVAILABLE"
    assert agent.engine.state.access == "LOCKED"
    agent.camera_fault = False
    agent.apply(command(agent, "UNLOCK", "camera-recovered"), now=1)
    assert agent.engine.state.access == "OPEN"


def test_completed_tail_records_without_observations_or_new_events(running_agent):
    agent, clock = running_agent
    agent.apply(command(agent, "END_AND_RELEASE", "end"), now=1)
    assert agent.recognition_paused and agent.record_until == clock[0] + 5
    before = len(agent.journal["events"])
    clock[0] += .2
    agent.observe(phone_confidence=.99, faces=0, frame=np.zeros((12, 16, 3), dtype=np.uint8), captured_at=clock[0])
    assert len(agent.journal["events"]) == before
    assert len(agent.recorder.frames) == 1
    assert agent.last_observation is None


def test_capture_timestamps_drive_rules_and_diagnostics_reject_stale_samples(running_agent):
    agent, clock = running_agent
    clock[0] += 1
    at = clock[0] - .5
    agent.observe(direction="LEFT", captured_at=at,
                  gaze_diagnostics={"source": "head_pose", "head_yaw": 40, "head_pitch": 5,
                                    "reference_ready": True, "offscreen_probability": .92})
    assert agent.engine.last_t == at - agent.origin
    clock[0] += .2
    agent.observe(direction="LEFT", captured_at=at + .2, gaze_diagnostics={"source": "head_pose"})
    diagnostic = agent.snapshot()["gaze_diagnostics"]
    assert diagnostic["direction"] == "LEFT" and diagnostic["source"] == "head_pose"
    assert diagnostic["interval_ms"] == pytest.approx(200)
    last = agent.last_observation_at
    for rejected in (last, last - .1, clock[0] - 3, clock[0] + 3):
        agent.observe(phone_confidence=.99, captured_at=rejected)
    assert agent.last_observation_at == last
    assert not any(present for _, present in agent.engine.phone_samples)


def test_event_evidence_excludes_phone_boxes_below_eighty_percent(running_agent):
    agent, clock = running_agent
    boxes = [{"label": "phone", "confidence": confidence, "box": [0, 0, .1, .1]}
             for confidence in (.79, .80, .95)]
    agent.observe(phone_confidence=.95, captured_at=clock[0], detections=boxes)
    clock[0] += .2
    agent.observe(phone_confidence=.95, captured_at=clock[0], detections=boxes)
    incident = next(e for e in agent.journal["events"] if e.get("type") == "PHONE_DETECTED")
    assert [box["confidence"] for box in incident["detections"]] == [.80, .95]


@pytest.mark.parametrize("confidence,allowed", [(.7999, True), (.80, False)])
def test_start_phone_gate_uses_same_eighty_percent_boundary(running_agent, monkeypatch, confidence, allowed):
    agent, clock = running_agent
    agent.engine = RuleEngine()
    agent.last_observation = {"faces": 1, "phone_confidence": confidence}
    agent.last_observation_at = clock[0]
    monkeypatch.setattr(agent, "launch_environment", lambda: None)
    agent.apply(command(agent, "START", "start"), now=1)
    assert agent.journal["acks"][-1]["ok"] is allowed
    if not allowed:
        assert agent.journal["acks"][-1]["error"] == "REMOVE_PHONE_BEFORE_START"
