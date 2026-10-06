"""Validate sources, annotations and split isolation without ML dependencies."""

import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "qorgau_dataset"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def safe_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside dataset: {relative}")
    return path


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def boxes_from_text(text, class_count):
    boxes = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f"Expected five YOLO fields: {line}")
        cid = int(fields[0])
        x, y, w, h = map(float, fields[1:])
        if not (
            0 <= cid < class_count
            and all(math.isfinite(v) for v in (x, y, w, h))
            and 0 <= x <= 1
            and 0 <= y <= 1
            and 0 < w <= 1
            and 0 < h <= 1
        ):
            raise ValueError(f"Invalid class or normalized coordinates: {line}")
        boxes.append([cid, x, y, w, h])
    return boxes


def validate(root=ROOT, hashes=False, decode_images=False, bundle=False):
    root = root.resolve()
    own = read_json(root / "file_manifest.json")["files"]
    public_manifest = read_json(root / "metadata/public_manifest.json")
    public = public_manifest["files"]
    catalog = read_json(root / "data_catalog.json")
    errors, group_splits, hash_splits = (
        [],
        collections.defaultdict(set),
        collections.defaultdict(set),
    )
    counts = collections.Counter()
    for source, records in [("personal", own), ("public", public)]:
        for r in records:
            relative = r["dataset_relative_path"]
            try:
                path = safe_path(root, relative)
                if not path.is_file() or path.stat().st_size != r["bytes"]:
                    raise ValueError("Missing file or wrong size")
                if hashes and sha256(path) != r["sha256"]:
                    raise ValueError("SHA-256 mismatch")
                counts[source] += 1
                if decode_images and path.suffix.lower() in (".jpg", ".jpeg", ".png"):
                    from PIL import Image

                    with Image.open(path) as im:
                        im.load()
                if source != "public":
                    continue
                counts[r["status"]] += 1
                label_relative = r["label_relative_path"]
                if label_relative:
                    label = safe_path(root, label_relative)
                    if not label.is_file() or sha256(label) != r["label_sha256"]:
                        raise ValueError("Missing or changed native annotation")
                if r["status"] == "ready":
                    boxes = boxes_from_text(label.read_text(encoding="utf-8-sig"), 5)
                    if boxes != r["boxes"]:
                        raise ValueError("Native label differs from manifest")
                    if r["split"] not in ("train", "val", "test"):
                        raise ValueError("Missing split")
                    group_splits[r["group_id"]].add(r["split"])
                    hash_splits[r["sha256"]].add(r["split"])
                    counts[r["split"]] += 1
                elif r["split"] is not None:
                    raise ValueError(
                        "Unlabeled/quarantined image assigned to training or evaluation"
                    )
            except (OSError, ValueError) as e:
                errors.append(f"{relative}: {e}")
    for group, splits in group_splits.items():
        if len(splits) > 1:
            errors.append(f"Recording group leakage: {group}: {sorted(splits)}")
    for digest, splits in hash_splits.items():
        if len(splits) > 1:
            errors.append(f"Exact duplicate leakage: {digest}: {sorted(splits)}")
    if sum(counts[s] for s in ("personal", "public")) != catalog["total_media_files"]:
        errors.append("Catalog total differs from manifests")
    for split, expected in public_manifest["splits"].items():
        images = list((root / "public_behavior/images" / split).glob("*.jpg"))
        labels = list((root / "public_behavior/labels" / split).glob("*.txt"))
        if len(images) != expected["images"] or len(labels) != expected["images"]:
            errors.append(f"{split}: directory counts differ from manifest")
        if {p.stem for p in images} != {p.stem for p in labels}:
            errors.append(f"{split}: image/label pair mismatch")
        if not all(expected["boxes_by_class"].values()):
            errors.append(f"{split}: missing native class")
    sequence = read_json(root / "calibration_sequence.json")["steps"]
    labels = [s["label"] for s in sequence]
    base = [
        "SCREEN",
        "SCREEN_LEFT",
        "SCREEN_RIGHT",
        "SCREEN_BOTTOM",
        "DOWN",
        "LEFT",
        "RIGHT",
    ]
    extended = [
        "SCREEN",
        "SCREEN_LEFT",
        "SCREEN_RIGHT",
        "SCREEN_TOP",
        "SCREEN_BOTTOM",
        "DOWN",
        "LEFT",
        "RIGHT",
    ]
    if labels not in (base, extended):
        errors.append("Calibration sequence differs from the specified positions")
    gaze_path = root / "prepared/gaze_annotation/annotations.json"
    if gaze_path.is_file():
        gaze = read_json(gaze_path)
        originals = {
            r["dataset_relative_path"]: r for r in own if r["collection"] == "gaze"
        }
        for video in gaze["videos"]:
            original = originals.get(video["video"])
            if not original or video["source_sha256"] != original["sha256"]:
                errors.append(f"Gaze annotation source mismatch: {video['video']}")
            previous_end = 0.0
            for segment in video["segments"]:
                if not (
                    previous_end
                    <= segment["start_s"]
                    < segment["end_s"]
                    <= video["duration_s"] + 0.001
                ):
                    errors.append(
                        f"Overlapping/out-of-range gaze segment: {video['video']}"
                    )
                previous_end = segment["end_s"]
                if segment.get("include_in_training") and segment["label"] not in (
                    "on_screen",
                    "off_screen",
                ):
                    errors.append(
                        f"Uncertain gaze segment included in training: {video['video']}"
                    )
            for sample in video["samples"]:
                image = safe_path(root / "prepared/gaze_annotation", sample["image"])
                if not image.is_file():
                    errors.append(f"Missing gaze review frame: {sample['image']}")
                if sample.get("include_in_training"):
                    counts["gaze_training_keyframes"] += 1
                    if video["category"] in ("calibration", "control"):
                        errors.append(
                            f"Calibration/control keyframe in training: {video['video']}"
                        )
        counts["gaze_annotated_videos"] = len(gaze["videos"])
        if set(v["video"] for v in gaze["videos"]) != set(originals):
            errors.append(
                "Gaze annotations do not cover the current personal gaze videos"
            )
    if bundle:
        for item in read_json(root / "package_manifest.json")["files"]:
            path = safe_path(root, item["path"])
            if not path.is_file() or path.stat().st_size != item["bytes"]:
                errors.append(f"Package file missing or changed size: {item['path']}")
            elif sha256(path) != item["sha256"]:
                errors.append(f"Package file hash changed: {item['path']}")
    return {
        "status": "passed" if not errors else "failed",
        "counts": dict(counts),
        "media_sha256_checked": hashes,
        "images_fully_decoded": decode_images,
        "package_manifest_checked": bundle,
        "group_leakage": False if not errors else None,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--hashes", action="store_true")
    parser.add_argument("--decode-images", action="store_true")
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="Check all packaged files against package_manifest.json",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate(args.root, args.hashes, args.decode_images, args.bundle)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
