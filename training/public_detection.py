"""Reproducible COCO person/phone and WIDER FACE preparation and training.

The official validation sets are final tests, never early-stopping sets.
Run ``python -m training.public_detection --help`` for the entry points.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil

SEED = 20261007
INITIAL_SHA256 = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
NAMES = {"coco": {0: "cell phone", 1: "person"}, "wider": {0: "face"}}


@contextmanager
def run_lock(output: Path):
    """Non-blocking OS lock: released after crashes, never unlink its inode."""
    output.mkdir(parents=True, exist_ok=True)
    path = output / ".public-detection.lock"
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f"Another process is using the detection run: {output}") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def verified_manifest(data: Path) -> dict:
    manifest = json.loads((data.parent / "manifest.json").read_text(encoding="utf-8"))
    if sha256(data) != manifest["data_yaml_sha256"]:
        raise ValueError("Prepared dataset YAML was changed")
    for row in manifest["splits"].values():
        if sha256(Path(row["path"])) != row["sha256"]:
            raise ValueError("Prepared split list changed")
    for row in manifest["sources"].values():
        if sha256(Path(row["path"])) != row["sha256"]:
            raise ValueError("Source annotations changed")
    return manifest


def check_resume(previous: dict, current: dict, checkpoint: Path, output: Path) -> None:
    for key in ("dataset", "dataset_manifest_sha256", "versions"):
        if previous[key] != current[key]:
            raise ValueError(f"Cannot resume with changed {key}")
    if previous["initialization"]["sha256"] != current["initialization"]["sha256"]:
        raise ValueError("Cannot resume with a changed initializer")
    # Device and worker count affect scheduling, not the optimizer/dataset protocol.
    for key, value in current["settings"].items():
        if key not in {"device", "workers"} and previous["settings"].get(key) != value:
            raise ValueError(f"Cannot resume with changed training setting: {key}")
    if checkpoint.resolve() != (output / "fit" / "weights" / "last.pt").resolve():
        raise ValueError("Resume checkpoint must be this run's fit/weights/last.pt")
    if (output / "heldout-metrics.json").exists() or (output / "final-evaluation.json").exists():
        raise ValueError("Final evaluation already started; resume training would invalidate it")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def stable_order(value: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()


def dev_members(groups: list[str], fraction: float, seed: int) -> set[str]:
    """Choose deterministic whole groups; keep a training group when possible."""
    if not 0 < fraction < 0.5:
        raise ValueError("dev fraction must be between 0 and 0.5")
    unique = sorted(set(groups), key=lambda value: stable_order(value, seed))
    if len(unique) < 2:
        return set()
    count = min(len(unique) - 1, max(1, round(len(unique) * fraction)))
    return set(unique[:count])


def normalized_box(box, width: int, height: int):
    if width <= 0 or height <= 0 or len(box) != 4:
        raise ValueError("Invalid image dimensions or box")
    x, y, w, h = map(float, box)
    if not all(math.isfinite(value) for value in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    x1, y1 = max(0.0, x), max(0.0, y)
    x2, y2 = min(float(width), x + w), min(float(height), y + h)
    if x2 <= x1 or y2 <= y1:
        return None
    return ((x1 + x2) / (2 * width), (y1 + y2) / (2 * height),
            (x2 - x1) / width, (y2 - y1) / height)


def link_image(source: Path, destination: Path, mode: str) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if mode == "hardlink" and not os.path.samefile(source, destination):
            raise ValueError(f"Existing view is not the same source file: {destination}")
        if destination.stat().st_size != source.stat().st_size:
            raise ValueError(f"Existing view has changed size: {destination}")
        return
    if mode == "hardlink":
        try:
            os.link(source, destination)
        except OSError as error:
            raise OSError("Image views require the same volume; use --link-mode copy explicitly otherwise") from error
    else:
        shutil.copy2(source, destination)


def add_image(root: Path, split: str, relative: str, source: Path, labels: list[str],
              lists: dict, counts: dict, mode: str) -> None:
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("Unsafe relative image path")
    destination = root / "images" / split / Path(relative)
    link_image(source, destination, mode)
    label = root / "labels" / split / Path(relative).with_suffix(".txt")
    label.parent.mkdir(parents=True, exist_ok=True)
    label.write_text("".join(line + "\n" for line in labels), encoding="utf-8")
    lists[split].append(destination.as_posix())
    counts[split]["images"] += 1
    counts[split]["negative_images"] += not labels
    counts[split]["boxes"] += len(labels)
    for line in labels:
        counts[split][f"class_{line.split()[0]}_boxes"] += 1


def finish_preparation(root: Path, manifest: dict, lists: dict, counts: dict) -> dict:
    split_sets = [set(Path(value).stem for value in lists[split]) for split in ("train", "val", "test")]
    if any(split_sets[i] & split_sets[j] for i in range(3) for j in range(i + 1, 3)):
        raise ValueError("Image identities overlap across splits")
    manifests = {}
    for split, files in lists.items():
        if not files:
            raise ValueError(f"Empty {split} split")
        path = root / f"{split}.txt"
        path.write_text("\n".join(sorted(files)) + "\n", encoding="utf-8")
        manifests[split] = {"path": path.as_posix(), "sha256": sha256(path), **counts[split]}
    yaml = "path: " + json.dumps(root.as_posix()) + "\n"
    yaml += "train: train.txt\nval: val.txt\ntest: test.txt\nnames:\n"
    yaml += "".join(f"  {key}: {json.dumps(value)}\n" for key, value in NAMES[manifest["dataset"]].items())
    (root / "data.yaml").write_text(yaml, encoding="utf-8")
    manifest.update({"schema": 1, "splits": manifests, "names": NAMES[manifest["dataset"]],
                     "data_yaml_sha256": sha256(root / "data.yaml"), "status": "prepared"})
    write_json(root / "manifest.json", manifest)
    print(json.dumps({"dataset": manifest["dataset"], "splits": counts, "output": str(root)}), flush=True)
    return manifest


def prepare_coco(datasets: Path, root: Path, seed=SEED, dev_fraction=0.05, mode="hardlink") -> dict:
    raw = datasets / "coco2017" / "raw"
    root.mkdir(parents=True, exist_ok=True)
    lists, counts = defaultdict(list), defaultdict(Counter)
    sources, exclusions = {}, Counter()
    source_categories = None
    for original in ("train2017", "val2017"):
        annotation = raw / "annotations" / f"instances_{original}.json"
        source = json.loads(annotation.read_text(encoding="utf-8"))
        by_name = {item["name"]: int(item["id"]) for item in source["categories"]}
        if not {"cell phone", "person"} <= by_name.keys():
            raise ValueError("COCO categories missing person or cell phone")
        categories = {by_name[name]: index for index, name in NAMES["coco"].items()}
        if source_categories is not None and categories != source_categories:
            raise ValueError("COCO source category IDs changed between splits")
        source_categories = categories
        annotations = defaultdict(list)
        for item in source["annotations"]:
            if item["category_id"] in categories:
                annotations[item["image_id"]].append(item)
        strata = defaultdict(list)
        for item in source["images"]:
            classes = tuple(sorted({a["category_id"] for a in annotations[item["id"]]}))
            strata[classes].append(str(item["id"]))
        development = set()
        if original == "train2017":
            for groups in strata.values():
                development.update(dev_members(groups, dev_fraction, seed))
        for image in source["images"]:
            split = "test" if original == "val2017" else ("val" if str(image["id"]) in development else "train")
            labels = []
            for item in annotations[image["id"]]:
                box = normalized_box(item["bbox"], image["width"], image["height"])
                if box is None:
                    exclusions[f"{original}_degenerate_boxes"] += 1
                    continue
                counts[split]["crowd_boxes_weak_supervision"] += bool(item.get("iscrowd", 0))
                labels.append(f"{categories[item['category_id']]} " + " ".join(f"{v:.9f}" for v in box))
            add_image(root, split, image["file_name"], raw / original / image["file_name"],
                      labels, lists, counts, mode)
        sources[original] = {"path": annotation.as_posix(), "sha256": sha256(annotation),
                             "original_images": len(source["images"])}
    return finish_preparation(root, {
        "dataset": "coco", "seed": seed, "dev_fraction": dev_fraction, "sources": sources,
        "source_category_to_model": source_categories, "exclusions": exclusions,
        "split_policy": "All COCO train2017 images partitioned by deterministic image ID hash, stratified by target presence; official val2017 used only as final test.",
        "limitations": [
            "Official YOLO11n initialization was pretrained on COCO train2017; internal dev is held out from this fine-tune only, not from pretraining.",
            "Image-level split does not establish subject/scene independence; COCO provides no exhaustive identity/video groups.",
            "All target crowd boxes are retained as weak group supervision to avoid silently treating crowds as background. YOLO loss has no ignore regions.",
            "Ultralytics label-space AP is diagnostic. The final pycocotools report uses original categories, crowd flags, areas and maxDets=100.",
            "Phone boxes do not label camera orientation, photographing intent, or temporal exam events.",
        ],
    }, lists, counts)


def read_wider(path: Path):
    lines = iter(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    pending = None
    while True:
        name = pending or next(lines, None)
        pending = None
        if name is None:
            return
        if not name.lower().endswith(".jpg") or ".." in PurePosixPath(name).parts:
            raise ValueError(f"Invalid WIDER path: {name}")
        count = int(next(lines))
        if count < 0:
            raise ValueError("Negative WIDER face count")
        boxes = []
        for _ in range(count):
            row = [int(value) for value in next(lines).split()]
            if len(row) != 10:
                raise ValueError("Expected ten WIDER bbox columns")
            boxes.append(row)
        if count == 0:
            pending = next(lines, None)
            if pending is not None and not pending.lower().endswith(".jpg"):
                if len(pending.split()) != 10 or any(int(value) for value in pending.split()):
                    raise ValueError("Invalid zero-face placeholder")
                pending = None
        yield name, boxes


def prepare_wider(datasets: Path, root: Path, seed=SEED, dev_fraction=0.1, mode="hardlink") -> dict:
    from PIL import Image
    raw = datasets / "widerface" / "raw"
    root.mkdir(parents=True, exist_ok=True)
    lists, counts = defaultdict(list), defaultdict(Counter)
    sources, exclusions, development = {}, Counter(), set()
    for original in ("train", "val"):
        annotation = raw / "wider_face_split" / f"wider_face_{original}_bbx_gt.txt"
        rows = list(read_wider(annotation))
        if original == "train":
            development = dev_members([name.split("/")[0] for name, _ in rows], dev_fraction, seed)
        for name, boxes in rows:
            split = "test" if original == "val" else ("val" if name.split("/")[0] in development else "train")
            # YOLO cannot express ignore regions: never train on invalid faces as background.
            if original == "train" and any(box[7] for box in boxes):
                exclusions[f"{split}_images_with_invalid_boxes"] += 1
                exclusions[f"{split}_boxes_in_excluded_images"] += len(boxes)
                continue
            source = raw / f"WIDER_{original}" / "images" / name
            with Image.open(source) as image:
                width, height = image.size
            labels = []
            for row in boxes:
                if row[7]:
                    exclusions[f"{split}_invalid_boxes"] += 1
                    continue
                box = normalized_box(row[:4], width, height)
                if box is None:
                    exclusions[f"{split}_degenerate_boxes"] += 1
                    continue
                counts[split]["boxes_below_4px_after_640_resize"] += min(row[2:4]) * min(640 / width, 640 / height) < 4
                labels.append("0 " + " ".join(f"{value:.9f}" for value in box))
            add_image(root, split, name, source, labels, lists, counts, mode)
        sources[original] = {"path": annotation.as_posix(), "sha256": sha256(annotation), "original_images": len(rows)}
    return finish_preparation(root, {
        "dataset": "wider", "seed": seed, "dev_fraction": dev_fraction, "sources": sources,
        "development_events": sorted(development), "exclusions": exclusions,
        "split_policy": "Whole WIDER train event categories held out by deterministic hash for dev; official val reserved for final test; official test is unlabelled and not scored.",
        "limitations": [
            "Event-disjoint internal dev is not guaranteed person-disjoint because WIDER does not provide exhaustive person identities.",
            "Whole train/dev images containing invalid-flagged faces are excluded; no tiny valid face box is excluded by size.",
            "Final label-space AP uses valid boxes on every official val image. It cannot implement WIDER ignore regions and is not the official easy/medium/hard AP. Predictions are exported for the official evaluator.",
            "WIDER faces do not establish webcam presence/second-person temporal event performance.",
        ],
    }, lists, counts)


def train(args):
    with run_lock(args.output.resolve()):
        _train_locked(args)


def _train_locked(args):
    import platform
    import torch
    import ultralytics
    from ultralytics import YOLO
    torch.set_num_threads(4)
    data, out = args.data.resolve(), args.output.resolve()
    manifest = verified_manifest(data)
    initial_hash = sha256(args.weights)
    if initial_hash != INITIAL_SHA256:
        raise ValueError("Initialization must be the pinned official YOLO11n COCO checkpoint")
    out.mkdir(parents=True, exist_ok=True)
    settings = dict(data=str(data), epochs=args.epochs, imgsz=640, batch=args.batch,
                    workers=args.workers, device=args.device, seed=args.seed, deterministic=True,
                    amp=True, optimizer="AdamW", lr0=0.001, lrf=0.01, cos_lr=True,
                    weight_decay=0.0005, warmup_epochs=3, patience=args.patience,
                    hsv_h=0.015, hsv_s=0.4, hsv_v=0.4, degrees=5, translate=0.1,
                    scale=0.5, fliplr=0.5, flipud=0.0, mosaic=0.75, close_mosaic=10,
                    mixup=0.0, project=str(out), name="fit", exist_ok=False,
                    plots=True, cache=False, save=True, save_period=10, val=True)
    config = {"schema": 1, "dataset": manifest["dataset"], "settings": settings,
              "initialization": {"sha256": initial_hash, "path": str(args.weights.resolve()),
                                 "source": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
                                 "training_data": "COCO train2017 (official pretrained weights)"},
              "dataset_manifest_sha256": sha256(data.parent / "manifest.json"),
              "versions": {"python": platform.python_version(), "torch": torch.__version__, "ultralytics": ultralytics.__version__},
              "status": "training", "limitations": manifest["limitations"]}
    configuration = out / "training.json"
    if args.resume:
        previous = json.loads(configuration.read_text(encoding="utf-8"))
        check_resume(previous, config, args.resume, out)
        config = previous
        model = YOLO(str(args.resume.resolve()))
        checkpoint_args = model.ckpt.get("train_args", {})
        for key in ("data", "seed", "epochs", "batch", "optimizer", "lr0", "patience", "project"):
            if checkpoint_args.get(key) != previous["settings"][key]:
                raise ValueError(f"Resume checkpoint disagrees with the recorded setting: {key}")
        model.train(resume=True, device=args.device, workers=args.workers)
    else:
        if configuration.exists():
            raise FileExistsError("A training run exists; choose a new output or --resume")
        write_json(configuration, config)
        model = YOLO(str(args.weights.resolve()))
        model.train(**settings)
    checkpoint = Path(model.trainer.best)
    config.update({"status": "trained_pending_final_evaluation", "completed_epochs": int(model.trainer.epoch + 1),
                   "best_checkpoint": str(checkpoint), "best_checkpoint_sha256": sha256(checkpoint)})
    write_json(configuration, config)
    if not args.train_only:
        from .public_detection_eval import _evaluate_locked
        _evaluate_locked(data, checkpoint, out, args.device, args.batch, args.workers, args.parity_images)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--datasets", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--only", choices=["coco", "wider"], action="append")
    prep.add_argument("--seed", type=int, default=SEED)
    prep.add_argument("--link-mode", choices=["hardlink", "copy"], default="hardlink")
    fit = commands.add_parser("train")
    fit.add_argument("--data", type=Path, required=True)
    fit.add_argument("--weights", type=Path, required=True)
    fit.add_argument("--output", type=Path, required=True)
    fit.add_argument("--epochs", type=int, default=80)
    fit.add_argument("--patience", type=int, default=20)
    fit.add_argument("--batch", type=int, default=16)
    fit.add_argument("--device", default="0")
    fit.add_argument("--workers", type=int, default=8)
    fit.add_argument("--seed", type=int, default=SEED)
    fit.add_argument("--resume", type=Path)
    fit.add_argument("--train-only", action="store_true")
    fit.add_argument("--parity-images", type=int, default=32)
    score = commands.add_parser("evaluate")
    score.add_argument("--data", type=Path, required=True)
    score.add_argument("--checkpoint", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--device", default="0")
    score.add_argument("--batch", type=int, default=16)
    score.add_argument("--workers", type=int, default=4)
    score.add_argument("--parity-images", type=int, default=32)
    export = commands.add_parser("export", help="Recover export/parity from immutable held-out metrics; never re-run final validation")
    export.add_argument("--data", type=Path, required=True)
    export.add_argument("--checkpoint", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        for name in args.only or ["wider", "coco"]:
            function = prepare_coco if name == "coco" else prepare_wider
            prepared_output = args.output.resolve() / name
            with run_lock(prepared_output):
                function(args.datasets.resolve(), prepared_output, seed=args.seed, mode=args.link_mode)
    elif args.command == "train":
        train(args)
    elif args.command == "evaluate":
        from .public_detection_eval import evaluate
        evaluate(args.data.resolve(), args.checkpoint.resolve(), args.output.resolve(),
                 args.device, args.batch, args.workers, args.parity_images)
    else:
        from .public_detection_eval import recover_export
        recover_export(args.data.resolve(), args.checkpoint.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
