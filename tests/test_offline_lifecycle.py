"""Real offline state transitions, without opening a camera, window, or network."""
from types import SimpleNamespace
from unittest.mock import Mock
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from agent.offline import OfflineAgent
from agent import client
from agent.profile import read_object
from agent.windows_guard import WindowTarget
from shared.storage import atomic_json
from shared.teacher_faces import MODEL_ID


@pytest.fixture
def local_exam(tmp_path, monkeypatch):
    clock = [100.]
    wall_start = time.time()
    time_source = SimpleNamespace(monotonic=lambda: clock[0], time=lambda: wall_start + clock[0])
    monkeypatch.setattr("agent.client.time", time_source)
    monkeypatch.setattr("agent.offline.time", time_source)
    monkeypatch.setattr("agent.session_control.time", time_source)
    monkeypatch.setattr("agent.program_check.running_tools", lambda: [])
    monkeypatch.setattr("agent.windows_guard.WindowsGuard", lambda: SimpleNamespace(valid=lambda _: True))
    requests = []

    def forbidden(request):
        requests.append(request)
        raise AssertionError("A local exam must not upload evidence or poll commands")

    agent = OfflineAgent(tmp_path, transport=httpx.MockTransport(forbidden))
    agent.setup_password("teacher-password")
    target = WindowTarget(42, 73, "Local exam", str(tmp_path / "exam.exe"), 99).public()

    def start():
        agent.offline_start(target)
        agent.camera = SimpleNamespace(requires_gaze_reference=False, screen_calibration_required=True, close=Mock())
        agent.recorder = Mock()
        agent.capabilities.update(window_guard=True, guard_active=True, camera=True, recording=True, gaze=True)
        agent.last_observation = {"faces": 1, "phone_confidence": 0}
        agent.last_observation_at = clock[0]
        token = agent.start_pending["id"]
        agent.prepare_start(token)
        agent.complete_start(token)
        assert agent.engine.state.lifecycle == "RUNNING"
        return agent.journal["exam_id"]

    yield agent, clock, start, requests
    agent.http.close()


@pytest.mark.parametrize("trigger", ["security", "browser"])
def test_second_offline_exam_does_not_inherit_first_exam_event_cooldown(local_exam, trigger):
    agent, clock, start, requests = local_exam

    def cause_event():
        if trigger == "security":
            agent.security_event("REMOTE_CONTROL_PROGRAM")
        else:
            agent.environment = {"kind": "BROWSER_TAB", "guarded": True, "url": "https://exam.test/",
                                 "tab_id": 1, "window_id": 2, "browser_instance": "profile"}
            atomic_json(agent.folder / "browser.json", {
                "at": client.time.time(),
                "exam_id": agent.journal["exam_id"], "binding": agent.bridge_binding,
                "observation": {"focused": True, "url": "https://exam.test/", "tab_id": 999,
                                "window_id": 2, "browser_instance": "profile"},
            })
            agent.read_browser()

    first_exam = start()
    clock[0] += 600
    cause_event()
    assert agent.engine.state.access == "LOCKED"
    agent.teacher_unlock("teacher-password", "END_AND_RELEASE")
    assert agent.engine.state.lifecycle == "COMPLETED"
    clock[0] += 1
    second_exam = start()
    assert second_exam != first_exam
    clock[0] += .2
    cause_event()
    assert agent.engine.state.access == "LOCKED"
    assert len([event for event in agent.journal["events"] if not event.get("update")]) == 1
    assert not requests


def test_end_closes_writers_before_purging_only_exam_data_and_next_exam_is_clean(local_exam, tmp_path):
    agent, clock, start, requests = local_exam
    first_exam = start()
    recorder, camera = agent.recorder, agent.camera
    close_order = []
    agent.capture_pump = SimpleNamespace(close=lambda: close_order.append("capture"), set_recognition=Mock())
    camera.close.side_effect = lambda: close_order.append("camera")
    recorder.close.side_effect = lambda: close_order.append("recorder")
    for sub in ("clips", "evidence"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "owned.data").write_bytes(b"private evidence")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("unrelated")
    agent.journal.update(events=[{"id": "old", "type": "TEST"}], recent_events=[{"id": "old"}], media=[{"path": "old"}])
    agent.teacher_unlock("teacher-password", "END_AND_RELEASE")
    assert close_order == ["capture", "camera", "recorder"]
    assert agent.camera is agent.recorder is agent.capture_pump is None
    assert not (tmp_path / "clips").exists() and not (tmp_path / "evidence").exists()
    assert unrelated.read_text() == "unrelated" and agent.local_access.ready
    journal = read_object(tmp_path / "journal.json")
    assert not journal["events"] and not journal["recent_events"] and not journal["media"]
    assert journal["state"]["lifecycle"] == "COMPLETED"
    clock[0] += 1
    assert start() != first_exam
    assert not agent.journal["events"] and not agent.journal["recent_events"]
    assert agent.engine.state.access == "OPEN" and not agent.engine.state.strikes
    assert not requests


def test_offline_finish_clears_persisted_strikes_and_browser_exam_metadata(local_exam, tmp_path):
    agent, _, start, _ = local_exam
    start()
    agent.engine.state.strikes.append({"id": "old-incident", "direction": "LEFT", "epoch": 1})
    agent.environment = {"kind": "BROWSER_TAB", "url": "https://exam.test/private?token=temporary"}
    agent.session = {"title": "Private exam title"}
    browser_files = ["browser.json", "browser-tabs-fixture.json", "browser-tab-command.json",
                     "browser-tab-ack.json", "browser-tabs-request.json"]
    for name in browser_files:
        atomic_json(tmp_path / name, {"url": "https://exam.test/private?token=temporary"})
    agent.teacher_unlock("teacher-password", "END_AND_RELEASE")
    journal = read_object(tmp_path / "journal.json")
    assert journal["state"]["strikes"] == []
    assert not journal.get("environment") and not journal.get("session")
    assert all(not (tmp_path / name).exists() for name in browser_files)
    assert agent.local_access.ready and (tmp_path / "config.json").is_file()


def test_first_offline_exam_can_load_teacher_exclusions_before_any_unlock(local_exam, monkeypatch):
    agent, _, start, _ = local_exam
    start()
    assert agent._face_http is None
    response = httpx.Response(200, json={"model": MODEL_ID, "expires_at": time.time() + 60,
                                        "templates": [{"teacher_id": "teacher", "embedding": [1.]}]})
    http = SimpleNamespace(get=Mock(return_value=response))
    identity = Mock(return_value=http)
    engine = object()
    monkeypatch.setattr(agent, "identity_http", identity)
    monkeypatch.setattr(agent, "teacher_engine", lambda: engine)
    agent.refresh_teacher_templates()
    identity.assert_called_once()
    http.get.assert_called_once_with("/api/agent/teacher-faces")
    assert agent.camera.teacher_identity[0] is engine
    assert agent.camera.teacher_identity[1][0]["teacher_id"] == "teacher"
    assert agent.engine.state.access == "OPEN"


def test_unavailable_teacher_database_does_not_stop_local_exam_or_password(local_exam, monkeypatch):
    agent, _, start, _ = local_exam
    start()
    identity = Mock(side_effect=httpx.ConnectError("No network"))
    monkeypatch.setattr(agent, "identity_http", identity)
    agent.refresh_teacher_templates()
    assert agent.engine.state.access == "OPEN" and agent.local_access.ready
    assert agent._teacher_templates == (None, [], 0)


def prepare_ready_agent(agent, clock):
    agent.journal["exam_id"] = "retry-exam"
    agent.camera = SimpleNamespace(requires_gaze_reference=False, screen_calibration_required=True)
    agent.recorder = Mock()
    agent.last_observation = {"faces": 1, "phone_confidence": 0}
    agent.last_observation_at = clock[0]
    agent.queue_start({"id": "initial", "type": "START", "exam_id": "retry-exam",
                       "expected_version": 0, "expires_at": client.time.time() + 30})
    return agent.start_pending["id"]


def test_complete_start_retry_uses_new_command_after_transient_camera_failure(local_exam, monkeypatch):
    agent, clock, _, _ = local_exam
    token = prepare_ready_agent(agent, clock)
    launch = Mock()
    monkeypatch.setattr(agent, "launch_environment", launch)
    agent.prepare_start(token)
    agent.camera_fault = True
    with pytest.raises(ValueError, match="CAMERA_NOT_READY"):
        agent.complete_start(token)
    assert agent.engine.state.lifecycle == "READY"
    first_command = next(iter(agent.journal["processed"]))
    assert not agent.journal["processed"][first_command]["ok"]
    agent.camera_fault = False
    agent.complete_start(token)
    assert agent.engine.state.lifecycle == "RUNNING" and agent.start_pending is None
    second_command = next(key for key in agent.journal["processed"] if key != first_command)
    assert agent.journal["processed"][second_command]["ok"]
    launch.assert_called_once()


def test_pending_start_can_be_cancelled_while_environment_activation_waits(local_exam, monkeypatch):
    agent, clock, _, _ = local_exam
    token = prepare_ready_agent(agent, clock)
    entered, release = Event(), Event()

    def launch():
        entered.set()
        assert release.wait(3)
        agent.guard_target = {"hwnd": 42}

    monkeypatch.setattr(agent, "launch_environment", launch)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.prepare_start, token)
        assert entered.wait(2)
        # The GUI can acquire the mutex while browser activation is waiting.
        assert agent.mutex.acquire(timeout=1)
        try:
            agent.cancel_start(token)
        finally:
            agent.mutex.release()
            release.set()
        with pytest.raises(ValueError, match="отменена"):
            future.result(timeout=3)
    assert agent.start_pending is None and agent.guard_target is None
    assert agent.engine.state.lifecycle == "READY"
