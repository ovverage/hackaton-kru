import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.proctor import app as app_module
from backend.proctor.app import create_app, digest


OLD_PASSWORD = "initial-password-123"
NEW_PASSWORD = "admin"


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(app_module, "time", SimpleNamespace(time=lambda: clock[0]))
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.headers["X-Requested-With"] = "Qorgau"
        response = client.post("/api/auth/setup", json={"name": "admin", "password": OLD_PASSWORD})
        assert response.status_code == 200
        yield SimpleNamespace(app=app, client=client, db=app.state.db, clock=clock, user=response.json()["user"])


def change(client, current=OLD_PASSWORD, new=NEW_PASSWORD):
    return client.post("/api/auth/password", json={"current_password": current, "new_password": new})


def test_setup_still_requires_eight_characters_and_login_checks_short_password(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        client.headers["X-Requested-With"] = "Qorgau"
        assert client.post("/api/auth/setup", json={"name": "admin", "password": "admin"}).status_code == 422
        assert client.get("/api/auth/status").json()["setup_required"] is True
        assert client.post("/api/auth/login", json={"name": "admin", "password": "admin"}).status_code == 401


def test_change_requires_authenticated_session_and_csrf(env):
    with TestClient(env.app) as stranger:
        stranger.headers["X-Requested-With"] = "Qorgau"
        assert change(stranger).status_code == 401
        stranger.cookies.update(env.client.cookies)
        del stranger.headers["X-Requested-With"]
        assert change(stranger).status_code == 403
        stranger.headers["X-Requested-With"] = "Qorgau"
        stranger.headers["Origin"] = "https://untrusted.example"
        assert change(stranger).status_code == 403
    assert env.client.get("/api/auth/status").json()["user"] == env.user
    with env.db.connect(True) as c:
        c.execute("UPDATE logins SET expires=?", (env.clock[0] - 1,))
    assert change(env.client).status_code == 401


@pytest.mark.parametrize("field,value", [
    ("current_password", ""), ("current_password", "x" * 129),
    ("new_password", "1234"), ("new_password", "x" * 129),
])
def test_change_validates_password_bounds(env, field, value):
    body = {"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD, field: value}
    assert env.client.post("/api/auth/password", json=body).status_code == 422


def test_wrong_current_password_preserves_password_session_and_audit(env):
    old_token = env.client.cookies.get("qorgau_session")
    with env.db.connect() as c:
        old_hash = c.execute("SELECT password FROM users WHERE id=?", (env.user["id"],)).fetchone()[0]
    response = change(env.client, current="incorrect")
    assert response.status_code == 403
    assert env.client.cookies.get("qorgau_session") == old_token
    assert env.client.get("/api/snapshot").status_code == 200
    with env.db.connect() as c:
        assert c.execute("SELECT password FROM users WHERE id=?", (env.user["id"],)).fetchone()[0] == old_hash
        assert c.execute("SELECT count(*) FROM audit WHERE action='PASSWORD_CHANGED'").fetchone()[0] == 0


def test_change_rotates_all_sessions_preserves_ownership_and_logs_no_secrets(env):
    original_token = env.client.cookies.get("qorgau_session")
    with TestClient(env.app) as second:
        second.headers["X-Requested-With"] = "Qorgau"
        assert second.post("/api/auth/login", json={"name": "admin", "password": OLD_PASSWORD}).status_code == 200
        second_token = second.cookies.get("qorgau_session")
        with env.db.connect(True) as c:
            c.execute("INSERT INTO devices VALUES(?,?,?,?)", ("device", env.user["id"], "device-token", "{}"))
            c.execute("INSERT INTO exams VALUES(?,?,?)", ("exam", env.user["id"], "{}"))
            c.execute("INSERT INTO teacher_faces VALUES(?,?,?,?,?,?)", ("face", env.user["id"], "Teacher", "[1,0]", "model", 1000))
            c.execute("INSERT INTO users VALUES(?,?,?)", ("other", "Other", app_module.PH.hash("another-password")))
            c.execute("INSERT INTO logins VALUES(?,?,?)", (digest("other-session"), "other", env.clock[0] + 100))
            related = {table: [tuple(row) for row in c.execute("SELECT * FROM " + table)] for table in ("devices", "exams", "teacher_faces")}
        response = change(env.client)
        assert response.status_code == 200, response.text
        assert response.json() == {"user": env.user}
        replacement = env.client.cookies.get("qorgau_session")
        assert replacement not in (original_token, second_token)
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite=strict" in response.headers["set-cookie"].lower()
        assert second.get("/api/snapshot").status_code == 401
        with env.db.connect() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (env.user["id"],)).fetchone()
            assert row["name"] == "admin"
            assert row["password"] != NEW_PASSWORD
            assert app_module.PH.verify(row["password"], NEW_PASSWORD)
            own_tokens = [r[0] for r in c.execute("SELECT token FROM logins WHERE user_id=?", (env.user["id"],))]
            assert own_tokens == [digest(replacement)]
            assert c.execute("SELECT token FROM logins WHERE user_id='other'").fetchone()[0] == digest("other-session")
            assert related == {table: [tuple(row) for row in c.execute("SELECT * FROM " + table)] for table in related}
            audit = c.execute("SELECT actor,body FROM audit WHERE action='PASSWORD_CHANGED'").fetchall()
            assert len(audit) == 1 and audit[0]["actor"] == env.user["id"]
            assert json.loads(audit[0]["body"]) == {}
            assert OLD_PASSWORD not in audit[0]["body"] and NEW_PASSWORD not in audit[0]["body"]
        assert env.client.get("/api/auth/status").json()["user"] == env.user
        assert second.post("/api/auth/login", json={"name": "admin", "password": OLD_PASSWORD}).status_code == 401
        assert second.post("/api/auth/login", json={"name": "admin", "password": NEW_PASSWORD}).status_code == 200


def test_change_throttle_is_persistent_namespaced_and_expires(env):
    with env.db.connect(True) as c:
        c.execute("INSERT INTO unlock_attempts VALUES(?,?,?)", (env.user["id"], 3, env.clock[0] + 60))
    for _ in range(5):
        assert change(env.client, current="incorrect").status_code == 403
    assert change(env.client).status_code == 429
    with env.db.connect() as c:
        row = c.execute("SELECT * FROM unlock_attempts WHERE owner=?", ("password-change:" + env.user["id"],)).fetchone()
        assert row["count"] == 5 and row["until"] == env.clock[0] + 60
        assert c.execute("SELECT count FROM unlock_attempts WHERE owner=?", (env.user["id"],)).fetchone()[0] == 3
    # Recreating the application does not erase the failed-attempt counter.
    with TestClient(create_app(env.db.path.parent)) as restarted:
        restarted.headers["X-Requested-With"] = "Qorgau"
        restarted.cookies.update(env.client.cookies)
        assert change(restarted).status_code == 429
        env.clock[0] += 61
        assert change(restarted).status_code == 200
    with env.db.connect() as c:
        assert c.execute("SELECT count(*) FROM unlock_attempts WHERE owner=?", ("password-change:" + env.user["id"],)).fetchone()[0] == 0


def test_old_password_login_cannot_create_session_after_concurrent_change(env, monkeypatch):
    original = app_module.PH
    reset_done = []

    def verify_with_concurrent_change(encoded, password):
        result = original.verify(encoded, password)
        if not reset_done:
            reset_done.append(True)
            response = change(env.client)
            assert response.status_code == 200, response.text
        return result

    monkeypatch.setattr(app_module, "PH", SimpleNamespace(hash=original.hash, verify=verify_with_concurrent_change))
    with TestClient(env.app) as racing_login:
        racing_login.headers["X-Requested-With"] = "Qorgau"
        response = racing_login.post("/api/auth/login", json={"name": "admin", "password": OLD_PASSWORD})
        assert reset_done
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
        assert racing_login.get("/api/snapshot").status_code == 401
    assert env.client.get("/api/auth/status").json()["user"] == env.user
    with env.db.connect() as c:
        assert c.execute("SELECT count(*) FROM logins WHERE user_id=?", (env.user["id"],)).fetchone()[0] == 1
