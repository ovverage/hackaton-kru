"""Shared first-run registration for desktop and command-line clients."""

from pathlib import Path
from urllib.parse import urlparse
import httpx


def server_address(value: str, *, testing=False):
    address = value.strip().rstrip("/")
    parsed = urlparse(address)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Проверьте порт в адресе сервера") from error
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise ValueError(
            "Укажите адрес сервера, например https://proctor.university.kz, без пути и параметров"
        )
    local = ("localhost", "127.0.0.1", "::1") + (("testserver",) if testing else ())
    if parsed.scheme != "https" and parsed.hostname not in local:
        raise ValueError(
            "Для подключения к компьютеру преподавателя по сети нужен адрес HTTPS"
        )
    if port is not None and port == 0:
        raise ValueError("Проверьте порт в адресе сервера")
    return address


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
