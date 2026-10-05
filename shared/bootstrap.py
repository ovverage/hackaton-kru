"""Small enrollment trailer attached to a reusable PyInstaller one-file executable."""

import json
import struct
from pathlib import Path
from urllib.parse import urlparse

MAGIC = b"QORGAU-BOOTSTRAP-V1\x00"
MAX_PAYLOAD = 8192


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
    if port == 0:
        raise ValueError("Проверьте порт в адресе сервера")
    return address


def validate_bootstrap(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Повреждены настройки EXE. Скачайте приложение заново.")
    for key, limit in (
        ("package_id", 80),
        ("account_id", 80),
        ("token", 128),
        ("room", 80),
        ("server", 2048),
    ):
        if not isinstance(value.get(key), str) or not 1 <= len(value[key]) <= limit:
            raise ValueError("Повреждены настройки EXE. Скачайте приложение заново.")
    return {**value, "server": server_address(value["server"])}


def bootstrap_trailer(config):
    payload = json.dumps(validate_bootstrap(config), ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("Слишком большой пакет настройки")
    return payload + struct.pack("<I", len(payload)) + MAGIC


def read_bootstrap(executable: Path):
    with executable.open("rb") as stream:
        size = stream.seek(0, 2)
        footer_size = 4 + len(MAGIC)
        if size < footer_size:
            return None
        stream.seek(-len(MAGIC), 2)
        if stream.read() != MAGIC:
            return None
        stream.seek(-footer_size, 2)
        length = struct.unpack("<I", stream.read(4))[0]
        if not 0 < length <= MAX_PAYLOAD or length + footer_size > size:
            raise ValueError("Повреждены настройки EXE. Скачайте приложение заново.")
        stream.seek(-(footer_size + length), 2)
        try:
            return validate_bootstrap(json.loads(stream.read(length)))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "Повреждены настройки EXE. Скачайте приложение заново."
            ) from error
