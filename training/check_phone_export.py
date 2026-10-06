"""Compare packaged CPU ONNX detections against PyTorch on held-out real images."""

from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time


def iou(a, b):
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0.0, min(a[3], b[3]) - max(a[1], b[1])
    )
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / max(area, 1e-9)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--reference-device", default="cpu")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import cv2
    import numpy as np
    import torch
    import onnxruntime
    from ultralytics import YOLO
    from agent.detector import PhoneDetector

    torch.set_num_threads(4)
    original = YOLO(str(args.checkpoint))
    runtime = PhoneDetector(args.onnx)
    rows = [
        r
        for r in json.loads(args.manifest.read_text(encoding="utf-8"))["items"]
        if r["split"] == "test"
    ]
    counts = Counter()
    timings, failures = [], []
    max_error, min_iou = 0.0, 1.0
    for row in rows:
        frame = cv2.imread(row["image"])
        if frame is None:
            raise ValueError("Unreadable test image")
        start = time.perf_counter()
        onnx = runtime.detect(frame)
        timings.append((time.perf_counter() - start) * 1000)
        pt = original.predict(
            frame,
            imgsz=640,
            rect=False,
            conf=0.4,
            iou=0.45,
            device=args.reference_device,
            verbose=False,
        )[0].boxes
        expected = [
            {"box": box.tolist(), "confidence": float(conf)}
            for box, conf in zip(pt.xyxy, pt.conf)
        ]
        if len(onnx) != len(expected):
            failures.append(
                {
                    "image_sha256": row["sha256"],
                    "reason": "count",
                    "onnx": len(onnx),
                    "pytorch": len(expected),
                }
            )
        else:
            remaining = list(expected)
            for detection in onnx:
                best = max(
                    range(len(remaining)),
                    key=lambda i: iou(detection["box"], remaining[i]["box"]),
                )
                ref = remaining.pop(best)
                overlap = iou(detection["box"], ref["box"])
                error = abs(detection["confidence"] - ref["confidence"])
                min_iou, max_error = min(min_iou, overlap), max(max_error, error)
                if overlap < 0.99 or error > 1e-3:
                    failures.append(
                        {
                            "image_sha256": row["sha256"],
                            "reason": "coordinates_or_score",
                            "iou": overlap,
                            "confidence_error": error,
                        }
                    )
        truths = []
        height, width = frame.shape[:2]
        for line in Path(row["label"]).read_text().splitlines():
            if line.strip():
                _, x, y, w, h = map(float, line.split())
                truths.append(
                    [
                        (x - w / 2) * width,
                        (y - h / 2) * height,
                        (x + w / 2) * width,
                        (y + h / 2) * height,
                    ]
                )
        accepted = [d for d in onnx if d["confidence"] >= 0.65]
        counts["positive_images"] += bool(truths)
        counts["negative_images"] += not truths
        counts["image_tp"] += bool(truths) and bool(accepted)
        counts["image_fn"] += bool(truths) and not accepted
        counts["image_fp"] += not truths and bool(accepted)
        counts["image_tn"] += not truths and not accepted
        matched = set()
        for detection in accepted:
            options = [
                (iou(detection["box"], box), i)
                for i, box in enumerate(truths)
                if i not in matched
            ]
            overlap, index = max(options, default=(0.0, -1))
            if overlap >= 0.5:
                counts["box_tp"] += 1
                matched.add(index)
            else:
                counts["box_fp"] += 1
        counts["box_fn"] += len(truths) - len(matched)
    report = {
        "status": "passed" if not failures else "failed",
        "images": len(rows),
        "onnx_sha256": hashlib.sha256(args.onnx.read_bytes()).hexdigest(),
        "provider": "CPUExecutionProvider",
        "reference_device": args.reference_device,
        "versions": {"torch": torch.__version__, "onnxruntime": onnxruntime.__version__},
        "confidence": 0.65,
        "nms_iou": 0.45,
        "parity_at_confidence": 0.4,
        "minimum_box_iou": min_iou,
        "maximum_confidence_error": max_error,
        "mismatches": failures,
        "runtime_counts": dict(counts),
        "inference_median_ms": float(np.median(timings)),
        "inference_p95_ms": float(np.percentile(timings, 95)),
        "note": "CPU timings on training server, not a student laptop; still-image audit, not temporal events.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    if failures:
        raise SystemExit("Export parity failed")


if __name__ == "__main__":
    main()
