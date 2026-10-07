"""Real raising/rules/capture integration with deterministic frames, no camera or hooks."""
import queue
import time
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from agent.behavior import PhoneRaising
from agent.capture import CapturePump
from agent.client import Agent, atomic_json
from agent.vision import Camera


class Recorder:
    def __init__(self):
        self.last_t = None
        self.events = []
        self.count = 0

    def push(self, t, frame):
        assert self.last_t is None or t >= self.last_t
        self.last_t = t
        self.count += 1

    def mark(self, event):
        self.events.append(dict(event))

    def completed(self, _):
        return []


@pytest.fixture
def setup(tmp_path, monkeypatch):
    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "fixture"})
    agent = Agent(tmp_path)
    agent.journal["exam_id"] = "exam"
    agent.engine.start()
    agent.recorder = Recorder()
    agent.capture_pump = Mock()
    clock = [time.monotonic() + 1]
    start = clock[0]
    monkeypatch.setattr("agent.client.time.monotonic", lambda: clock[0])
    camera = Camera.__new__(Camera)
    camera.cv2, camera.centres = cv2, {}
    camera.raising = PhoneRaising()
    camera.gaze_vector = None
    camera.face_features = Mock(return_value=(1, None))
    camera.gaze = Mock()
    camera.gaze.observe.return_value = {"direction": "SCREEN"}
    camera.face_detector = Mock()
    camera.face_detector.detect.return_value = []
    camera.phone = Mock()
    camera.capture = Mock()
    camera.last_frame = start - 1
    camera.frame_digest = None
    camera.frame_changed_at = start
    camera.recognition_was_paused = False
    agent.camera = camera
    agent.capabilities.update(camera=True, recording=True, gaze=True)
    yield agent, camera, clock, start
    agent.http.close()


def capture(agent, camera, clock, start, index, *, stationary=False):
    clock[0] = start + index / 10
    frame = np.full((480, 640, 3), index % 255, dtype=np.uint8)
    y = 400 if stationary or index < 5 else max(190, 400 - (index - 5) * 30)
    camera.phone.detect.return_value = [{"confidence": .9, "box": [200, y - 80, 280, y + 80]}]
    camera.capture.read.return_value = True, frame
    agent.update_recognition_mode()
    mode = agent.phone_review_mode()
    frame, observation = camera.read(analyze=not agent.recognition_paused, phone_review=mode is not None)
    if observation is not None:
        if observation.get("phone_review_only"):
            observation["phone_review_lock_id"] = mode[0]
        agent.observe(frame=frame, **observation)
    else:
        agent.record_frame(frame, captured_at=clock[0])


def events(agent, kind):
    return [event for event in agent.journal["events"] if event.get("type") == kind]


def arm(setup):
    agent, camera, clock, start = setup
    capture(agent, camera, clock, start, 0)
    capture(agent, camera, clock, start, 1)
    assert agent.engine.state.reason == "PHONE_DETECTED"
    assert agent.recognition_paused
    return dict(agent._phone_review)


def command(agent, kind):
    return {"id": kind, "type": kind, "exam_id": "exam", "expires_at": time.time() + 60,
            "expected_version": agent.engine.state.version, "lock_id": agent.engine.state.lock_id,
            "require_camera": True}


def review_sample(agent, at, lock_id, **changes):
    sample = {"phone_review_only": True, "phone_review_lock_id": lock_id, "captured_at": at,
              "phone_confidence": .9, "phone_aiming": True,
              "detections": [{"label": "phone", "confidence": .9, "box": [.2, .2, .4, .7]}]}
    sample.update(changes)
    agent.observe(**sample)


def test_real_raising_sequence_survives_phone_lock_without_new_penalties(setup):
    agent, camera, clock, start = setup
    review = arm(setup)
    state = agent.engine.state.public()
    face_calls = camera.face_features.call_count
    gaze_calls = camera.gaze.observe.call_count
    for index in range(2, 50):
        capture(agent, camera, clock, start, index)
        assert agent.engine.state.public() == state
    phone = events(agent, "PHONE_DETECTED")
    aim = events(agent, "PHONE_AIM_REVIEW")
    assert len(phone) == len(aim) == 1
    assert phone[0]["at"] == pytest.approx(start + .1 - agent.origin)
    assert 0 < aim[0]["at"] - phone[0]["at"] <= 2.5
    assert aim[0]["category"] == "REVIEW"
    assert aim[0]["evidence_lock_id"] == state["lock_id"]
    assert aim[0]["related_event_id"] == phone[0]["id"]
    assert "факт фотографии не установлен" in aim[0]["detail"]
    assert camera.face_features.call_count == face_calls == 2
    assert camera.gaze.observe.call_count == gaze_calls == 2
    assert camera.face_detector.detect.call_count == 2
    assert agent.recorder.count == 50
    assert any(event.get("type") == "PHONE_AIM_REVIEW" for event in agent.recorder.events)
    assert agent._phone_review is None
    assert agent.engine.state.strikes == [] and agent.engine.state.locks == 1
    assert agent.engine.last_t is None  # No locked evidence sample re-enters the rules.
    assert agent.capture_pump.set_recognition.call_args.kwargs["phone_review"] is None
    assert review["until"] == pytest.approx(start + .1 + 2.5)


def test_stationary_phone_has_finite_window_and_no_aim_review(setup):
    agent, camera, clock, start = setup
    for index in range(50):
        capture(agent, camera, clock, start, index, stationary=True)
    assert len(events(agent, "PHONE_DETECTED")) == 1
    assert not events(agent, "PHONE_AIM_REVIEW")
    assert agent._phone_review is None
    assert camera.phone.detect.call_count <= 28
    assert agent.recorder.count == 50


@pytest.mark.parametrize("legacy_without_id", [False, True])
def test_queue_drops_early_phone_frames_but_consumed_prelock_aim_is_not_replayed(setup, legacy_without_id):
    agent, camera, clock, start = setup
    delivery_started = False
    first_signal_at = None
    for index in range(45):
        clock[0] = start + index / 10
        y = 400 if index < 5 else max(190, 400 - (index - 5) * 30)
        camera.phone.detect.return_value = [{"confidence": .9, "box": [200, y - 80, 280, y + 80]}]
        camera.capture.read.return_value = True, np.full((480, 640, 3), index, dtype=np.uint8)
        agent.update_recognition_mode()
        mode = agent.phone_review_mode()
        frame, observation = camera.read(analyze=not agent.recognition_paused, phone_review=mode is not None)
        if observation is None:
            agent.record_frame(frame, captured_at=clock[0])
            continue
        if legacy_without_id:
            observation.pop("phone_episode_id", None)
        if observation.get("phone_review_only"):
            observation["phone_review_lock_id"] = mode[0]
        # A slow newest-only consumer never receives the early detections. Its
        # first positive observation happens to contain the one-shot rise.
        if not delivery_started:
            if not observation["phone_aiming"]:
                continue
            delivery_started = True
            first_signal_at = clock[0]
        agent.observe(frame=frame, **observation)
    assert first_signal_at is not None
    phone, aim = events(agent, "PHONE_DETECTED"), events(agent, "PHONE_AIM_REVIEW")
    assert len(phone) == 1 and len(aim) == 1
    assert aim[0]["at"] == pytest.approx(first_signal_at - agent.origin)
    assert phone[0]["at"] - aim[0]["at"] == pytest.approx(.1)
    assert aim[0]["category"] == "REVIEW"
    assert agent.engine.state.reason == "PHONE_DETECTED"
    assert agent.engine.state.locks == 1 and not agent.engine.state.strikes
    assert agent._phone_review is None
    # No evidence-only window needs to be armed for this already-recorded rise.
    assert all(call.kwargs.get("phone_review") is None for call in agent.capture_pump.set_recognition.call_args_list)


def test_new_phone_tracker_episode_is_not_suppressed_after_teacher_unlock(setup):
    agent, camera, clock, start = setup
    for index in range(25):
        capture(agent, camera, clock, start, index)
    first = events(agent, "PHONE_AIM_REVIEW")[0]
    old_id = first["phone_episode_id"]
    agent.apply(command(agent, "UNLOCK"))
    assert agent.journal["acks"][-1]["ok"]
    next_start = clock[0] + .1
    for index in range(25):
        capture(agent, camera, clock, next_start, index)
    aim = events(agent, "PHONE_AIM_REVIEW")
    assert len(aim) == 2
    assert aim[1]["phone_episode_id"] != old_id
    assert len(events(agent, "PHONE_DETECTED")) == 2
    assert agent.engine.state.locks == 2
    assert not agent.engine.state.strikes


def test_phone_disappearance_resets_episode_identity_once():
    raising = PhoneRaising()
    initial = raising.episode_id
    raising.update(0., [{"confidence": .9, "box": [200, 200, 280, 400]}], 640, 480)
    raising.update(1.1, [], 640, 480)
    after_absence = raising.episode_id
    assert after_absence != initial
    raising.update(1.2, [], 640, 480)
    assert raising.episode_id == after_absence


@pytest.mark.parametrize("change", [
    {"phone_confidence": .799}, {"phone_aiming": False},
    {"detections": []},
    {"detections": [{"label": "phone", "confidence": .799, "box": [.1, .1, .2, .2]}]},
    {"phone_review_lock_id": "another-lock"},
])
def test_evidence_is_filtered_and_never_changes_lock_state(setup, change):
    agent, _, clock, _ = setup
    review = arm(setup)
    state = agent.engine.state.public()
    clock[0] += .2
    review_sample(agent, clock[0], review["lock_id"], **change)
    assert not events(agent, "PHONE_AIM_REVIEW")
    assert agent.engine.state.public() == state


@pytest.mark.parametrize("offset", [-.1, 0., 2.6, 10., float("nan")])
def test_stale_duplicate_late_or_invalid_capture_is_rejected(setup, offset):
    agent, _, clock, _ = setup
    review = arm(setup)
    clock[0] += .2
    review_sample(agent, review["started"] + offset, review["lock_id"])
    assert not events(agent, "PHONE_AIM_REVIEW")


@pytest.mark.parametrize("kind", ["UNLOCK", "END_AND_RELEASE"])
def test_teacher_transition_cancels_window_and_old_result(setup, kind):
    agent, _, clock, _ = setup
    review = arm(setup)
    agent.apply(command(agent, kind))
    assert agent.journal["acks"][-1]["ok"]
    assert agent._phone_review is None
    clock[0] += .2
    review_sample(agent, clock[0], review["lock_id"])
    assert not events(agent, "PHONE_AIM_REVIEW")
    assert agent.engine.state.access == "OPEN"


def test_new_critical_lock_cancels_old_phone_window(setup):
    agent, _, clock, _ = setup
    review = arm(setup)
    agent.engine.lock("CAMERA_UNAVAILABLE")
    agent.save()
    state = agent.engine.state.public()
    clock[0] += .2
    review_sample(agent, clock[0], review["lock_id"])
    assert agent._phone_review is None
    assert agent.engine.state.public() == state
    assert not events(agent, "PHONE_AIM_REVIEW")


def test_consumed_signal_cannot_create_a_second_review(setup):
    agent, _, clock, _ = setup
    review = arm(setup)
    clock[0] += .2
    review_sample(agent, clock[0], review["lock_id"])
    clock[0] += .2
    review_sample(agent, clock[0], review["lock_id"])
    assert len(events(agent, "PHONE_AIM_REVIEW")) == 1


def test_phone_only_capture_keeps_signal_until_consumed_and_never_runs_other_models(setup):
    _, camera, clock, start = setup
    camera.analyze = Mock(side_effect=AssertionError("Full inference must remain paused"))
    signals = []
    for index in range(25):
        clock[0] = start + index / 10
        y = 400 if index < 5 else max(190, 400 - (index - 5) * 30)
        camera.phone.detect.return_value = [{"confidence": .9, "box": [200, y - 80, 280, y + 80]}]
        camera.capture.read.return_value = True, np.full((480, 640, 3), index, dtype=np.uint8)
        _, observation = camera.read(analyze=False, phone_review=True)
        signals.append(observation["phone_aiming"])
        assert observation["phone_review_only"] is True
        assert "faces" not in observation and "direction" not in observation
    assert sum(signals) > 1 and signals[-1]
    camera.analyze.assert_not_called()
    camera.face_features.assert_not_called()
    camera.face_detector.detect.assert_not_called()
    camera.gaze.observe.assert_not_called()


class ControlledCamera:
    def __init__(self):
        self.calls = queue.Queue()
        self.frames = queue.Queue()

    def read(self, *, analyze=True, phone_review=False, phone_review_until=None):
        self.calls.put((analyze, phone_review))
        return self.frames.get(timeout=5)


def test_capture_generation_rejects_old_review_across_unlock_and_new_lock():
    camera = ControlledCamera()
    deadline = time.monotonic() + 2.5
    pump = CapturePump(camera, recognize=False, phone_review=("old-lock", deadline))
    try:
        assert camera.calls.get(timeout=2) == (False, True)
        pump.set_recognition(True)
        pump.set_recognition(False, phone_review=("new-lock", deadline))
        camera.frames.put(("old-frame", {"phone_review_only": True, "phone_aiming": True}))
        assert camera.calls.get(timeout=2) == (False, True)
        assert pump.poll() == ("old-frame", None)
        camera.frames.put(("new-frame", {"phone_review_only": True, "phone_aiming": True}))
        assert camera.calls.get(timeout=2) == (False, True)
        frame, observation = pump.poll()
        assert frame == "new-frame" and observation["phone_review_lock_id"] == "new-lock"
        pump.set_recognition(False)
        camera.frames.put(("after-stop", {"phone_review_only": True, "phone_aiming": True}))
        assert camera.calls.get(timeout=2) == (False, False)
        assert pump.poll() == ("after-stop", None)
    finally:
        pump.stop.set()
        camera.frames.put((None, None))
        pump.close()


def test_expired_capture_window_does_not_call_phone_detector():
    camera = ControlledCamera()
    pump = CapturePump(camera, recognize=False, phone_review=("expired", time.monotonic() - .1))
    try:
        assert camera.calls.get(timeout=2) == (False, False)
        camera.frames.put(("raw", None))
        assert camera.calls.get(timeout=2) == (False, False)
        assert pump.poll() == ("raw", None)
    finally:
        pump.stop.set()
        camera.frames.put((None, None))
        pump.close()


def test_camera_delay_cannot_start_phone_inference_after_deadline(setup):
    _, camera, clock, _ = setup
    deadline = clock[0] + .05

    def delayed_capture():
        clock[0] = deadline + .01
        return True, np.zeros((12, 16, 3), dtype=np.uint8)

    camera.capture.read.side_effect = delayed_capture
    frame, observation = camera.read(analyze=False, phone_review=True, phone_review_until=deadline)
    assert frame is not None and observation is None
    camera.phone.detect.assert_not_called()
