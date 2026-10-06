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
        self.frame_digest = None
        self.frame_changed_at = time.monotonic()
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
        from shared.gaze import features
        vector = features(faces[0])
        return len(faces), self.np.asarray(vector) if vector is not None else None

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
        import hashlib
        digest = hashlib.blake2s(frame.tobytes()).digest()
        if digest != self.frame_digest:
            self.frame_digest = digest
            self.frame_changed_at = time.monotonic()
        elif time.monotonic() - self.frame_changed_at > 5:
            raise OSError("CAMERA_FROZEN: изображение не меняется более 5 секунд")
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


from .recording import ClipRecorder as ClipRecorder  # noqa: E402 - public compatibility export
