"""Hardware-free check executed by the same frozen EXE distributed to students."""
import json
from pathlib import Path
import statistics
import tempfile
import time


def run(output):
    import cv2
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from .resources import verified_models
    from .detector import PhoneDetector
    from .recording import ClipRecorder
    phone_path, face_path = verified_models()
    detector = PhoneDetector(phone_path)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    times = []
    for _ in range(12):
        start = time.perf_counter()
        assert detector.detect(frame) == []
        times.append(time.perf_counter() - start)
    with vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(face_path)), num_faces=2,
    )) as face:
        result = face.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=frame))
        assert not result.face_landmarks
    with tempfile.TemporaryDirectory(prefix="qorgau-selftest-") as folder:
        recorder = ClipRecorder(Path(folder))
        try:
            for i in range(21):
                recorder.push(i / 10, frame)
                if i == 10:
                    recorder.mark({"id": "selftest", "at": 1})
            clip = recorder.completed(float("inf"))[0]
            capture = cv2.VideoCapture(clip["path"])
            ok, decoded = capture.read()
            capture.release()
            assert ok and decoded.shape == frame.shape
        finally:
            recorder.close()
    report = {"result": "PASS", "kind": "synthetic packaging test; no webcam or accuracy claim",
              "models_verified": True, "onnx_inference": True, "face_landmarker": True,
              "h264_encode_decode": True, "phone_cpu_median_ms": round(statistics.median(times[2:]) * 1000, 2)}
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
