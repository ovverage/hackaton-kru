"""Experimental local CV pipeline. Calibrated eye/head features need classroom validation."""

from __future__ import annotations
from pathlib import Path
import time


class Camera:
    def __init__(self, index, phone_model: Path, face_model: Path, *, calibrate=True):
        import cv2
        import numpy as np
        import mediapipe as mp
        from .detector import PhoneDetector
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        for path in (phone_model, face_model):
            if not path.is_file():
                raise ValueError(f"Локальная модель не найдена: {path}")
        self.cv2, self.np, self.mp = cv2, np, mp
        self.phone = PhoneDetector(phone_model)
        self.face = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(face_model)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=2,
                min_face_detection_confidence=0.6,
                min_face_presence_confidence=0.6,
                min_tracking_confidence=0.6,
            )
        )
        self.capture = cv2.VideoCapture(index)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.capture.set(cv2.CAP_PROP_FPS, 15)
        if not self.capture.isOpened():
            self.capture.release()
            self.face.close()
            raise OSError("Камера не открылась")
        self.timestamp = 0
        self.centres = {}
        self.last_frame = 0
        from .behavior import PhoneRaising
        self.raising = PhoneRaising()
        if calibrate:
            self.calibrate()

    def face_features(self, frame, timestamp_ms=None):
        self.timestamp = max(self.timestamp + 1, int(time.monotonic() * 1000) if timestamp_ms is None else timestamp_ms)
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        result = self.face.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb),
            self.timestamp,
        )
        faces = result.face_landmarks
        if len(faces) != 1 or len(faces[0]) < 478:
            return len(faces), None
        p = faces[0]

        # Eye coordinates are local to the eyelids; camera image is not mirrored.
        def eye(corner_a, corner_b, top, bottom, iris):
            a, b = p[corner_a], p[corner_b]
            width = abs(b.x - a.x)
            height = abs(p[bottom].y - p[top].y)
            if width < 0.015 or height < 0.003 or height / width < 0.12:
                return None
            return [
                (p[iris].x - min(a.x, b.x)) / width,
                (p[iris].y - min(p[top].y, p[bottom].y)) / height,
            ]

        left = eye(33, 133, 159, 145, 468)
        right = eye(362, 263, 386, 374, 473)
        if left is None or right is None:
            return len(faces), None
        # Coarse normalized nose position gives head motion context; not a gaze angle.
        width = abs(p[454].x - p[234].x)
        height = abs(p[152].y - p[10].y)
        if width < 0.1 or height < 0.1:
            return len(faces), None
        feature = self.np.array(
            [
                (left[0] + right[0]) / 2,
                (left[1] + right[1]) / 2,
                (p[1].x - p[234].x) / width,
                (p[1].y - p[10].y) / height,
            ],
            dtype=float,
        )
        return len(faces), feature

    def calibrate(self):
        cv2 = self.cv2
        from .behavior import POSITIONS
        prompts = [(key, "Look at: " + key.replace("_", " ")) for key, _ in POSITIONS]
        try:
            for label, prompt in prompts:
                samples = []
                collect = False
                while len(samples) < 25:
                    ok, frame = self.capture.read()
                    if not ok:
                        raise OSError("Камера недоступна во время калибровки")
                    faces, feature = self.face_features(frame)
                    if collect and feature is not None and faces == 1:
                        samples.append(feature)
                    shown = frame.copy()
                    cv2.rectangle(shown, (0, 0), (640, 85), (26, 54, 38), -1)
                    cv2.putText(
                        shown,
                        prompt,
                        (15, 28),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (230, 245, 224),
                        2,
                    )
                    cv2.putText(
                        shown,
                        f"SPACE: capture | ESC: cancel | {len(samples)}/25",
                        (15, 60),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (205, 225, 200),
                        1,
                    )
                    cv2.imshow("Qorgau calibration - camera is active", shown)
                    key = cv2.waitKey(30) & 0xFF
                    if key == 27:
                        raise ValueError("Калибровка отменена")
                    if key == 32:
                        collect = True
                self.centres[label] = self.np.median(samples, axis=0)
            self.validate_calibration()
        finally:
            cv2.destroyAllWindows()

    def validate_calibration(self):
        from .behavior import validate_centres
        validate_centres(self.centres)

    def read(self):
        # Request 720p/15fps for recording; inference uses a smaller image.
        delay = 1/15 - (time.monotonic() - self.last_frame)
        if delay > 0:
            time.sleep(delay)
        self.last_frame = time.monotonic()
        ok, frame = self.capture.read()
        if not ok:
            raise OSError("Не получен кадр камеры")
        return frame, self.analyze(frame)

    def analyze(self, frame, at=None):
        """The same inference for live capture and timestamped offline evaluation."""
        frame = self.cv2.resize(frame, (640, 480))
        faces, feature = self.face_features(frame, None if at is None else int(at * 1000))
        from .behavior import classify_gaze
        direction = classify_gaze(feature, self.centres)
        detections = self.phone.detect(frame)
        confidence = max((x["confidence"] for x in detections), default=0.0)
        return {
            "direction": direction,
            "phone_confidence": confidence,
            "faces": faces,
            "phone_aiming": self.raising.update(time.monotonic() if at is None else at, detections, 640, 480),
        }

    def close(self):
        self.capture.release()
        self.face.close()


from .recording import ClipRecorder  # compatibility import
