"""Cache landmark/eye features and crops without distorting camera aspect ratio."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--face-model", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    import cv2
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from shared.gaze_v2 import extract_features

    root = args.dataset.resolve()
    annotation = root / "prepared/gaze_annotation/training_samples.csv"
    with annotation.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    crops = output / "crops"
    crops.mkdir(exist_ok=True)
    options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(args.face_model)),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=2,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True,
    )
    samples, missing = [], []
    with vision.FaceLandmarker.create_from_options(options) as detector:
        for i, row in enumerate(rows):
            if row["include_in_training"].lower() != "true" or row["category"] in (
                "calibration",
                "control",
            ):
                continue
            path = (root / "prepared/gaze_annotation" / row["image"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Unsafe frame path")
            original = cv2.imread(str(path))
            if original is None:
                raise ValueError(f"Cannot read {path}")
            h, w = original.shape[:2]
            scale = min(1.0, 960 / max(h, w))
            frame = cv2.resize(original, (round(w * scale), round(h * scale)))
            result = detector.detect(
                mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                )
            )
            if len(result.face_landmarks) != 1:
                missing.append(
                    {**row, "reason": "face_count", "faces": len(result.face_landmarks)}
                )
                continue
            landmarks = result.face_landmarks[0]
            blend = {
                b.category_name: float(b.score) for b in result.face_blendshapes[0]
            }
            matrix = result.facial_transformation_matrixes[0]
            vector = extract_features(
                landmarks, matrix, blend, frame.shape[1], frame.shape[0]
            )
            if vector is None:
                missing.append({**row, "reason": "features"})
                continue
            xy = np.array([[v.x * w, v.y * h] for v in landmarks[:468]])
            lo, hi = xy.min(axis=0), xy.max(axis=0)
            pad = (hi - lo) * 0.15
            x0, y0 = np.maximum(0, lo - pad).astype(int)
            x1, y1 = np.minimum([w, h], hi + pad).astype(int)
            name = f"{i:05d}.jpg"
            crop = original[y0:y1, x0:x1]
            cv2.imwrite(
                str(crops / name),
                cv2.resize(crop, (160, 160)),
                [cv2.IMWRITE_JPEG_QUALITY, 94],
            )
            samples.append(
                {
                    **row,
                    "features": vector,
                    "landmarks": [[v.x, v.y, v.z] for v in landmarks],
                    "matrix": matrix.tolist(),
                    "blendshapes": blend,
                    "crop": f"crops/{name}",
                }
            )
            if i % 100 == 0:
                print(f"extracted {i}/{len(rows)}", flush=True)
    report = {
        "schema": 2,
        "samples": samples,
        "missing": missing,
        "annotation_sha256": hashlib.sha256(annotation.read_bytes()).hexdigest(),
        "face_model_sha256": hashlib.sha256(args.face_model.read_bytes()).hexdigest(),
    }
    (output / "features.json").write_text(json.dumps(report), encoding="utf-8")
    print(f"COMPLETE {len(samples)} usable; {len(missing)} missing", flush=True)


if __name__ == "__main__":
    main()
