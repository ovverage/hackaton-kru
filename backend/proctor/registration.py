"""Code-free registration for the explicitly configured deployment's classroom.

Public callers can register only their own installation, never choose a teacher
or gain teacher permissions. A random client secret makes retries idempotent.
"""

import hashlib
import hmac
import os
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from shared.rules import State
from shared.version import APP_VERSION
from .db import encode


class PublicRegistration(BaseModel):
    installation_secret: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    name: str = Field(min_length=1, max_length=80)


def register_public_registration(app, db, audit, root):
    owner_name = os.getenv("PROCTOR_PUBLIC_ENROLLMENT_OWNER", "").strip()
    capacity = int(os.getenv("PROCTOR_PUBLIC_ENROLLMENT_LIMIT", "500"))
    if not 1 <= capacity <= 5000:
        raise ValueError("PROCTOR_PUBLIC_ENROLLMENT_LIMIT must be between 1 and 5000")

    @app.get("/api/student/download-offline")
    def download_offline_student():
        return RedirectResponse(
            f"https://github.com/ovverage/hackaton-kru/releases/download/v{APP_VERSION}/Qorgau-Offline.exe",
            status_code=307,
        )

    @app.get("/api/student/download")
    def download_default_student():
        executable = Path(
            os.getenv("PROCTOR_STUDENT_EXE", root / "dist/Qorgau-Student.exe")
        )
        if not owner_name or not executable.is_file():
            raise HTTPException(
                503, "Приложение пока недоступно. Повторите скачивание позже."
            )
        return FileResponse(
            executable,
            media_type="application/octet-stream",
            filename="Qorgau-Student.exe",
        )

    @app.post("/api/agent/register")
    def register(body: PublicRegistration, request: Request):
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "Не удалось определить название компьютера")
        installation_hash = hashlib.sha256(
            body.installation_secret.encode()
        ).hexdigest()
        with db.connect(True) as c:
            owner = (
                c.execute("SELECT id FROM users WHERE name=?", (owner_name,)).fetchone()
                if owner_name
                else None
            )
            if owner is None:
                raise HTTPException(
                    503, "Сервер готовит подключение. Повторим автоматически."
                )
            owner = owner["id"]
            token = hmac.new(
                body.installation_secret.encode(),
                ("qorgau-public-device-v1\n" + owner).encode(),
                hashlib.sha256,
            ).hexdigest()
            previous = c.execute(
                "SELECT devices.id,devices.token FROM public_devices JOIN devices ON devices.id=public_devices.device_id WHERE public_devices.owner=? AND installation_hash=?",
                (owner, installation_hash),
            ).fetchone()
            if previous:
                if previous["token"] is None:
                    raise HTTPException(
                        403, "Доступ этого компьютера отозван преподавателем"
                    )
                return {"device_id": previous["id"], "token": token}
            now = time.time()
            c.execute("DELETE FROM public_registration_attempts WHERE until<=?", (now,))
            address = request.client.host if request.client else "unknown"
            attempts = c.execute(
                "SELECT count FROM public_registration_attempts WHERE client=?",
                (address,),
            ).fetchone()
            # Allow a whole 50-seat room behind one NAT to start together.
            if attempts and attempts["count"] >= 60:
                raise HTTPException(
                    429,
                    "Подключается много компьютеров. Повторим автоматически.",
                    headers={"Retry-After": "60"},
                )
            used = c.execute(
                "SELECT count(*) FROM public_devices JOIN devices ON devices.id=public_devices.device_id WHERE public_devices.owner=? AND devices.token IS NOT NULL",
                (owner,),
            ).fetchone()[0]
            if used >= capacity:
                raise HTTPException(
                    503,
                    "Достигнут лимит компьютеров сервера. Обратитесь к преподавателю.",
                )
            device = {
                "id": str(uuid4()),
                "name": name,
                "student": name,
                "room": "Новые компьютеры",
                "simulated": False,
                "state": State().public(),
                "exam_id": None,
                "targets": [],
                "capabilities": {"strict": False},
                "last_seen": now,
            }
            c.execute(
                "INSERT INTO devices VALUES(?,?,?,?)",
                (
                    device["id"],
                    owner,
                    hashlib.sha256(token.encode()).hexdigest(),
                    encode(device),
                ),
            )
            c.execute(
                "INSERT INTO public_devices VALUES(?,?,?)",
                (owner, installation_hash, device["id"]),
            )
            c.execute(
                "INSERT INTO public_registration_attempts VALUES(?,1,?) ON CONFLICT(client) DO UPDATE SET count=count+1",
                (address, now + 60),
            )
            audit(
                c, owner, "DEVICE_AUTO_REGISTERED", {"id": device["id"], "name": name}
            )
        return {"device_id": device["id"], "token": token}
