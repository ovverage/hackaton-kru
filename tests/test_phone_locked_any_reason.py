"""Locked evidence may detect phones; ordinary events/recording remain frozen."""
import numpy as np
import pytest

from agent.recording import ClipRecorder
from tests.test_phone_review_window import events, review_sample, setup as setup
from tests.test_recognition_pause import running_agent as running_agent


@pytest.mark.parametrize("reason", ["GAZE_LIMIT_LEFT", "FACE_ABSENCE_TECHNICAL", "TEACHER_LOCK", "CAMERA_UNAVAILABLE"])
def test_phone_can_appear_long_after_any_lock_without_non_phone_events_or_penalties(setup, reason):
    agent, camera, clock, _ = setup
    agent.engine.lock(reason)
    agent.save()
    state = agent.engine.state.public()
    lock_id = state["lock_id"]
    assert agent.phone_review_mode() == (lock_id, float("inf"))
    # A fresh observation after thirty seconds must not inherit the old 2.5s deadline.
    clock[0] += 30
    review_sample(agent, clock[0], lock_id, phone_aiming=False, phone_episode_id="new-phone")
    assert len(events(agent, "PHONE_LOCKED_REVIEW")) == 1
    for _ in range(30):
        clock[0] += .2
        review_sample(agent, clock[0], lock_id, phone_aiming=False, phone_episode_id="new-phone",
                      faces=0, direction="LEFT", gaze_diagnostics={"head_yaw": 80, "head_away": True})
    assert agent.engine.state.public() == state
    assert len(events(agent, "PHONE_LOCKED_REVIEW")) == 1
    assert not events(agent, "GAZE_AWAY_LEFT")
    assert not events(agent, "FACE_ABSENCE_REVIEW")
    assert not events(agent, "HEAD_TURN_REVIEW")
    assert agent.engine.last_t is None
    assert camera.face_features.call_count == 0


def test_frozen_non_phone_clip_does_not_take_later_locked_phone_frames(running_agent, tmp_path):
    agent, clock = running_agent
    recorder = ClipRecorder(tmp_path / "actual-clips")
    agent.recorder = recorder
    black = np.zeros((120, 160, 3), dtype=np.uint8)
    red = black.copy()
    red[:, :, 2] = 255
    try:
        for _ in range(5):
            agent.observe(frame=black, direction="SCREEN", captured_at=clock[0])
            clock[0] += .1
        lock_t = clock[0] - agent.origin
        recorder.mark({"id": "old-gaze", "type": "GAZE_AWAY_LEFT", "at": lock_t, "start": lock_t, "ongoing": True})
        agent.engine.lock("GAZE_LIMIT_LEFT")
        agent.save()
        assert recorder.pending["old-gaze"]["capture_end"] == lock_t
        lock_id = agent.engine.state.lock_id
        for _ in range(10):
            clock[0] += .2
            review_sample(agent, clock[0], lock_id, frame=red, phone_aiming=False, phone_episode_id="locked-phone")
        parts = recorder.completed(float("inf"))
        old = [part for part in parts if part["event_id"] == "old-gaze"]
        assert len(old) == 1 and old[0]["end"] <= lock_t
        assert old[0]["requested_end"] == old[0]["capture_end"] == lock_t
        phone_ids = {event["id"] for event in events(agent, "PHONE_LOCKED_REVIEW")}
        assert len(phone_ids) == 1
        assert any(part["event_id"] in phone_ids and part["end"] > lock_t for part in parts)
        assert all(event.get("type") == "PHONE_LOCKED_REVIEW" for event in agent.journal["events"])
        assert not agent.engine.state.strikes
    finally:
        recorder.close()


def test_phone_disappearance_allows_new_review_without_replaying_held_presence(setup):
    agent, _, clock, _ = setup
    agent.engine.lock("TEACHER_LOCK")
    agent.save()
    state = agent.engine.state.public()
    # A one-frame miss stays the same episode; sustained absence ends it.
    for present in (True, True, False, True, True, *([False] * 7), True, True):
        clock[0] += .2
        review_sample(agent, clock[0], state["lock_id"], phone_aiming=False,
                      phone_confidence=.9 if present else 0,
                      detections=[{"label": "phone", "confidence": .9, "box": [.1, .1, .3, .3]}] if present else [])
    assert len(events(agent, "PHONE_LOCKED_REVIEW")) == 2
    assert agent.engine.state.public() == state


def test_phone_raised_minutes_after_lock_requests_only_recent_episode_context(setup):
    agent, _, clock, _ = setup
    agent.engine.lock("TEACHER_LOCK")
    agent.save()
    clock[0] += 180
    review_sample(agent, clock[0], agent.engine.state.lock_id, phone_episode_id="late-rise")
    aim = events(agent, "PHONE_AIM_REVIEW")
    assert len(aim) == 1
    assert aim[0]["at"] - aim[0]["start"] <= 2.5 + 1e-8
