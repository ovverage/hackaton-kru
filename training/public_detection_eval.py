"""Final held-out metrics, fixed operating-point audit and CPU ONNX export checks."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import shutil
import time

from .public_detection import NAMES, read_wider, run_lock, sha256, stable_order, verified_manifest, write_json

# Declared before final-test execution. These are prototype acceptance floors,
# not promises of exam-event accuracy or a substitute for webcam field testing.
MIN_AP50 = {"cell phone": 0.40, "person": 0.60, "face": 0.50}
THRESHOLDS = {"cell phone": 0.80, "person": 0.55, "face": 0.55}
MIN_PRECISION = {"cell phone": 0.80, "person": 0.80, "face": 0.80}
MIN_RECALL = {"cell phone": 0.25, "person": 0.50, "face": 0.50}


def overlap(a, b, crowd=False):
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    area_a, area_b = a[2] * a[3], b[2] * b[3]
    return intersection / max(1e-9, area_a if crowd else area_a + area_b - intersection)


def operating_counts(predictions, truths, ignored, threshold):
    """Greedy score-ordered IoU matching, with crowd/invalid ignore regions."""
    accepted = sorted((p for p in predictions if p["score"] >= threshold),
                      key=lambda p: p["score"], reverse=True)
    matched, tp, fp, skip = set(), 0, 0, 0
    for prediction in accepted:
        options = [(overlap(prediction["bbox"], box), index) for index, box in enumerate(truths) if index not in matched]
        score, index = max(options, default=(0, -1))
        if score >= 0.5:
            matched.add(index)
            tp += 1
        elif any(overlap(prediction["bbox"], box, crowd=True) >= 0.5 for box in ignored):
            skip += 1
        else:
            fp += 1
    return {"tp": tp, "fp": fp, "fn": len(truths) - len(matched), "ignored_predictions": skip,
            "images": 1, "positive_images": int(bool(truths)),
            "negative_images": int(not truths and not ignored),
            "negative_image_false_positives": int(not truths and not ignored and fp > 0)}


def operating_audit(manifest: dict, predictions: list[dict]) -> dict:
    names = NAMES[manifest["dataset"]]
    truths, ignored, predicted = defaultdict(list), defaultdict(list), defaultdict(list)
    image_ids = set()
    if manifest["dataset"] == "coco":
        source = json.loads(Path(manifest["sources"]["val2017"]["path"]).read_text(encoding="utf-8"))
        categories = {int(key): int(value) for key, value in manifest["source_category_to_model"].items()}
        image_ids = {item["id"] for item in source["images"]}
        for annotation in source["annotations"]:
            if annotation["category_id"] in categories:
                key = (annotation["image_id"], categories[annotation["category_id"]])
                (ignored if annotation.get("iscrowd", 0) else truths)[key].append(annotation["bbox"])
    else:
        for name, rows in read_wider(Path(manifest["sources"]["val"]["path"])):
            image_id = Path(name).stem
            image_ids.add(image_id)
            for row in rows:
                if row[2] <= 0 or row[3] <= 0:
                    continue
                (ignored if row[7] else truths)[(image_id, 0)].append(row[:4])
    for prediction in predictions:
        model_id = int(prediction["category_id"]) - 1
        if model_id not in names or prediction["image_id"] not in image_ids:
            raise ValueError("Unexpected prediction category or image identity")
        predicted[(prediction["image_id"], model_id)].append(prediction)
    result = {}
    for index, name in names.items():
        counts = Counter()
        for image_id in image_ids:
            key = (image_id, index)
            counts.update(operating_counts(predicted[key], truths[key], ignored[key], THRESHOLDS[name]))
        result[name] = {**counts, "confidence": THRESHOLDS[name], "iou": 0.5,
                        "precision": counts["tp"] / max(1, counts["tp"] + counts["fp"]),
                        "recall": counts["tp"] / max(1, counts["tp"] + counts["fn"]),
                        "negative_image_false_positive_rate": counts["negative_image_false_positives"] / max(1, counts["negative_images"])}
    return result


def official_coco(manifest: dict, predictions: list[dict], output: Path) -> dict:
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    original = COCO(manifest["sources"]["val2017"]["path"])
    model_to_source = {int(value): int(key) for key, value in manifest["source_category_to_model"].items()}
    remapped = [{**row, "category_id": model_to_source[int(row["category_id"]) - 1]} for row in predictions]
    write_json(output / "coco-original-category-predictions.json", remapped)
    if not remapped:
        return {"status": "failed", "reason": "No predictions on final COCO validation set"}
    detected = original.loadRes(remapped)
    reports = {}
    for name, categories in [("all_targets", list(model_to_source.values()))] + [(NAMES["coco"][key], [value]) for key, value in model_to_source.items()]:
        evaluator = COCOeval(original, detected, "bbox")
        evaluator.params.catIds = categories
        evaluator.params.imgIds = sorted(original.getImgIds())
        evaluator.evaluate()
        evaluator.accumulate()
        evaluator.summarize()
        reports[name] = dict(zip(("AP50_95", "AP50", "AP75", "AP_small", "AP_medium", "AP_large", "AR1", "AR10", "AR100", "AR_small", "AR_medium", "AR_large"), map(float, evaluator.stats)))
    return {"status": "passed", "maxDets": [1, 10, 100], "images": len(original.getImgIds()), "metrics": reports}


def wider_predictions(manifest: dict, predictions: list[dict], output: Path) -> None:
    grouped = defaultdict(list)
    for row in predictions:
        grouped[str(row["image_id"])].append(row)
    for name, _ in read_wider(Path(manifest["sources"]["val"]["path"])):
        rows = grouped[Path(name).stem]
        destination = output / "wider-official-evaluator-input" / Path(name).with_suffix(".txt")
        destination.parent.mkdir(parents=True, exist_ok=True)
        lines = [Path(name).stem, str(len(rows))]
        for row in rows:
            lines.append(" ".join(f"{value:.6f}" for value in [*row["bbox"], row["score"]]))
        destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def select_parity_images(manifest: dict, samples: int) -> list[dict]:
    if samples < 4:
        raise ValueError("At least four held-out parity images required")
    images = Path(manifest["splits"]["test"]["path"]).read_text(encoding="utf-8").splitlines()
    images = sorted(images, key=lambda value: stable_order(Path(value).name, 20261007))[:samples]
    if len(images) < 4:
        raise ValueError("Insufficient held-out images for parity")
    return [{"path": value, "sha256": sha256(Path(value))} for value in images]


def cpu_parity(checkpoint: Path, onnx_path: Path, manifest: dict, samples: int,
               selection: list[dict] | None = None) -> dict:
    import ast
    import cv2
    import numpy as np
    import onnxruntime as ort
    import torch
    from ultralytics import YOLO
    selection = selection if selection is not None else select_parity_images(manifest, samples)
    if len(selection) < 4 or selection != select_parity_images(manifest, len(selection)):
        raise ValueError("The predetermined parity subset or its image bytes changed")
    torch.set_num_threads(2)
    original = YOLO(str(checkpoint)).model.float().cpu().eval()
    original.fuse(verbose=False)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"])
    input_meta = session.get_inputs()[0]
    expected_names = NAMES[manifest["dataset"]]
    names = ast.literal_eval(session.get_modelmeta().custom_metadata_map.get("names", "{}"))
    if input_meta.shape != [1, 3, 640, 640] or names != expected_names:
        raise ValueError("ONNX input or class metadata violates runtime contract")
    images = [row["path"] for row in selection]
    max_coordinates = max_scores = 0.0
    timings, failures = [], []
    warmup = np.zeros((1, 3, 640, 640), dtype=np.float32)
    for _ in range(3):
        session.run(None, {input_meta.name: warmup})
    for image_path in images:
        image = cv2.imread(image_path)
        if image is None:
            raise ValueError(f"Unreadable parity image: {image_path}")
        height, width = image.shape[:2]
        scale = min(640 / width, 640 / height)
        resized = cv2.resize(image, (round(width * scale), round(height * scale)))
        xpad, ypad = (640 - resized.shape[1]) // 2, (640 - resized.shape[0]) // 2
        padded = np.full((640, 640, 3), 114, dtype=np.uint8)
        padded[ypad:ypad + resized.shape[0], xpad:xpad + resized.shape[1]] = resized
        tensor = np.ascontiguousarray(padded[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
        with torch.inference_mode():
            reference = original(torch.from_numpy(tensor))
            reference = reference[0] if isinstance(reference, (tuple, list)) else reference
            reference = reference.numpy()
        start = time.perf_counter()
        observed = session.run(None, {input_meta.name: tensor})[0]
        timings.append((time.perf_counter() - start) * 1000)
        if observed.shape != (1, 4 + len(names), 8400) or not np.isfinite(observed).all() or not np.isfinite(reference).all():
            raise ValueError("ONNX raw output violates runtime contract")
        coordinate_error = float(np.max(np.abs(reference[:, :4] - observed[:, :4])))
        score_error = float(np.max(np.abs(reference[:, 4:] - observed[:, 4:])))
        max_coordinates = max(max_coordinates, coordinate_error)
        max_scores = max(max_scores, score_error)
        if coordinate_error > 0.05 or score_error > 0.0005:
            failures.append({"image": Path(image_path).name, "coordinate_error_px": coordinate_error, "score_error": score_error})
    return {"status": "passed" if not failures else "failed", "images": len(images),
            "provider": "CPUExecutionProvider", "cpu_threads": 2, "max_box_coordinate_error_px": max_coordinates,
            "max_score_error": max_scores, "tolerance_coordinate_px": 0.05, "tolerance_score": 0.0005,
            "inference_median_ms": float(np.median(timings)), "inference_p95_ms": float(np.percentile(timings, 95)),
            "failures": failures, "note": "Same letterboxed tensors as runtime; raw FP32 graph parity. Latency excludes camera, preprocessing and NMS and is specific to this CPU.",
            "versions": {"onnxruntime": ort.__version__, "torch": torch.__version__}}


def evaluate(data: Path, checkpoint: Path, output: Path, device="0", batch=16, workers=4, parity_images=32):
    with run_lock(output):
        return _evaluate_locked(data, checkpoint, output, device, batch, workers, parity_images)


def _evaluate_locked(data: Path, checkpoint: Path, output: Path, device="0", batch=16, workers=4, parity_images=32):
    from ultralytics import YOLO
    manifest = verified_manifest(data)
    model = YOLO(str(checkpoint))
    if model.names != NAMES[manifest["dataset"]]:
        raise ValueError("Checkpoint class names do not match prepared dataset")
    output.mkdir(parents=True, exist_ok=True)
    receipt = output / "final-evaluation.json"
    if receipt.exists() or (output / "heldout-metrics.json").exists():
        raise FileExistsError("Final evaluation exists. Use 'export' to recover only export/parity from held-out metrics")
    report = {"status": "evaluating", "checkpoint_sha256": sha256(checkpoint),
              "dataset": manifest["dataset"],
              "dataset_manifest_sha256": sha256(data.parent / "manifest.json"),
              "predeclared_ap50_floors": MIN_AP50, "fixed_operating_thresholds": THRESHOLDS,
              "predeclared_precision_floors": MIN_PRECISION, "predeclared_recall_floors": MIN_RECALL,
              "parity_selection": select_parity_images(manifest, parity_images),
              "limitations": manifest["limitations"],
              "deployment_readiness": "Requires application/temporal webcam validation in addition to these image-level gates."}
    write_json(receipt, report)
    try:
        if (output / "final_test").exists():
            raise FileExistsError("The final_test directory already exists; preserve the existing audit")
        coverage = {}

        def record_coverage(validator):
            coverage["images"] = int(validator.seen)

        model.add_callback("on_val_end", record_coverage)
        metrics = model.val(data=str(data), split="test", imgsz=640, batch=batch,
                            device=device, workers=workers, plots=True, save_json=True,
                            conf=0.001, iou=0.45, max_det=300, rect=False,
                            project=str(output), name="final_test", exist_ok=True)
        if coverage.get("images") != manifest["splits"]["test"]["images"]:
            raise ValueError("Final validation did not process every held-out image")
        report["validation_images"] = coverage["images"]
        prediction_file = output / "final_test" / "predictions.json"
        if not prediction_file.is_file():
            raise FileNotFoundError("Final validation did not save predictions; no metrics or export pass can be claimed")
        predictions = json.loads(prediction_file.read_text(encoding="utf-8"))
        if not isinstance(predictions, list):
            raise ValueError("Final predictions must be a JSON list")
        report["prediction_sha256"] = sha256(prediction_file)
        report["diagnostic_yolo_metrics"] = {key: float(value) for key, value in metrics.results_dict.items()}
        report["operating_point_audit"] = operating_audit(manifest, predictions)
        classes = {}
        for position, index in enumerate(metrics.box.ap_class_index):
            precision, recall, ap50, ap5095 = metrics.box.class_result(position)
            classes[NAMES[manifest["dataset"]][int(index)]] = {"precision_at_max_F1": float(precision), "recall_at_max_F1": float(recall), "AP50": float(ap50), "AP50_95": float(ap5095)}
        report["diagnostic_per_class"] = classes
        if manifest["dataset"] == "coco":
            report["official_coco"] = official_coco(manifest, predictions, output)
        else:
            wider_predictions(manifest, predictions, output)
        report["status"] = "heldout_metrics_complete"
        heldout = output / "heldout-metrics.json"
        write_json(heldout, report)
        report["heldout_metrics_sha256"] = sha256(heldout)
        write_json(receipt, report)
    except Exception as error:
        report.update({"status": "failed", "error": str(error)})
        write_json(receipt, report)
        raise
    return _export_locked(data, checkpoint, output)


def load_heldout_receipt(data: Path, checkpoint: Path, output: Path) -> tuple[dict, dict]:
    """Read immutable metrics without running prediction or recalculating scores."""
    manifest = verified_manifest(data)
    heldout = output / "heldout-metrics.json"
    report = json.loads(heldout.read_text(encoding="utf-8"))
    state = json.loads((output / "final-evaluation.json").read_text(encoding="utf-8"))
    if state.get("heldout_metrics_sha256") != sha256(heldout):
        raise ValueError("Held-out metrics receipt changed or was never finalized")
    if report.get("status") != "heldout_metrics_complete" or report.get("dataset") != manifest["dataset"]:
        raise ValueError("Held-out metrics are incomplete or belong to another dataset")
    if report.get("checkpoint_sha256") != sha256(checkpoint):
        raise ValueError("Export recovery checkpoint differs from the evaluated checkpoint")
    if report.get("dataset_manifest_sha256") != sha256(data.parent / "manifest.json"):
        raise ValueError("Export recovery dataset differs from the evaluated dataset")
    prediction_file = output / "final_test" / "predictions.json"
    if report.get("prediction_sha256") != sha256(prediction_file):
        raise ValueError("Held-out predictions changed")
    for key, expected in [("predeclared_ap50_floors", MIN_AP50), ("fixed_operating_thresholds", THRESHOLDS),
                          ("predeclared_precision_floors", MIN_PRECISION), ("predeclared_recall_floors", MIN_RECALL)]:
        if report.get(key) != expected:
            raise ValueError("Acceptance policy changed after held-out evaluation")
    selection = report.get("parity_selection", [])
    if len(selection) < 4 or selection != select_parity_images(manifest, len(selection)):
        raise ValueError("Recorded parity subset or image bytes changed")
    report["heldout_metrics_sha256"] = sha256(heldout)
    return manifest, report


def image_gate_failures(manifest: dict, report: dict) -> list[str]:
    scoring = report.get("official_coco", {}).get("metrics", {}) if manifest["dataset"] == "coco" else report.get("diagnostic_per_class", {})
    failures = []
    if report.get("validation_images") != manifest["splits"]["test"]["images"]:
        failures.append("Final validation image coverage is incomplete")
    if manifest["dataset"] == "coco" and report.get("official_coco", {}).get("status") != "passed":
        failures.append("Official COCO evaluation did not complete")
    for name in NAMES[manifest["dataset"]].values():
        audit = report.get("operating_point_audit", {}).get(name, {})
        checks = [("AP50", scoring.get(name, {}).get("AP50"), MIN_AP50[name]),
                  ("precision", audit.get("precision"), MIN_PRECISION[name]),
                  ("recall", audit.get("recall"), MIN_RECALL[name])]
        for label, value, floor in checks:
            if not isinstance(value, (float, int)) or not math.isfinite(value) or not floor <= value <= 1:
                failures.append(f"{name} {label} missing, non-finite or below {floor:.2f}")
        if audit.get("confidence") != THRESHOLDS[name] or audit.get("images") != manifest["splits"]["test"]["images"]:
            failures.append(f"{name} operating audit has a changed threshold or incomplete image coverage")
    return failures


def recover_export(data: Path, checkpoint: Path, output: Path):
    with run_lock(output):
        return _export_locked(data, checkpoint, output)


def _export_locked(data: Path, checkpoint: Path, output: Path):
    manifest, report = load_heldout_receipt(data, checkpoint, output)
    receipt = output / "final-evaluation.json"
    report["status"] = "exporting_from_saved_metrics"
    write_json(receipt, report)
    try:
        from ultralytics import YOLO
        model = YOLO(str(checkpoint))
        if model.names != NAMES[manifest["dataset"]]:
            raise ValueError("Checkpoint class names do not match prepared dataset")
        exported = Path(model.export(format="onnx", imgsz=640, batch=1, opset=17,
                                      simplify=False, dynamic=False, nms=False, half=False, device="cpu"))
        target = output / ("person-phone-yolo11n.onnx" if manifest["dataset"] == "coco" else "face-yolo11n.onnx")
        if exported.resolve() != target.resolve():
            shutil.copy2(exported, target)
        report["export"] = {"path": str(target), "sha256": sha256(target), "bytes": target.stat().st_size,
                             "input": [1, 3, 640, 640], "opset": 17, "names": model.names}
        selection = report["parity_selection"]
        report["cpu_parity"] = cpu_parity(checkpoint, target, manifest, len(selection), selection=selection)
        failures = image_gate_failures(manifest, report)
        if report["cpu_parity"]["status"] != "passed":
            failures.append("CPU ONNX parity failed")
        report.update({"status": "passed_image_model_gates" if not failures else "failed", "gate_failures": failures})
        write_json(receipt, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        if failures:
            raise RuntimeError("Model acceptance gates failed: " + "; ".join(failures))
    except Exception as error:
        report.update({"status": "failed", "error": str(error)})
        write_json(receipt, report)
        raise
    return report
