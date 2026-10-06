"""Build the full Windows installer with the installed Inno Setup 6 compiler."""

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib


def version(root):
    value = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Installer requires a three-part numeric version")
    return value


def installer_command(root, compiler):
    source = root / "dist/Qorgau-Student"
    for relative in (
        "Qorgau-Student.exe",
        "_internal/models/yolo11n.onnx",
        "_internal/models/face_landmarker.task",
    ):
        if not (source / relative).is_file():
            raise FileNotFoundError(f"Full Windows build is missing: {relative}")
    return [
        str(compiler),
        "/Qp",
        f"/DAppVersion={version(root)}",
        f"/DSourceDir={source}",
        f"/DOutputPath={root / 'artifacts'}",
        str(root / "packaging/windows/qorgau.iss"),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Build the Windows installer on Windows")
    root = Path(__file__).resolve().parents[1]
    compiler = args.iscc or shutil.which("ISCC.exe")
    if not compiler:
        compiler = (
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Inno Setup 6/ISCC.exe"
        )
    if not Path(compiler).is_file():
        parser.error("Install Inno Setup 6, or specify --iscc PATH")
    command = installer_command(root, compiler)
    source = root / "dist/Qorgau-Student"
    for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(root / name, source / name)
    shutil.copytree(root / "docs", source / "docs", dirs_exist_ok=True)
    (source / "training").mkdir(exist_ok=True)
    shutil.copy2(root / "training/README.md", source / "training/README.md")
    (root / "artifacts").mkdir(exist_ok=True)
    subprocess.run(command, cwd=root, check=True)
    result = root / f"artifacts/Qorgau-Student-Setup-{version(root)}-x64.exe"
    if not result.is_file():
        raise FileNotFoundError(result)
    print("Installer built:", result)


if __name__ == "__main__":
    main()
