"""Packaged diagnostic: no camera, keyboard hooks, credentials or network."""

import json
import os
import platform
import sys
import traceback


def check_browser():
    """Exercise the packaged Chromium helper/resources, without any network I/O."""
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication.instance() or QApplication([])
    loop = QEventLoop()
    view = QWebEngineView()
    observed = []

    def received(value):
        observed.append(value)
        loop.quit()

    def loaded(ok):
        if ok:
            view.page().runJavaScript(
                "document.getElementById('qorgau-check').textContent", received
            )
        else:
            loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    view.loadFinished.connect(loaded)
    view.setHtml('<!doctype html><p id="qorgau-check">Qorgau browser ready</p>')
    timer.start(20000)
    loop.exec()
    timer.stop()
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    if observed != ["Qorgau browser ready"]:
        raise RuntimeError("Packaged browser renderer did not load the offline test page")


def selftest(output):
    result = {
        "version": "0.3.1",
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
        from .resources import model_path
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        check_browser()
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
            from .install_guard import hold_installation_mutex
            from .windows_guard import WindowsGuard

            result["installer_mutex"] = bool(hold_installation_mutex())
            guard = WindowsGuard()
            result["win32_enumeration"] = len(guard.windows())
        result.update(
            status="passed",
            cv2=cv2.__version__,
            mediapipe=mp.__version__,
            torch=torch.__version__,
            checks=[
                "QtWebEngine offline page rendering and JavaScript",
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
