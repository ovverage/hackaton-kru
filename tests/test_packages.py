import json
import secrets
import struct
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.client import Agent
from agent.provision import EnrollmentUnavailable, auto_enroll, bootstrap_data_dir
from backend.proctor.app import create_app
from shared.bootstrap import MAGIC, bootstrap_trailer, read_bootstrap


@pytest.fixture
def client(tmp_path, monkeypatch):
    template = tmp_path / "template.exe"
    template.write_bytes(b"MZ" + b"template" * 100)
    monkeypatch.setenv("PROCTOR_STUDENT_EXE", str(template))
    monkeypatch.setenv("PROCTOR_PUBLIC_URL", "https://qorgau.example.kz")
    with TestClient(create_app(tmp_path / "server")) as client:
        client.headers["X-Requested-With"] = "Qorgau"
        assert (
            client.post(
                "/api/auth/setup",
                json={"name": "Teacher", "password": "test-password-123"},
            ).status_code
            == 200
        )
        yield client


def package(client, tmp_path, **overrides):
    response = client.post(
        "/api/student-packages",
        json={
            "room": "301",
            "server": "https://qorgau.example.kz",
            "max_devices": 50,
            **overrides,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    response = client.get(result["download_path"])
    assert response.status_code == 200
    assert int(response.headers["content-length"]) == len(response.content)
    assert "attachment" in response.headers["content-disposition"]
    path = tmp_path / "classroom.exe"
    path.write_bytes(response.content)
    return result, read_bootstrap(path)


def transport_for(client):
    def dispatch(request):
        response = client.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return httpx.MockTransport(dispatch)


def test_revoked_device_cannot_sync_or_recover_old_token(client, tmp_path):
    _package, bootstrap = package(client, tmp_path)
    payload = {"token": bootstrap["token"], "installation_secret": "a" * 43, "name": "PC"}
    registered = client.post("/api/agent/auto-enroll", json=payload)
    assert registered.status_code == 200
    device = registered.json()
    response = client.post(f"/api/devices/{device['device_id']}/revoke", json={"reason": "Вывод компьютера из аудитории"})
    assert response.status_code == 200
    from shared.rules import State
    assert client.post("/api/agent/sync", headers={"Authorization": "Bearer " + device['token']}, json={"state": State().public()}).status_code == 401
    assert client.post("/api/agent/auto-enroll", json=payload).status_code == 403


def test_download_enroll_two_computers_relaunch_and_sync(client, tmp_path):
    created, bootstrap = package(client, tmp_path)
    transport = transport_for(client)
    first = auto_enroll(tmp_path / "pc1", bootstrap, transport=transport)
    second = auto_enroll(tmp_path / "pc2", bootstrap, transport=transport)
    assert (
        first["token"] != second["token"] and first["device_id"] != second["device_id"]
    )
    assert auto_enroll(tmp_path / "pc1", bootstrap) == first  # no network required
    devices = client.get("/api/snapshot").json()["devices"]
    assert len(devices) == 2 and all(d["room"] == "301" for d in devices)
    assert all(d["name"].startswith("301 · ") for d in devices)
    for folder in ("pc1", "pc2"):
        agent = Agent(tmp_path / folder, transport=transport)
        agent.sync()
        agent.http.close()
    listing = client.get("/api/student-packages").json()
    assert listing["packages"][0]["used_devices"] == 2
    assert bootstrap["token"] not in json.dumps(listing)
    assert bootstrap["token"] not in client.get("/api/snapshot").text
    assert created["download_url"].startswith("https://qorgau.example.kz/")


def test_lost_response_retries_same_registration_after_revocation(client, tmp_path):
    created, bootstrap = package(client, tmp_path, max_devices=1)
    transport = transport_for(client)
    responses = []

    def lose_response(request):
        responses.append(transport.handle_request(request))
        raise httpx.ReadTimeout("lost", request=request)

    folder = tmp_path / "student"
    with pytest.raises(EnrollmentUnavailable):
        auto_enroll(folder, bootstrap, transport=httpx.MockTransport(lose_response))
    assert not (folder / "config.json").exists()
    assert (folder / "installation.json").exists()
    assert (
        client.post(
            f"/api/student-packages/{created['id']}/revoke", json={}
        ).status_code
        == 200
    )
    config = auto_enroll(folder, bootstrap, transport=transport)
    assert config["device_id"] == responses[0].json()["device_id"]
    assert config["token"] == responses[0].json()["token"]
    assert (
        client.get("/api/student-packages").json()["packages"][0]["used_devices"] == 1
    )
    with pytest.raises(ValueError, match="отключён"):
        auto_enroll(tmp_path / "new-student", bootstrap, transport=transport)
    assert client.get(created["download_path"]).status_code == 403
    agent = Agent(folder, transport=transport)
    agent.sync()  # revoking the installer never disconnects existing devices
    agent.http.close()


def test_concurrent_enrollment_cannot_exceed_package_capacity(client, tmp_path):
    _, bootstrap = package(client, tmp_path, max_devices=2)

    def enroll_one(_):
        return client.post(
            "/api/agent/auto-enroll",
            json={
                "token": bootstrap["token"],
                "installation_secret": secrets.token_urlsafe(32),
                "name": "PC",
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(enroll_one, range(8)))
    assert statuses.count(200) == 2 and statuses.count(403) == 6
    assert len(client.get("/api/snapshot").json()["devices"]) == 2


def test_expiry_auth_and_teacher_ownership(client, tmp_path):
    created, bootstrap = package(client, tmp_path)
    with client.app.state.db.connect(True) as c:
        row = c.execute(
            "SELECT body FROM student_packages WHERE id=?", (created["id"],)
        ).fetchone()
        body = json.loads(row["body"])
        body["expires_at"] = time.time() - 1
        c.execute(
            "UPDATE student_packages SET body=? WHERE id=?",
            (json.dumps(body), created["id"]),
        )
        c.execute(
            "UPDATE student_packages SET owner='another-teacher' WHERE id=?",
            (created["id"],),
        )
    assert (
        client.post(
            f"/api/student-packages/{created['id']}/revoke", json={}
        ).status_code
        == 404
    )
    assert client.get("/api/student-packages").json()["packages"] == []
    client.cookies.clear()
    assert client.get("/api/student-packages").status_code == 401
    assert (
        client.post(
            "/api/student-packages",
            json={"room": "301", "server": "https://example.com"},
        ).status_code
        == 401
    )
    assert client.get(created["download_path"]).status_code == 403
    with pytest.raises(ValueError, match="истёк"):
        auto_enroll(tmp_path / "new", bootstrap, transport=transport_for(client))


def test_download_needs_no_teacher_login_and_missing_template_is_reported(
    client, tmp_path, monkeypatch
):
    created, _ = package(client, tmp_path)
    cookie = client.cookies.get("qorgau_session")
    client.cookies.clear()
    assert client.get(created["download_path"]).status_code == 200
    assert client.get("/api/student-download/invalid").status_code == 404
    client.cookies.set("qorgau_session", cookie)
    monkeypatch.setenv("PROCTOR_STUDENT_EXE", str(tmp_path / "missing.exe"))
    assert not client.get("/api/student-packages").json()["ready"]
    assert (
        client.post(
            "/api/student-packages",
            json={"room": "301", "server": "https://example.com"},
        ).status_code
        == 503
    )
    assert client.get(created["download_path"]).status_code == 503


def test_package_validation_and_scope(client, tmp_path):
    for overrides in (
        {"server": "http://192.168.1.2"},
        {"server": "https://example.com/api"},
        {"max_devices": 0},
        {"max_devices": 101},
        {"room": " "},
        {"expires_hours": 169},
    ):
        assert (
            client.post(
                "/api/student-packages",
                json={"room": "301", "server": "https://example.com", **overrides},
            ).status_code
            == 422
        )
    _, bootstrap = package(client, tmp_path)
    folder = bootstrap_data_dir(bootstrap, tmp_path)
    assert folder == bootstrap_data_dir(
        {**bootstrap, "package_id": "new-package"}, tmp_path
    )
    assert folder != bootstrap_data_dir(
        {**bootstrap, "account_id": "another"}, tmp_path
    )
    auto_enroll(folder, bootstrap, transport=transport_for(client))
    with pytest.raises(ValueError, match="другому кабинету"):
        auto_enroll(folder, {**bootstrap, "account_id": "another"})


def test_executable_trailer_rejects_corruption(tmp_path):
    path = tmp_path / "test.exe"
    path.write_bytes(b"MZ" + b"X" * 100)
    assert read_bootstrap(path) is None
    path.write_bytes(b"MZ" + struct.pack("<I", 100000) + MAGIC)
    with pytest.raises(ValueError, match="Повреждены"):
        read_bootstrap(path)
    path.write_bytes(b"MZ" + b"bad" + struct.pack("<I", 3) + MAGIC)
    with pytest.raises(ValueError, match="Повреждены"):
        read_bootstrap(path)
    with pytest.raises(ValueError):
        bootstrap_trailer({"version": 2})
