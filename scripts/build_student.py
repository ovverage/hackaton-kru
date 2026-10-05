"""Build a portable student app on its target OS. Windows builds must run on Windows."""

from pathlib import Path
import argparse
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument(
    "--with-cv",
    action="store_true",
    help="Include installed CV libraries. Models and FFmpeg are provisioned separately.",
)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
command = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    "--onedir",
    "--name",
    "Qorgau-Student",
    "--paths",
    str(root),
    "--distpath",
    str(root / "dist"),
    "--workpath",
    str(root / "build"),
    "--specpath",
    str(root / "build"),
    "--collect-data",
    "certifi",
]
if not args.with_cv:
    for module in ("cv2", "numpy", "ultralytics", "mediapipe", "torch", "torchvision"):
        command.extend(["--exclude-module", module])
command.append(str(root / "agent" / "student_entry.py"))
subprocess.run(command, cwd=root, check=True)
print("Приложение собрано:", root / "dist" / "Qorgau-Student")
print(
    "CV включён в сборку."
    if args.with_cv
    else "Сборка интерфейса и связи: CV-модули не включены."
)
