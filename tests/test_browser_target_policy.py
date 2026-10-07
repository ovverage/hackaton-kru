"""A raw browser window cannot bypass the dedicated browser policy."""
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.client import Agent, atomic_json
from backend.proctor.app import create_app


@pytest.mark.parametrize("kind,target_id", [("DESKTOP", "primary-window"), ("APP", "primary-window"), ("BROWSER", "chrome")])
def test_agent_rejects_unguardable_browser_even_for_stale_assignment(tmp_path, kind, target_id):
    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "fixture"})
    agent = Agent(tmp_path)
    agent.environment = {"kind": kind, "target_id": target_id, "guarded": True}
    agent.targets = [{"id": target_id, "kind": "APP" if kind == "DESKTOP" else kind,
                      "guardable": False, "window": {}}]
    try:
        with patch("agent.client.subprocess.Popen") as launch:
            with pytest.raises(ValueError, match="BROWSER_REQUIRES_QORGAU_BROWSER"):
                agent.launch_environment()
            launch.assert_not_called()
    finally:
        agent.http.close()


def test_api_rejects_raw_browser_selected_through_desktop_mode(tmp_path):
    with TestClient(create_app(tmp_path / "server"), headers={"X-Requested-With": "Qorgau"}) as server:
        assert server.post("/api/auth/setup", json={"name": "Teacher", "password": "test-password"}).status_code == 200
        code = server.post("/api/pairings", json={}).json()["code"]
        identity = server.post("/api/agent/enroll", json={"code": code, "name": "Test PC"}).json()
        folder = tmp_path / "agent"
        atomic_json(folder / "config.json", {**identity, "server": "http://testserver"})

        def forward(request):
            return server.request(request.method, request.url.path, content=request.content, headers=dict(request.headers))

        agent = Agent(folder, transport=httpx.MockTransport(forward))
        try:
            agent.capabilities.update(desktop_monitor=True, window_guard=True, camera=True, recording=True)
            agent.targets = [{"id": "primary-window", "kind": "APP", "name": "Chrome", "guardable": False}]
            agent.sync()
            response = server.post("/api/exams", json={"title": "Exam", "group": "A", "room": "1",
                "device_ids": [identity["device_id"]], "mode": "GUARDED", "environment": {"kind": "DESKTOP"}})
            assert response.status_code == 409
            assert "Qorgau Browser" in response.json()["detail"]
        finally:
            agent.http.close()
