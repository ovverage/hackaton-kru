"""Exercise real overlay widgets, including uncertain research gaze feedback."""

import os
from pathlib import Path

import pytest

from agent.client import Agent, atomic_json


@pytest.fixture(scope="module")
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    yield application


@pytest.fixture
def running(app, tmp_path):
    from PySide6.QtWidgets import QWidget
    from agent.exam_ui import ExamController

    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "fixture"})
    agent = Agent(tmp_path)
    agent.environment = {"kind": "DESKTOP", "guarded": False}
    agent.journal["exam_id"] = "direction-banner-test"
    agent.capabilities.update(camera=True, gaze=True)
    agent.gaze_diagnostics = {
        "source": "public_gaze_model",
        "direction": "UNKNOWN", "attention_away": False,
        "reference_ready": True, "gaze_tracking_status": "tracked",
        "gaze_display_stale": False, "gaze_observed_direction": "RIGHT",
        "gaze_observation_uncertain": True,
    }
    parent = QWidget()
    controller = ExamController(agent, parent)
    controller.timer.stop()
    agent.engine.start()
    yield agent, controller
    controller.release()
    for warning in controller.gaze_warnings:
        warning.close()
    parent.close()
    agent.http.close()


@pytest.mark.parametrize("direction,russian", [
    ("LEFT", "влево"), ("RIGHT", "вправо"), ("UP", "вверх"), ("DOWN", "вниз"),
])
@pytest.mark.parametrize("uncertain", [False, True])
def test_actual_overlay_names_all_directions_and_marks_uncertainty(running, direction, russian, uncertain):
    agent, controller = running
    agent.gaze_diagnostics.update(gaze_observed_direction=direction, gaze_observation_uncertain=uncertain)
    before = agent.engine.state.public()
    controller.tick()
    assert controller.gaze_warnings
    for warning in controller.gaze_warnings:
        assert warning.isVisible()
        message = warning.message.text()
        assert russian in message
        assert "по изображению камеры" in message
        assert "Смотрите на экран" in message
        assert ("Предварительная оценка" in message) is uncertain
        assert warning.height() >= warning.message.heightForWidth(warning.width() - 52) + 30
        assert warning.message.wordWrap()
    assert agent.engine.state.public() == before
    assert not agent.journal["events"]


def test_visible_overlay_updates_its_text_when_direction_changes(running):
    agent, controller = running
    for direction, word in [("RIGHT", "вправо"), ("DOWN", "вниз"), ("UP", "вверх"), ("LEFT", "влево")]:
        agent.gaze_diagnostics["gaze_observed_direction"] = direction
        controller.tick()
        assert all(w.isVisible() and word in w.message.text() for w in controller.gaze_warnings)


@pytest.mark.parametrize("change", [
    {"gaze_tracking_status": "blink"},
    {"gaze_tracking_status": "face_landmarks_missing"},
    {"gaze_tracking_status": "multiple_faces"},
    {"gaze_tracking_status": "invalid_crop"},
    {"gaze_tracking_status": "eye_state_missing"},
    {"gaze_tracking_status": "not_calibrated"},
    {"gaze_tracking_status": None},
    {"gaze_display_stale": True},
    {"reference_ready": False},
    {"gaze_observed_direction": "SCREEN"},
    {"gaze_observed_direction": "UNKNOWN"},
    {"gaze_observed_direction": None},
])
def test_overlay_hides_after_short_hold_for_stale_missing_or_centre_observation(running, change, monkeypatch):
    agent, controller = running
    clock = [__import__('time').monotonic()]
    monkeypatch.setattr('agent.exam_ui.time.monotonic', lambda: clock[0])
    controller.tick()
    assert all(w.isVisible() for w in controller.gaze_warnings)
    # An old strict result must not override a missing fresh public observation.
    agent.gaze_diagnostics.update(direction="RIGHT", attention_away=True, **change)
    controller.tick()
    assert all(w.isVisible() for w in controller.gaze_warnings)
    clock[0] += 1.01
    controller.tick()
    assert not any(w.isVisible() for w in controller.gaze_warnings)


@pytest.mark.parametrize("gate", ["camera", "camera_fault", "recognition_paused", "locked", "completed", "ready"])
def test_existing_camera_pause_and_lifecycle_controls_hide_overlay(running, gate):
    agent, controller = running
    controller.tick()
    assert all(w.isVisible() for w in controller.gaze_warnings)
    if gate == "camera":
        agent.capabilities["camera"] = False
    elif gate == "locked":
        agent.engine.lock("PHONE_DETECTED")
    elif gate == "completed":
        agent.engine.end()
    elif gate == "ready":
        agent.engine.state.lifecycle = "READY"
    else:
        setattr(agent, gate, True)
    controller.tick()
    assert not any(w.isVisible() for w in controller.gaze_warnings)


def test_release_hides_overlay(running):
    _, controller = running
    controller.tick()
    assert all(w.isVisible() for w in controller.gaze_warnings)
    controller.release()
    assert not any(w.isVisible() for w in controller.gaze_warnings)


def test_legacy_attention_keeps_original_banner_contract(running, monkeypatch):
    agent, controller = running
    clock = [__import__('time').monotonic()]
    monkeypatch.setattr('agent.exam_ui.time.monotonic', lambda: clock[0])
    agent.gaze_diagnostics = {
        "source": "legacy", "direction": "SCREEN", "reference_ready": True,
        "attention_away": True, "attention_direction": "LEFT",
    }
    controller.tick()
    assert all(w.isVisible() and w.message.text() == "Смотрите на экран"
               for w in controller.gaze_warnings)
    agent.gaze_diagnostics.update(attention_away=False)
    controller.tick()
    assert all(w.isVisible() for w in controller.gaze_warnings)
    clock[0] += 1.01
    controller.tick()
    assert not any(w.isVisible() for w in controller.gaze_warnings)
