"""One-time per-browser registration of the bundled native helper."""
import json
import os
from pathlib import Path
import re
import shutil
from .profile import profile_identity, read_object
from .resources import resource_root
from shared.storage import atomic_json


def install(folder, extension_id, browser_instance, browser="edge", replace=False):
    if os.name != "nt":
        raise ValueError("Bundled native installation currently supports Windows only")
    if not re.fullmatch("[a-p]{32}", extension_id) or browser not in ("chrome", "edge"):
        raise ValueError("Некорректный браузер или ID расширения")
    from uuid import UUID
    browser_instance = str(UUID(browser_instance))
    helper = resource_root() / "native" / "Qorgau-NativeHost.exe"
    if not helper.is_file():
        helper = resource_root() / "dist" / "Qorgau-NativeHost.exe"
    if not helper.is_file():
        raise ValueError("В сборке нет NativeHost. Пересоберите полный пакет.")
    destination = Path.home() / ".qorgau" / "native" / browser
    destination.mkdir(parents=True, exist_ok=True)
    binding = {"folder": str(folder.resolve()), "identity": profile_identity(folder),
               "origin": f"chrome-extension://{extension_id}/", "browser_instance": browser_instance}
    previous = read_object(destination / "binding.json")
    if previous and previous != binding and not replace:
        raise ValueError("Браузер уже привязан к другому профилю. Нужен явный --replace-binding.")
    executable = destination / "Qorgau-NativeHost.exe"
    shutil.copyfile(helper, executable)
    atomic_json(destination / "binding.json", binding)
    manifest = destination / "kz.qorgau.agent.json"
    atomic_json(manifest, {"name": "kz.qorgau.agent", "description": "Qorgau observation bridge",
                          "path": str(executable), "type": "stdio", "allowed_origins": [binding["origin"]]})
    import winreg
    vendor = "Microsoft\\Edge" if browser == "edge" else "Google\\Chrome"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"Software\\{vendor}\\NativeMessagingHosts\\kz.qorgau.agent") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest))
    return manifest
