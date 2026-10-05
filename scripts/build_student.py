"""Build a portable student app on its target OS. Windows builds must run on Windows."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument(
    "--console",
    action="store_true",
    help="Build a separate diagnostic executable with console output.",
)
parser.add_argument(
    "--onedir",
    action="store_true",
    help="Build a folder instead of the single EXE used for classroom downloads.",
)
parser.add_argument(
    "--with-cv",
    action="store_true",
    help="Include verified models, ONNX/MediaPipe and the bundled FFmpeg encoder.",
)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
name = "Qorgau-Student-Debug" if args.console else "Qorgau-Student"
command = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--console" if args.console else "--windowed",
    "--onedir" if args.onedir else "--onefile",
    "--name",
    name,
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
else:
    sys.path.insert(0, str(root))
    from agent.resources import verified_models, ffmpeg_executable
    models = verified_models()
    ffmpeg_executable()
    notices = root / "dist/third-party"
    if not (notices / "package-inventory.json").is_file():
        raise ValueError("Run scripts/prepare_licenses.py before the release build")
    command.extend(["--add-data", str(notices) + os.pathsep + "third-party"])
    for module in ("torch", "torchvision", "ultralytics", "IPython"):
        command.extend(["--exclude-module", module])
    for module in ("mediapipe", "onnxruntime", "imageio_ffmpeg"):
        command.extend(["--collect-all", module])
    for model in models:
        command.extend(["--add-data", str(model) + os.pathsep + "models"])
    command.extend(["--add-data", str(root / "model-manifest.json") + os.pathsep + "."])
command.append(str(root / "agent" / "student_entry.py"))
environment = os.environ.copy()
if sys.platform == "win32":
    # Do not bundle an unrelated application's old UCRT/Qt DLLs from PATH.
    # PyInstaller's package hooks add the dependencies' own DLL directories.
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join(
        map(
            str,
            [
                Path(sys.executable).parent,
                Path(sys.base_prefix),
                windows / "System32",
                windows,
            ],
        )
    )
if sys.platform == "win32":
    for helper_name, entry in (("Qorgau-NativeHost", "native_entry.py"), ("Qorgau-SecurityBridge", "seb_entry.py")):
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
                        "--name", helper_name, "--paths", str(root), "--distpath", str(root / "dist"),
                        "--workpath", str(root / "build"), "--specpath", str(root / "build"),
                        str(root / "agent" / entry)], cwd=root, check=True, env=environment)
    command[-1:-1] = ["--add-binary", str(root / "dist/Qorgau-NativeHost.exe") + os.pathsep + "native"]
subprocess.run(command, cwd=root, check=True, env=environment)
artifact = (
    root
    / "dist"
    / (name + ".exe" if sys.platform == "win32" and not args.onedir else name)
)
print("Приложение собрано:", artifact)
if not args.onedir and sys.platform == "win32":
    print(
        "Загрузите этот EXE на сервер в dist/ или укажите PROCTOR_STUDENT_EXE. Панель добавит настройки аудитории при скачивании."
    )
print(
    "CV включён в сборку."
    if args.with_cv
    else "Сборка интерфейса и связи: CV-модули не включены."
)
