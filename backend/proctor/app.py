from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import io
import math
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shared.rules import RuleEngine, State
from shared.version import APP_VERSION, RULE_VERSION

from .db import Database, decode, encode
from .packages import register_package_routes

ROOT = Path(__file__).resolve().parents[2]
PH = PasswordHasher()


def uid():
    return str(uuid4())


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Credentials(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=8, max_length=128)


class Enrollment(BaseModel):
    code: str
    name: str = Field(min_length=1, max_length=80)


class ExamInput(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    group: str = Field(min_length=1, max_length=80)
    room: str = Field(min_length=1, max_length=80)
    device_ids: list[str] = Field(min_length=1, max_length=100)
    student_names: dict[str, str] = Field(default_factory=dict, max_length=100)
    environment: dict
    mode: str = "OBSERVE"
    require_camera: bool = True


class ActionInput(BaseModel):
    type: str
    expected_version: int
    lock_id: str | None = None
    reason: str = Field(default="", max_length=500)


class ReviewInput(BaseModel):
    decision: str
    expected_revision: int
    reason: str = Field(min_length=1, max_length=500)


class TeacherUnlock(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    exam_id: str
    lock_id: str | None = None
    expected_version: int = Field(ge=0)
    action: Literal["UNLOCK", "END_AND_RELEASE"] = "UNLOCK"

class RetentionInput(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    reason: str = Field(min_length=1, max_length=500)


class RevocationInput(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class SimulationInput(BaseModel):
    scenario: str


class DeviceUpdate(BaseModel):
    state: dict
    events: list[dict] = Field(default_factory=list, max_length=100)
    targets: list[dict] = Field(default_factory=list, max_length=100)
    capabilities: dict = Field(default_factory=dict)
    acknowledgements: list[dict] = Field(default_factory=list, max_length=100)
    exam_id: str | None = None


def create_app(data_dir=None):
    base = Path(data_dir or os.getenv("PROCTOR_DATA", ROOT / ".local"))
    base.mkdir(parents=True, exist_ok=True)
    media_dir = base / "media"
    media_dir.mkdir(exist_ok=True)
    media_reserve_bytes = max(
        0, int(os.getenv("PROCTOR_MEDIA_RESERVE_BYTES", str(512 * 1024 * 1024)))
    )
    db = Database(base / "proctor.sqlite3")
    app = FastAPI(title="Qorgau / локальный прокторинг", version=APP_VERSION)
    app.state.db = db
    simulations = {}
    failures = {}
    origins = set(
        os.getenv(
            "PROCTOR_ALLOWED_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000",
        ).split(",")
    )

    @app.middleware("http")
    async def protect(request, call_next):
        if request.method not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ) and not request.url.path.startswith("/api/agent/"):
            if request.headers.get("x-requested-with") != "Qorgau":
                return Response("Required request header", 403)
            origin = request.headers.get("origin")
            if origin and origin not in origins:
                return Response("Origin denied", 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def user(request):
        token = request.cookies.get("qorgau_session", "")
        with db.connect() as c:
            row = c.execute(
                "SELECT users.id,users.name FROM logins JOIN users ON users.id=logins.user_id WHERE token=? AND expires>?",
                (digest(token), time.time()),
            ).fetchone()
        if not row:
            raise HTTPException(401, "Войдите в панель преподавателя")
        return dict(row)

    def device_auth(request, c):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        row = c.execute(
            "SELECT * FROM devices WHERE token=?", (digest(token),)
        ).fetchone()
        if not row:
            raise HTTPException(401, "Неизвестное устройство")
        return row, decode(row)

    def owned(c, table, id, owner):
        assert table in ("devices", "exams")
        row = c.execute(
            f"SELECT * FROM {table} WHERE id=? AND owner=?", (id, owner)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Объект не найден")
        return decode(row)

    def audit(c, actor, action, body):
        c.execute(
            "INSERT INTO audit(at,actor,action,body) VALUES(?,?,?,?)",
            (time.time(), actor, action, encode(body)),
        )

    def write_device(c, d):
        c.execute("UPDATE devices SET body=? WHERE id=?", (encode(d), d["id"]))
        if d.get("exam_id"):
            r = c.execute(
                "SELECT body FROM exams WHERE id=?", (d["exam_id"],)
            ).fetchone()
            if r:
                e = decode(r)
                participant = {
                    k: d[k] for k in ("id", "name", "student", "state", "simulated")
                }
                previous = e["participants"].get(d["id"], {})
                if all(previous.get(k) == value for k, value in participant.items()):
                    return
                e["participants"][d["id"]] = participant
                e["participants"][d["id"]]["last_seen"] = d.get("last_seen", 0)
                states = [x["state"]["lifecycle"] for x in e["participants"].values()]
                e["status"] = (
                    "COMPLETED"
                    if all(x == "COMPLETED" for x in states)
                    else "RUNNING"
                    if any(x == "RUNNING" for x in states)
                    else "READY"
                )
                if e["status"] == "COMPLETED" and not e.get("ended_at"):
                    e["ended_at"] = time.time()
                c.execute("UPDATE exams SET body=? WHERE id=?", (encode(e), e["id"]))

    def engine_for(d):
        existing = simulations.get(d["id"])
        if not existing:
            existing = RuleEngine(
                State(
                    **{
                        k: v
                        for k, v in d["state"].items()
                        if k in State.__dataclass_fields__
                    }
                )
            )
            simulations[d["id"]] = existing
        return existing

    def save_events(c, d, events):
        if not d.get("exam_id"):
            return
        for event in events:
            if not isinstance(event.get("id"), str) or len(event["id"]) > 80:
                raise HTTPException(422, "Некорректный event_id")
            fields = ("end",) if event.get("update") else ("at", "start")
            for field in (
                *fields,
                *[k for k in ("created_at", "duration") if k in event],
            ):
                value = event.get(field)
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise HTTPException(422, "Некорректная временная метка события")
            row = c.execute(
                "SELECT * FROM events WHERE id=?", (event["id"],)
            ).fetchone()
            if row:
                if row["device_id"] != d["id"] or row["exam_id"] != d["exam_id"]:
                    raise HTTPException(409, "Конфликт события")
                if event.get("update"):
                    old = decode(row)
                    old["end"] = event.get("end")
                    c.execute(
                        "UPDATE events SET body=? WHERE id=?", (encode(old), old["id"])
                    )
                continue
            if event.get("update"):
                continue
            allowed = {
                "GAZE_DOWN",
                "GAZE_LEFT",
                "GAZE_RIGHT",
                "PHONE_DETECTED",
                "SECOND_FACE_REVIEW",
                "FREQUENT_GAZE_REVIEW",
                "CAMERA_UNAVAILABLE",
                "EXTENSION_DISCONNECTED",
                "BROWSER_ATTEMPT",
                "HEAD_TURN_REVIEW",
                "FACE_ABSENCE_REVIEW",
                "TARGET_CLOSED",
                "REMOTE_SESSION",
                "ENVIRONMENT_ATTEMPT",
                "SERVER_UNAVAILABLE",
                "GUARD_UNAVAILABLE",
                "DISPLAY_CHANGED",
                "CAMERA_FROZEN",
                "AGENT_RESTARTED",
                "AGENT_FAILURE",
                "TEACHER_REQUEST",

                "FACE_ABSENCE_TECHNICAL",
                "PHONE_AIM_REVIEW",
            }
            if event.get("type") not in allowed:
                raise HTTPException(422, "Неизвестный тип события")
            item = {
                **event,
                "exam_id": d["exam_id"],
                "device_id": d["id"],
                "student": d["student"],
                "device_name": d["name"],
                "created_at": event.get("created_at", time.time()),
                "decision": "PENDING",
                "revision": 0,
                "reviews": [],
                "media": [],
                "simulated": d["simulated"],
            }
            c.execute(
                "INSERT INTO events VALUES(?,?,?,?)",
                (item["id"], d["exam_id"], d["id"], encode(item)),
            )

    def snapshot(owner):
        with db.connect() as c:
            devices = [
                decode(r)
                for r in c.execute("SELECT body FROM devices WHERE owner=?", (owner,))
            ]
            exams = [
                decode(r)
                for r in c.execute(
                    "SELECT body FROM exams WHERE owner=? ORDER BY rowid DESC", (owner,)
                )
            ]
            events = [
                decode(r)
                for r in c.execute(
                    "SELECT events.body FROM events JOIN exams ON exams.id=events.exam_id WHERE exams.owner=? ORDER BY events.rowid DESC LIMIT 1000",
                    (owner,),
                )
            ]
            commands = [
                decode(r)
                for r in c.execute(
                    "SELECT commands.body FROM commands JOIN devices ON devices.id=commands.device_id WHERE devices.owner=? ORDER BY commands.rowid DESC LIMIT 100",
                    (owner,),
                )
            ]
        for d in devices:
            d["online"] = d["simulated"] or time.time() - d.get("last_seen", 0) < 6
        return {
            "devices": devices,
            "exams": exams,
            "events": events,
            "commands": commands,
            "server_time": time.time(),
        }

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": APP_VERSION}

    @app.get("/api/auth/status")
    def status(request: Request):
        with db.connect() as c:
            setup = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        try:
            me = user(request)
        except HTTPException:
            me = None
        return {"setup_required": setup, "user": me}

    def login_response(u, response):
        token = secrets.token_urlsafe(32)
        with db.connect(True) as c:
            c.execute(
                "INSERT INTO logins VALUES(?,?,?)",
                (digest(token), u["id"], time.time() + 12 * 3600),
            )
        response.set_cookie(
            "qorgau_session",
            token,
            httponly=True,
            samesite="strict",
            secure=os.getenv("PROCTOR_SECURE_COOKIE") == "1",
            max_age=43200,
        )
        return {"user": u}

    @app.post("/api/auth/setup")
    def setup(body: Credentials, response: Response):
        with db.connect(True) as c:
            if c.execute("SELECT 1 FROM users").fetchone():
                raise HTTPException(409, "Учётная запись уже создана")
            u = {"id": uid(), "name": body.name}
            c.execute(
                "INSERT INTO users VALUES(?,?,?)",
                (u["id"], u["name"], PH.hash(body.password)),
            )
        return login_response(u, response)

    @app.post("/api/auth/login")
    def login(body: Credentials, response: Response, request: Request):
        key = request.client.host if request.client else "local"
        now = time.time()
        recent = [x for x in failures.get(key, []) if now - x < 60]
        if len(recent) >= 5:
            raise HTTPException(429, "Слишком много попыток. Повторите через минуту.")
        with db.connect() as c:
            row = c.execute("SELECT * FROM users WHERE name=?", (body.name,)).fetchone()
        valid = False
        if row:
            try:
                valid = PH.verify(row["password"], body.password)
            except VerificationError:
                pass
        if not valid:
            failures[key] = recent + [now]
            raise HTTPException(401, "Неверное имя или пароль")
        failures.pop(key, None)
        return login_response({"id": row["id"], "name": row["name"]}, response)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        with db.connect(True) as c:
            c.execute(
                "DELETE FROM logins WHERE token=?",
                (digest(request.cookies.get("qorgau_session", "")),),
            )
        response.delete_cookie("qorgau_session")
        return {"ok": True}

    @app.get("/api/snapshot")
    def get_snapshot(request: Request):
        return snapshot(user(request)["id"])

    @app.websocket("/ws/teacher")
    async def updates(ws: WebSocket):
        if ws.headers.get("origin") not in origins:
            await ws.close(code=4403)
            return
        try:
            u = user(ws)
        except HTTPException:
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                user(ws)
                await ws.send_json(await asyncio.to_thread(snapshot, u["id"]))
                await asyncio.sleep(1)
        except (WebSocketDisconnect, RuntimeError, HTTPException):
            try:
                await ws.close()
            except RuntimeError:
                pass

    @app.post("/api/pairings")
    def pairing(request: Request):
        u = user(request)
        code = secrets.token_hex(4).upper()
        with db.connect(True) as c:
            c.execute(
                "INSERT INTO pairings VALUES(?,?,?)",
                (digest(code), u["id"], time.time() + 300),
            )
        return {"code": code, "expires_in": 300}

    @app.post("/api/agent/enroll")
    def enroll(body: Enrollment):
        with db.connect(True) as c:
            row = c.execute(
                "SELECT * FROM pairings WHERE code=? AND expires>?",
                (digest(body.code.upper()), time.time()),
            ).fetchone()
            if not row:
                raise HTTPException(403, "Код недействителен или уже использован")
            c.execute("DELETE FROM pairings WHERE code=?", (row["code"],))
            token = secrets.token_urlsafe(32)
            d = {
                "id": uid(),
                "name": body.name,
                "student": body.name,
                "simulated": False,
                "state": State().public(),
                "exam_id": None,
                "targets": [],
                "capabilities": {"strict": False},
                "last_seen": time.time(),
            }
            c.execute(
                "INSERT INTO devices VALUES(?,?,?,?)",
                (d["id"], row["user_id"], digest(token), encode(d)),
            )
            audit(
                c, row["user_id"], "DEVICE_ENROLLED", {"id": d["id"], "name": body.name}
            )
        return {"device_id": d["id"], "token": token}

    @app.post("/api/demo/devices")
    def demo(request: Request):
        u = user(request)
        with db.connect(True) as c:
            existing = [
                decode(r)
                for r in c.execute("SELECT body FROM devices WHERE owner=?", (u["id"],))
            ]
            if any(d["simulated"] for d in existing):
                return {"ok": True}
            for i, name in enumerate(
                ["Алина Серикова", "Данияр Омаров", "Аружан Касымова", "Тимур Ахметов"],
                1,
            ):
                d = {
                    "id": uid(),
                    "name": f"ПК-{i:02}",
                    "student": name,
                    "simulated": True,
                    "state": State().public(),
                    "exam_id": None,
                    "last_seen": time.time(),
                    "targets": [
                        {"id": "chrome", "kind": "BROWSER", "name": "Google Chrome"},
                        {
                            "id": "exam-app",
                            "kind": "APP",
                            "name": "Приложение для тестирования · стенд",
                        },
                    ],
                    "capabilities": {
                        "strict": False,
                        "camera": False,
                        "recording": False,
                        "description": "Виртуальный клиент: без камеры и блокировки ОС",
                    },
                }
                c.execute(
                    "INSERT INTO devices VALUES(?,?,?,?)",
                    (d["id"], u["id"], None, encode(d)),
                )
            audit(c, u["id"], "DEMO_DEVICES_CREATED", {})
        return {"ok": True}

    @app.post("/api/exams")
    def create_exam(body: ExamInput, request: Request):
        u = user(request)
        if len(set(body.device_ids)) != len(body.device_ids):
            raise HTTPException(422, "Устройства должны быть уникальны")
        if set(body.student_names) - set(body.device_ids):
            raise HTTPException(422, "Назначьте учеников только выбранным компьютерам")
        if any(len(name.strip()) > 80 for name in body.student_names.values()):
            raise HTTPException(422, "Имя ученика должно быть не длиннее 80 символов")
        if body.mode not in ("OBSERVE", "GUARDED", "STRICT"):
            raise HTTPException(422, "Неизвестный режим")
        env = body.environment
        # Policy comes from the teacher's mode, never an unchecked environment field.
        env = {**env, "guarded": body.mode == "GUARDED"}
        if env.get("kind") not in ("BROWSER", "APP"):
            raise HTTPException(422, "Выберите среду")
        if env["kind"] == "BROWSER":
            address = urlparse(env.get("url", ""))
            if (
                address.scheme not in ("http", "https")
                or not address.hostname
                or address.username
            ):
                raise HTTPException(422, "Укажите полный адрес сайта http(s)")
            env = {**env, "allowed_origins": [f"{address.scheme}://{address.netloc}"]}
        with db.connect(True) as c:
            devices = [owned(c, "devices", id, u["id"]) for id in body.device_ids]
            for d in devices:
                if d.get("revoked_at"):
                    raise HTTPException(409, f"{d['name']}: доступ отозван")
                if d.get("capabilities", {}).get("recording_tail"):
                    raise HTTPException(409, f"{d['name']}: завершается запись предыдущего сеанса")
                if d.get("exam_id") and d["state"]["lifecycle"] != "COMPLETED":
                    raise HTTPException(409, f"{d['name']}: завершите текущий сеанс")
                if not d["simulated"] and time.time() - d.get("last_seen", 0) >= 6:
                    raise HTTPException(409, f"{d['name']}: нет связи")
                if not any(
                    t["id"] == env.get("target_id") and t["kind"] == env["kind"]
                    for t in d["targets"]
                ):
                    raise HTTPException(422, f"{d['name']}: среда недоступна")
                if body.mode == "STRICT" and not d["capabilities"].get("strict"):
                    raise HTTPException(
                        409,
                        "Системная защита не подтверждена. Доступен только режим наблюдения.",
                    )
                if body.mode == "GUARDED" and (
                    d["simulated"]
                    or not all(
                        d["capabilities"].get(k)
                        for k in ("window_guard", "camera", "recording")
                    )
                ):
                    raise HTTPException(
                        409,
                        f"{d['name']}: нужны Windows-агент, подготовленная камера и запись",
                    )
                if body.mode == "GUARDED":
                    target = next(
                        t for t in d["targets"] if t["id"] == env["target_id"]
                    )
                    if (
                        env["kind"] == "BROWSER" and target["id"] != "qorgau-browser"
                    ) or target.get("guardable") is False:
                        raise HTTPException(
                            409,
                            "Для сайта выберите Qorgau Browser: он ограничивает адрес и не содержит вкладок",
                        )
            e = {
                "id": uid(),
                "title": body.title,
                "group": body.group,
                "room": body.room,
                "environment": env,
                "mode": body.mode,
                "require_camera": body.require_camera,
                "created_at": time.time(),
                "status": "READY",
                "participants": {},
                "rule_version": RULE_VERSION,
                "simulated": all(d["simulated"] for d in devices),
            }
            for d in devices:
                d["exam_id"] = e["id"]
                if d["simulated"]:
                    d.setdefault("demo_student", d["student"])
                d["student"] = body.student_names.get(d["id"], "").strip() or (
                    d.get("demo_student", d["name"]) if d["simulated"] else d["name"]
                )
                d["state"] = State().public()
                d["environment"] = env
                simulations.pop(d["id"], None)
                e["participants"][d["id"]] = {
                    k: d[k] for k in ("id", "name", "student", "state", "simulated")
                }
                c.execute("UPDATE devices SET body=? WHERE id=?", (encode(d), d["id"]))
            c.execute("INSERT INTO exams VALUES(?,?,?)", (e["id"], u["id"], encode(e)))
            audit(c, u["id"], "EXAM_CREATED", {"id": e["id"]})
        return e

    @app.post("/api/devices/{device_id}/revoke")
    def revoke_device(device_id: str, body: RevocationInput, request: Request):
        u = user(request)
        with db.connect(True) as c:
            d = owned(c, "devices", device_id, u["id"])
            if d["state"]["lifecycle"] == "RUNNING":
                raise HTTPException(409, "Сначала завершите активный контроль устройства")
            if d.get("simulated"):
                raise HTTPException(409, "Тренировочное устройство не имеет токена")
            d["revoked_at"] = time.time()
            d["last_seen"] = 0
            c.execute("UPDATE devices SET token=NULL,body=? WHERE id=?", (encode(d), device_id))
            audit(c, u["id"], "DEVICE_REVOKED", {"id": device_id, "reason": body.reason})
        return {"ok": True}

    @app.post("/api/devices/{device_id}/commands")
    def command(device_id: str, body: ActionInput, request: Request):
        u = user(request)
        if body.type not in ("START", "LOCK", "UNLOCK", "END_AND_RELEASE"):
            raise HTTPException(422, "Неизвестная команда")
        if (
            body.type in ("LOCK", "UNLOCK", "END_AND_RELEASE")
            and not body.reason.strip()
        ):
            raise HTTPException(422, "Укажите причину")
        with db.connect(True) as c:
            d = owned(c, "devices", device_id, u["id"])
            if not d.get("exam_id"):
                raise HTTPException(409, "Сначала создайте сеанс")
            if d["state"]["lifecycle"] == "COMPLETED":
                raise HTTPException(409, "Сеанс уже завершён")
            if body.type in ("LOCK", "UNLOCK") and d["state"]["lifecycle"] != "RUNNING":
                raise HTTPException(409, "Контроль ещё не начат")
            if body.expected_version != d["state"]["version"]:
                raise HTTPException(409, "Состояние изменилось. Обновите карточку.")
            exam = owned(c, "exams", d["exam_id"], u["id"])
            require_camera = exam.get("require_camera", True)
            if body.type == "START" and not d["simulated"] and require_camera:
                caps = d.get("capabilities", {})
                if time.time() - d.get("last_seen", 0) >= 6:
                    raise HTTPException(409, "Нет связи с компьютером")
                if not caps.get("camera") or not caps.get("recording") or caps.get("camera_fault"):
                    raise HTTPException(409, "Камера и запись не готовы. Выполните калибровку в приложении студента.")
            cmd = {
                "id": uid(),
                "device_id": device_id,
                "exam_id": d["exam_id"],
                **body.model_dump(),
                "actor": u["name"],
                "created_at": time.time(),
                "expires_at": time.time() + 30,
                "status": "PENDING",
                "require_camera": require_camera,
            }
            if d["simulated"]:
                engine = engine_for(d)
                try:
                    if body.type == "START":
                        engine.start()
                    elif body.type == "LOCK":
                        engine.lock("TEACHER_LOCK")
                    elif body.type == "UNLOCK":
                        engine.unlock(body.lock_id, body.expected_version)
                    else:
                        engine.end()
                except ValueError as err:
                    raise HTTPException(409, str(err))
                cmd["status"] = "APPLIED"
                d["state"] = engine.state.public()
                write_device(c, d)
            c.execute(
                "INSERT INTO commands VALUES(?,?,?)",
                (cmd["id"], device_id, encode(cmd)),
            )
            audit(c, u["id"], body.type, cmd)
        return cmd

    @app.post("/api/agent/teacher-unlock")
    def teacher_unlock(body: TeacherUnlock, request: Request):
        error = None
        result = None
        with db.connect(True) as c:
            row, d = device_auth(request, c)
            owner = row["owner"]
            now = time.time()
            attempts = c.execute(
                "SELECT * FROM unlock_attempts WHERE owner=?", (owner,)
            ).fetchone()
            count = attempts["count"] if attempts and attempts["until"] > now else 0
            until = attempts["until"] if count else now + 60
            if count >= 5:
                error = (429, "Слишком много попыток. Подождите одну минуту.")
            else:
                teacher = c.execute(
                    "SELECT * FROM users WHERE id=?", (owner,)
                ).fetchone()
                try:
                    PH.verify(teacher["password"], body.password)
                except VerificationError:
                    c.execute(
                        "INSERT OR REPLACE INTO unlock_attempts VALUES(?,?,?)",
                        (owner, count + 1, until),
                    )
                    audit(c, owner, "LOCAL_UNLOCK_DENIED", {"device_id": d["id"]})
                    error = (403, "Неверный пароль преподавателя")
                else:
                    state = d["state"]
                    if (
                        d.get("exam_id") != body.exam_id
                        or (body.action == "UNLOCK" and state["access"] != "LOCKED")
                        or state["lifecycle"] != "RUNNING"
                        or state["lock_id"] != body.lock_id
                        or state["version"] != body.expected_version
                    ):
                        error = (409, "Состояние изменилось. Повторите проверку.")
                    else:
                        c.execute("DELETE FROM unlock_attempts WHERE owner=?", (owner,))
                        result = {
                            "id": uid(),
                            "device_id": d["id"],
                            "exam_id": body.exam_id,
                            "type": body.action,
                            "require_camera": owned(c, "exams", body.exam_id, owner).get("require_camera", True),
                            "expected_version": body.expected_version,
                            "lock_id": body.lock_id,
                            "reason": "Пароль преподавателя на рабочем месте",
                            "actor": teacher["name"],
                            "created_at": now,
                            "expires_at": now + 30,
                            "status": "PENDING",
                        }
                        c.execute(
                            "INSERT INTO commands VALUES(?,?,?)",
                            (result["id"], d["id"], encode(result)),
                        )
                        audit(c, owner, "LOCAL_TEACHER_" + body.action, result)
        # Commit failed attempts before returning an HTTP error.
        if error:
            raise HTTPException(*error)
        return result

    @app.post("/api/events/{event_id}/retain")
    def retain_event(event_id: str, body: RetentionInput, request: Request):
        u = user(request)
        with db.connect(True) as c:
            row = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Событие не найдено")
            owned(c, "exams", row["exam_id"], u["id"])
            event = decode(row)
            event["retain_until"] = max(event.get("retain_until", 0), event["created_at"] + 7 * 86400, time.time() + body.days * 86400)
            c.execute("UPDATE events SET body=? WHERE id=?", (encode(event), event_id))
            audit(c, u["id"], "RETENTION_EXTENDED", {"event_id": event_id, "until": event["retain_until"], "reason": body.reason})
        return {"retain_until": event["retain_until"]}

    @app.post("/api/devices/{device_id}/simulate")
    def simulate(device_id: str, body: SimulationInput, request: Request):
        u = user(request)
        if body.scenario not in (
            "DOWN",
            "LEFT",
            "RIGHT",
            "PHONE",
            "SECOND_FACE",
            "FREQUENT",
        ):
            raise HTTPException(422, "Неизвестный сценарий")
        with db.connect(True) as c:
            d = owned(c, "devices", device_id, u["id"])
            if not d["simulated"]:
                raise HTTPException(403, "Симуляция запрещена на реальном устройстве")
            en = engine_for(d)
            if en.state.lifecycle != "RUNNING":
                raise HTTPException(409, "Сначала начните контроль")
            if en.state.access == "LOCKED":
                raise HTTPException(409, "Сначала разрешите продолжить")
            t = en.last_t or 0.0
            events = []

            def feed(duration, direction="SCREEN", phone=0, faces=1):
                nonlocal t
                for _ in range(round(duration * 10) + 1):
                    t = round(t + 0.1, 4)
                    events.extend(en.observe(t, direction, phone, faces))

            if body.scenario in ("DOWN", "LEFT", "RIGHT"):
                feed(5.1, body.scenario)
                feed(1.2)
            elif body.scenario == "PHONE":
                feed(0.4, phone=0.95)
                feed(1.5)
            elif body.scenario == "SECOND_FACE":
                feed(1.2, faces=2)
                feed(1.2)
            else:
                for direction in ("DOWN", "LEFT", "RIGHT"):
                    feed(2.2, direction)
                    feed(1.2)
            save_events(c, d, events)
            d["state"] = en.state.public()
            write_device(c, d)
        return {"ok": True}

    @app.post("/api/events/{event_id}/review")
    def review(event_id: str, body: ReviewInput, request: Request):
        u = user(request)
        if not body.reason.strip():
            raise HTTPException(422, "Укажите причину решения")
        if body.decision not in ("CONFIRMED", "REJECTED"):
            raise HTTPException(422, "Выберите решение")
        with db.connect(True) as c:
            row = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Событие не найдено")
            owned(c, "exams", row["exam_id"], u["id"])
            ev = decode(row)
            if ev["revision"] != body.expected_revision:
                raise HTTPException(409, "Решение уже изменено. Обновите событие.")
            ev["revision"] += 1
            ev["decision"] = body.decision
            ev["reviews"].append(
                {
                    "decision": body.decision,
                    "reason": body.reason,
                    "author": u["name"],
                    "at": time.time(),
                    "revision": ev["revision"],
                }
            )
            c.execute("UPDATE events SET body=? WHERE id=?", (encode(ev), event_id))
            d = owned(c, "devices", row["device_id"], u["id"])
            if d.get("exam_id") == ev["exam_id"]:
                if d["simulated"]:
                    en = engine_for(d)
                    en.review(event_id, body.decision)
                    d["state"] = en.state.public()
                    write_device(c, d)
                else:
                    cmd = {
                        "id": uid(),
                        "device_id": d["id"],
                        "exam_id": ev["exam_id"],
                        "type": "REVIEW",
                        "event_id": event_id,
                        "decision": body.decision,
                        "review_revision": ev["revision"],
                        "created_at": time.time(),
                        "expires_at": time.time() + 7 * 86400,
                        "status": "PENDING",
                    }
                    c.execute(
                        "INSERT INTO commands VALUES(?,?,?)",
                        (cmd["id"], d["id"], encode(cmd)),
                    )
            audit(c, u["id"], "REVIEW", {"event_id": event_id, **body.model_dump()})
        return ev

    @app.post("/api/agent/sync")
    def sync(body: DeviceUpdate, request: Request):
        with db.connect(True) as c:
            _row, d = device_auth(request, c)
            d["last_seen"] = time.time()
            d["targets"] = body.targets
            # STRICT remains unavailable until the managed Windows implementation is certified.
            d["capabilities"] = {**body.capabilities, "strict": False}
            if body.exam_id and body.exam_id == d.get("exam_id"):
                try:
                    st = State(
                        **{
                            k: v
                            for k, v in body.state.items()
                            if k in State.__dataclass_fields__
                        }
                    )
                except (TypeError, ValueError):
                    raise HTTPException(422, "Некорректное состояние")
                old = d["state"]
                needs_release = old["access"] == "LOCKED" and (
                    st.access == "OPEN" or st.epoch > old["epoch"]
                )
                needs_end = (
                    old["lifecycle"] != "COMPLETED" and st.lifecycle == "COMPLETED"
                )
                if needs_release or needs_end:
                    authorized = False
                    for ack in body.acknowledgements:
                        command_row = c.execute(
                            "SELECT body FROM commands WHERE id=? AND device_id=?",
                            (ack.get("id"), d["id"]),
                        ).fetchone()
                        if not ack.get("ok") or not command_row:
                            continue
                        cmd = decode(command_row)
                        valid = (
                            cmd["exam_id"] == d["exam_id"]
                            and cmd["status"] == "PENDING"
                            and cmd["expected_version"] == old["version"]
                        )
                        if valid and (
                            cmd["type"] == "END_AND_RELEASE"
                            or (
                                not needs_end
                                and cmd["type"] == "UNLOCK"
                                and cmd.get("lock_id") == old["lock_id"]
                            )
                        ):
                            authorized = True
                    if not authorized:
                        raise HTTPException(
                            409, "Снятие блокировки требует команды преподавателя"
                        )
                if old["lifecycle"] == "RUNNING" and st.lifecycle == "READY":
                    raise HTTPException(409, "Нельзя сбросить активный сеанс")
                if st.version >= d["state"]["version"]:
                    d["state"] = st.public()
                save_events(c, d, body.events)
            elif body.events:
                raise HTTPException(409, "События другого сеанса")
            for ack in body.acknowledgements:
                r = c.execute(
                    "SELECT * FROM commands WHERE id=? AND device_id=?",
                    (ack.get("id"), d["id"]),
                ).fetchone()
                if r:
                    command = decode(r)
                    if command["status"] == "PENDING":
                        command["status"] = "APPLIED" if ack.get("ok") else "REJECTED"
                        command["error"] = str(ack.get("error", ""))[:200]
                        c.execute(
                            "UPDATE commands SET body=? WHERE id=?",
                            (encode(command), command["id"]),
                        )
            write_device(c, d)
            commands = []
            for r in c.execute(
                "SELECT * FROM commands WHERE device_id=? ORDER BY rowid", (d["id"],)
            ).fetchall():
                cmd = decode(r)
                if cmd["status"] != "PENDING":
                    continue
                if cmd["expires_at"] < time.time():
                    cmd["status"] = "EXPIRED"
                    c.execute(
                        "UPDATE commands SET body=? WHERE id=?",
                        (encode(cmd), cmd["id"]),
                    )
                else:
                    commands.append(cmd)
            session_row = c.execute(
                "SELECT body FROM exams WHERE id=?", (d.get("exam_id"),)
            ).fetchone()
            session_data = decode(session_row) if session_row else None
            config = {
                "session": {
                    **{k: session_data[k] for k in ("title", "group", "room")},
                    "student": d["student"],
                }
                if session_data
                else None,
                "exam_id": d.get("exam_id"),
                "environment": d.get("environment"),
                "commands": commands,
            }
        return config

    @app.post("/api/agent/media/{event_id}")
    async def upload_agent_video(event_id: str, request: Request):
        with db.connect() as c:
            _, d = device_auth(request, c)
            row = c.execute(
                "SELECT * FROM events WHERE id=? AND device_id=?", (event_id, d["id"])
            ).fetchone()
            if not row:
                raise HTTPException(404, "Событие не найдено")
        return await store_video(event_id, d["id"], request)

    async def store_video(event_id, device_id, request):
        mime = request.headers.get("content-type", "").split(";")[0]
        if mime not in ("video/mp4", "video/webm"):
            raise HTTPException(415, "Поддерживаются MP4 и WebM")
        mid = uid()
        file = media_dir / (mid + (".mp4" if mime == "video/mp4" else ".webm"))
        size = 0
        sha = hashlib.sha256()
        try:
            with file.open("wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > 50 * 1024 * 1024:
                        raise HTTPException(413, "Максимум 50 МБ на фрагмент")
                    if shutil.disk_usage(media_dir).free < media_reserve_bytes + len(chunk):
                        raise HTTPException(
                            507,
                            "Недостаточно места на сервере для видео. Повторите отправку после освобождения диска.",
                        )
                    f.write(chunk)
                    sha.update(chunk)
            if size < 12:
                raise HTTPException(422, "Пустой видеофайл")
            with file.open("rb") as stream:
                signature = stream.read(12)
            if (mime == "video/mp4" and signature[4:8] != b"ftyp") or (
                mime == "video/webm" and signature[:4] != b"\x1aE\xdf\xa3"
            ):
                raise HTTPException(422, "Содержимое не соответствует формату видео")
            try:
                clip_start = float(request.headers.get("x-clip-start", "0"))
                clip_end = float(request.headers.get("x-clip-end", "0"))
                if not 0 <= clip_start <= clip_end < 1e8:
                    raise ValueError()
                quality_text = request.headers.get("x-clip-quality", "{}")
                if len(quality_text) > 6000:
                    raise ValueError()
                quality = json.loads(quality_text)
                if not isinstance(quality, dict):
                    raise ValueError()
                gaps = quality.get("gaps", [])
                if (not isinstance(gaps, list) or len(gaps) > 200
                    or any(not isinstance(g, list) or len(g) != 2 or
                           not clip_start <= float(g[0]) < float(g[1]) <= clip_end for g in gaps)):
                    raise ValueError()
                complete = quality.get("complete")
                if complete is not None and type(complete) is not bool:
                    raise ValueError()
            except (ValueError, TypeError):
                raise HTTPException(422, "Некорректные границы фрагмента")
            with db.connect(True) as c:
                ev = decode(
                    c.execute(
                        "SELECT body FROM events WHERE id=?", (event_id,)
                    ).fetchone()
                )
                duplicate = next(
                    (m for m in ev["media"] if m["sha256"] == sha.hexdigest()
                     and m["clip_start"] == clip_start and m["clip_end"] == clip_end), None
                )
                if duplicate:
                    file.unlink(missing_ok=True)
                    return {"id": duplicate["id"], "sha256": sha.hexdigest()}
                c.execute(
                    "INSERT INTO media VALUES(?,?,?,?,?)",
                    (mid, event_id, device_id, str(file), mime),
                )
                ev["media"].append(
                    {
                        "id": mid,
                        "url": f"/api/media/{mid}",
                        "mime": mime,
                        "size": size,
                        "sha256": sha.hexdigest(),
                        "clip_start": clip_start,
                        "clip_end": clip_end,
                        "gaps": gaps,
                        "complete": False if gaps else complete,
                    }
                )
                c.execute("UPDATE events SET body=? WHERE id=?", (encode(ev), event_id))
        except BaseException:
            file.unlink(missing_ok=True)
            raise
        return {"id": mid, "sha256": sha.hexdigest()}

    @app.post("/api/events/{event_id}/demo-video")
    async def demo_video(event_id: str, request: Request):
        u = user(request)
        with db.connect() as c:
            r = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not r:
                raise HTTPException(404, "Событие не найдено")
            owned(c, "exams", r["exam_id"], u["id"])
            ev = decode(r)
            if not ev["simulated"]:
                raise HTTPException(
                    403, "Запись реального агента нельзя заменить вручную"
                )
        return await store_video(event_id, ev["device_id"], request)

    @app.get("/api/media/{media_id}")
    def get_media(media_id: str, request: Request):
        u = user(request)
        with db.connect() as c:
            row = c.execute(
                "SELECT media.* FROM media JOIN events ON events.id=media.event_id JOIN exams ON exams.id=events.exam_id WHERE media.id=? AND exams.owner=?",
                (media_id, u["id"]),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Видео не найдено")
        return FileResponse(row["path"], media_type=row["mime"])

    @app.get("/api/exams/{exam_id}/report.csv")
    def report(exam_id: str, request: Request):
        u = user(request)
        with db.connect() as c:
            e = owned(c, "exams", exam_id, u["id"])
            events = [
                decode(r)
                for r in c.execute(
                    "SELECT body FROM events WHERE exam_id=?", (exam_id,)
                )
            ]
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(
            [
                "Ученик",
                "Рабочее место",
                "Вниз",
                "Влево",
                "Вправо",
                "Телефон",
                "Подтверждено",
                "Отклонено",
                "На проверке",
                "Блокировки",
                "Стенд",
            ]
        )
        for d in e["participants"].values():
            mine = [x for x in events if x["device_id"] == d["id"]]
            valid = [x for x in mine if x["decision"] != "REJECTED"]
            def safe(s):
                return "'" + s if s.lstrip().startswith(("=", "+", "-", "@")) else s
            w.writerow(
                [
                    safe(d["student"]),
                    safe(d["name"]),
                    *[
                        sum(x["type"] == "GAZE_" + k for x in valid)
                        for k in ("DOWN", "LEFT", "RIGHT")
                    ],
                    sum(x["type"] == "PHONE_DETECTED" for x in valid),
                    *[
                        sum(x["decision"] == k for x in mine)
                        for k in ("CONFIRMED", "REJECTED", "PENDING")
                    ],
                    d["state"]["locks"],
                    "Да" if d["simulated"] else "Нет",
                ]
            )
        return Response(
            "\ufeff" + out.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="report-{exam_id}.csv"'
            },
        )

    register_package_routes(app, db, user, audit, ROOT)
    dist = ROOT / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
