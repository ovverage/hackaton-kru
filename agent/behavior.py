"""Personal gaze calibration and an explicitly heuristic phone-raising signal."""
from collections import deque
import numpy as np
from shared.rules import PHONE_CONFIDENCE_THRESHOLD

POSITIONS = [
    ("SCREEN", "Смотрите в центр экрана"),
    ("SCREEN_LEFT", "Читайте у левого края экрана"),
    ("SCREEN_RIGHT", "Читайте у правого края экрана"),
    ("SCREEN_TOP", "Читайте у верхнего края экрана"),
    ("SCREEN_BOTTOM", "Читайте у нижнего края экрана"),
    ("DOWN", "Посмотрите вниз, за пределы экрана"),
    ("LEFT", "Посмотрите влево, за пределы экрана"),
    ("RIGHT", "Посмотрите вправо, за пределы экрана"),
]
WEIGHTS = np.array([1., .45, 1., 1.])


def validate_centres(centres):
    if any(key not in centres for key, _ in POSITIONS):
        raise ValueError("Калибровка не завершена: нужны центр, четыре края экрана и три направления снаружи.")
    if set(centres) != {key for key, _ in POSITIONS} or any(
        np.asarray(value).shape != (4,) or not np.isfinite(value).all()
        for value in centres.values()
    ):
        raise ValueError("Калибровка содержит некорректные координаты")
    keys = list(centres)
    for i, a in enumerate(keys):
        for b in keys[i+1:]:
            if a.startswith("SCREEN") and b.startswith("SCREEN"):
                continue
            if np.linalg.norm((centres[a] - centres[b]) * WEIGHTS) < .08:
                raise ValueError("Экран и взгляд за его пределы неразличимы. Поправьте камеру и повторите калибровку.")


def classify_gaze(feature, centres):
    if feature is None or not centres:
        return "UNKNOWN"
    distances = {}
    for key, value in centres.items():
        category = "SCREEN" if key.startswith("SCREEN") else key
        distance = float(np.linalg.norm((feature - value) * WEIGHTS))
        distances[category] = min(distance, distances.get(category, float("inf")))
    ordered = sorted(distances, key=distances.get)
    if len(ordered) < 4:
        return "UNKNOWN"
    best = ordered[0]
    return best if distances[best] < .25 and distances[ordered[1]] - distances[best] > .035 else "UNKNOWN"


class PhoneRaising:
    """Upward motion followed by a hold; cannot establish lens direction or shutter use."""
    def __init__(self):
        self.history = deque()
        self.raised_at = None
        self.fired = False
        self.last_seen = None
        self.previous_box = None

    def update(self, t, detections, width, height):
        valid = [x for x in detections if x["confidence"] >= PHONE_CONFIDENCE_THRESHOLD]
        if not valid:
            if self.last_seen is not None and t - self.last_seen > 1:
                self.history.clear()
                self.raised_at = None
                self.fired = False
            return False
        detection = max(valid, key=lambda x: x["confidence"])
        x1, y1, x2, y2 = detection["box"]
        cx, cy = (x1+x2)/2/width, (y1+y2)/2/height
        if self.last_seen is not None and t - self.last_seen > .5:
            self.history.clear()
            self.raised_at = None
        self.last_seen = t
        while self.history and t - self.history[0][0] > 2.5:
            self.history.popleft()
        # Reject jumps between unrelated objects instead of claiming a tracked rise.
        if self.previous_box and abs(cx - self.previous_box[0]) > .25:
            self.history.clear()
            self.raised_at = None
        self.previous_box = (cx, cy)
        self.history.append((t, cy))
        raised = any(old_y - cy >= .15 for _, old_y in self.history)
        near_screen = .1 < cx < .9 and cy < .7 and (y2-y1) / max(1, x2-x1) > 1.1
        if raised and near_screen:
            if self.raised_at is None:
                self.raised_at = t
            if t - self.raised_at >= .6 and not self.fired:
                self.fired = True
                return True
        else:
            self.raised_at = None
        return False
