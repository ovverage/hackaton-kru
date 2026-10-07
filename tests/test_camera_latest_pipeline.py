"""Latest-frame integration preserves model coverage, timestamps and cleanup."""

from collections import deque
import queue
import threading
import time
from unittest.mock import Mock

import numpy as np
import pytest

from agent.behavior import PhoneRaising
from agent.capture import CapturePump
from agent.latest_frame import FramePacket, LatestFrameSource
from test_gaze_input_independence import camera as gaze_camera
from test_latest_frame import Capture
from test_screen_calibration_runtime import fitted_profile, raw_gaze


class Packets:
    def __init__(self, *packets):
        self.packets = deque(packets)
        self.calls = []

    def read(self, after_sequence=0, timeout=2):
        self.calls.append((after_sequence, timeout))
        packet = self.packets.popleft()
        assert packet.sequence > after_sequence
        return packet

    def close(self, timeout=.5):
        return True


def image(value):
    return np.full((100, 100, 3), value, dtype=np.uint8)


def wired_camera(source):
    camera = gaze_camera()
    camera.capture = Mock()
    camera.capture.read.side_effect = AssertionError('Only the producer may read native capture')
    camera.frame_source = source
    camera._capture_sequence = 0
    camera.last_captured_at = None
    camera._read_lock = threading.RLock()
    camera._closing = camera._face_closed = False
    camera.face.close = Mock()
    camera.last_frame = 0
    camera.frame_digest = None
    camera.frame_changed_at = time.monotonic()
    camera.recognition_was_paused = False
    camera.centres = {}
    camera.raising = PhoneRaising()
    camera.face_detector = Mock()
    camera.face_detector.detect.return_value = [{'confidence': .9, 'box': [1, 1, 90, 90]}]
    camera.phone = Mock()
    camera.phone.detect.return_value = [
        {'confidence': .9, 'box': [10, 20, 30, 40]},
        {'confidence': .79, 'box': [50, 60, 70, 80]},
    ]
    camera.begin_screen_calibration('test-monitor')
    assert camera.install_screen_calibration(fitted_profile(), [raw_gaze()] * 3,
                                             [np.eye(3)] * 3, 'test-monitor')
    return camera


def test_camera_consumes_new_sequence_and_preserves_capture_time_and_all_models():
    first, last = FramePacket(4, 40.123, image(1)), FramePacket(17, 40.456, image(2))
    source = Packets(first, last)
    camera = wired_camera(source)
    for packet in (first, last):
        frame, observation = camera.read()
        assert frame is packet.frame
        assert observation['captured_at'] == camera.last_captured_at == packet.captured_at
        assert observation['direction'] == 'SCREEN' and observation['faces'] == 1
        assert observation['phone_confidence'] == .9
        assert observation['detections'] == [
            {'label': 'phone', 'confidence': .9, 'box': [.1, .2, .3, .4]},
        ]
    assert source.calls == [(0, 2.), (4, 2.)]
    assert camera.face.detect_for_video.call_count == 2
    assert camera.face_detector.detect.call_count == 2
    assert camera.public_gaze.estimate.call_count == 2
    assert camera.phone.detect.call_count == 2
    assert camera.face.detect_for_video.call_args.args[1] == 40456
    camera.capture.read.assert_not_called()


def test_adaptive_calibration_keeps_all_models_in_the_frame_pipeline():
    packet = FramePacket(5, 70.5, image(7))
    camera = wired_camera(Packets(packet))
    camera.begin_screen_calibration('test-monitor')
    _, observation = camera.read()
    sample = camera.screen_calibration_sample()
    assert sample['at'] == packet.captured_at
    assert sample['gaze']['vector'] == [0, 0, -1]
    assert observation['direction'] == 'UNKNOWN'
    assert observation['phone_confidence'] == .9
    for model in (camera.face.detect_for_video, camera.face_detector.detect,
                  camera.public_gaze.estimate, camera.phone.detect):
        model.assert_called_once()


def test_paused_raw_frame_keeps_original_time_without_any_model():
    packet = FramePacket(5, 70.5, image(7))
    camera = wired_camera(Packets(packet))
    frame, observation = camera.read(analyze=False)
    assert frame is packet.frame and observation is None
    assert camera.last_captured_at == 70.5
    assert camera.recognition_was_paused
    for model in (camera.face.detect_for_video, camera.face_detector.detect,
                  camera.public_gaze.estimate, camera.phone.detect):
        model.assert_not_called()


def test_paused_phone_review_uses_only_phone_model_and_original_capture_time(monkeypatch):
    monkeypatch.setattr('agent.vision.time.monotonic', lambda: 80.)
    camera = wired_camera(Packets(FramePacket(8, 79.9, image(8))))
    _, observation = camera.read(analyze=False, phone_review=True, phone_review_until=81.)
    assert observation['phone_review_only']
    assert observation['captured_at'] == 79.9 and observation['phone_confidence'] == .9
    camera.phone.detect.assert_called_once()
    camera.face.detect_for_video.assert_not_called()
    camera.face_detector.detect.assert_not_called()
    camera.public_gaze.estimate.assert_not_called()


def test_old_buffered_packet_cannot_start_phone_review_after_wall_clock_deadline(monkeypatch):
    monkeypatch.setattr('agent.vision.time.monotonic', lambda: 80.)
    camera = wired_camera(Packets(FramePacket(8, 79.8, image(8))))
    frame, observation = camera.read(analyze=False, phone_review=True, phone_review_until=79.9)
    assert frame is not None and observation is None
    camera.phone.detect.assert_not_called()


def test_close_waits_for_active_model_without_concurrent_capture_release():
    capture = Capture()
    source = LatestFrameSource(capture)
    assert capture.calls.get(timeout=1) == 'qorgau-frame-source'
    camera = wired_camera(source)
    camera.capture = capture
    model_entered, model_finish = threading.Event(), threading.Event()
    result = queue.Queue()
    active = [False]
    original_face_call = camera.face.detect_for_video.return_value

    def blocked_face(*args):
        active[0] = True
        model_entered.set()
        assert model_finish.wait(3)
        active[0] = False
        return original_face_call

    def close_face():
        assert not active[0]

    camera.face.detect_for_video.side_effect = blocked_face
    camera.face.close.side_effect = close_face

    def inference():
        try:
            result.put(camera.read())
        except OSError as error:
            result.put(error)

    reader = threading.Thread(target=inference)
    reader.start()
    try:
        capture.frames.put((True, image(1)))
        assert capture.calls.get(timeout=1) == 'qorgau-frame-source'
        assert model_entered.wait(1)
        assert not camera.close()
        assert camera.requires_gaze_reference
        assert not capture.releases
        camera.face.close.assert_not_called()
        assert camera.latest_preview() is None
        model_finish.set()
        reader.join(timeout=2)
        assert not reader.is_alive()
        assert isinstance(result.get_nowait(), OSError)  # In-flight result cannot escape close.
        camera.face.close.assert_called_once()
        capture.frames.put((False, None))
        assert camera.close()
        assert capture.releases == ['qorgau-frame-source']
        camera.face.close.assert_called_once()
        with pytest.raises(OSError, match='закрыта'):
            camera.read()
    finally:
        model_finish.set()
        capture.frames.put((False, None))
        source.close(timeout=1)
        reader.join(timeout=2)


def test_capture_pump_raw_observation_preserves_camera_packet_timestamp():
    class BufferedCamera:
        def __init__(self):
            self.calls, self.frames = queue.Queue(), queue.Queue()
            self.last_captured_at = None

        def read(self, *, analyze=True):
            self.calls.put(analyze)
            packet = self.frames.get(timeout=3)
            self.last_captured_at = packet.captured_at
            return packet.frame, None

    camera = BufferedCamera()
    pump = CapturePump(camera, recognize=False)
    try:
        assert camera.calls.get(timeout=1) is False
        camera.frames.put(FramePacket(3, 12.25, 'raw-frame'))
        assert camera.calls.get(timeout=1) is False
        assert pump.poll() == ('raw-frame', None)
        assert pump.last_captured_at == 12.25
    finally:
        pump.stop.set()
        camera.frames.put(FramePacket(4, 12.35, 'ending-frame'))
        pump.close()
