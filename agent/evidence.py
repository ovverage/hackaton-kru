"""Burn detector boxes into the same frames used for thumbnails and video."""

import math


def annotate(frame, detections):
    import cv2

    if not detections:
        return frame
    shown = frame.copy()
    height, width = shown.shape[:2]
    for detection in detections:
        box = detection.get('box', [])
        if len(box) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in box):
            continue
        x1, y1, x2, y2 = [round(max(0, min(1, v)) * (width - 1 if i % 2 == 0 else height - 1)) for i, v in enumerate(box)]
        if x2 <= x1 or y2 <= y1:
            continue
        color = (65, 80, 245)
        cv2.rectangle(shown, (x1, y1), (x2, y2), color, max(2, width // 320))
        label = f"PHONE {detection.get('confidence', 0):.0%}"
        cv2.putText(shown, label, (x1, max(22, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                    max(.5, width / 1500), color, 2, cv2.LINE_AA)
    return shown
