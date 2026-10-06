"""Small durable JSON files shared by the agent and its native bridge."""
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    fd, name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
