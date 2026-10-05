"""YOLO11 COCO ONNX inference, CPU only. No torch, model downloads or API calls."""
import ast
from pathlib import Path
import cv2
import numpy as np
import onnxruntime as ort


class PhoneDetector:
    def __init__(self, path: Path):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0]
        if self.input.shape != [1, 3, 640, 640]:
            raise ValueError("Модель телефона должна иметь вход 1×3×640×640")
        names = ast.literal_eval(self.session.get_modelmeta().custom_metadata_map.get("names", "{}"))
        self.class_id = next((int(k) for k, v in names.items() if v == "cell phone"), None)
        if self.class_id is None:
            raise ValueError("В модели отсутствует класс cell phone")

    def detect(self, frame):
        height, width = frame.shape[:2]
        scale = min(640 / width, 640 / height)
        resized = cv2.resize(frame, (round(width * scale), round(height * scale)))
        xpad, ypad = (640 - resized.shape[1]) // 2, (640 - resized.shape[0]) // 2
        padded = np.full((640, 640, 3), 114, dtype=np.uint8)
        padded[ypad:ypad + resized.shape[0], xpad:xpad + resized.shape[1]] = resized
        tensor = np.ascontiguousarray(padded[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
        output = self.session.run(None, {self.input.name: tensor})[0]
        rows = output[0].T
        scores = rows[:, 4 + self.class_id]
        rows, scores = rows[scores >= .4], scores[scores >= .4]
        boxes = []
        for row in rows:
            cx, cy, w, h = row[:4]
            boxes.append([float((cx - w/2 - xpad) / scale), float((cy - h/2 - ypad) / scale), float(w / scale), float(h / scale)])
        indices = cv2.dnn.NMSBoxes(boxes, scores.tolist(), .4, .45)
        result = []
        for i in np.asarray(indices).reshape(-1):
            x, y, w, h = boxes[i]
            result.append({"confidence": float(scores[i]), "box": [max(0., x), max(0., y), min(float(width), x+w), min(float(height), y+h)]})
        return result
