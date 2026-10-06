"""Teacher-created classroom downloads and idempotent enrollment for student EXEs."""

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from shared.bootstrap import bootstrap_trailer, server_address
from shared.rules import State

from .db import decode, encode


class PackageInput(BaseModel):
    room: str = Field(min_length=1, max_length=80)
    server: str = Field(min_length=1, max_length=2048)
    max_devices: int = Field(default=50, ge=1, le=100)
    expires_hours: int = Field(default=24, ge=1, le=168)


class AutomaticEnrollment(BaseModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    installation_secret: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    name: str = Field(min_length=1, max_length=80)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def register_package_routes(app, db, user, audit, root):
    def template():
        return Path(
            os.getenv("PROCTOR_STUDENT_EXE", root / "dist" / "Qorgau-Student.exe")
        )

    def public_url(request):
        return os.getenv("PROCTOR_PUBLIC_URL", str(request.base_url).rstrip("/"))

    def check_active(package):
        if package["revoked"] or package["expires_at"] <= time.time():
            raise HTTPException(
                403,
                "Срок подключения истёк или пакет отключён. Получите новый EXE у преподавателя.",
            )

    @app.get("/api/student-packages")
    def list_packages(request: Request):
        owner = user(request)["id"]
        with db.connect() as c:
            packages = [
                decode(r)
                for r in c.execute(
                    "SELECT body FROM student_packages WHERE owner=? ORDER BY rowid DESC",
                    (owner,),
                )
            ]
        return {
            "ready": template().is_file(),
            "server": public_url(request),
            "packages": packages,
        }

    @app.post("/api/student-packages")
    def create_package(body: PackageInput, request: Request):
        owner = user(request)["id"]
        if not template().is_file():
            raise HTTPException(
                503,
                "EXE ещё не подготовлен на сервере. Администратору нужно загрузить сборку приложения ученика.",
            )
        try:
            server = server_address(body.server)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if not body.room.strip():
            raise HTTPException(422, "Укажите аудиторию")
        token = secrets.token_urlsafe(32)
        package = {
            "id": str(uuid4()),
            "room": body.room.strip(),
            "server": server,
            "max_devices": body.max_devices,
            "used_devices": 0,
            "created_at": time.time(),
            "expires_at": time.time() + body.expires_hours * 3600,
            "revoked": False,
        }
        with db.connect(True) as c:
            c.execute(
                "INSERT INTO student_packages VALUES(?,?,?,?)",
                (package["id"], owner, digest(token), encode(package)),
            )
            audit(
                c,
                owner,
                "STUDENT_PACKAGE_CREATED",
                {"id": package["id"], "room": package["room"]},
            )
        return {
            **package,
            "download_path": f"/api/student-download/{token}",
            "download_url": f"{server}/api/student-download/{token}",
        }

    @app.post("/api/student-packages/{package_id}/revoke")
    def revoke_package(package_id: str, request: Request):
        owner = user(request)["id"]
        with db.connect(True) as c:
            row = c.execute(
                "SELECT * FROM student_packages WHERE id=? AND owner=?",
                (package_id, owner),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Пакет не найден")
            package = decode(row)
            package["revoked"] = True
            c.execute(
                "UPDATE student_packages SET body=? WHERE id=?",
                (encode(package), package_id),
            )
            audit(c, owner, "STUDENT_PACKAGE_REVOKED", {"id": package_id})
        return {"ok": True}

    @app.get("/api/student-download/{token}")
    def download_student(token: str):
        with db.connect() as c:
            row = c.execute(
                "SELECT * FROM student_packages WHERE token_hash=?", (digest(token),)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Ссылка недействительна")
        package = decode(row)
        check_active(package)
        try:
            stream = template().open("rb")
        except OSError as error:
            raise HTTPException(503, "Сборка приложения ученика недоступна") from error
        trailer = bootstrap_trailer(
            {
                "version": 1,
                "package_id": package["id"],
                "account_id": row["owner"],
                "server": package["server"],
                "token": token,
                "room": package["room"],
            }
        )
        size = os.fstat(stream.fileno()).st_size + len(trailer)

        def content():
            with stream:
                while chunk := stream.read(1024 * 1024):
                    yield chunk
                yield trailer

        return StreamingResponse(
            content(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="Qorgau-Classroom.exe"',
                "Content-Length": str(size),
            },
        )

    @app.post("/api/agent/auto-enroll")
    def auto_enroll(body: AutomaticEnrollment):
        if not body.name.strip():
            raise HTTPException(422, "Не удалось определить название компьютера")
        installation_hash = digest(body.installation_secret)
        with db.connect(True) as c:
            row = c.execute(
                "SELECT * FROM student_packages WHERE token_hash=?",
                (digest(body.token),),
            ).fetchone()
            if not row:
                raise HTTPException(
                    403,
                    "Пакет подключения недействителен. Получите новый EXE у преподавателя.",
                )
            package = decode(row)
            # The client persists its secret BEFORE the request, so retrying a lost
            # response returns the same credentials without using another seat.
            token = hmac.new(
                body.token.encode(), body.installation_secret.encode(), hashlib.sha256
            ).hexdigest()
            previous = c.execute(
                "SELECT device_id FROM package_devices WHERE package_id=? AND installation_hash=?",
                (row["id"], installation_hash),
            ).fetchone()
            if previous:
                registered = c.execute("SELECT token FROM devices WHERE id=?", (previous["device_id"],)).fetchone()
                if not registered or registered["token"] is None:
                    raise HTTPException(403, "Доступ этого компьютера отозван преподавателем")
                return {"device_id": previous["device_id"], "token": token}
            check_active(package)
            if package["used_devices"] >= package["max_devices"]:
                raise HTTPException(
                    403, "Все места пакета заняты. Получите новый EXE у преподавателя."
                )
            name = f"{package['room']} · {body.name.strip()}"[:80]
            device = {
                "id": str(uuid4()),
                "name": name,
                "student": name,
                "room": package["room"],
                "simulated": False,
                "state": State().public(),
                "exam_id": None,
                "targets": [],
                "capabilities": {"strict": False},
                "last_seen": time.time(),
            }
            c.execute(
                "INSERT INTO devices VALUES(?,?,?,?)",
                (device["id"], row["owner"], digest(token), encode(device)),
            )
            c.execute(
                "INSERT INTO package_devices VALUES(?,?,?)",
                (row["id"], installation_hash, device["id"]),
            )
            package["used_devices"] += 1
            c.execute(
                "UPDATE student_packages SET body=? WHERE id=?",
                (encode(package), row["id"]),
            )
            audit(
                c,
                row["owner"],
                "DEVICE_AUTO_ENROLLED",
                {"id": device["id"], "package_id": row["id"], "name": name},
            )
        return {"device_id": device["id"], "token": token}
