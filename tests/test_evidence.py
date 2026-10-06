from unittest.mock import Mock, patch

import numpy as np

from agent.client import Agent, atomic_json
from agent.evidence import annotate


def test_detection_scales_from_normalised_coordinates_and_keeps_source():
    original = np.zeros((720, 1280, 3), dtype=np.uint8)
    shown = annotate(original, [{'box': [.25, .25, .5, .75], 'confidence': .95}])
    assert not original.any()
    assert shown[180, 320].any() and shown[539, 640].any()
    assert not shown[600, 1000].any()


def test_phone_photo_and_recorded_frames_include_box(tmp_path):
    import cv2
    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture'})
    agent = Agent(tmp_path)
    recorder = Mock(last_t=None)
    recorder.completed.return_value = []
    agent.recorder = recorder
    agent.engine.start()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    boxes = [{'box': [.25, .25, .5, .75], 'confidence': .99, 'label': 'phone'}]
    # Samples must follow the agent's real initialization/reference timestamp.
    # A frozen value also keeps the fixture independent of internal clock reads.
    with patch('agent.client.time.monotonic', return_value=agent.origin + 1) as clock:
        agent.observe(frame=image, phone_confidence=.99, detections=boxes, captured_at=clock.return_value)
        clock.return_value += .2
        agent.observe(frame=image, phone_confidence=.99, detections=boxes, captured_at=clock.return_value)
    event = agent.snapshot()['recent_events'][0]
    assert event['detections'] == boxes
    photo = cv2.imread(event['thumbnail_path'])
    assert photo.shape == image.shape and photo[120, 160].max() > 100
    assert recorder.push.call_args.args[1][120, 160].any()
    assert not image.any()
    agent.http.close()
