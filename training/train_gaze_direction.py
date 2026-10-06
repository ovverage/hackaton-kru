"""Train direction decisions from Face Mesh features relative to session start."""

from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

CLASSES = ["SCREEN", "DOWN", "LEFT", "RIGHT", "UP"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--features", type=Path, required=True)
    p.add_argument("--split", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    import numpy as np
    from sklearn.ensemble import ExtraTreesClassifier
    from sklearn.metrics import (
        balanced_accuracy_score,
        confusion_matrix,
        classification_report,
    )
    from shared.gaze_v2 import FEATURE_VERSION, GazeClassifier

    data = json.loads(args.features.read_text(encoding="utf-8"))
    rows = data["samples"]
    x = np.asarray([r["features"] for r in rows])
    y = np.asarray(
        [
            CLASSES.index(
                "SCREEN" if r["label"] == "on_screen" else r["category"].upper()
            )
            for r in rows
        ]
    )
    group = np.asarray([r["video_id"] for r in rows])
    people = np.asarray([r["participant"] for r in rows])
    for name in sorted(set(group)):
        indices = np.flatnonzero(group == name)
        first = min(indices, key=lambda i: float(rows[i]["timestamp_s"]))
        x[indices] -= x[first].copy()
    split = json.loads(args.split.read_text(encoding="utf-8"))
    tr = np.isin(group, split["train"])
    val = np.isin(group, split["val"])
    te = np.isin(group, split["test"])
    if np.any(tr & te) or np.any(val & te) or np.any(tr & val):
        raise ValueError("Recording leakage")
    params = {
        "n_estimators": 300,
        "max_depth": 10,
        "min_samples_leaf": 3,
        "class_weight": "balanced",
        "random_state": 20261006,
        "n_jobs": 4,
    }

    def make(mask):
        return ExtraTreesClassifier(**params).fit(x[mask], y[mask])

    def metrics(labels, probs, threshold):
        off = 1 - probs[:, 0]
        pred = off >= threshold
        binary = labels != 0
        cm = confusion_matrix(binary, pred, labels=[False, True])
        direction = probs[:, 1:].argmax(axis=1) + 1
        accepted = (off >= threshold) & (
            probs[:, 1:].max(axis=1) / np.maximum(off, 1e-9) >= 0.55
        )
        # Match runtime abstention; uncertain samples must not be reported as
        # correct SCREEN predictions in the direction confusion matrix.
        chosen = np.where(off < threshold - 0.15, 0, 5)
        chosen = np.where(accepted, direction, chosen)
        return {
            "n": len(labels),
            "threshold": threshold,
            "binary_confusion_matrix": cm.tolist(),
            "balanced_accuracy": float(balanced_accuracy_score(binary, pred)),
            "recall": float(cm[1, 1] / max(1, cm[1].sum())),
            "precision": float(cm[1, 1] / max(1, cm[:, 1].sum())),
            "false_positive_rate": float(cm[0, 1] / max(1, cm[0].sum())),
            "direction_confusion_matrix": confusion_matrix(
                labels, chosen, labels=list(range(6))
            ).tolist(),
            "direction_labels": CLASSES + ["UNKNOWN"],
            "unknown_decision_count": int((chosen == 5).sum()),
            "direction_report": classification_report(
                labels,
                chosen,
                labels=list(range(6)),
                target_names=CLASSES + ["UNKNOWN"],
                output_dict=True,
                zero_division=0,
            ),
        }

    selection = make(tr)
    candidates = [
        metrics(y[val], selection.predict_proba(x[val]), t)
        for t in (0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)
    ]
    eligible = [c for c in candidates if c["false_positive_rate"] <= 0.03]
    selected = max(
        eligible or candidates,
        key=lambda c: (
            (c["recall"], c["precision"])
            if eligible
            else (c["balanced_accuracy"], c["precision"])
        ),
    )
    threshold = selected["threshold"]
    model = make(tr | val)
    heldout = metrics(y[te], model.predict_proba(x[te]), threshold)
    folds = []
    for name in sorted(set(people)):
        other = people != name
        # Do not choose even the probability threshold using the excluded
        # person's labels. Feature/algorithm development still saw both people,
        # so this remains a development audit, not a fresh external test.
        fold_selector = make(tr & other)
        fold_candidates = [
            metrics(y[val & other], fold_selector.predict_proba(x[val & other]), t)
            for t in (0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)
        ]
        fold_eligible = [c for c in fold_candidates if c["false_positive_rate"] <= 0.03]
        fold_selected = max(
            fold_eligible or fold_candidates,
            key=lambda c: (
                (c["recall"], c["precision"])
                if fold_eligible
                else (c["balanced_accuracy"], c["precision"])
            ),
        )
        fold = make(other)
        folds.append(
            {
                "participant": name,
                "threshold_selection": "validation recordings of the other participant only",
                "validation": fold_selected,
                **metrics(
                    y[people == name],
                    fold.predict_proba(x[people == name]),
                    fold_selected["threshold"],
                ),
            }
        )
    trees = []
    for estimator in model.estimators_:
        t = estimator.tree_
        v = t.value[:, 0, :]
        trees.append(
            {
                "left": t.children_left.tolist(),
                "right": t.children_right.tolist(),
                "feature": t.feature.tolist(),
                "threshold": t.threshold.tolist(),
                "probabilities": (v / v.sum(axis=1, keepdims=True)).tolist(),
            }
        )
    artifact = {
        "schema": 3,
        "task": "gaze_direction",
        "feature_version": FEATURE_VERSION,
        "features": int(x.shape[1]),
        "classes": CLASSES,
        "threshold": threshold,
        "direction_threshold": 0.55,
        "reference": "first_valid_observation",
        "trees": trees,
        "manual_calibration_required": False,
        "automatic_reference": True,
        "feature_min": [float(v) for v in np.quantile(x[tr | val], 0.005, axis=0)],
        "feature_max": [float(v) for v in np.quantile(x[tr | val], 0.995, axis=0)],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "gaze-direction.json"
    path.write_text(json.dumps(artifact, separators=(",", ":")), encoding="utf-8")
    runtime = GazeClassifier(path)
    exported = np.asarray([runtime.probabilities(row) for row in x])
    max_error = float(np.abs(exported - model.predict_proba(x)).max())
    if max_error > 1e-12:
        raise ValueError(f"Export changes gaze probabilities: {max_error}")
    report = {
        "seed": 20261006,
        "algorithm": "ExtraTrees over MediaPipe Face Mesh eye/head features",
        "classes": CLASSES,
        "parameters": params,
        "usable": len(rows),
        "export_parity": {"samples": len(rows), "max_absolute_error": max_error},
        "missing": len(data["missing"]),
        "missing_by_split": {
            name: sum(row["video_id"] in ids for row in data["missing"])
            for name, ids in split.items()
        },
        "labels": dict(Counter(CLASSES[i] for i in y)),
        "validation": selected,
        "development_holdout_recordings": heldout,
        "unseen_participant_development_audit": folds,
        "model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "annotation_sha256": data["annotation_sha256"],
        "split_sha256": hashlib.sha256(args.split.read_bytes()).hexdigest(),
        "limitations": [
            "Only two participants",
            "The development holdout was observed during previous model iterations; not a fresh final test",
            "Every supplied recording starts looking at the screen; inference uses the first valid observation as a reference",
            "No eye tracker or physical screen boundaries; direction labels come from the scripted scenario",
            "Automatic reference is not a user calibration wizard, but starting away from screen can bias predictions",
            "Frame metrics do not establish event-level false-alarm rate; evaluate complete withheld control videos separately",
        ],
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (args.output / "predictions.json").write_text(
        json.dumps(
            [
                {
                    "video_id": rows[i]["video_id"],
                    "image": rows[i]["image"],
                    "label": CLASSES[y[i]],
                    "probabilities": prob.tolist(),
                }
                for i, prob in zip(np.flatnonzero(te), model.predict_proba(x[te]))
            ]
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in ("unseen_participant_development_audit",)
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
