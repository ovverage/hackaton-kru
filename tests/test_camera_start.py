import os
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from agent.behavior import PhoneRaising
from agent.client import Agent, atomic_json
from agent.vision import Camera
from shared.rules import RuleEngine


@pytest.fixture(scope="module")
def application():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


class FakeCamera:
    def __init__(self, *args, calibrate=False):
        import cv2

        assert not calibrate, "Ordinary camera start must not collect calibration"
        self.cv2 = cv2
        self.centres = {}
        self.gaze_enabled = True
        self.closed = False

    def read(self):
        # No face is needed to open a functioning camera.
        return np.zeros((48, 64, 3), dtype=np.uint8), {
            "faces": 0,
            "direction": "UNKNOWN",
        }

    def close(self):
        self.closed = True


def test_unknown_gaze_does_not_prevent_phone_detection_and_aspect_is_preserved():
    import cv2

    camera = Camera.__new__(Camera)
    camera.cv2 = cv2
    camera.centres = {}
    camera.raising = PhoneRaising()
    camera.face_features = lambda *args: (1, np.array([0.1, 0.9, 0.2, 0.9]))
    camera.gaze_vector = None
    from unittest.mock import Mock
    camera.gaze = Mock()
    camera.gaze.observe.return_value = {'direction':'UNKNOWN'}
    camera.face_detector = Mock()
    camera.face_detector.detect.return_value = []

    class Detector:
        def detect(self, frame):
            assert frame.shape[:2] == (720, 1280)
            return [{"confidence": 0.95, "box": [200, 100, 280, 260]}]

    camera.phone = Detector()
    engine = RuleEngine()
    engine.start()
    for t in (0.0, 0.2, 0.4):
        observation = camera.analyze(np.zeros((720, 1280, 3), dtype=np.uint8), at=t)
        assert observation["direction"] == "UNKNOWN" and observation["faces"] == 1
        engine.observe(t, **observation)
    assert engine.state.reason == "PHONE_DETECTED"
    assert not any(engine.state.counts().values())


@pytest.mark.parametrize("failure", ["capture", "cancel"])
def test_camera_start_cleans_up_without_returning_unusable_camera(application, failure):
    from agent.camera_setup import CameraStartWorker

    worker = CameraStartWorker(0, Path("phone"), Path("face"), Path("unused"))
    camera = FakeCamera()

    def read():
        if failure == "capture":
            raise OSError("Camera read failed")
        worker.stop.set()
        return FakeCamera.read(camera)

    camera.read = read
    with patch("agent.vision.Camera", return_value=camera):
        worker.run()
    assert camera.closed and worker.result is None
    assert bool(worker.error) == (failure == "capture")


def test_one_click_camera_enables_auto_gaze_and_recovery_keeps_lock(
    application, tmp_path
):
    from agent.camera_setup import CameraSetup

    atomic_json(
        tmp_path / "config.json", {"server": "http://localhost:8000", "token": "test"}
    )
    agent = Agent(tmp_path)
    model = tmp_path / "model"
    model.write_bytes(b"test fixture")
    callbacks = []
    with (
        patch("agent.resources.verified_models", return_value=(model, model)),
        patch(
            "agent.camera_setup.QTimer.singleShot",
            side_effect=lambda _, callback: callbacks.append(callback),
        ),
    ):
        dialog = CameraSetup(agent)
    assert (
        not dialog.calibrate
        and dialog.progress.isHidden()
        and dialog.capture.isHidden()
    )
    assert len(callbacks) == 1
    with (
        patch("agent.vision.Camera", FakeCamera),
        patch(
            "agent.camera_setup.CalibrationWorker",
            side_effect=AssertionError("No wizard"),
        ),
    ):
        callbacks[0]()
        deadline = time.monotonic() + 10
        while dialog.preparing and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.01)
    assert not dialog.preparing and dialog.result() == dialog.DialogCode.Accepted
    assert agent.camera is not None and agent.recorder is not None
    assert agent.capabilities["camera"] and agent.snapshot()["gaze"]
    assert agent.camera.centres == {}
    agent.journal["exam_id"] = "exam"
    agent.last_observation = {
        "faces": 1,
        "phone_confidence": 0.0,
        "direction": "UNKNOWN",
    }
    agent.last_observation_at = time.monotonic()
    with patch.object(agent, "launch_environment"):
        agent.apply(
            {
                "id": "start",
                "exam_id": "exam",
                "expires_at": 100,
                "type": "START",
                "expected_version": 0,
                "require_camera": True,
            },
            now=1,
        )
    assert agent.engine.state.lifecycle == "RUNNING", agent.journal["acks"]
    agent.engine.lock("CAMERA_UNAVAILABLE")
    lock_id = agent.engine.state.lock_id
    callbacks.clear()
    with (
        patch("agent.resources.verified_models", return_value=(model, model)),
        patch(
            "agent.camera_setup.QTimer.singleShot",
            side_effect=lambda _, callback: callbacks.append(callback),
        ),
    ):
        recovery = CameraSetup(agent)
    with patch("agent.vision.Camera", FakeCamera):
        callbacks[0]()
        deadline = time.monotonic() + 10
        while recovery.preparing and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.01)
    assert recovery.result() == recovery.DialogCode.Accepted
    assert (
        agent.engine.state.access == "LOCKED" and agent.engine.state.lock_id == lock_id
    )
    assert agent.capabilities["gaze"]
    agent.engine.end()
    agent.camera.close()
    agent.recorder.close()
    agent.http.close()


def test_cancel_before_queued_start_never_opens_camera(application, tmp_path):
    from agent.camera_setup import CameraSetup

    atomic_json(
        tmp_path / "config.json", {"server": "http://localhost:8000", "token": "test"}
    )
    agent = Agent(tmp_path)
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    callbacks = []
    with (
        patch("agent.resources.verified_models", return_value=(model, model)),
        patch(
            "agent.camera_setup.QTimer.singleShot",
            side_effect=lambda _, callback: callbacks.append(callback),
        ),
    ):
        dialog = CameraSetup(agent)
    dialog.reject()
    with patch("agent.vision.Camera") as camera:
        callbacks[0]()
        camera.assert_not_called()
    assert not agent.camera_preparing and not agent.capabilities["camera"]
    agent.http.close()
