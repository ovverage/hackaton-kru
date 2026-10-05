from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
from backend.proctor.app import create_app
from agent.client import Agent, atomic_json


def test_actual_agent_enrollment_sync_commands_restart(tmp_path):
    server = TestClient(
        create_app(tmp_path / "server"), headers={"X-Requested-With": "Qorgau"}
    )
    server.post(
        "/api/auth/setup", json={"name": "Teacher", "password": "test-password"}
    )
    code = server.post("/api/pairings", json={}).json()["code"]
    registered = server.post(
        "/api/agent/enroll", json={"code": code, "name": "Agent PC"}
    ).json()
    folder = tmp_path / "agent"
    executable = tmp_path / "test-app"
    executable.write_text("test executable placeholder")
    atomic_json(
        folder / "config.json",
        {
            **registered,
            "server": "http://testserver",
            "targets": [
                {
                    "id": "test-app",
                    "kind": "APP",
                    "name": "Test app",
                    "executable": str(executable),
                }
            ],
        },
    )

    def forward(request):
        return server.request(
            request.method,
            request.url.path,
            content=request.content,
            headers=dict(request.headers),
        )

    transport = httpx.MockTransport(forward)
    agent = Agent(folder, transport=transport)
    agent.sync()
    ex = server.post(
        "/api/exams",
        json={
            "title": "Real agent",
            "require_camera": False,  # This test exercises transport, not CV hardware.
            "group": "A",
            "room": "1",
            "device_ids": [registered["device_id"]],
            "environment": {"kind": "APP", "target_id": "test-app"},
        },
    ).json()
    agent.sync()
    assert agent.journal["exam_id"] == ex["id"]
    command = server.post(
        "/api/devices/" + registered["device_id"] + "/commands",
        json={"type": "START", "expected_version": 0},
    ).json()
    assert command["status"] == "PENDING"
    with patch("agent.client.subprocess.Popen") as launched:
        agent.sync()
        assert launched.call_count == 1
    assert agent.engine.state.lifecycle == "RUNNING"
    with patch("agent.client.subprocess.Popen") as launched:
        agent.apply(command)
        assert not launched.called
    agent.sync()
    assert server.get("/api/snapshot").json()["commands"][0]["status"] == "APPLIED"
    agent.observe(phone_confidence=0.95)
    agent.observe(phone_confidence=0.95)
    agent.sync()
    snap = server.get("/api/snapshot").json()
    assert snap["devices"][0]["state"]["access"] == "LOCKED"
    assert len(snap["events"]) == 1
    assert agent.journal["events"] == []
    restored = Agent(folder, transport=transport)
    assert (
        restored.engine.state.access == "LOCKED"
        and restored.engine.state.reason == "AGENT_RESTARTED"
    )
    assert restored.engine.state.version > agent.engine.state.version


def test_expired_command_does_not_mutate_state(tmp_path):
    atomic_json(
        tmp_path / "config.json",
        {"server": "http://localhost:8000", "token": "test", "targets": []},
    )
    agent = Agent(tmp_path)
    agent.journal["exam_id"] = "exam"
    command = {
        "id": "1",
        "exam_id": "exam",
        "expires_at": 10,
        "type": "START",
        "expected_version": 0,
    }
    agent.apply(command, now=20)
    assert agent.engine.state.lifecycle == "READY"
    assert not agent.journal["acks"][0]["ok"]
