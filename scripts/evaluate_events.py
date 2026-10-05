"""Evaluate interval predictions against a held-out, manually labelled recording."""
import argparse
import json
import math
from pathlib import Path


def interval_iou(a, b):
    intersection = max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    union = max(a["end"], b["end"]) - min(a["start"], b["start"])
    return intersection / union if union > 0 else float(a["start"] == b["start"])


def wilson(successes, total):
    if not total:
        return None
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z*z/total
    centre = (p + z*z/(2*total)) / denominator
    half = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denominator
    return [max(0, centre-half), min(1, centre+half)]


def evaluate(truth, predictions):
    types = sorted({x["type"] for x in truth + predictions})
    result = {}
    for kind in types:
        expected = [x for x in truth if x["type"] == kind]
        actual = [x for x in predictions if x["type"] == kind]
        candidates = sorted(((interval_iou(a, b), i, j) for i, a in enumerate(expected)
                             for j, b in enumerate(actual)
                             if a["recording"] == b["recording"]), reverse=True)
        matched_truth, matched_actual = set(), set()
        for overlap, i, j in candidates:
            if overlap >= .5 and i not in matched_truth and j not in matched_actual:
                matched_truth.add(i)
                matched_actual.add(j)
        tp = len(matched_truth)
        result[kind] = {"tp": tp, "fp": len(actual) - tp, "fn": len(expected) - tp,
                        "precision": tp / len(actual) if actual else None,
                        "recall": tp / len(expected) if expected else None,
                        "precision_ci95": wilson(tp, len(actual)),
                        "recall_ci95": wilson(tp, len(expected)),
                        "support": len(expected)}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("truth", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    truth, predictions = [json.loads(p.read_text(encoding="utf-8")) for p in (args.truth, args.predictions)]
    for item in truth + predictions:
        if not (isinstance(item["recording"], str) and isinstance(item["type"], str)
                and math.isfinite(item["start"]) and math.isfinite(item["end"])
                and 0 <= item["start"] <= item["end"]):
            raise ValueError("Invalid labelled interval")
    if not truth:
        raise ValueError("No ground truth: accuracy cannot be measured")
    report = {"match": "same recording, same class, one-to-one interval IoU >= 0.5",
              "uncertainty": "Wilson 95%; descriptive intervals assume independent episodes; repeated subjects are not independent",
              "metrics": evaluate(truth, predictions)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
