import json
import os
from unittest.mock import patch

import httpx
import pytest

from agent.client import Agent, atomic_json
from agent.provision import enroll, server_address
from agent.student_state import present
from shared.rules import State


def test_registration_validates_destination_and_does_not_overwrite_credentials(
    tmp_path,
):
    calls = []

    def response(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"token": "test-secret", "device_id": "pc-1"})

    transport = httpx.MockTransport(response)
    with pytest.raises(ValueError, match="HTTPS"):
        enroll(
            tmp_path, "http://192.168.1.2:8000", "AB12CD34", "ПК-1", transport=transport
        )
    with pytest.raises(ValueError, match="8 символов"):
        enroll(tmp_path, "http://localhost:8000", "bad", "ПК-1", transport=transport)
    config = enroll(
        tmp_path,
        "http://localhost:8000/",
        "ab12cd34",
        "  ПК-1 — Алина  ",
        transport=transport,
    )
    assert config["name"] == "ПК-1 — Алина" and calls[0]["code"] == "AB12CD34"
    with pytest.raises(ValueError, match="уже подключён"):
        enroll(
            tmp_path,
            "http://localhost:8000",
            "AB12CD34",
            "changed",
            transport=transport,
        )
    assert (
        len(calls) == 1
        and json.loads((tmp_path / "config.json").read_text(encoding="utf-8")) == config
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@university.test",
        "https://university.test/api?token=x",
        "http://testserver",
        "http://localhost:0",
        "file:///tmp/server",
        "https://university.test:invalid",
    ],
)
def test_invalid_addresses_are_rejected(url):
    with pytest.raises(ValueError):
        server_address(url)


def test_expired_registration_has_actionable_error_and_no_saved_config(tmp_path):
    with pytest.raises(ValueError, match="Код истёк"):
        enroll(
            tmp_path,
            "http://localhost:8000",
            "AB12CD34",
            "ПК-1",
            transport=httpx.MockTransport(lambda _: httpx.Response(403)),
        )
    assert not (tmp_path / "config.json").exists()


def test_offline_does_not_clear_student_lock():
    state = State(
        lifecycle="RUNNING", access="LOCKED", lock_id="lock", reason="PHONE_DETECTED"
    ).public()
    view = present({"state": state, "connected": False})
    assert view["locked"] and not view["can_open"] and not view["can_calibrate"]
    assert "телефон" in view["message"]


@pytest.fixture(scope="module")
def application():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    yield app


def test_desktop_first_run_and_locked_actions(application, tmp_path):
    from agent.desktop import StudentWindow

    window = StudentWindow(tmp_path, run_worker=False)
    assert window.pages.currentIndex() == 0
    assert not (tmp_path / "config.json").exists()
    assert window.code_input.text() == ""
    window.close()
    atomic_json(
        tmp_path / "config.json",
        {
            "server": "http://localhost:8000",
            "token": "test",
            "name": "ПК-1 — Алина",
            "targets": [],
        },
    )
    agent = Agent(tmp_path)
    window = StudentWindow(tmp_path, agent=agent, run_worker=False)
    assert window.pages.currentIndex() == 1
    agent.engine.start()
    window.refresh()
    assert window.test_button.isEnabled()
    agent.engine.lock("PHONE_DETECTED")
    window.refresh()
    assert not window.test_button.isEnabled()
    assert not window.camera_button.isEnabled()
    with patch.object(agent, "launch_environment") as launch:
        window.open_exam()
        launch.assert_not_called()
    assert "Позовите преподавателя" in window.hero_title.text()
    agent.engine.phone_present = False
    agent.engine.unlock(agent.engine.state.lock_id, agent.engine.state.version)
    window.refresh()
    assert window.test_button.isEnabled() and window.epoch.text() == "Цикл 2"
    window.close()
    agent.http.close()


def test_worker_failure_locks_an_active_session(application, tmp_path):
    from agent.desktop import StudentWindow

    atomic_json(
        tmp_path / "config.json",
        {"server": "http://localhost:8000", "token": "fixture"},
    )
    agent = Agent(tmp_path)
    window = StudentWindow(tmp_path, agent=agent, run_worker=False)
    agent.engine.start()
    with patch.object(window, "show_status"):
        window.agent_failed("Тестовая техническая ошибка")
    assert agent.engine.state.access == "LOCKED"
    assert agent.engine.state.reason == "AGENT_FAILURE" and agent.camera_fault
    window.hide()
    agent.engine.end()
    agent.http.close()


def test_start_during_calibration_is_rejected_without_launching_exam(tmp_path):
    atomic_json(
        tmp_path / "config.json",
        {"server": "http://localhost:8000", "token": "test", "targets": []},
    )
    agent = Agent(tmp_path)
    agent.journal["exam_id"] = "exam"
    agent.camera_preparing = True
    with patch.object(agent, "launch_environment") as launch:
        agent.apply(
            {
                "id": "start",
                "exam_id": "exam",
                "expires_at": 100,
                "type": "START",
                "expected_version": 0,
            },
            now=1,
        )
        launch.assert_not_called()
    assert agent.engine.state.lifecycle == "READY"
    assert agent.journal["acks"][0]["error"] == "CAMERA_PREPARING"
    agent.http.close()


def test_tray_start_close_and_exit_preserve_active_control(application, tmp_path):
    from agent.desktop import StudentWindow

    atomic_json(
        tmp_path / "config.json",
        {
            "server": "http://localhost:8000",
            "token": "test",
            "name": "ПК-01",
            "targets": [],
        },
    )
    agent = Agent(tmp_path)
    with patch(
        "agent.desktop.QSystemTrayIcon.isSystemTrayAvailable", return_value=True
    ):
        window = StudentWindow(tmp_path, agent=agent, run_worker=False)
    window.start_visibility()
    assert not window.isVisible() and window.tray.isVisible()
    window.show_status()
    assert window.isVisible()
    agent.engine.start()
    agent.engine.lock("PHONE_DETECTED")
    window.refresh()
    assert not window.tray_exit.isEnabled()
    window.close()
    assert not window.isVisible() and not window.stop.is_set()
    assert agent.engine.state.access == "LOCKED"
    with patch("agent.desktop.QMessageBox.information"):
        window.request_exit()
    assert not window.stop.is_set()
    agent.engine.end()
    window.refresh()
    assert window.tray_exit.isEnabled()
    window.request_exit()
    assert window.stop.is_set() and not window.tray.isVisible()
    agent.http.close()


def test_no_tray_fallback_and_first_setup_stay_accessible(application, tmp_path):
    from agent.desktop import StudentWindow

    with patch(
        "agent.desktop.QSystemTrayIcon.isSystemTrayAvailable", return_value=False
    ):
        setup = StudentWindow(tmp_path, run_worker=False)
    setup.start_visibility()
    assert setup.isVisible() and setup.pages.currentIndex() == 0
    setup.close()
    atomic_json(
        tmp_path / "config.json",
        {
            "server": "http://localhost:8000",
            "token": "test",
            "name": "ПК-01",
            "targets": [],
        },
    )
    agent = Agent(tmp_path)
    with patch(
        "agent.desktop.QSystemTrayIcon.isSystemTrayAvailable", return_value=False
    ):
        window = StudentWindow(tmp_path, agent=agent, run_worker=False)
    window.start_visibility()
    assert window.isVisible()
    window.close()
    assert window.isMinimized() and not window.stop.is_set()
    window.request_exit()
    agent.http.close()
