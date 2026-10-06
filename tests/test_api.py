import pytest
from fastapi.testclient import TestClient

from backend.proctor.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, allow_demo=True)) as client:
        client.headers["X-Requested-With"] = "Qorgau"
        assert (
            client.post(
                "/api/auth/setup",
                json={"name": "Преподаватель", "password": "test-password-123"},
            ).status_code
            == 200
        )
        yield client


def snapshot(client):
    return client.get("/api/snapshot").json()


def prepare(client):
    assert client.post("/api/demo/devices", json={}).status_code == 200
    devices = snapshot(client)["devices"]
    r = client.post(
        "/api/exams",
        json={
            "title": "Проверка",
            "group": "ИС-23",
            "room": "301",
            "device_ids": [d["id"] for d in devices],
            "environment": {
                "kind": "BROWSER",
                "target_id": "chrome",
                "url": "https://example.com/test",
            },
        },
    )
    assert r.status_code == 200, r.text
    return snapshot(client)["devices"]


def cmd(client, d, kind, **kwargs):
    return client.post(
        "/api/devices/" + d["id"] + "/commands",
        json={
            "type": kind,
            "expected_version": d["state"]["version"],
            "lock_id": d["state"]["lock_id"],
            "reason": "Проверено преподавателем",
            **kwargs,
        },
    )


def test_complete_teacher_flow(client):
    devices = prepare(client)
    d = devices[0]
    assert cmd(client, d, "START").status_code == 200
    for _ in range(3):
        assert (
            client.post(
                "/api/devices/" + d["id"] + "/simulate", json={"scenario": "DOWN"}
            ).status_code
            == 200
        )
    snap = snapshot(client)
    current = next(x for x in snap["devices"] if x["id"] == d["id"])
    assert current["state"]["access"] == "LOCKED"
    assert all(
        x["state"]["access"] == "OPEN" for x in snap["devices"] if x["id"] != d["id"]
    )
    event = snap["events"][0]
    assert (
        client.post(
            "/api/events/" + event["id"] + "/review",
            json={
                "decision": "REJECTED",
                "expected_revision": 0,
                "reason": "Ложное срабатывание",
            },
        ).status_code
        == 200
    )
    assert cmd(client, current, "UNLOCK").status_code == 409  # stale state version
    current = next(x for x in snapshot(client)["devices"] if x["id"] == d["id"])
    assert (
        current["state"]["access"] == "LOCKED"
        and current["state"]["counts"]["DOWN"] == 2
    )
    assert cmd(client, current, "UNLOCK").status_code == 200
    current = next(x for x in snapshot(client)["devices"] if x["id"] == d["id"])
    assert current["state"]["counts"]["DOWN"] == 0
    assert cmd(client, current, "END_AND_RELEASE").status_code == 200
    current = next(x for x in snapshot(client)["devices"] if x["id"] == d["id"])
    assert cmd(client, current, "LOCK").status_code == 409
    report = client.get("/api/exams/" + current["exam_id"] + "/report.csv")
    assert report.status_code == 200 and "Алина" in report.text


def test_auth_csrf_and_single_use_pairing(client):
    cookie = client.cookies.get("qorgau_session")
    client.cookies.clear()
    assert client.get("/api/snapshot").status_code == 401
    client.cookies.set("qorgau_session", cookie)
    assert (
        client.post(
            "/api/demo/devices", json={}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    code = client.post("/api/pairings", json={}).json()["code"]
    enrollment = client.post(
        "/api/agent/enroll", json={"code": code, "name": "Test-PC"}
    )
    assert enrollment.status_code == 200
    assert (
        client.post(
            "/api/agent/enroll", json={"code": code, "name": "Test-PC"}
        ).status_code
        == 403
    )
    token = enrollment.json()["token"]
    r = client.post(
        "/api/agent/sync",
        json={"state": {}, "targets": [], "capabilities": {"strict": True}},
        headers={"Authorization": "Bearer " + token},
    )
    assert r.status_code == 200
    assert snapshot(client)["devices"][0]["capabilities"]["strict"] is False


def test_no_strict_mode_without_verified_os_protection(client):
    client.post("/api/demo/devices", json={})
    d = snapshot(client)["devices"][0]
    r = client.post(
        "/api/exams",
        json={
            "title": "Strict",
            "group": "A",
            "room": "1",
            "device_ids": [d["id"]],
            "mode": "STRICT",
            "environment": {"kind": "APP", "target_id": "exam-app"},
        },
    )
    assert r.status_code == 409


def test_review_revision_conflict(client):
    d = prepare(client)[0]
    cmd(client, d, "START")
    client.post(
        "/api/devices/" + d["id"] + "/simulate", json={"scenario": "SECOND_FACE"}
    )
    e = snapshot(client)["events"][0]
    body = {"decision": "CONFIRMED", "expected_revision": 0, "reason": "Проверено"}
    assert (
        client.post("/api/events/" + e["id"] + "/review", json=body).status_code == 200
    )
    assert (
        client.post("/api/events/" + e["id"] + "/review", json=body).status_code == 409
    )


def test_media_validation_idempotency_and_authorization(client):
    d = prepare(client)[0]
    cmd(client, d, "START")
    client.post("/api/devices/" + d["id"] + "/simulate", json={"scenario": "PHONE"})
    ev = snapshot(client)["events"][0]
    path = "/api/events/" + ev["id"] + "/demo-video"
    assert (
        client.post(
            path, content=b"not a video file", headers={"Content-Type": "video/mp4"}
        ).status_code
        == 422
    )
    fixture = (
        b"\x00\x00\x00\x18ftypisom" + b"\x00" * 30
    )  # container signature fixture, not a playable video
    r = client.post(path, content=fixture, headers={"Content-Type": "video/mp4"})
    assert r.status_code == 200
    assert (
        client.post(
            path, content=fixture, headers={"Content-Type": "video/mp4"}
        ).json()["id"]
        == r.json()["id"]
    )
    url = "/api/media/" + r.json()["id"]
    assert client.get(url).status_code == 200
    # Static images can produce identical bytes in adjacent parts of one incident.
    # Only the same bytes AND interval are a retry; the other interval must survive.
    next_part = client.post(path, content=fixture, headers={"Content-Type": "video/mp4", "X-Clip-Start": "30", "X-Clip-End": "33"})
    assert next_part.status_code == 200 and next_part.json()["id"] != r.json()["id"]
    assert len(next(e for e in snapshot(client)["events"] if e["id"] == ev["id"])["media"]) == 2
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_media_low_disk_rejects_upload_without_losing_event_and_allows_retry(client, monkeypatch):
    from types import SimpleNamespace

    d = prepare(client)[0]
    assert cmd(client, d, "START").status_code == 200
    client.post("/api/devices/" + d["id"] + "/simulate", json={"scenario": "PHONE"})
    event = snapshot(client)["events"][0]
    path = "/api/events/" + event["id"] + "/demo-video"
    content = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 30
    media_dir = client.app.state.db.path.parent / "media"
    with monkeypatch.context() as patch:
        patch.setattr("shutil.disk_usage", lambda _: SimpleNamespace(free=0))
        response = client.post(path, content=content, headers={"Content-Type": "video/mp4"})
    assert response.status_code == 507
    assert list(media_dir.iterdir()) == []
    saved = next(e for e in snapshot(client)["events"] if e["id"] == event["id"])
    assert saved["media"] == []
    response = client.post(path, content=content, headers={"Content-Type": "video/mp4"})
    assert response.status_code == 200
    assert len(list(media_dir.iterdir())) == 1


def test_agent_state_validation_and_cross_device_event_collision(client):
    tokens = []
    ids = []
    for n in range(2):
        code = client.post("/api/pairings", json={}).json()["code"]
        r = client.post(
            "/api/agent/enroll", json={"code": code, "name": f"ПК-{n}"}
        ).json()
        tokens.append({"Authorization": "Bearer " + r["token"]})
        ids.append(r["device_id"])
        assert (
            client.post(
                "/api/agent/sync",
                json={
                    "state": {},
                    "targets": [{"id": "app", "kind": "APP", "name": "Test"}],
                },
                headers=tokens[-1],
            ).status_code
            == 200
        )
    ex = client.post(
        "/api/exams",
        json={
            "title": "Тест",
            "group": "A",
            "room": "1",
            "device_ids": ids,
            "environment": {"kind": "APP", "target_id": "app"},
        },
    ).json()
    payload = {"exam_id": ex["id"], "state": {"version": "oops"}}
    assert (
        client.post("/api/agent/sync", json=payload, headers=tokens[0]).status_code
        == 422
    )
    payload = {
        "exam_id": ex["id"],
        "state": {},
        "events": [
            {"id": "unique-event", "type": "SECOND_FACE_REVIEW", "at": 1, "start": 0}
        ],
    }
    assert (
        client.post("/api/agent/sync", json=payload, headers=tokens[0]).status_code
        == 200
    )
    assert (
        client.post("/api/agent/sync", json=payload, headers=tokens[0]).status_code
        == 200
    )
    assert (
        client.post("/api/agent/sync", json=payload, headers=tokens[1]).status_code
        == 409
    )


def test_workstation_identity_and_pupils_are_separate_between_exams(client):
    code = client.post("/api/pairings", json={}).json()["code"]
    enrolled = client.post(
        "/api/agent/enroll", json={"code": code, "name": "ПК-01"}
    ).json()
    headers = {"Authorization": "Bearer " + enrolled["token"]}
    identity = enrolled["device_id"]
    client.post(
        "/api/agent/sync",
        headers=headers,
        json={"state": {}, "targets": [{"id": "app", "kind": "APP", "name": "Test"}]},
    )
    body = {
        "title": "Тест",
        "group": "A",
        "room": "301",
        "device_ids": [identity],
        "environment": {"kind": "APP", "target_id": "app"},
        "student_names": {identity: "Алина"},
    }
    first = client.post("/api/exams", json=body).json()
    response = client.post(
        "/api/agent/sync",
        headers=headers,
        json={
            "state": {},
            "exam_id": first["id"],
            "targets": [{"id": "app", "kind": "APP", "name": "Test"}],
        },
    ).json()
    assert response["session"]["student"] == "Алина"
    assert first["participants"][identity]["name"] == "ПК-01"
    end = client.post(
        f"/api/devices/{identity}/commands",
        json={
            "type": "END_AND_RELEASE",
            "expected_version": 0,
            "reason": "Группа завершила тест",
        },
    ).json()
    client.post(
        "/api/agent/sync",
        headers=headers,
        json={
            "exam_id": first["id"],
            "state": {"lifecycle": "COMPLETED", "version": 1},
            "acknowledgements": [{"id": end["id"], "ok": True}],
            "targets": [{"id": "app", "kind": "APP", "name": "Test"}],
        },
    )
    body["student_names"] = {identity: "Данияр"}
    second = client.post("/api/exams", json=body).json()
    assert second["participants"][identity]["student"] == "Данияр"
    assert "Алина" in client.get(f"/api/exams/{first['id']}/report.csv").text
    assert "Данияр" not in client.get(f"/api/exams/{first['id']}/report.csv").text
    end_second = client.post(
        f"/api/devices/{identity}/commands",
        json={
            "type": "END_AND_RELEASE",
            "expected_version": 0,
            "reason": "Следующая группа завершила тест",
        },
    ).json()
    client.post(
        "/api/agent/sync",
        headers=headers,
        json={
            "exam_id": second["id"],
            "state": {"lifecycle": "COMPLETED", "version": 1},
            "acknowledgements": [{"id": end_second["id"], "ok": True}],
            "targets": [{"id": "app", "kind": "APP", "name": "Test"}],
        },
    )
    body.pop("student_names")
    third = client.post("/api/exams", json=body).json()
    assert third["participants"][identity]["student"] == "ПК-01"
    assert snapshot(client)["devices"][0]["id"] == identity


def test_assignment_rejects_names_for_unselected_computers(client):
    client.post("/api/demo/devices", json={})
    d = snapshot(client)["devices"][0]
    body = {
        "title": "Тест",
        "group": "A",
        "room": "1",
        "device_ids": [d["id"]],
        "environment": {"kind": "APP", "target_id": "exam-app"},
        "student_names": {"other-pc": "Ученик"},
    }
    assert client.post("/api/exams", json=body).status_code == 422
