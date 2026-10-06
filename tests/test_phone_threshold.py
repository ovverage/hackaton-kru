from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from agent.behavior import PhoneRaising
from agent.detector import PhoneDetector
from agent.evidence import annotate
from agent.vision import Camera
from shared.rules import RuleEngine


@pytest.mark.parametrize("confidence, expected", [(0.799, False), (0.80, True)])
def test_phone_inference_and_evidence_have_an_inclusive_eighty_percent_boundary(confidence, expected):
    # Run real letterboxing, score filtering and OpenCV NMS with a controlled
    # model output. In particular, NMS must not remove the exact .80 boundary.
    session = Mock()
    session.get_inputs.return_value = [SimpleNamespace(name="images", shape=[1, 3, 640, 640])]
    session.get_modelmeta.return_value = SimpleNamespace(custom_metadata_map={"names": "{0: 'cell phone'}"})
    session.run.return_value = [np.array([[[320, 320, 160, 240, confidence]]], dtype=np.float32).transpose(0, 2, 1)]
    with patch("agent.detector.ort.InferenceSession", return_value=session):
        detector = PhoneDetector("fixture.onnx")
    frame = np.zeros((640, 640, 3), dtype=np.uint8)
    detections = detector.detect(frame)
    assert bool(detections) is expected
    if expected:
        assert detections[0]["confidence"] == pytest.approx(0.80)
    # A direct evidence caller is also protected against low-confidence boxes.
    shown = annotate(frame, [{"confidence": confidence, "box": [.25, .25, .5, .75]}])
    assert bool(shown.any()) is expected
    assert not frame.any()


@pytest.mark.parametrize("confidence, expected", [(0.799, False), (0.80, True)])
def test_camera_does_not_publish_low_confidence_phone_boxes_or_scores(confidence, expected):
    import cv2

    camera = Camera.__new__(Camera)
    camera.cv2 = cv2
    camera.centres = {}
    camera.raising = PhoneRaising()
    camera.gaze_vector = None
    camera.face_features = lambda *args: (1, None)
    camera.gaze = Mock()
    camera.gaze.observe.return_value = {"direction": "UNKNOWN"}
    camera.face_detector = Mock()
    camera.face_detector.detect.return_value = []
    camera.phone = Mock()
    camera.phone.detect.return_value = [{"confidence": confidence, "box": [200, 100, 280, 260]}]
    result = camera.analyze(np.zeros((480, 640, 3), dtype=np.uint8), at=0)
    assert bool(result["detections"]) is expected
    assert result["phone_confidence"] == (confidence if expected else 0.0)


def test_repeated_low_confidence_phone_never_locks_or_creates_aiming_events():
    engine = RuleEngine()
    engine.start()
    events = []
    for i in range(50):
        events.extend(engine.observe(i / 10, phone_confidence=0.799, phone_aiming=True))
    assert not events
    assert not engine.phone_present
    assert engine.state.access == "OPEN"


def test_only_eighty_percent_samples_confirm_phone_and_low_scores_allow_teacher_unlock():
    engine = RuleEngine()
    engine.start()
    assert not engine.observe(0, phone_confidence=0.80)
    assert not engine.observe(0.1, phone_confidence=0.799)
    assert engine.state.access == "OPEN"
    events = engine.observe(0.2, phone_confidence=0.80)
    assert [event["type"] for event in events] == ["PHONE_DETECTED"]
    assert events[0]["confidence"] == 0.80
    assert engine.state.access == "LOCKED"
    for i in range(3, 18):
        engine.observe(i / 10, phone_confidence=0.799)
    assert not engine.phone_present
    assert engine.state.access == "LOCKED"  # A score change never auto-unlocks.
    engine.unlock(engine.state.lock_id, engine.state.version)
    assert engine.state.access == "OPEN"


@pytest.mark.parametrize("confidence, expected", [(0.799, 0), (0.80, 1)])
def test_phone_raising_uses_the_same_eighty_percent_gate(confidence, expected):
    detector = PhoneRaising()
    signals = []
    for i in range(50):
        y = 400 if i < 5 else max(190, 400 - (i - 5) * 30)
        detections = [{"confidence": confidence, "box": [200, y - 80, 280, y + 80]}]
        signals.append(detector.update(i / 10, detections, 640, 480))
    assert sum(signals) == expected


def test_phone_aim_review_accepts_eighty_percent_confidence():
    engine = RuleEngine()
    engine.start()
    events = engine.observe(0, phone_confidence=0.80, phone_aiming=True)
    assert [event["type"] for event in events] == ["PHONE_AIM_REVIEW"]
