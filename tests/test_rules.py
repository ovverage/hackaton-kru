import pytest
from shared.rules import RuleEngine


class Feed:
    def __init__(self):
        self.e = RuleEngine()
        self.e.start()
        self.t = 0

    def run(self, seconds, direction="SCREEN", phone=0, faces=1):
        result = []
        for _ in range(round(seconds * 10) + 1):
            self.t = round(self.t + 0.1, 3)
            result.extend(self.e.observe(self.t, direction, phone, faces))
        return [e for e in result if not e.get("update")]

    def episode(self, direction):
        events = self.run(5, direction)
        self.run(1.1)
        return events


def test_five_second_boundary_and_one_strike_per_departure():
    f = Feed()
    assert not f.run(4.9, "DOWN")
    assert len(f.run(0.1, "DOWN")) == 1
    assert not f.run(20, "DOWN")
    assert f.e.state.counts()["DOWN"] == 1


def test_three_independent_counters_lock_on_third_same_direction():
    f = Feed()
    for d in ("DOWN", "LEFT", "RIGHT", "DOWN", "LEFT", "RIGHT"):
        f.episode(d)
    assert f.e.state.counts() == dict(DOWN=2, LEFT=2, RIGHT=2)
    assert f.e.state.access == "OPEN"
    f.episode("LEFT")
    assert f.e.state.access == "LOCKED" and f.e.state.reason == "GAZE_LEFT"


def test_direction_changes_do_not_accumulate_five_seconds():
    f = Feed()
    f.run(3, "DOWN")
    f.run(3, "LEFT")
    assert sum(f.e.state.counts().values()) == 0


def test_phone_single_frame_does_not_lock_but_stable_detection_does():
    f = Feed()
    f.e.observe(0.1, phone_confidence=0.9)
    assert f.e.state.access == "OPEN"
    events = f.e.observe(0.2, phone_confidence=0.9)
    assert events[0]["type"] == "PHONE_DETECTED" and f.e.state.access == "LOCKED"
    with pytest.raises(ValueError, match="PHONE_STILL_PRESENT"):
        f.e.unlock(f.e.state.lock_id, f.e.state.version)
    f.t = 0.2
    f.run(1.5)
    assert f.e.state.access == "LOCKED"


def test_review_never_automatically_unlocks_and_epoch_resets_counts():
    f = Feed()
    events = [f.episode("DOWN")[0] for _ in range(3)]
    f.e.review(events[0]["id"], "REJECTED")
    assert f.e.state.counts()["DOWN"] == 2 and f.e.state.access == "LOCKED"
    old = f.e.state.lock_id
    version = f.e.state.version
    f.e.unlock(old, version)
    assert sum(f.e.state.counts().values()) == 0 and len(f.e.state.strikes) == 3
    with pytest.raises(ValueError):
        f.e.unlock(old, version)


def test_new_critical_cause_invalidates_queued_unlock():
    f = Feed()
    f.e.lock("TEACHER_LOCK")
    old = f.e.state.lock_id
    v = f.e.state.version
    f.e.lock("PHONE_DETECTED")
    with pytest.raises(ValueError):
        f.e.unlock(old, v)


def test_second_face_is_review_only():
    f = Feed()
    events = f.run(2, faces=2)
    assert len(events) == 1 and events[0]["type"] == "SECOND_FACE_REVIEW"
    assert f.e.state.access == "OPEN"


def test_frequent_short_departures_review_without_strikes():
    f = Feed()
    events = []
    for d in ("DOWN", "LEFT", "RIGHT"):
        events += f.run(2.2, d)
        events += f.run(1.1)
    assert [e["type"] for e in events] == ["FREQUENT_GAZE_REVIEW"]
    assert sum(f.e.state.counts().values()) == 0


def test_unknown_and_capture_gap_never_count_as_gaze_time():
    f = Feed()
    f.run(4, "DOWN")
    f.run(1, "UNKNOWN")
    f.run(1, "DOWN")
    assert sum(f.e.state.counts().values()) == 0
    f.e.observe(f.t + 50, "DOWN")
    assert sum(f.e.state.counts().values()) == 0


def test_completed_exam_cannot_restart_or_lock():
    f = Feed()
    f.e.end()
    assert f.e.observe(100, "DOWN", 1, 2) == []
    with pytest.raises(ValueError):
        f.e.start()
    with pytest.raises(ValueError):
        f.e.lock("TEACHER_LOCK")


def test_brief_return_to_screen_interrupts_five_second_timer():
    f = Feed()
    f.run(4, "DOWN")
    f.run(0.2, "SCREEN")
    f.run(1, "DOWN")
    assert f.e.state.counts()["DOWN"] == 0


def test_face_duration_does_not_bridge_missing_frames():
    f = Feed()
    f.e.observe(0, faces=2)
    assert not f.e.observe(10, faces=2)
    f.e.observe(11, faces=0)
    assert not f.e.observe(30, faces=0)


def test_event_start_matches_qualifying_direction_not_prior_departure():
    f = Feed()
    f.run(3, "LEFT")
    expected_start = round(f.t + 0.1, 3)
    event = f.run(5, "DOWN")[0]
    assert event["start"] == expected_start
    assert event["at"] - event["start"] == pytest.approx(5)


def test_absence_three_second_review_ten_second_technical_block():
    f = Feed()
    assert not f.run(2.9, faces=0)
    events = f.run(.1, faces=0)
    assert events[0]["type"] == "FACE_ABSENCE_REVIEW"
    assert f.e.state.access == "OPEN"
    events = f.run(7, faces=0)
    assert any(x["type"] == "FACE_ABSENCE_TECHNICAL" for x in events)
    assert f.e.state.reason == "FACE_ABSENCE_TECHNICAL"
    assert not f.run(20, faces=0)


def test_blink_does_not_erase_observed_gaze_but_unknown_time_is_not_counted():
    f = Feed()
    f.run(4.9, "DOWN")
    before = f.e.seconds
    f.t += .1
    assert not f.e.observe(f.t, "UNKNOWN")
    f.t += .1
    assert not f.e.observe(f.t, "DOWN")
    assert f.e.seconds == pytest.approx(before)
    assert any(e['type'] == 'GAZE_DOWN' for e in f.run(.2, 'DOWN'))


def test_slow_regular_camera_counts_observed_time_and_long_gap_resets():
    engine = RuleEngine()
    engine.start()
    events = []
    for i in range(10):
        events += engine.observe(i*.6, 'LEFT')
    assert len([e for e in events if e.get('type') == 'GAZE_LEFT']) == 1
    engine = RuleEngine()
    engine.start()
    for i in range(8):
        engine.observe(i*.6, 'RIGHT')
    assert not engine.observe(6, 'RIGHT')
    assert engine.seconds == 0


def test_brief_missing_face_never_bridges_gaze_episode():
    f = Feed()
    f.run(4.9, 'LEFT')
    f.t += .1
    f.e.observe(f.t, 'UNKNOWN', faces=0)
    assert f.e.seconds == 0
    assert not f.run(.5, 'LEFT')


def test_phone_interval_keeps_recording_until_phone_disappears():
    f = Feed()
    events = f.run(10, phone=.9)
    assert len(events) == 1 and events[0]["ongoing"]
    updates = []
    for i in range(15):
        f.t += .1
        updates.extend(f.e.observe(f.t, phone_confidence=0))
    assert any(x.get("update") and x["id"] == events[0]["id"] for x in updates)
