"""Register only after the user explicitly runs this installer with extension ID."""

import argparse
import json
import os
from pathlib import Path
import shlex
import sys

parser = argparse.ArgumentParser()
parser.add_argument(
    "extension_id", help="ID unpacked extension from chrome://extensions"
)
parser.add_argument(
    "--browser", choices=["chrome", "chromium", "edge"], default="chrome"
)
args = parser.parse_args()
if len(args.extension_id) != 32 or any(
    c not in "abcdefghijklmnop" for c in args.extension_id
):
    parser.error("Некорректный ID расширения Chrome")
root = Path(__file__).resolve().parents[1]
folder = Path.home() / ".qorgau" / "native"
folder.mkdir(parents=True, exist_ok=True)
if os.name == "nt":
    launcher = folder / "host.cmd"
    launcher.write_text(
        f'@echo off\ncd /d "{root}"\n"{sys.executable}" -m agent.native_host\n'
    )
else:
    launcher = folder / "host.sh"
    launcher.write_text(
        f"#!/bin/sh\ncd {shlex.quote(str(root))}\nexec {shlex.quote(sys.executable)} -m agent.native_host\n"
    )
    launcher.chmod(0o700)
manifest = {
    "name": "kz.qorgau.agent",
    "description": "Qorgau local observation bridge",
    "path": str(launcher),
    "type": "stdio",
    "allowed_origins": [f"chrome-extension://{args.extension_id}/"],
}
if os.name == "nt":
    import winreg

    path = folder / "kz.qorgau.agent.json"
    vendor = "Microsoft\\Edge" if args.browser == "edge" else "Google\\Chrome"
    key = winreg.CreateKey(
        winreg.HKEY_CURRENT_USER,
        f"Software\\{vendor}\\NativeMessagingHosts\\kz.qorgau.agent",
    )
    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(path))
    winreg.CloseKey(key)
else:
    profile = {
        "chrome": "google-chrome",
        "chromium": "chromium",
        "edge": "microsoft-edge",
    }[args.browser]
    path = (
        Path.home()
        / ".config"
        / profile
        / "NativeMessagingHosts"
        / "kz.qorgau.agent.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(manifest, indent=2))
print("Локальный мост зарегистрирован:", path)
