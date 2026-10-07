"""Verified local assets, independent of a working directory or PyInstaller temp path."""
import hashlib
import json
from pathlib import Path
import sys


def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


RUNTIME_MODELS = ('yolo11n.onnx', 'face_landmarker.task', 'face_yolov8n.onnx', 'gaze-direction.json')
PUBLIC_GAZE_MODELS = ('gaze-public.onnx', 'gaze-public.json')


def verified_assets():
    root = resource_root()
    try:
        manifest = json.loads((root / "model-manifest.json").read_text(encoding="utf-8"))
        paths = []
        gaze_profile = manifest.get("runtime_gaze", "legacy")
        if gaze_profile not in ("legacy", "public-gaze-v1"):
            raise ValueError("Неизвестный комплект модели взгляда. Повторно скачайте приложение.")
        names = RUNTIME_MODELS + (PUBLIC_GAZE_MODELS if gaze_profile == "public-gaze-v1" else ())
        for name in names:
            item = next(x for x in manifest["files"] if x["file"] == name)
            path = root / "models" / name
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != item["sha256"]:
                raise ValueError(f"Повреждена модель {name}. Повторно скачайте приложение.")
            paths.append(path)
        if gaze_profile == "legacy" and any((root / "models" / name).exists() for name in PUBLIC_GAZE_MODELS):
            raise ValueError("Модель взгляда не зарегистрирована в комплекте. Повторно скачайте приложение.")
        return tuple(paths)
    except (OSError, KeyError, StopIteration, json.JSONDecodeError) as error:
        raise ValueError("В сборке нет проверенного комплекта моделей. Скачайте полный EXE.") from error


def verified_models():
    """Keep the camera's public phone/landmarker pair while verifying all assets."""
    return verified_assets()[:2]


def ffmpeg_executable():
    from imageio_ffmpeg import get_ffmpeg_exe
    # imageio's wheel includes the binary; no runtime network/download is used.
    return get_ffmpeg_exe()
