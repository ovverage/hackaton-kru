"""The GUI previews the latest raw slot while model work stays off its thread."""

import os
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from agent.client import Agent, atomic_json
from agent.latest_frame import FramePacket


@pytest.fixture(scope="module")
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def until(app, condition, seconds=4):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not condition():
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    assert condition()


class BlockedModelCamera:
    """The preview slot is independent of the intentionally blocked model pass."""

    def __init__(self):
        import cv2

        self.cv2 = cv2
        self.gui_thread = threading.get_ident()
        self.centres = {}
        self.gaze_enabled = True
        self.started = threading.Event()
        self.release = threading.Event()
        self.packet = None
        self.read_threads = []
        self.analysis_threads = []
        self.preview_threads = []
        self.raise_on_release = False
        self.closed = False

    def latest_preview(self):
        self.preview_threads.append(threading.get_ident())
        return self.packet

    def analyze(self, frame):
        ident = threading.get_ident()
        assert ident != self.gui_thread, "Analysis must not execute on the GUI thread"
        self.analysis_threads.append(ident)
        self.started.set()
        if not self.release.wait(5):
            raise RuntimeError("Test did not release the blocked model pass")
        if self.raise_on_release:
            raise OSError("fixture model failure")
        return {"faces": 1, "direction": "UNKNOWN"}

    def read(self):
        ident = threading.get_ident()
        assert ident != self.gui_thread, "Camera.read must not execute on the GUI thread"
        self.read_threads.append(ident)
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        return frame, self.analyze(frame)

    def close(self):
        self.closed = True
        return True

    def publish(self, sequence, bgr):
        pixels = np.full((720, 1280, 3), bgr, dtype=np.uint8)
        self.packet = FramePacket(sequence, time.monotonic(), pixels)


@pytest.fixture
def setup(app, tmp_path, monkeypatch):
    from agent.camera_setup import CameraSetup

    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "fixture"})
    agent = Agent(tmp_path)
    camera = BlockedModelCamera()
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setattr("agent.resources.verified_models", lambda: (model, model))
    monkeypatch.setattr("agent.vision.Camera", lambda *args, **kwargs: camera)
    dialog = CameraSetup(agent)
    dialog.show()
    until(app, lambda: camera.started.is_set())
    try:
        yield agent, camera, dialog
    finally:
        if dialog.worker and dialog.worker.isRunning():
            dialog.reject()
            camera.release.set()
            until(app, lambda: not dialog.worker.isRunning())
        dialog.close()
        if agent.camera:
            agent.camera.close()
        if agent.recorder:
            agent.recorder.close()
        agent.http.close()
        app.processEvents()


def pixel(dialog):
    image = dialog.preview.pixmap().toImage()
    color = image.pixelColor(image.width() // 2, image.height() // 2)
    return color.red(), color.green(), color.blue()


def test_timer_preview_updates_while_the_model_worker_remains_blocked(app, setup):
    agent, camera, dialog = setup
    queued_images = []
    dialog.worker.frame.connect(queued_images.append)
    assert dialog.preview_timer.isActive() and dialog.preview_timer.interval() == 67
    camera.publish(1, (0, 0, 255))
    until(app, lambda: dialog._preview_sequence == 1)
    assert pixel(dialog) == (255, 0, 0)
    camera.publish(2, (255, 0, 0))
    until(app, lambda: dialog._preview_sequence == 2)
    assert pixel(dialog) == (0, 0, 255)
    assert not camera.release.is_set() and len(camera.read_threads) == 1
    assert len(camera.analysis_threads) == 1 and not dialog.preview_ready
    assert dialog.worker.isRunning() and agent.camera is None
    assert not queued_images
    assert set(camera.preview_threads) == {threading.get_ident()}
    assert set(camera.read_threads).isdisjoint(camera.preview_threads)
    assert set(camera.analysis_threads).isdisjoint(camera.preview_threads)


def test_duplicate_sequence_is_not_repainted_and_superseded_frames_are_skipped(app, setup):
    _, camera, dialog = setup
    camera.publish(1, (0, 0, 255))
    until(app, lambda: dialog._preview_sequence == 1)
    first_key = dialog.preview.pixmap().cacheKey()
    camera.publish(1, (255, 0, 0))
    dialog.update_latest_preview()
    assert dialog.preview.pixmap().cacheKey() == first_key
    assert pixel(dialog) == (255, 0, 0)
    camera.publish(2, (255, 0, 0))
    camera.publish(3, (0, 255, 0))
    dialog.update_latest_preview()
    assert dialog._preview_sequence == 3
    assert pixel(dialog) == (0, 255, 0)


def test_preview_stops_polling_while_real_calibration_overlay_is_visible(app, setup):
    from agent.screen_calibration import ScreenCalibrationDialog

    _, camera, dialog = setup
    camera.publish(1, (0, 0, 255))
    until(app, lambda: dialog._preview_sequence == 1)
    overlay = ScreenCalibrationDialog(dialog)
    dialog.screen_dialog = overlay
    overlay.showFullScreen()
    app.processEvents()
    preview_calls = len(camera.preview_threads)
    try:
        assert overlay.isVisible()
        camera.publish(2, (255, 0, 0))
        for _ in range(3):
            dialog.update_latest_preview()
        assert len(camera.preview_threads) == preview_calls
        assert dialog._preview_sequence == 1 and pixel(dialog) == (255, 0, 0)
        overlay.hide()
        dialog.update_latest_preview()
        assert dialog._preview_sequence == 2 and pixel(dialog) == (0, 0, 255)
    finally:
        dialog.screen_dialog = None
        overlay.close()


def test_cancel_stops_preview_timer_before_blocked_model_returns(app, setup):
    agent, camera, dialog = setup
    assert dialog.preview_timer.isActive()
    dialog.reject()
    assert not dialog.preview_timer.isActive()
    assert dialog.worker.isRunning() and not camera.release.is_set()
    camera.publish(1, (0, 0, 255))
    dialog.update_latest_preview()
    assert dialog._preview_sequence is None
    camera.release.set()
    until(app, lambda: not dialog.preparing)
    assert camera.closed and agent.camera is None
    assert dialog.worker.preview_source is None


@pytest.mark.parametrize("failure", [False, True])
def test_finished_worker_stops_preview_timer_and_clears_preview_source(app, setup, failure):
    agent, camera, dialog = setup
    camera.raise_on_release = failure
    camera.release.set()
    if not failure:
        until(app, lambda: dialog.preview_ready)
        dialog.start_button.click()
    until(app, lambda: not dialog.preparing)
    assert not dialog.worker.isRunning() and not dialog.preview_timer.isActive()
    assert dialog.worker.preview_source is None
    if failure:
        assert "fixture model failure" in dialog.feedback.text()
        assert camera.closed and agent.camera is None
    else:
        assert agent.camera is camera and not camera.closed
        assert dialog.result() == dialog.DialogCode.Accepted


def test_adaptive_setup_copy_states_three_seconds_and_twenty_seven_total(app, setup):
    _, _, dialog = setup
    # Avoid starting a second modal workflow; the copy itself is generated by
    # the same camera_ready slot that the real worker emits after inference.
    dialog.open_screen_calibration = lambda: None
    dialog.screen_reference = True
    dialog.camera_ready()
    assert "3 секунды" in dialog.instruction.text()
    assert "27 секунд" in dialog.instruction.text()
    assert "9 точками" in dialog.instruction.text()
    app.processEvents()


@pytest.mark.parametrize("reason", ["CAMERA_UNAVAILABLE", "DISPLAY_CHANGED"])
def test_recovery_does_not_open_a_second_camera_when_old_close_is_pending(
    app, tmp_path, monkeypatch, reason,
):
    from agent.camera_setup import CameraSetup

    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "fixture"})
    agent = Agent(tmp_path)
    agent.engine.start()
    agent.engine.lock(reason)
    calls = []
    created = []

    def pending_close():
        calls.append(True)
        return False

    previous = SimpleNamespace(close=pending_close)
    agent.camera = previous
    agent.capabilities.update(camera=True, gaze=True)
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    monkeypatch.setattr("agent.resources.verified_models", lambda: (model, model))
    monkeypatch.setattr("agent.vision.Camera", lambda *args, **kwargs: created.append(True))
    dialog = CameraSetup(agent)
    dialog.show()
    try:
        until(app, lambda: bool(calls))
        assert not created and dialog.worker is None
        assert agent.camera is previous
        assert not dialog.preparing and not agent.camera_preparing
        assert not dialog.preview_timer.isActive() and not agent.capabilities["gaze"]
        assert "Камера завершает обработку" in dialog.feedback.text()
        assert agent.engine.state.access == "LOCKED" and agent.engine.state.reason == reason
    finally:
        dialog.close()
        agent.http.close()
