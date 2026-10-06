import os
from pathlib import Path

import pytest

from agent.install_guard import INSTALL_MUTEX, hold_installation_mutex
from scripts.build_installer import installer_command, version


def test_installer_rejects_incomplete_payload(tmp_path):
    with pytest.raises(FileNotFoundError, match="Full Windows build"):
        installer_command(tmp_path, "ISCC.exe")


def test_installer_paths_with_spaces_and_version(tmp_path):
    root = tmp_path / "A project with spaces"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nversion = "0.3.1"\n')
    for name in (
        "Qorgau-Student.exe",
        "_internal/models/yolo11n.pt",
        "_internal/models/face_landmarker.task",
    ):
        path = root / "dist/Qorgau-Student" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    command = installer_command(root, "C:/Program Files (x86)/Inno Setup 6/ISCC.exe")
    assert version(root) == "0.3.1"
    assert "/DAppVersion=0.3.1" in command
    assert f"/DSourceDir={root / 'dist/Qorgau-Student'}" in command
    assert command[-1] == str(root / "packaging/windows/qorgau.iss")


def test_installer_safety_contract():
    root = Path(__file__).resolve().parents[1]
    script = (root / "packaging/windows/qorgau.iss").read_text()
    assert f"AppMutex={INSTALL_MUTEX}" in script
    assert "PrivilegesRequired=lowest" in script
    assert "CloseApplications=no" in script
    assert "\n[UninstallDelete]" not in script
    assert 'Parameters: "--show"' in script


def test_installer_rejects_non_numeric_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.3.1-dev"\n')
    with pytest.raises(ValueError, match="three-part"):
        version(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows installer mutex")
def test_installation_mutex_is_held_for_process_lifetime():
    import ctypes
    from ctypes import wintypes

    handle = hold_installation_mutex()
    assert handle and handle == hold_installation_mutex()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    opened = kernel.OpenMutexW(0x00100000, False, INSTALL_MUTEX)
    assert opened
    kernel.CloseHandle(opened)
