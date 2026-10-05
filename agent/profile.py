"""Explicit native-host binding; never guess accounts by directory timestamps."""
import json
from pathlib import Path


def read_object(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def profile_identity(folder):
    config = read_object(Path(folder) / "config.json")
    identity = {k: config.get(k) for k in ("device_id", "server", "account_id")}
    if not identity["device_id"] or not identity["server"]:
        raise ValueError("PROFILE_NOT_REGISTERED")
    return identity


def load_binding(path, origin):
    binding = read_object(path)
    if origin != binding.get("origin"):
        raise ValueError("BROWSER_ORIGIN_MISMATCH")
    folder = Path(binding.get("folder", "")).resolve()
    if profile_identity(folder) != binding.get("identity"):
        raise ValueError("PROFILE_BINDING_CHANGED")
    return folder
