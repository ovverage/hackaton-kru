"""Build a portable student app on its target OS. Windows builds must run on Windows."""

from pathlib import Path
import argparse
import subprocess
import sys
import importlib.util

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
else:
    for model in ("yolo11n.pt", "face_landmarker.task"):
        if not (root / "models" / model).is_file():
            parser.error("Сначала выполните python scripts/fetch_models.py")
    command.extend(["--add-data", str(root / "models") + ":models"])
    for module in ("mediapipe", "ultralytics"):
        command.extend(["--collect-data", module])
    command.extend(
        ["--collect-binaries", "mediapipe", "--collect-all", "imageio_ffmpeg"]
    )
    # torchvision >=0.29 uses _C_stable/image_stable instead of the old _C module.
    # Current upstream hooks still name _C, so include the actual native libraries.
    vision_spec = importlib.util.find_spec("torchvision")
    for folder in vision_spec.submodule_search_locations:
        for binary in Path(folder).rglob("*"):
            if binary.suffix.lower() in (".so", ".pyd", ".dll", ".dylib"):
                destination = "torchvision/" + str(
                    binary.parent.relative_to(folder)
                ).replace("\\", "/")
                command.extend(["--add-binary", str(binary) + ":" + destination])
    # Do not import every training/tracking module while building the inference client.
    for module in (
        "pytest",
        "sklearn",
        "IPython",
        "notebook",
        "tensorflow",
        "jax",
        "tkinter",
    ):
        command.extend(["--exclude-module", module])
# QWebEngine resources and helpers are collected by PyInstaller's Qt hook.
command.extend(["--hidden-import", "PySide6.QtWebEngineWidgets"])
command.append(str(root / "agent" / "student_entry.py"))
subprocess.run(command, cwd=root, check=True)
print("Приложение собрано:", root / "dist" / "Qorgau-Student")
print(
    "CV включён в сборку."
    if args.with_cv
    else "Сборка интерфейса и связи: CV-модули не включены."
)
