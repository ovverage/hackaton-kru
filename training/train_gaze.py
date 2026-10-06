"""Train a binary gaze baseline on reviewed personal keyframes, evaluate by participant."""

from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import platform

from shared.gaze import features


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--face-model", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("training/runs/gaze"))
    args = parser.parse_args()
    import cv2
    import mediapipe as mp
    import numpy as np
    import sklearn
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import (
        balanced_accuracy_score,
        confusion_matrix,
        classification_report,
    )
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision

    root = args.dataset.resolve()
    labels = root / "prepared/gaze_annotation/training_samples.csv"
    args.output.mkdir(parents=True, exist_ok=True)
    samples, skipped = [], []
    options = vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(args.face_model)),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=2,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
    )
    with labels.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    with vision.FaceLandmarker.create_from_options(options) as detector:
        for i, row in enumerate(rows):
            if row["include_in_training"].lower() != "true" or row["category"] in (
                "calibration",
                "control",
            ):
                continue
            if row["label"] not in ("on_screen", "off_screen"):
                continue
            path = (root / "prepared/gaze_annotation" / row["image"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Image path escapes dataset")
            frame = cv2.imread(str(path))
            if frame is None:
                raise ValueError(f"Cannot decode {path}")
            frame = cv2.resize(frame, (640, 480))
            result = detector.detect(
                mp.Image(
                    image_format=mp.ImageFormat.SRGB,
                    data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                )
            )
            vector = (
                features(result.face_landmarks[0])
                if len(result.face_landmarks) == 1
                else None
            )
            if vector is None:
                skipped.append(
                    {
                        "video_id": row["video_id"],
                        "label": row["label"],
                        "sample_index": row["sample_index"],
                    }
                )
                continue
            samples.append(
                {
                    "x": vector,
                    "y": int(row["label"] == "off_screen"),
                    "participant": row["participant"],
                    "video_id": row["video_id"],
                    "sample_index": row["sample_index"],
                }
            )
            if i % 100 == 0:
                print(f"features {i}/{len(rows)}", flush=True)
    x = np.asarray([s["x"] for s in samples])
    y = np.asarray([s["y"] for s in samples])
    people = np.asarray([s["participant"] for s in samples])
    if len(set(people)) < 2 or len(set(y)) != 2:
        raise ValueError("Need at least two participants and both classes")
    params = dict(
        n_estimators=100,
        max_depth=5,
        min_samples_leaf=6,
        class_weight="balanced",
        random_state=20261006,
        n_jobs=2,
    )
    evaluations = []
    for held_out in sorted(set(people)):
        train, test = people != held_out, people == held_out
        model = RandomForestClassifier(**params).fit(x[train], y[train])
        predicted = model.predict(x[test])
        evaluations.append(
            {
                "held_out_participant": held_out,
                "train_n": int(train.sum()),
                "test_n": int(test.sum()),
                "balanced_accuracy": float(balanced_accuracy_score(y[test], predicted)),
                "confusion_matrix": confusion_matrix(
                    y[test], predicted, labels=[0, 1]
                ).tolist(),
                "classification_report": classification_report(
                    y[test],
                    predicted,
                    labels=[0, 1],
                    target_names=["on_screen", "off_screen"],
                    output_dict=True,
                    zero_division=0,
                ),
            }
        )
    final = RandomForestClassifier(**params).fit(x, y)
    trees = []
    for estimator in final.estimators_:
        t = estimator.tree_
        values = t.value[:, 0, :]
        probabilities = values[:, 1] / values.sum(axis=1)
        trees.append(
            {
                "left": t.children_left.tolist(),
                "right": t.children_right.tolist(),
                "feature": t.feature.tolist(),
                "threshold": t.threshold.tolist(),
                "off_probability": probabilities.tolist(),
            }
        )
    model_path = args.output / "gaze-baseline.json"
    model_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "task": "binary_gaze",
                "features": 4,
                "trees": trees,
                "experimental": True,
            }
        ),
        encoding="utf-8",
    )
    report = {
        "run_type": "experimental_supervised_training",
        "seed": 20261006,
        "source": "personal",
        "input_keyframes": len(rows),
        "usable_keyframes": len(samples),
        "skipped_keyframes": len(skipped),
        "skipped_by_label": {
            k: sum(s["label"] == k for s in skipped)
            for k in ("on_screen", "off_screen")
        },
        "class_counts": {
            "on_screen": int((y == 0).sum()),
            "off_screen": int((y == 1).sum()),
        },
        "participants": sorted(set(people)),
        "recordings": len({s["video_id"] for s in samples}),
        "evaluation": "leave_one_participant_out; no frame-level random split",
        "folds": evaluations,
        "parameters": params,
        "annotation_sha256": hashlib.sha256(labels.read_bytes()).hexdigest(),
        "face_model_sha256": hashlib.sha256(args.face_model.read_bytes()).hexdigest(),
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "versions": {
            "python": platform.python_version(),
            "mediapipe": mp.__version__,
            "sklearn": sklearn.__version__,
        },
        "limitations": [
            "Only two participants",
            "Preliminary script-assisted visual labels, no eye tracker",
            "Metrics exclude missing/ambiguous face features; see skipped_by_label",
            "Final model refit on all usable samples, not a held-out test score",
            "Does not determine DOWN/LEFT/RIGHT or prove cheating",
            "Not enabled for automatic sanctions",
        ],
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (args.output / "features.json").write_text(json.dumps(samples), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
