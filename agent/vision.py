"""Experimental local CV pipeline. Calibrated eye/head features need classroom validation."""

from __future__ import annotations
from collections import deque
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor


class Camera:
    def __init__(self, index, phone_model: Path, face_model: Path, *, calibrate=True):
        import cv2
        import numpy as np
        import mediapipe as mp
        from ultralytics import YOLO
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        for path in (phone_model, face_model):
            if not path.is_file():
                raise ValueError(f"Локальная модель не найдена: {path}")
        self.cv2, self.np, self.mp = cv2, np, mp
        self.phone = YOLO(str(phone_model))
        names = self.phone.names
        self.phone_class = next(
            (k for k, v in names.items() if v == "cell phone"), None
        )
        if self.phone_class is None:
            raise ValueError("Модель должна содержать класс COCO cell phone")
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
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.capture.set(cv2.CAP_PROP_FPS, 15)
        if not self.capture.isOpened():
            raise OSError("Камера не открылась")
        self.timestamp = 0
        self.centres = {}
        self.last_frame = 0
        self.frame_digest = None
        self.frame_changed_at = time.monotonic()
        if calibrate:
            self.calibrate()

    def face_features(self, frame):
        self.timestamp = max(self.timestamp + 1, int(time.monotonic() * 1000))
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        result = self.face.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb),
            self.timestamp,
        )
        faces = result.face_landmarks
        if len(faces) != 1 or len(faces[0]) < 478:
            return len(faces), None
        p = faces[0]
        from shared.gaze import features

        vector = features(p)
        return len(faces), self.np.asarray(vector) if vector is not None else None

    def calibrate(self):
        cv2 = self.cv2
        prompts = [
            ("SCREEN", "Look at screen centre"),
            ("SCREEN_LEFT", "Look at LEFT edge of screen"),
            ("SCREEN_RIGHT", "Look at RIGHT edge of screen"),
            ("SCREEN_TOP", "Look at TOP edge of screen"),
            ("SCREEN_BOTTOM", "Look at BOTTOM edge of screen"),
            ("DOWN", "Look down below screen"),
            ("LEFT", "Look to YOUR left"),
            ("RIGHT", "Look to YOUR right"),
        ]
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
        baseline = self.centres["SCREEN"]
        if any(
            self.np.linalg.norm((v - baseline) * [1, 0.45, 1, 1]) < 0.08
            for k, v in self.centres.items()
            if not k.startswith("SCREEN")
        ):
            raise ValueError(
                "Позиции калибровки неразличимы. Измените положение камеры и повторите."
            )

    def read(self):
        # Cap inference and clip buffer at 10 FPS.
        delay = 0.1 - (time.monotonic() - self.last_frame)
        if delay > 0:
            time.sleep(delay)
        self.last_frame = time.monotonic()
        ok, frame = self.capture.read()
        if not ok:
            raise OSError("Не получен кадр камеры")
        frame = self.cv2.resize(frame, (640, 480))
        import hashlib

        digest = hashlib.blake2s(frame.tobytes()).digest()
        if digest != self.frame_digest:
            self.frame_digest = digest
            self.frame_changed_at = time.monotonic()
        elif time.monotonic() - self.frame_changed_at > 5:
            raise OSError("Изображение камеры не меняется более 5 секунд")
        faces, feature = self.face_features(frame)
        direction = "UNKNOWN"
        if feature is not None:
            distances = {
                k: float(self.np.linalg.norm((feature - v) * [1, 0.45, 1, 1]))
                for k, v in self.centres.items()
            }
            best = min(distances, key=distances.get)
            ordered = sorted(distances.values())
            if distances[best] < 0.25 and ordered[1] - ordered[0] > 0.035:
                direction = "SCREEN" if best.startswith("SCREEN") else best
        result = self.phone.predict(
            frame, classes=[self.phone_class], conf=0.4, imgsz=640, verbose=False
        )[0]
        confidence = max((float(x) for x in result.boxes.conf), default=0.0)
        return frame, {
            "direction": direction,
            "phone_confidence": confidence,
            "faces": faces,
        }

    def close(self):
        self.capture.release()
        self.face.close()


class ClipRecorder:
    """10 seconds before trigger, 5 after; H.264 clips with actual sample timing."""

    def __init__(self, folder: Path):
        import cv2

        self.cv2 = cv2
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        try:
            import imageio_ffmpeg

            self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            self.ffmpeg = shutil.which("ffmpeg")
        if not self.ffmpeg:
            raise ValueError("Для записи фрагментов установите FFmpeg")
        self.frames = deque()
        self.pending = []
        self.encoding = []
        self.pool = ThreadPoolExecutor(max_workers=1)

    def push(self, t, frame):
        ok, jpeg = self.cv2.imencode(".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            raise OSError("Не удалось сохранить кадр")
        item = (t, jpeg.tobytes())
        self.frames.append(item)
        while self.frames and t - self.frames[0][0] > 10:
            self.frames.popleft()
        for incident in self.pending:
            incident["frames"].append(item)

    def mark(self, event):
        self.pending.append(
            {"id": event["id"], "due": event["at"] + 5, "frames": list(self.frames)}
        )

    def completed(self, t):
        ready = []
        for incident in list(self.pending):
            if t < incident["due"] or len(incident["frames"]) < 2:
                continue
            if len(self.encoding) >= 8:
                raise OSError("Очередь кодирования переполнена")
            self.encoding.append(self.pool.submit(self.encode, incident))
            self.pending.remove(incident)
        for future in list(self.encoding):
            if future.done() or t == float("inf"):
                self.encoding.remove(future)
                ready.append(future.result(timeout=50))
        return ready

    def encode(self, incident):
        frames = incident["frames"]
        if sum(p.stat().st_size for p in self.folder.glob("*.mp4")) > 2 * 1024**3:
            raise OSError("Локальная очередь видео превысила 2 ГБ")
        output = self.folder / (incident["id"] + ".mp4")
        with tempfile.TemporaryDirectory(prefix="qorgau-clip-") as tmp:
            directory = Path(tmp)
            lines = []
            for i, (at, jpg) in enumerate(frames):
                (directory / f"{i}.jpg").write_bytes(jpg)
                duration = frames[i + 1][0] - at if i + 1 < len(frames) else 0.1
                lines.extend(
                    [f"file '{i}.jpg'", f"duration {max(0.001, duration):.6f}"]
                )
            lines.append(f"file '{len(frames) - 1}.jpg'")
            (directory / "frames.txt").write_text("\n".join(lines))
            result = subprocess.run(
                [
                    self.ffmpeg,
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "1",
                    "-i",
                    str(directory / "frames.txt"),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "25",
                    "-pix_fmt",
                    "yuv420p",
                    "-fps_mode",
                    "vfr",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                capture_output=True,
                timeout=45,
            )
            if result.returncode:
                raise OSError("Ошибка кодирования видеозаписи")
        return {
            "event_id": incident["id"],
            "path": str(output),
            "start": frames[0][0],
            "end": frames[-1][0],
        }

    def close(self):
        self.pool.shutdown(wait=True)
