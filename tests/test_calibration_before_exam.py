"""The setup worker runs full inference and fits a screen before any exam starts."""

import os
from types import SimpleNamespace

import numpy as np

from agent import screen_capture, vision
from agent.latest_frame import FramePacket
from test_camera_latest_pipeline import wired_camera
from test_screen_calibration_runtime import raw_gaze


def test_camera_start_worker_calibrates_with_gaze_disabled_and_no_started_exam(monkeypatch, tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from agent.camera_setup import CameraStartWorker

    application = QApplication.instance() or QApplication([])
    assert application is not None
    clock = SimpleNamespace(at=100.)
    clock.monotonic = lambda: clock.at
    monkeypatch.setattr(screen_capture, "time", clock)
    monkeypatch.setattr(vision, "time", clock)
    worker = CameraStartWorker(0, tmp_path / "phone", tmp_path / "face", tmp_path)
    signature = {"name": "fake monitor", "geometry": [0, 0, 1920, 1080]}

    class Source:
        sequence = 0
        point = (.5, .5)
        pending = None

        def read(self, after_sequence=0, timeout=2):
            assert after_sequence == self.sequence
            clock.at += .1
            self.sequence += 1
            if self.pending is not None and clock.at >= self.pending[1]:
                worker.screen_session.presented(self.pending[0], signature)
                self.pending = None
            # All frames differ, including after sequence 255; no camera is opened.
            frame = np.empty((100, 100, 3), np.uint8)
            frame[:] = [self.sequence % 256, self.sequence // 256, 0]
            return FramePacket(self.sequence, clock.at, frame)

        def close(self, timeout=.5):
            return True

    source = Source()
    camera = wired_camera(source)
    camera.begin_screen_calibration(signature)
    assert not camera.gaze_enabled and camera.public_gaze.reference is None
    camera.public_gaze.estimate.side_effect = lambda *_: raw_gaze(
        (.5 - source.point[0]) * 40, (.5 - source.point[1]) * 24)
    monkeypatch.setattr(vision, "Camera", lambda *args, **kwargs: camera)
    targets = []
    before_targets = []
    worker.ready.connect(lambda: before_targets.append(camera.public_gaze.estimate.call_count))

    def present(index, phase, point, count, minimum):
        targets.append((index, phase))
        source.point = point
        source.pending = (index, clock.at + screen_capture.TARGET_SETTLE_SECONDS)

    worker.screen_target.connect(present)
    worker.screen_session.start(signature)
    try:
        # Runs the actual worker/session/camera methods; no Agent or START exists.
        worker.run()
        assert worker.error == ""
        assert worker.result is camera
        assert before_targets == [1]  # CNN already ran before the first target.
        assert len(targets) == 9
        assert camera.screen_calibration.ready and camera.gaze_enabled
        assert len(camera.screen_calibration.quality["sample_counts"]) == 9
        for model in (camera.face.detect_for_video, camera.face_detector.detect,
                      camera.public_gaze.estimate, camera.phone.detect):
            assert model.call_count == source.sequence
        camera.capture.read.assert_not_called()
    finally:
        camera.close()
