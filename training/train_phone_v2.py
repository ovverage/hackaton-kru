"""Fine-tune YOLO11n phones on COCO + reviewed personal images, export and audit."""

from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def image_audit(model, rows, class_id, device, threshold=0.65):
    import numpy as np

    counts = Counter()
    for row in rows:
        result = model.predict(
            row["image"],
            classes=[class_id],
            conf=threshold,
            imgsz=640,
            device=device,
            verbose=False,
        )[0]
        pred = result.boxes.xyxy.cpu().numpy()
        h, w = result.orig_shape
        truths = []
        for line in Path(row["label"]).read_text().splitlines():
            if line.strip():
                _, cx, cy, bw, bh = map(float, line.split())
                truths.append(
                    np.array(
                        [
                            (cx - bw / 2) * w,
                            (cy - bh / 2) * h,
                            (cx + bw / 2) * w,
                            (cy + bh / 2) * h,
                        ]
                    )
                )
        counts["images"] += 1
        counts["positive_images"] += bool(truths)
        counts["negative_images"] += not truths
        counts["image_true_positive"] += bool(truths) and bool(len(pred))
        counts["image_false_negative"] += bool(truths) and not len(pred)
        counts["image_false_positive"] += not truths and bool(len(pred))
        counts["image_true_negative"] += not truths and not len(pred)
        matched = set()
        for box in pred:
            candidates = []
            for i, truth in enumerate(truths):
                lo = np.maximum(box[:2], truth[:2])
                hi = np.minimum(box[2:], truth[2:])
                intersection = np.maximum(0, hi - lo).prod()
                area = (
                    (box[2:] - box[:2]).prod()
                    + (truth[2:] - truth[:2]).prod()
                    - intersection
                )
                candidates.append(
                    float(intersection / max(area, 1e-9)) if i not in matched else 0.0
                )
            best = int(np.argmax(candidates)) if candidates else -1
            if best >= 0 and candidates[best] >= 0.5:
                counts["box_tp"] += 1
                matched.add(best)
            else:
                counts["box_fp"] += 1
        counts["box_fn"] += len(truths) - len(matched)
    return {
        **counts,
        "confidence": threshold,
        "box_iou": 0.5,
        "box_precision": counts["box_tp"] / max(1, counts["box_tp"] + counts["box_fp"]),
        "box_recall": counts["box_tp"] / max(1, counts["box_tp"] + counts["box_fn"]),
        "image_phone_recall": counts["image_true_positive"]
        / max(1, counts["positive_images"]),
        "image_false_positive_rate": counts["image_false_positive"]
        / max(1, counts["negative_images"]),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--weights", type=Path, default=Path("models/yolo11n.pt"))
    p.add_argument("--epochs", type=int, default=60)
    p.add_argument("--batch", type=int, default=16)
    p.add_argument("--device", default="0")
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--cache", choices=["ram", "disk", "none"], default="disk")
    args = p.parse_args()
    import torch
    import ultralytics
    from ultralytics import YOLO

    torch.set_num_threads(4)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    data = args.data.resolve()
    manifest = json.loads((data.parent / "manifest.json").read_text(encoding="utf-8"))
    model = YOLO(str(args.weights))
    model.train(
        data=str(data),
        epochs=args.epochs,
        imgsz=640,
        batch=args.batch,
        workers=args.workers,
        device=args.device,
        seed=20261006,
        deterministic=True,
        amp=True,
        optimizer="AdamW",
        lr0=0.001,
        lrf=0.05,
        weight_decay=0.0005,
        warmup_epochs=3,
        patience=15,
        hsv_h=0.015,
        hsv_s=0.4,
        hsv_v=0.4,
        degrees=8,
        translate=0.1,
        scale=0.35,
        fliplr=0.5,
        flipud=0,
        mosaic=0.5,
        close_mosaic=10,
        mixup=0,
        project=str(out),
        name="fit",
        exist_ok=False,
        plots=True,
        cache=False if args.cache == "none" else args.cache,
        save=True,
    )
    best = YOLO(str(model.trainer.best))
    if best.names != {0: "cell phone"}:
        raise ValueError("Runtime phone class contract changed")
    evaluation = best.val(
        data=str(data),
        split="test",
        imgsz=640,
        batch=args.batch,
        device=args.device,
        workers=0,
        plots=True,
        project=str(out),
        name="test",
    )
    tests = [r for r in manifest["items"] if r["split"] == "test"]
    tuned_audit = image_audit(best, tests, 0, args.device)
    baseline_audit = image_audit(YOLO(str(args.weights)), tests, 67, args.device)
    exported = Path(
        best.export(
            format="onnx",
            imgsz=640,
            opset=17,
            simplify=False,
            dynamic=False,
            nms=False,
            device="cpu",
        )
    )
    destination = out / "phone-yolo11n.onnx"
    destination.write_bytes(exported.read_bytes())
    report = {
        "architecture": "YOLO11n",
        "task": "cell phone",
        "classes": best.names,
        "seed": 20261006,
        "requested_epochs": args.epochs,
        "completed_epochs": int(model.trainer.epoch + 1),
        "dataset_images": dict(Counter(r["split"] for r in manifest["items"])),
        "split_policy": manifest["split_policy"],
        "test_metrics": {k: float(v) for k, v in evaluation.results_dict.items()},
        "test_runtime_threshold_audit": tuned_audit,
        "baseline_runtime_threshold_audit": baseline_audit,
        "dataset_manifest_sha256": hashlib.sha256(
            (data.parent / "manifest.json").read_bytes()
        ).hexdigest(),
        "initial_weights_sha256": hashlib.sha256(args.weights.read_bytes()).hexdigest(),
        "weights_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "checkpoint_sha256": hashlib.sha256(
            Path(model.trainer.best).read_bytes()
        ).hexdigest(),
        "versions": {
            "torch": torch.__version__,
            "ultralytics": ultralytics.__version__,
        },
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU",
        "limitations": manifest["limitations"]
        + [
            "Still images do not establish temporal event quality",
            "No raw private images or facial features may be published in the release",
        ],
    }
    (out / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
