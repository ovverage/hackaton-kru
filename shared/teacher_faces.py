"""YuNet/SFace identity matching with a limited head-motion challenge.

Official API: https://docs.opencv.org/4.13.0/d0/dd4/tutorial_dnn_face.html
This is a prototype identity comparison, not certified liveness/anti-spoofing.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
import threading

MODEL_ID = 'opencv-zoo-sface-2021dec-yunet-2023mar'
MATCH_THRESHOLD = .5
AMBIGUITY_MARGIN = .05
MAX_IMAGE_BYTES = 3 * 1024 * 1024


def image_dimensions(content):
    """Inspect supported compressed headers before OpenCV allocates pixels."""
    import struct
    if content.startswith(b'\x89PNG\r\n\x1a\n') and len(content) >= 24 and content[12:16] == b'IHDR':
        width, height = struct.unpack('>II', content[16:24])
    elif content.startswith(b'\xff\xd8'):
        offset, dimensions = 2, None
        while offset < len(content):
            if content[offset] != 255:
                break
            while offset < len(content) and content[offset] == 255:
                offset += 1
            if offset >= len(content):
                break
            marker = content[offset]
            offset += 1
            if marker in (0xD9, 0xDA):
                break
            if marker == 1 or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(content):
                break
            size = int.from_bytes(content[offset:offset + 2], 'big')
            if size < 2 or offset + size > len(content):
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and size >= 8:
                height, width = struct.unpack('>HH', content[offset + 3:offset + 7])
                dimensions = width, height
                break
            offset += size
        if dimensions is None:
            raise ValueError('FACE_IMAGE_INVALID')
        width, height = dimensions
    else:
        raise ValueError('FACE_IMAGE_FORMAT')
    if min(width, height) < 64 or width * height > 16_000_000:
        raise ValueError('FACE_IMAGE_DIMENSIONS')
    return width, height


def normalized_embedding(value):
    import numpy as np
    vector = np.asarray(value, dtype=np.float32)
    if vector.shape not in ((128,), (1, 128)) or not np.isfinite(vector).all():
        raise ValueError('FACE_EMBEDDING_INVALID')
    vector = vector.reshape(128)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm < 1e-8:
        raise ValueError('FACE_EMBEDDING_INVALID')
    return vector / norm


def verify_motion(observations, captured_ms, turn_sign):
    """Three frames, front/turn/front; timestamps alone do not prove freshness."""
    if len(observations) != 3 or len(captured_ms) != 3 or turn_sign not in (-1, 1):
        return False
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in captured_ms):
        return False
    a, b, c = captured_ms
    if not (a == 0 and b >= 250 and c - b >= 250 and 1000 <= c <= 35000):
        return False
    poses = [row.get('pose') for row in observations]
    if any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1.5 for x in poses):
        return False
    return (abs(poses[0]) <= .35 and turn_sign * (poses[1] - poses[0]) >= .18
            and abs(poses[2] - poses[0]) <= .12)


class TeacherFaceEngine:
    def __init__(self, models_dir, manifest_path=None):
        import cv2
        self.cv2 = cv2
        self.lock = threading.RLock()
        root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
        manifest_path = Path(manifest_path) if manifest_path else root / 'teacher-face-manifest.json'
        if not manifest_path.is_file():
            manifest_path = root / 'backend/proctor/assets/teacher-faces/teacher-face-manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('version') != MODEL_ID:
            raise ValueError('TEACHER_FACE_MODEL_VERSION')
        names = ('face_detection_yunet_2023mar.onnx', 'face_recognition_sface_2021dec.onnx')
        paths = []
        for name in names:
            entry = next(x for x in manifest['files'] if x['file'] == name)
            path = Path(models_dir) / name
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                    raise ValueError('TEACHER_FACE_MODEL_HASH')
            paths.append(str(path))
        try:
            self.detector = cv2.FaceDetectorYN.create(paths[0], '', (320, 320), .9, .3, 1000)
            self.recognizer = cv2.FaceRecognizerSF.create(paths[1], '')
        except cv2.error as error:
            raise ValueError('TEACHER_FACE_MODEL_LOAD') from error

    def decode(self, content):
        import numpy as np
        if not isinstance(content, bytes) or not 12 <= len(content) <= MAX_IMAGE_BYTES:
            raise ValueError('FACE_IMAGE_SIZE')
        image_dimensions(content)
        try:
            frame = self.cv2.imdecode(np.frombuffer(content, dtype=np.uint8), self.cv2.IMREAD_COLOR)
        except self.cv2.error as error:
            raise ValueError('FACE_IMAGE_INVALID') from error
        if frame is None or frame.ndim != 3 or not 64 <= min(frame.shape[:2]) or frame.shape[0] * frame.shape[1] > 16_000_000:
            raise ValueError('FACE_IMAGE_INVALID')
        return frame

    def detect(self, frame):
        try:
            return self._detect(frame)
        except self.cv2.error as error:
            raise ValueError('FACE_INFERENCE_FAILED') from error

    def _detect(self, frame):
        import numpy as np
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError('FACE_IMAGE_INVALID')
        height, width = frame.shape[:2]
        if min(height, width) < 64 or height * width > 16_000_000:
            raise ValueError('FACE_IMAGE_DIMENSIONS')
        scale = min(1., 1024 / max(width, height))
        image = self.cv2.resize(frame, (round(width * scale), round(height * scale))) if scale < 1 else frame
        h, w = image.shape[:2]
        with self.lock:
            self.detector.setInputSize((w, h))
            _, faces = self.detector.detect(image)
            if faces is None:
                return []
            if len(faces) > 10:
                raise ValueError('FACE_COUNT_LIMIT')
            result = []
            for face in faces:
                if not np.isfinite(face).all():
                    raise ValueError('FACE_DETECTION_INVALID')
                box = [float(face[0] / w), float(face[1] / h), float(face[2] / w), float(face[3] / h)]
                eye_distance = float(np.linalg.norm(face[4:6] - face[6:8]))
                if min(face[2:4]) < 40 or eye_distance < 10:
                    # Preserve small faces in the count; never enrol a multi-face photo.
                    result.append({'embedding': None, 'box': box, 'pose': None})
                    continue
                crop = self.recognizer.alignCrop(image, face)
                embedding = normalized_embedding(self.recognizer.feature(crop)).tolist()
                pose = float((face[8] - (face[4] + face[6]) / 2) / eye_distance)
                result.append({'embedding': embedding,
                               'box': box,
                               'pose': pose})
            return result

    def encode(self, frame):
        faces = self.detect(frame)
        if len(faces) != 1:
            raise ValueError('FACE_EXACTLY_ONE_REQUIRED')
        if faces[0]['embedding'] is None:
            raise ValueError('FACE_TOO_SMALL')
        return faces[0]

    def match(self, embedding, templates):
        if embedding is None:
            return None
        vector = normalized_embedding(embedding)
        scores = {}
        for item in templates[:20]:
            try:
                score = float(vector @ normalized_embedding(item['embedding']))
                key = str(item['teacher_id'])
                value = {'teacher_id': key, 'name': str(item['name']), 'score': score}
                if key not in scores or score > scores[key]['score']:
                    scores[key] = value
            except (ValueError, TypeError, KeyError):
                continue
        ordered = sorted(scores.values(), key=lambda item: item['score'], reverse=True)
        if not ordered or ordered[0]['score'] < MATCH_THRESHOLD:
            return None
        if len(ordered) > 1 and ordered[0]['score'] - ordered[1]['score'] < AMBIGUITY_MARGIN:
            return None
        return ordered[0]
