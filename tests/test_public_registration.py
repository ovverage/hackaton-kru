import json
import secrets
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from agent.client import Agent, atomic_json
from agent.provision import auto_enroll, bootstrap_data_dir, EnrollmentUnavailable
from backend.proctor.app import create_app
from shared.bootstrap import default_bootstrap, validate_bootstrap
from shared.rules import State


@pytest.fixture
def public_server(tmp_path, monkeypatch):
    monkeypatch.setenv("PROCTOR_PUBLIC_ENROLLMENT_OWNER", "Teacher")
    monkeypatch.setenv("PROCTOR_PUBLIC_ENROLLMENT_LIMIT", "50")
    with TestClient(create_app(tmp_path / "server")) as client:
        client.headers["X-Requested-With"] = "Qorgau"
        client.post(
            "/api/auth/setup", json={"name": "Teacher", "password": "test-password-123"}
        ).raise_for_status()
        yield client


def transport_for(client):
    def request(req):
        response = client.request(
            req.method,
            req.url.raw_path.decode(),
            content=req.content,
            headers=dict(req.headers),
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return httpx.MockTransport(request)


def test_plain_client_registers_appears_in_teacher_list_and_reuses_identity(
    public_server, tmp_path
):
    bootstrap = default_bootstrap()
    assert validate_bootstrap(bootstrap) == bootstrap
    assert "token" not in bootstrap and "package_id" not in bootstrap
    transport = transport_for(public_server)
    configs = [
        auto_enroll(tmp_path / name, bootstrap, transport=transport)
        for name in ["pc1", "pc2"]
    ]
    assert configs[0]["token"] != configs[1]["token"]
    assert auto_enroll(tmp_path / "pc1", bootstrap) == configs[0]
    agent = Agent(tmp_path / "pc1", transport=transport)
    agent.sync()
    agent.http.close()
    devices = public_server.get("/api/snapshot").json()["devices"]
    assert len(devices) == 2 and all(d["room"] == "Новые компьютеры" for d in devices)
    assert all(
        not d["simulated"] and d["state"]["lifecycle"] == "READY" for d in devices
    )
    assert public_server.get("/api/student-packages").json()["packages"] == []
    public_server.cookies.clear()
    assert (
        public_server.get(
            "/api/snapshot", headers={"Authorization": "Bearer " + configs[0]["token"]}
        ).status_code
        == 401
    )


def test_lost_response_reuses_identity_and_revocation_cannot_be_bypassed(
    public_server, tmp_path
):
    bootstrap = default_bootstrap()
    original = transport_for(public_server)
    responses = []

    def drop(req):
        responses.append(original.handle_request(req).json())
        raise httpx.ReadTimeout("lost response", request=req)

    with pytest.raises(EnrollmentUnavailable):
        auto_enroll(tmp_path / "pc", bootstrap, transport=httpx.MockTransport(drop))
    config = auto_enroll(tmp_path / "pc", bootstrap, transport=original)
    assert config["device_id"] == responses[0]["device_id"]
    assert config["token"] == responses[0]["token"]
    assert len(public_server.get("/api/snapshot").json()["devices"]) == 1
    public_server.post(
        f"/api/devices/{config['device_id']}/revoke", json={"reason": "Test revocation"}
    ).raise_for_status()
    (tmp_path / "pc/config.json").unlink()
    with pytest.raises(ValueError, match="отозван"):
        auto_enroll(tmp_path / "pc", bootstrap, transport=original)
    assert (
        public_server.post(
            "/api/agent/sync",
            headers={"Authorization": "Bearer " + config["token"]},
            json={"state": State().public()},
        ).status_code
        == 401
    )


def test_registration_defaults_to_disabled_and_needs_existing_owner(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("PROCTOR_PUBLIC_ENROLLMENT_OWNER", raising=False)
    payload = {"installation_secret": secrets.token_urlsafe(32), "name": "PC"}
    with TestClient(create_app(tmp_path / "disabled")) as client:
        assert client.post("/api/agent/register", json=payload).status_code == 503
    monkeypatch.setenv("PROCTOR_PUBLIC_ENROLLMENT_OWNER", "Missing teacher")
    with TestClient(create_app(tmp_path / "missing")) as client:
        assert client.post("/api/agent/register", json=payload).status_code == 503


def test_shared_nat_can_register_50_students_but_capacity_is_atomic(public_server):
    def register(_):
        return public_server.post(
            "/api/agent/register",
            json={"installation_secret": secrets.token_urlsafe(32), "name": "PC"},
        ).status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(register, range(55)))
    assert statuses.count(200) == 50 and statuses.count(503) == 5
    assert len(public_server.get("/api/snapshot").json()["devices"]) == 50


def test_registration_rate_limit_and_validations(public_server):
    payload = {"installation_secret": secrets.token_urlsafe(32), "name": "PC"}
    registration = public_server.post("/api/agent/register", json=payload).json()
    with public_server.app.state.db.connect(True) as db:
        db.execute("UPDATE public_registration_attempts SET count=60")
    assert (
        public_server.post("/api/agent/register", json=payload).json() == registration
    )
    assert (
        public_server.post(
            "/api/agent/register",
            json={**payload, "installation_secret": secrets.token_urlsafe(32)},
        ).status_code
        == 429
    )
    assert (
        public_server.post(
            "/api/agent/register", json={**payload, "installation_secret": "guess"}
        ).status_code
        == 422
    )
    assert (
        public_server.post(
            "/api/agent/register", json={**payload, "name": "   "}
        ).status_code
        == 422
    )


def test_public_download_is_the_unmodified_executable(
    public_server, tmp_path, monkeypatch
):
    binary = tmp_path / "student.exe"
    binary.write_bytes(b"MZ-official-student-executable")
    monkeypatch.setenv("PROCTOR_STUDENT_EXE", str(binary))
    public_server.cookies.clear()
    response = public_server.get("/api/student/download")
    assert response.status_code == 200 and response.content == binary.read_bytes()
    assert "Qorgau-Student.exe" in response.headers["content-disposition"]


def test_default_profile_is_stable_and_upgrades_existing_manual_registration(tmp_path):
    bootstrap = default_bootstrap()
    folder = bootstrap_data_dir(bootstrap, tmp_path)
    assert folder == bootstrap_data_dir(default_bootstrap(), tmp_path)
    legacy = tmp_path / ".qorgau/config.json"
    atomic_json(
        legacy,
        {"server": bootstrap["server"], "device_id": "previous", "token": "previous"},
    )
    assert bootstrap_data_dir(bootstrap, tmp_path) == legacy.parent
    config = json.loads(legacy.read_text())
    atomic_json(legacy, {**config, "server": "https://another.example"})
    assert bootstrap_data_dir(bootstrap, tmp_path) == folder
