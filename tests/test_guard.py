from unittest.mock import patch
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.client import Agent, atomic_json
from agent.guard_policy import blocked_key
from backend.proctor.app import create_app


@pytest.mark.parametrize(
    "vk,modifiers",
    [
        (0x09, {"alt": True}),
        (0x5B, {}),
        (0x2C, {}),
        (0x43, {"ctrl": True}),
        (0x56, {"ctrl": True}),
        (0x1B, {"ctrl": True, "shift": True}),
        (0x09, {"ctrl": True}),
        (0x4C, {"ctrl": True}),
        (0x31, {"ctrl": True}),
        (0x7B, {}),
        (0x49, {"ctrl": True, "shift": True}),
        (0x2D, {"shift": True}),
    ],
)
def test_exam_shortcuts_are_blocked(vk, modifiers):
    assert blocked_key(vk, **modifiers)


def test_teacher_can_type_password_and_keyboard_navigate():
    for vk in (0x41, 0x39, 0x08, 0x09, 0x0D, 0xBD):
        assert not blocked_key(vk, locked=True)
    assert blocked_key(0x09, alt=True, locked=True)


@pytest.fixture
def live_agent(tmp_path):
    server = TestClient(
        create_app(tmp_path / "server"), headers={"X-Requested-With": "Qorgau"}
    )
    server.post(
        "/api/auth/setup", json={"name": "Teacher", "password": "teacher-password"}
    )
    code = server.post("/api/pairings", json={}).json()["code"]
    registered = server.post(
        "/api/agent/enroll", json={"code": code, "name": "PC"}
    ).json()
    executable = tmp_path / "exam-app"
    executable.touch()
    folder = tmp_path / "agent"
    atomic_json(
        folder / "config.json",
        {
            **registered,
            "server": "http://testserver",
            "targets": [
                {
                    "id": "exam-app",
                    "kind": "APP",
                    "name": "Exam",
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

    agent = Agent(folder, transport=httpx.MockTransport(forward))
    agent.sync()
    exam = server.post(
        "/api/exams",
        json={
            "title": "Exam",
            "group": "1",
            "room": "1",
            "require_camera": False,
            "device_ids": [registered["device_id"]],
            "environment": {"kind": "APP", "target_id": "exam-app"},
        },
    )
    assert exam.status_code == 200
    agent.sync()
    server.post(
        f"/api/devices/{registered['device_id']}/commands",
        json={"type": "START", "expected_version": 0},
    )
    with patch.object(agent, "launch_environment"):
        agent.sync()
    assert agent.engine.state.lifecycle == "RUNNING"
    agent.security_event("ENVIRONMENT_ATTEMPT")
    agent.sync()
    yield agent, server
    agent.http.close()
    server.close()


def test_password_unlock_uses_owner_and_preserves_evidence(live_agent):
    agent, server = live_agent
    assert agent.engine.state.access == "LOCKED"
    recent = list(agent.journal["recent_events"])
    with pytest.raises(ValueError, match="Неверный пароль"):
        agent.teacher_unlock("wrong-password")
    assert agent.engine.state.access == "LOCKED"
    agent.teacher_unlock("teacher-password")
    assert agent.engine.state.access == "OPEN" and agent.engine.state.epoch == 2
    assert agent.journal["recent_events"] == recent
    with server.app.state.db.connect() as c:
        audit = [r["body"] for r in c.execute("SELECT body FROM audit")]
        assert not any(
            "teacher-password" in row or "wrong-password" in row for row in audit
        )


def test_website_unlock_survives_a_review_command_in_same_delivery(live_agent):
    agent, server = live_agent
    device = server.get('/api/snapshot').json()['devices'][0]
    event = agent.journal['recent_events'][0]
    reviewed = server.post('/api/events/' + event['id'] + '/review', json={
        'decision': 'REJECTED', 'reason': 'Checked the recording', 'expected_revision': 0,
    })
    assert reviewed.status_code == 200
    sent = server.post('/api/devices/' + device['id'] + '/commands', json={
        'type': 'UNLOCK', 'reason': 'Allow continuation', 'lock_id': device['state']['lock_id'],
        'expected_version': device['state']['version'],
    })
    assert sent.status_code == 200
    agent.sync()  # REVIEW increments the local version before UNLOCK arrives.
    agent.sync()  # Server must accept the authorised release and its acknowledgement.
    fresh = server.get('/api/snapshot').json()
    assert agent.engine.state.access == fresh['devices'][0]['state']['access'] == 'OPEN'
    assert next(c for c in fresh['commands'] if c['id'] == sent.json()['id'])['status'] == 'APPLIED'


def test_website_unlock_never_applies_to_a_new_critical_lock(live_agent):
    agent, server = live_agent
    device = server.get('/api/snapshot').json()['devices'][0]
    response = server.post('/api/devices/' + device['id'] + '/commands', json={
        'type': 'UNLOCK', 'reason': 'Checked', 'lock_id': device['state']['lock_id'],
        'expected_version': device['state']['version'],
    })
    assert response.status_code == 200
    agent.engine.lock('PHONE_DETECTED')
    agent.sync()
    agent.sync()
    assert agent.engine.state.access == 'LOCKED'
    commands = server.get('/api/snapshot').json()['commands']
    assert next(c for c in commands if c['id'] == response.json()['id'])['status'] == 'REJECTED'


@pytest.mark.parametrize("change", ["review", "new_lock"])
def test_website_end_survives_in_flight_state_changes(live_agent, change):
    agent, server = live_agent
    device = server.get('/api/snapshot').json()['devices'][0]
    if change == "review":
        event = agent.journal['recent_events'][0]
        assert server.post('/api/events/' + event['id'] + '/review', json={
            'decision': 'REJECTED', 'reason': 'Checked', 'expected_revision': 0,
        }).status_code == 200
    response = server.post('/api/devices/' + device['id'] + '/commands', json={
        'type': 'END_AND_RELEASE', 'reason': 'Exam finished', 'exam_id': device['exam_id'],
        'expected_version': device['state']['version'],
    })
    assert response.status_code == 200
    if change == "new_lock":
        agent.engine.lock('PHONE_DETECTED')
    agent.sync()  # A review or the new lock can advance the version before END.
    agent.sync()  # Backend accepts the end acknowledgement against that new version.
    fresh = server.get('/api/snapshot').json()
    assert agent.engine.state.lifecycle == fresh['devices'][0]['state']['lifecycle'] == 'COMPLETED'
    assert agent.engine.state.access == fresh['devices'][0]['state']['access'] == 'OPEN'
    assert fresh['exams'][0]['status'] == 'COMPLETED'
    assert next(c for c in fresh['commands'] if c['id'] == response.json()['id'])['status'] == 'APPLIED'


@pytest.mark.parametrize("invalid", ["future", "other_exam", "expired"])
def test_end_still_rejects_future_or_unrelated_authorization(live_agent, invalid):
    agent, _ = live_agent
    command = {
        'id': 'invalid-end', 'type': 'END_AND_RELEASE',
        'exam_id': agent.journal['exam_id'], 'expected_version': agent.engine.state.version,
        'expires_at': time.time() + 30,
    }
    if invalid == 'future':
        command['expected_version'] += 1
    elif invalid == 'other_exam':
        command['exam_id'] = 'previous-exam'
    else:
        command['expires_at'] = time.time() - 1
    agent.apply(command)
    assert agent.engine.state.lifecycle == 'RUNNING'
    assert agent.journal['acks'][-1]['ok'] is False


def test_command_post_checks_exam_identity_after_snapshot(live_agent):
    agent, server = live_agent
    device = server.get('/api/snapshot').json()['devices'][0]
    response = server.post('/api/devices/' + device['id'] + '/commands', json={
        'type': 'END_AND_RELEASE', 'reason': 'End old exam', 'exam_id': 'previous-exam',
        'expected_version': device['state']['version'],
    })
    assert response.status_code == 409 and 'Сеанс компьютера изменился' in response.json()['detail']
    agent.sync()
    assert agent.engine.state.lifecycle == 'RUNNING'


def test_end_accepts_a_snapshot_before_a_new_incident_reaches_server(live_agent):
    agent, server = live_agent
    before = server.get('/api/snapshot').json()['devices'][0]
    agent.engine.lock('PHONE_DETECTED')
    agent.sync()
    response = server.post('/api/devices/' + before['id'] + '/commands', json={
        'type': 'END_AND_RELEASE', 'reason': 'Exam finished', 'exam_id': before['exam_id'],
        'expected_version': before['state']['version'],
    })
    assert response.status_code == 200
    agent.sync()
    agent.sync()
    assert server.get('/api/snapshot').json()['exams'][0]['status'] == 'COMPLETED'


def test_agent_cannot_end_without_a_teacher_command(live_agent):
    agent, server = live_agent
    forged = agent.engine.state.public()
    forged.update(lifecycle='COMPLETED', access='OPEN', lock_id=None,
                  reason=None, version=forged['version'] + 1)
    response = agent.http.post('/api/agent/sync', json={
        'exam_id': agent.journal['exam_id'], 'state': forged,
        'acknowledgements': [{'id': 'invented-command', 'ok': True}],
    })
    assert response.status_code == 409
    assert server.get('/api/snapshot').json()['devices'][0]['state']['lifecycle'] == 'RUNNING'


def test_agent_cannot_clear_a_lock_by_forging_heartbeat(live_agent):
    agent, _ = live_agent
    forged = agent.engine.state.public()
    forged.update(
        access="OPEN", lock_id=None, reason=None, version=forged["version"] + 1
    )
    response = agent.http.post(
        "/api/agent/sync", json={"exam_id": agent.journal["exam_id"], "state": forged}
    )
    assert response.status_code == 409


def test_unlock_is_rate_limited_and_stale_lock_is_rejected(live_agent):
    agent, server = live_agent
    state = agent.engine.state
    stale = {
        "password": "teacher-password",
        "exam_id": agent.journal["exam_id"],
        "lock_id": "stale",
        "expected_version": state.version,
    }
    assert agent.http.post("/api/agent/teacher-unlock", json=stale).status_code == 409
    for _ in range(5):
        with pytest.raises(ValueError, match="Неверный пароль"):
            agent.teacher_unlock("wrong-password")
    with pytest.raises(ValueError, match="Слишком много попыток"):
        agent.teacher_unlock("teacher-password")
    assert agent.engine.state.access == "LOCKED"


def test_password_does_not_override_phone_or_camera_fault(live_agent):
    agent, _ = live_agent
    agent.engine.phone_present = True
    with pytest.raises(ValueError, match="Причина блокировки"):
        agent.teacher_unlock("teacher-password")
    assert agent.engine.state.access == "LOCKED"
    agent.engine.phone_present = False
    agent.camera_fault = True
    with pytest.raises(ValueError, match="Причина блокировки"):
        agent.teacher_unlock("teacher-password")
    assert agent.engine.state.access == "LOCKED"


def test_security_event_is_durable_bounded_and_deduplicated(live_agent):
    agent, _ = live_agent
    agent.security_event("SERVER_UNAVAILABLE")
    agent.security_event("SERVER_UNAVAILABLE")
    assert (
        sum(e["type"] == "SERVER_UNAVAILABLE" for e in agent.journal["recent_events"])
        == 1
    )
    agent.sync()
    assert agent.journal["events"] == []
    assert any(
        e["type"] == "SERVER_UNAVAILABLE" for e in agent.snapshot()["recent_events"]
    )


def test_guarded_session_cannot_start_without_camera(tmp_path):
    atomic_json(
        tmp_path / "config.json", {"server": "http://localhost:8000", "token": "x"}
    )
    agent = Agent(tmp_path)
    agent.environment = {"guarded": True}
    agent.capabilities["window_guard"] = True
    agent.journal["exam_id"] = "exam"
    agent.apply(
        {
            "id": "start",
            "type": "START",
            "exam_id": "exam",
            "expires_at": time.time() + 30,
            "expected_version": 0,
        }
    )
    assert agent.engine.state.lifecycle == "READY"
    assert agent.journal["acks"][-1]["error"] == "CAMERA_REQUIRED"
    agent.http.close()


def test_browser_origin_policy():
    from agent.exam_browser import origin

    assert origin("https://exam.example/test") == origin(
        "https://EXAM.example:443/next"
    )
    assert origin("https://exam.example:8443") != origin("https://exam.example")
    for url in (
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:pass@exam.example",
        "https://exam.example:bad",
    ):
        assert origin(url) is None


def test_windows_api_window_identity_smoke():
    import os

    if os.name != "nt":
        pytest.skip("Real Win32 API is validated in Windows CI")
    from agent.windows_guard import WindowsGuard

    guard = WindowsGuard()
    windows = guard.windows()
    for target in windows:
        assert guard.valid(target)
    guard.stop()


def test_windows_hooks_can_start_and_release_on_a_test_window():
    import os

    if os.name != "nt" or os.getenv("QORGAU_TEST_INPUT_HOOKS") != "1":
        pytest.skip("Set QORGAU_TEST_INPUT_HOOKS=1 only on a disposable Windows test desktop")
    import ctypes as c
    from ctypes import wintypes as w
    from agent.windows_guard import WindowsGuard

    guard = WindowsGuard()
    create = guard.u.CreateWindowExW
    create.argtypes = [
        w.DWORD,
        w.LPCWSTR,
        w.LPCWSTR,
        w.DWORD,
        c.c_int,
        c.c_int,
        c.c_int,
        c.c_int,
        w.HWND,
        w.HMENU,
        w.HINSTANCE,
        w.LPVOID,
    ]
    create.restype = w.HWND
    guard.u.DestroyWindow.argtypes = [w.HWND]
    hwnd = create(
        0,
        "STATIC",
        "Qorgau CI test window",
        0x00CF0000,
        100,
        100,
        400,
        300,
        None,
        None,
        guard.k.GetModuleHandleW(None),
        None,
    )
    assert hwnd
    try:
        target = guard.info(hwnd)
        assert target and guard.valid(target)
        guard.start(target)
        assert len(guard.hooks) == 2
        assert guard.allowed(hwnd)
        guard.stop()
        assert guard.hooks == [] and guard.target is None
        guard.start(desktop=True)
        assert len(guard.hooks) == 2 and guard.target is None
        assert guard.allowed(hwnd)
        guard.stop()
        assert not guard.hooks and not guard.desktop
    finally:
        guard.stop()
        guard.u.DestroyWindow(hwnd)


def test_teacher_can_end_on_the_student_computer_even_with_camera_fault(live_agent):
    agent, server = live_agent
    agent.camera_fault = True
    with pytest.raises(ValueError, match="Неверный пароль"):
        agent.teacher_unlock("wrong-password", "END_AND_RELEASE")
    assert agent.engine.state.lifecycle == "RUNNING"
    agent.teacher_unlock("teacher-password", "END_AND_RELEASE")
    assert agent.engine.state.lifecycle == "COMPLETED"
    assert agent.engine.state.access == "OPEN"
    device = server.get("/api/snapshot").json()["devices"][0]
    assert device["state"]["lifecycle"] == "COMPLETED"
    assert any(e["type"] == "ENVIRONMENT_ATTEMPT" for e in agent.snapshot()["recent_events"])


def test_forged_completion_and_stale_release_cannot_clear_lock(live_agent):
    agent, server = live_agent
    forged = agent.engine.state.public()
    forged.update(access="OPEN", lifecycle="COMPLETED", lock_id=None, reason=None, version=forged["version"] + 1)
    response = agent.http.post("/api/agent/sync", json={"exam_id": agent.journal["exam_id"], "state": forged})
    assert response.status_code == 409
    assert server.get("/api/snapshot").json()["devices"][0]["state"]["access"] == "LOCKED"


def test_retry_after_lost_release_ack_is_idempotent(live_agent):
    agent, _ = live_agent
    agent.teacher_unlock("teacher-password")
    payload = {"exam_id": agent.journal["exam_id"], "state": agent.engine.state.public(), "acknowledgements": list(agent.journal["acks"])}
    assert agent.http.post("/api/agent/sync", json=payload).status_code == 200
    assert agent.http.post("/api/agent/sync", json=payload).status_code == 200
    agent.sync()
    assert agent.engine.state.epoch == 2


def test_prepared_camera_survives_first_and_next_assignment(tmp_path):
    from unittest.mock import Mock
    atomic_json(tmp_path / "config.json", {"server": "http://testserver", "token": "fixture"})
    assignments = iter(["one", "two"])
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"exam_id": next(assignments), "environment": {}, "commands": []}))
    agent = Agent(tmp_path, transport=transport)
    for _ in range(2):
        camera, recorder = Mock(), Mock()
        recorder.completed.return_value = []
        agent.camera, agent.recorder = camera, recorder
        agent.capabilities.update(camera=True, recording=True)
        agent.sync()
        assert agent.camera is camera and agent.capabilities["camera"]
        camera.close.assert_not_called()
        recorder.reset_session.assert_called_once_with(agent.journal["exam_id"])
        agent.engine.start()
        agent.engine.end()
    agent.http.close()
