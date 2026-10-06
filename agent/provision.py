"""Shared first-run registration for desktop and command-line clients."""

import hashlib
import json
import secrets
import socket
from pathlib import Path

import httpx

from shared.bootstrap import server_address, validate_bootstrap


class EnrollmentUnavailable(ValueError):
    """A transient failure that the desktop client may retry automatically."""


def bootstrap_data_dir(bootstrap, home=None):
    identity = bootstrap["server"] + "\n" + bootstrap["account_id"]
    scope = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return (home or Path.home()) / ".qorgau" / "accounts" / scope


def auto_enroll(folder: Path, bootstrap, *, transport=None):
    from .client import atomic_json

    bootstrap = validate_bootstrap(bootstrap)
    config_path = folder / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if (
            config.get("server") != bootstrap["server"]
            or config.get("account_id") != bootstrap["account_id"]
        ):
            raise ValueError(
                "В этой папке сохранено подключение к другому кабинету. Обратитесь к преподавателю."
            )
        return config
    identity_path = folder / "installation.json"
    if identity_path.exists():
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    else:
        identity = {"secret": secrets.token_urlsafe(32)}
        atomic_json(identity_path, identity)
    name = socket.gethostname()[:80] or "Компьютер"
    try:
        with httpx.Client(
            base_url=bootstrap["server"], timeout=8, transport=transport
        ) as client:
            response = client.post(
                "/api/agent/auto-enroll",
                json={
                    "token": bootstrap["token"],
                    "installation_secret": identity["secret"],
                    "name": name,
                },
            )
        if response.status_code >= 500 or response.status_code == 429:
            raise EnrollmentUnavailable(
                "Сервер временно недоступен. Подключение повторится автоматически."
            )
        if response.status_code == 403:
            detail = response.json().get("detail")
            raise ValueError(
                detail
                if isinstance(detail, str)
                else "Получите новый EXE у преподавателя."
            )
        if response.status_code >= 400:
            raise ValueError(
                "Сервер не поддерживает автоматическое подключение. Обратитесь к преподавателю."
            )
        result = response.json()
        if not isinstance(result, dict) or not all(
            isinstance(result.get(k), str) and result[k] for k in ("device_id", "token")
        ):
            raise ValueError(
                "Сервер вернул некорректный ответ подключения. Обратитесь к преподавателю."
            )
    except httpx.HTTPError as error:
        raise EnrollmentUnavailable(
            "Нет связи с сервером. Проверьте сеть; подключение повторится автоматически."
        ) from error
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError(
            "Адрес в EXE не ведёт к серверу Qorgau. Получите новый файл у преподавателя."
        ) from error
    config = {
        **result,
        "server": bootstrap["server"],
        "account_id": bootstrap["account_id"],
        "name": f"{bootstrap['room']} · {name}"[:80],
        "room": bootstrap["room"],
        "targets": [],
    }
    atomic_json(config_path, config)
    return config


def enroll(folder: Path, server: str, code: str, name: str, *, transport=None):
    from .client import atomic_json

    if (folder / "config.json").exists():
        raise ValueError(
            "Этот компьютер уже подключён. Повторная регистрация не нужна."
        )
    server = server_address(server, testing=transport is not None)
    code = code.strip().upper()
    name = name.strip()
    if len(code) != 8 or any(c not in "0123456789ABCDEF" for c in code):
        raise ValueError(
            "Код должен содержать 8 символов. Получите его у преподавателя."
        )
    if not 1 <= len(name) <= 80:
        raise ValueError("Укажите имя компьютера длиной до 80 символов")
    try:
        with httpx.Client(base_url=server, timeout=8, transport=transport) as client:
            response = client.post(
                "/api/agent/enroll", json={"code": code, "name": name}
            )
        if response.status_code == 403:
            raise ValueError(
                "Код истёк или уже использован. Попросите преподавателя создать новый."
            )
        response.raise_for_status()
        result = response.json()
        if (
            not isinstance(result.get("token"), str)
            or not result["token"]
            or not isinstance(result.get("device_id"), str)
        ):
            raise ValueError("Сервер вернул некорректный ответ регистрации")
    except httpx.HTTPError as error:
        raise ValueError(
            "Не удалось подключиться. Проверьте адрес сервера, сеть и сертификат."
        ) from error
    config = {**result, "server": server, "name": name, "targets": []}
    atomic_json(folder / "config.json", config)
    return config
