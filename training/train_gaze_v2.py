"""Train/evaluate a forest with entire recordings held out, plus identity audit."""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

SEED = 20261006


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--features", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--relative",
        action="store_true",
        help="Subtract first observed frame of each recording without reading its label",
    )
    args = p.parse_args()
    import numpy as np
    import sklearn
    from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
    from sklearn.metrics import (
        balanced_accuracy_score,
        confusion_matrix,
        precision_recall_fscore_support,
    )
    from sklearn.model_selection import GroupShuffleSplit
    from shared.gaze_v2 import FEATURE_VERSION

    args.output.mkdir(parents=True, exist_ok=True)
    data = json.loads(args.features.read_text(encoding="utf-8"))
    samples = data["samples"]
    x = np.asarray([r["features"] for r in samples])
    y = np.asarray([int(r["label"] == "off_screen") for r in samples])
    groups = np.array([r["video_id"] for r in samples])
    people = np.array([r["participant"] for r in samples])
    if args.relative:
        relative = x.copy()
        for group in sorted(set(groups)):
            idx = np.flatnonzero(groups == group)
            first = min(idx, key=lambda i: float(samples[i]["timestamp_s"]))
            relative[idx] -= x[first]
        x = relative
    trainval, test = next(
        GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=SEED).split(
            x, y, groups
        )
    )
    train0, val0 = next(
        GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=SEED + 1).split(
            x[trainval], y[trainval], groups[trainval]
        )
    )
    train, val = trainval[train0], trainval[val0]
    sets = {
        key: sorted(set(groups[idx]))
        for key, idx in [("train", train), ("val", val), ("test", test)]
    }
    if set(sets["train"]) & set(sets["test"]) or set(sets["val"]) & set(sets["test"]):
        raise ValueError("Recording leakage")
    (args.output / "split.json").write_text(
        json.dumps(sets, indent=2), encoding="utf-8"
    )

    def metric(labels, probability, threshold):
        predicted = probability >= threshold
        cm = confusion_matrix(labels, predicted, labels=[0, 1])
        precision, recall, f1, _ = precision_recall_fscore_support(
            labels, predicted, average="binary", zero_division=0
        )
        return {
            "n": len(labels),
            "threshold": float(threshold),
            "confusion_matrix": cm.tolist(),
            "balanced_accuracy": float(balanced_accuracy_score(labels, predicted)),
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "false_positive_rate": float(cm[0, 1] / max(1, cm[0].sum())),
        }

    def make(kind, depth, leaf):
        cls = ExtraTreesClassifier if kind == "extra" else RandomForestClassifier
        return cls(
            n_estimators=300,
            max_depth=depth,
            min_samples_leaf=leaf,
            class_weight="balanced",
            random_state=SEED,
            n_jobs=4,
        )

    candidates = []
    for kind in ("extra", "random"):
        for depth, leaf in ((6, 4), (10, 3), (14, 2)):
            model = make(kind, depth, leaf).fit(x[train], y[train])
            prob = model.predict_proba(x[val])[:, 1]
            for threshold in (0.5, 0.6, 0.7, 0.8, 0.9):
                m = metric(y[val], prob, threshold)
                candidates.append(
                    {"kind": kind, "depth": depth, "leaf": leaf, "metrics": m}
                )
    # Selection uses validation only. Prefer <=5% false positives, then recall.
    eligible = [c for c in candidates if c["metrics"]["false_positive_rate"] <= 0.05]
    selected = max(
        eligible or candidates,
        key=lambda c: (
            (c["metrics"]["recall"], c["metrics"]["precision"])
            if eligible
            else (c["metrics"]["balanced_accuracy"], c["metrics"]["precision"])
        ),
    )
    threshold = selected["metrics"]["threshold"]
    model = make(selected["kind"], selected["depth"], selected["leaf"]).fit(
        x[trainval], y[trainval]
    )
    test_metrics = metric(y[test], model.predict_proba(x[test])[:, 1], threshold)
    folds = []
    for heldout in sorted(set(people)):
        tr, te = people != heldout, people == heldout
        fold = make(selected["kind"], selected["depth"], selected["leaf"]).fit(
            x[tr], y[tr]
        )
        folds.append(
            {
                "heldout": heldout,
                **metric(y[te], fold.predict_proba(x[te])[:, 1], threshold),
            }
        )
    # Ship this evaluated checkpoint, not a refit that has seen test labels.
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
                "off_probability": (v[:, 1] / v.sum(axis=1)).tolist(),
            }
        )
    exported = {
        "schema": 2,
        "feature_version": FEATURE_VERSION,
        "features": x.shape[1],
        "threshold": threshold,
        "trees": trees,
        "task": "binary_gaze",
        "calibration_required": False,
        "automatic_sanctions": False,
    }
    exported["reference"] = "first_observation" if args.relative else "absolute"
    path = args.output / "gaze-v2.json"
    path.write_text(json.dumps(exported, separators=(",", ":")), encoding="utf-8")
    report = {
        "seed": SEED,
        "features": x.shape[1],
        "usable": len(samples),
        "missing": len(data["missing"]),
        "reference": exported["reference"],
        "missing_by_label": {
            label: sum(r["label"] == label for r in data["missing"])
            for label in ("on_screen", "off_screen")
        },
        "participants": sorted(set(people)),
        "split_recordings": {k: len(v) for k, v in sets.items()},
        "validation_selection": selected,
        "held_out_recordings": test_metrics,
        "unseen_participant_audit": folds,
        "versions": {"sklearn": sklearn.__version__},
        "annotation_sha256": data["annotation_sha256"],
        "weights_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "limitations": [
            "Two participants only",
            "Preliminary visual labels, no eye-tracker ground truth",
            "Recording split shares participants and rooms with training",
            "Participant audit is diagnostic and not used to tune thresholds",
            "Off-screen classification cannot establish cheating or an exact screen boundary",
        ],
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
