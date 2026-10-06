"""Audit detection of a second person in the reviewed presence-photo scenarios."""

from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--models", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from agent.detector import FaceDetector

    args.output.mkdir(parents=True, exist_ok=True)
    yolo = FaceDetector(args.models / "face_yolov8n.onnx")
    counts = Counter()
    rows = []
    options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(
            model_asset_path=str(args.models / "face_landmarker.task")
        ),
        num_faces=2,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
    )
    with vision.FaceLandmarker.create_from_options(options) as mesh:
        for path in sorted((args.dataset / "person_detection/photos").rglob("*")):
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError("Unreadable presence image")
            boxes = yolo.detect(frame)
            h, w = frame.shape[:2]
            scale = min(1.0, 960 / max(h, w))
            small = cv2.resize(frame, (round(w * scale), round(h * scale)))
            result = mesh.detect(
                mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB),
                )
            )
            faces = max(len(boxes), len(result.face_landmarks))
            present = path.parent.name == "person_present"
            # Visual review of all 204 images established these folders refer
            # to a SECOND person, not disappearance of the seated student.
            counts["second_person_present" if present else "second_person_absent"] += 1
            counts[
                "tp"
                if present and faces >= 2
                else "fn"
                if present
                else "fp"
                if faces >= 2
                else "tn"
            ] += 1
            if present:
                counts["yolo_two"] += len(boxes) >= 2
                counts["mediapipe_two"] += len(result.face_landmarks) >= 2
            rows.append(
                {
                    "image": path.relative_to(args.dataset).as_posix(),
                    "yolo_faces": len(boxes),
                    "mesh_faces": len(result.face_landmarks),
                    "runtime_faces": faces,
                    "folder_second_person_present": present,
                    "boxes": boxes,
                }
            )
    (args.output / "predictions.json").write_text(
        json.dumps(rows, indent=2), encoding="utf-8"
    )
    report = {
        "images": len(rows),
        "counts_against_second_person_scenario": dict(counts),
        "yolo_sha256": hashlib.sha256(
            (args.models / "face_yolov8n.onnx").read_bytes()
        ).hexdigest(),
        "mediapipe_sha256": hashlib.sha256(
            (args.models / "face_landmarker.task").read_bytes()
        ).hexdigest(),
        "note": "All 204 images were visually reviewed: person_absent means second person absent (one seated student in 33 images), person_present means another person entering/standing near the student (171 images). Some second heads are outside or cropped by the frame. Counts use the full scenario without excluding those difficult frames; not a visible-face box benchmark or empty-room test. These are correlated photos of the same two people, not a general-population accuracy estimate. No private images are included in this report.",
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
