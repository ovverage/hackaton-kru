"""Packaged diagnostic: no camera, keyboard hooks, credentials or network."""

import json
import os
import platform
import sys
import traceback


def selftest(output):
    result = {
        "version": "0.3.0",
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    try:
        import cv2
        import imageio_ffmpeg
        import mediapipe as mp
        import numpy as np
        import torch
        from ultralytics import YOLO
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        from .resources import model_path
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        frame = np.zeros((320, 320, 3), dtype=np.uint8)
        phone = YOLO(str(model_path("yolo11n.pt")))
        prediction = phone.predict(frame, imgsz=320, verbose=False)
        assert "cell phone" in phone.names.values() and prediction
        with vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(
                    model_asset_path=str(model_path("face_landmarker.task"))
                )
            )
        ) as face:
            face.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=frame))
        import subprocess

        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=black:s=64x64:d=0.1",
                "-c:v",
                "libx264",
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
        )
        if os.name == "nt":
            from .windows_guard import WindowsGuard

            guard = WindowsGuard()
            result["win32_enumeration"] = len(guard.windows())
        result.update(
            status="passed",
            cv2=cv2.__version__,
            mediapipe=mp.__version__,
            torch=torch.__version__,
            checks=[
                "QtWebEngine import",
                "YOLO CPU inference",
                "MediaPipe inference",
                "H264 encoder",
            ],
        )
    except Exception:
        result.update(status="failed", error=traceback.format_exc())
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if result["status"] != "passed":
        raise SystemExit(1)
