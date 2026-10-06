"""Install/repair/test/uninstall only on a disposable Windows GitHub runner."""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import traceback
import uuid

from build_installer import version

APP_ID = "{AF12B236-70BA-4B34-A991-A94AC778451D}_is1"
MUTEX = r"Local\Qorgau.Student.Running"


def run(args, *, success=True):
    completed = subprocess.run([str(arg) for arg in args], timeout=300)
    if (completed.returncode == 0) != success:
        raise RuntimeError(
            f"Unexpected exit {completed.returncode}: {Path(args[0]).name}"
        )


def smoke(installer, output):
    import winreg

    root = Path(__file__).resolve().parents[1]
    report = {"version": version(root), "platform": platform.platform(), "checks": []}
    marker = None
    try:
        with tempfile.TemporaryDirectory(prefix="qorgau installer ") as temporary:
            sandbox = Path(temporary)
            target = sandbox / "Qorgau Student"
            suffix = uuid.uuid4().hex
            group = "Qorgau"
            # This runner-only sentinel tests the real application-data directory.
            data = Path.home() / ".qorgau"
            data.mkdir(exist_ok=True)
            marker = data / f"installer-ci-{suffix}.txt"
            marker.write_text(suffix, encoding="utf-8")
            flags = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
            setup = [
                installer,
                *flags,
                "/LANG=russian",
                f"/DIR={target}",
                "/TASKS=desktopicon",
            ]
            run(setup)
            executable = target / "Qorgau-Student.exe"
            uninstaller = target / "unins000.exe"
            assert executable.is_file() and uninstaller.is_file()
            registry = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                registry,
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                assert (
                    winreg.QueryValueEx(key, "DisplayVersion")[0] == report["version"]
                )
                location = winreg.QueryValueEx(key, "InstallLocation")[0]
                assert Path(location) == target
            report["checks"].append("per-user installation and uninstall registration")
            shortcut = (
                Path(os.environ["APPDATA"])
                / "Microsoft/Windows/Start Menu/Programs"
                / group
                / "Qorgau Student.lnk"
            )
            desktop_path = ctypes.create_unicode_buffer(32768)
            shell = ctypes.WinDLL("shell32")
            assert shell.SHGetFolderPathW(None, 0x10, None, 0, desktop_path) == 0
            desktop_shortcut = Path(desktop_path.value) / "Qorgau Student.lnk"
            assert shortcut.is_file() and desktop_shortcut.is_file()
            report["checks"].append("Start menu and requested desktop shortcuts")
            run(setup)
            report["checks"].append("same-version repair/reinstall")

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [
                ctypes.c_void_p,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            ]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.CreateMutexW(None, False, MUTEX)
            assert handle
            try:
                run(setup, success=False)
                run([uninstaller, *flags], success=False)
                assert executable.is_file()
            finally:
                kernel.CloseHandle(handle)
            report["checks"].append("running-client mutex blocks install and uninstall")

            runtime_report = sandbox / "installed runtime.json"
            run([executable, "--self-test", runtime_report])
            runtime = json.loads(runtime_report.read_text(encoding="utf-8"))
            assert runtime["status"] == "passed" and runtime["frozen"]
            assert (
                runtime["version"] == report["version"] and runtime["installer_mutex"]
            )
            report["runtime"] = runtime
            report["checks"].append("installed browser, CV models and H264 runtime")
            run([uninstaller, *flags])
            assert (
                not executable.exists()
                and not shortcut.exists()
                and not desktop_shortcut.exists()
            )
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    registry,
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                ):
                    raise AssertionError("Uninstall registry entry was left behind")
            except FileNotFoundError:
                pass
            assert marker.read_text(encoding="utf-8") == suffix
            report["checks"].extend(
                [
                    "uninstall removes app, shortcuts and registration",
                    "user data retained",
                ]
            )
            report["status"] = "passed"
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
    finally:
        if marker is not None:
            marker.unlink(
                missing_ok=True
            )  # Only our unique CI sentinel, never user data.
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if report["status"] != "passed":
        print(report["error"])
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("installer-selftest.json"))
    args = parser.parse_args()
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error(
            "Run this destructive lifecycle test only on a disposable Windows GitHub runner"
        )
    smoke(args.installer.resolve(), args.output.resolve())
