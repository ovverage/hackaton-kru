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
    recorder = Mock()
    recorder.completed.return_value = []
    agent.recorder = recorder
    agent.engine.start()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    boxes = [{'box': [.25, .25, .5, .75], 'confidence': .99, 'label': 'phone'}]
    with patch('agent.client.time.monotonic', side_effect=[100, 100, 100.2, 100.2, 100.2, 100.2]):
        agent.origin = 100
        agent.observe(frame=image, phone_confidence=.99, detections=boxes)
        agent.observe(frame=image, phone_confidence=.99, detections=boxes)
    event = agent.snapshot()['recent_events'][0]
    assert event['detections'] == boxes
    photo = cv2.imread(event['thumbnail_path'])
    assert photo.shape == image.shape and photo[120, 160].max() > 100
    assert recorder.push.call_args.args[1][120, 160].any()
    assert not image.any()
    agent.http.close()
