"""Reproducible COCO person/phone and WIDER FACE preparation and training.

The official validation sets are final tests, never early-stopping sets.
Run ``python -m training.public_detection --help`` for the entry points.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import time

SEED = 20261007
INITIAL_SHA256 = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
NAMES = {"coco": {0: "cell phone", 1: "person"}, "wider": {0: "face"}}


@contextmanager
def label_scan_threads(count: int, dataset_module=None, version=None):
    """Tune only the native verifier pool, retaining validation and cache format."""
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 48:
        raise ValueError("Label scan threads must be an integer between 1 and 48")
    if dataset_module is None:
        import ultralytics
        from ultralytics.data import dataset as dataset_module
        version = ultralytics.__version__
    if version != "8.3.221" or dataset_module.DATASET_CACHE_VERSION != "1.0.3":
        raise ValueError("Label scan tuning requires Ultralytics 8.3.221 cache version 1.0.3")
    previous = dataset_module.NUM_THREADS
    dataset_module.NUM_THREADS = count
    try:
        yield {"scan_threads": count, "native_default_threads": previous,
               "cache_version": dataset_module.DATASET_CACHE_VERSION,
               "verification": "Unmodified YOLODataset.cache_labels, verify_image_label, ordered ThreadPool.imap and get_hash; train/dev loaders only before final evaluation."}
    finally:
        dataset_module.NUM_THREADS = previous


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


def runtime_budget(time_hours=None, deadline_utc=None, reserve_minutes=45.0, now=None) -> dict | None:
    """Budget includes setup; a deadline reserves time for evaluation/export."""
    now = time.time() if now is None else now
    if time_hours is None and deadline_utc is None:
        return None
    if not math.isfinite(reserve_minutes) or reserve_minutes < 0:
        raise ValueError("Evaluation reserve must be finite and non-negative")
    cutoffs = []
    if time_hours is not None:
        if not math.isfinite(time_hours) or time_hours <= 0:
            raise ValueError("Training time budget must be positive and finite")
        cutoffs.append(now + time_hours * 3600)
    deadline = None
    if deadline_utc is not None:
        parsed = datetime.fromisoformat(deadline_utc.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Deadline must include an explicit UTC offset or Z")
        deadline = parsed.timestamp()
        cutoffs.append(deadline - reserve_minutes * 60)
    cutoff = min(cutoffs)
    if cutoff <= now:
        raise TimeoutError("No training time remains after reserving final evaluation")
    return {"requested_time_hours": time_hours, "started_timestamp": now,
            "started_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "training_cutoff_timestamp": cutoff,
            "training_cutoff_utc": datetime.fromtimestamp(cutoff, timezone.utc).isoformat(),
            "deadline_timestamp": deadline, "deadline_utc": deadline_utc,
            "evaluation_reserve_minutes": reserve_minutes,
            "timing_semantics": "Wall time includes setup. Remaining time is armed via native Ultralytics time at on_train_start. Native time dynamically replans epochs/LR and may stop partway through an epoch. Internal dev/final export use the reserve; an external watchdog must enforce an absolute deadline during blocking setup/I/O."}


def resumed_budget(previous: dict | None, requested: dict | None) -> dict | None:
    if previous is None:
        return requested
    result = dict(previous)
    if requested is not None:
        result["training_cutoff_timestamp"] = min(previous["training_cutoff_timestamp"], requested["training_cutoff_timestamp"])
        deadlines = [value for value in (previous.get("deadline_timestamp"), requested.get("deadline_timestamp")) if value is not None]
        result["deadline_timestamp"] = min(deadlines) if deadlines else None
        result["evaluation_reserve_minutes"] = max(previous["evaluation_reserve_minutes"], requested["evaluation_reserve_minutes"])
        if result["deadline_timestamp"] is not None:
            result["training_cutoff_timestamp"] = min(result["training_cutoff_timestamp"], result["deadline_timestamp"] - result["evaluation_reserve_minutes"] * 60)
            result["deadline_utc"] = datetime.fromtimestamp(result["deadline_timestamp"], timezone.utc).isoformat()
        result["training_cutoff_utc"] = datetime.fromtimestamp(result["training_cutoff_timestamp"], timezone.utc).isoformat()
        result["resume_requested_budget"] = requested
    return result


class TimedRun:
    """Arm the pinned native timer after setup and report full versus partial epochs."""

    def __init__(self, budget: dict | None, requested_workers: int, output: Path, mosaic_window=10):
        self.budget = budget
        self.requested_workers = requested_workers
        self.output = output
        self.epochs = []
        self.batches = 0
        self.clock = time.time
        self.mosaic_window = mosaic_window
        self.mosaic_closed = False

    def before_setup(self, trainer):
        # 8.3.221 check_resume ignores workers; set it before loaders are built.
        trainer.args.workers = self.requested_workers
        if self.budget and self.clock() >= self.budget["training_cutoff_timestamp"]:
            raise TimeoutError("Training cutoff reached before setup")

    def start(self, trainer):
        if not self.budget:
            return
        remaining = self.budget["training_cutoff_timestamp"] - self.clock()
        if remaining <= 0:
            raise TimeoutError("Dataset/cache setup exhausted the training budget")
        # Native trainer starts its clock immediately before this callback.
        trainer.args.time = remaining / 3600
        self.budget["native_time_hours_after_setup"] = trainer.args.time
        self.budget["setup_seconds_this_invocation"] = self.clock() - self.budget.get("invocation_started_timestamp", self.budget["started_timestamp"])
        write_json(self.output / "timing.json", {"budget": self.budget, "epochs": self.epochs})

    def epoch_start(self, trainer):
        self.batches = 0
        if self.budget and self.mosaic_window > 0 and not self.mosaic_closed:
            restored = "mosaic_closed_epoch" in self.budget
            if restored or trainer.epoch >= max(0, trainer.epochs - self.mosaic_window):
                # Timed horizons move: equality in native 8.3.221 can be skipped.
                trainer._close_dataloader_mosaic()
                trainer.train_loader.reset()
                trainer.args.close_mosaic = 0  # prevent a second native reset
                self.mosaic_closed = True
                self.budget.setdefault("mosaic_closed_epoch", int(trainer.epoch + 1))
                self.budget["mosaic_close_horizon"] = int(trainer.epochs)
                self.budget["mosaic_close_restored_on_resume"] = restored
                write_json(self.output / "timing.json", {"budget": self.budget, "epochs": self.epochs})

    def batch_start(self, trainer):
        self.batches += 1

    def epoch_end(self, trainer):
        expected = len(trainer.train_loader)
        self.epochs.append({"epoch": int(trainer.epoch + 1), "training_batches": self.batches,
                            "expected_batches": expected, "full_epoch": self.batches == expected})
        if self.budget:
            write_json(self.output / "timing.json", {"budget": self.budget, "epochs": self.epochs})

    def attach(self, model):
        for event, callback in [("on_pretrain_routine_start", self.before_setup), ("on_train_start", self.start),
                                ("on_train_epoch_start", self.epoch_start), ("on_train_batch_start", self.batch_start),
                                ("on_train_epoch_end", self.epoch_end)]:
            model.add_callback(event, callback)


def stage_initialization(parent_output: Path, weights: Path, data_manifest_sha256: str, output: Path) -> dict:
    """A declared new optimizer stage, never disguised as a resumed old run."""
    parent_output = parent_output.resolve()
    if parent_output == output.resolve():
        raise ValueError("A continuation stage needs a new output directory")
    if weights.resolve() != parent_output / "fit" / "weights" / "best.pt":
        raise ValueError("A continuation stage must use the parent's dev-selected best.pt")
    with run_lock(parent_output):
        receipt = parent_output / "training.json"
        parent = json.loads(receipt.read_text(encoding="utf-8"))
        if parent["dataset_manifest_sha256"] != data_manifest_sha256:
            raise ValueError("A continuation stage cannot switch the prepared dataset")
        if (parent_output / "heldout-metrics.json").exists() or (parent_output / "final-evaluation.json").exists():
            raise ValueError("Parent final test was already opened; do not fit another stage against that test")
        original = parent["initialization"].get("original_pretraining_sha256", parent["initialization"]["sha256"])
        if original != INITIAL_SHA256:
            raise ValueError("Parent lacks the pinned official initializer provenance")
        return {"kind": "new_optimizer_continuation_stage", "sha256": sha256(weights), "path": str(weights.resolve()),
                "original_pretraining_sha256": original, "parent_run": str(parent_output),
                "parent_training_receipt_sha256": sha256(receipt),
                "parent_requested_settings": parent["settings"],
                "parent_versions": parent.get("versions", {}),
                "training_data": "Same prepared train/dev split; parent dev-selected checkpoint, optimizer reset; no final-test feedback"}


def stage_parent_fitness(checkpoint: dict, parent_versions: dict, runtime_version: str) -> float:
    """8.3.221 detection fitness is exactly dev mAP50-95, not the older AP blend."""
    if any(version != "8.3.221" for version in
           (runtime_version, parent_versions.get("ultralytics"), checkpoint.get("version"))):
        raise ValueError("Stage selection requires parent/checkpoint/runtime Ultralytics 8.3.221")
    values = [checkpoint.get("best_fitness"), checkpoint.get("train_metrics", {}).get("fitness"),
              checkpoint.get("train_metrics", {}).get("metrics/mAP50-95(B)")]
    if any(value is None or isinstance(value, bool) for value in values):
        raise ValueError("Parent checkpoint lacks preserved development fitness evidence")
    values = [float(value) for value in values]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise ValueError("Parent development fitness must be finite and within [0, 1]")
    if not all(math.isclose(values[0], value, rel_tol=0, abs_tol=1e-10) for value in values[1:]):
        raise ValueError("Parent best checkpoint fitness disagrees with its development mAP50-95")
    return values[0]


def select_stage_checkpoint(parent: Path, candidate: Path, parent_sha256: str,
                            parent_fitness: float, child_fitness: float, output: Path) -> dict:
    """Keep the stronger dev checkpoint; preserve child bytes and audit selection."""
    for value in (parent_fitness, child_fitness):
        if value is None or not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
            raise ValueError("Stage selection requires finite development fitness in [0, 1]")
    if parent.resolve() == candidate.resolve() or sha256(parent) != parent_sha256:
        raise ValueError("Stage parent checkpoint identity changed")
    candidate_sha = sha256(candidate)
    preserved = candidate.with_name("child-best.pt")
    if preserved.exists() and sha256(preserved) != candidate_sha:
        raise FileExistsError("A different preserved child candidate already exists")
    if not preserved.exists():
        shutil.copy2(candidate, preserved)
    if sha256(preserved) != candidate_sha:
        raise RuntimeError("Child candidate preservation failed integrity verification")
    improved = float(child_fitness) > float(parent_fitness)
    source = candidate if improved else parent
    selected_sha = candidate_sha if improved else parent_sha256
    receipt = {"status": "selection_pending", "policy": "Strictly higher internal-dev mAP50-95(B); ties retain parent; no final-test feedback",
               "ultralytics_version": "8.3.221", "fitness_weights_P_R_AP50_AP50_95": [0, 0, 0, 1],
               "parent": {"path": str(parent.resolve()), "sha256": parent_sha256, "dev_fitness": float(parent_fitness)},
               "child": {"path": str(preserved.resolve()), "sha256": candidate_sha, "dev_fitness": float(child_fitness)},
               "selected_source": "child" if improved else "parent", "selected_source_path": str(source.resolve()),
               "selected_checkpoint": str(candidate.resolve()), "selected_sha256": selected_sha}
    write_json(output / "stage-selection.json", receipt)
    if not improved:
        temporary = candidate.with_name("best.parent-selection.tmp")
        shutil.copy2(parent, temporary)
        if sha256(temporary) != parent_sha256:
            raise RuntimeError("Parent checkpoint copy failed integrity verification")
        os.replace(temporary, candidate)
    receipt["status"] = "selected_before_final_evaluation"
    write_json(output / "stage-selection.json", receipt)
    return receipt


class BenchmarkComplete(Exception):
    """Intentional end before epoch-end validation or saving a candidate."""


class BatchBenchmark:
    def __init__(self, steps: int, warmup_steps: int, batch_size: int, synchronize=lambda: None):
        if not 0 <= warmup_steps < steps or steps < 2:
            raise ValueError("Benchmark requires steps > warmup_steps >= 0 and at least two steps")
        self.steps, self.warmup_steps, self.batch_size = steps, warmup_steps, batch_size
        self.synchronize = synchronize
        self.clock = time.perf_counter
        self.count = 0
        self.previous_end = None
        self.rows = []

    def start(self, trainer):
        self.synchronize()
        self.started = self.clock()
        self.gap = 0.0 if self.previous_end is None else self.started - self.previous_end

    def end(self, trainer):
        self.synchronize()
        ended = self.clock()
        self.count += 1
        if self.count > self.warmup_steps:
            self.rows.append({"batch_seconds": ended - self.started, "loader_and_logging_gap_seconds": self.gap})
        self.previous_end = ended
        if self.count >= self.steps:
            raise BenchmarkComplete()

    def report(self):
        duration = sum(row["batch_seconds"] + row["loader_and_logging_gap_seconds"] for row in self.rows)
        return {"completed_batches": self.count, "warmup_batches": self.warmup_steps,
                "measured_batches": len(self.rows), "measured_images": len(self.rows) * self.batch_size,
                "measured_seconds": duration, "images_per_second": len(self.rows) * self.batch_size / max(duration, 1e-9),
                "mean_synchronized_batch_seconds": sum(row["batch_seconds"] for row in self.rows) / max(1, len(self.rows)),
                "mean_loader_and_logging_gap_seconds": sum(row["loader_and_logging_gap_seconds"] for row in self.rows) / max(1, len(self.rows))}


def fit_settings(data, out, *, epochs, batch, workers, device, seed, patience, cache):
    return dict(data=str(data), epochs=epochs, imgsz=640, batch=batch,
                workers=workers, device=device, seed=seed, deterministic=True,
                amp=True, optimizer="AdamW", lr0=0.001, lrf=0.01, cos_lr=True,
                weight_decay=0.0005, warmup_epochs=3, patience=patience,
                hsv_h=0.015, hsv_s=0.4, hsv_v=0.4, degrees=5, translate=0.1,
                scale=0.5, fliplr=0.5, flipud=0.0, mosaic=0.75, close_mosaic=10,
                mixup=0.0, project=str(out), name="fit", exist_ok=False,
                plots=True, cache=False if cache == "none" else cache,
                save=True, save_period=10, val=True)


def benchmark(args):
    with run_lock(args.output.resolve()):
        with label_scan_threads(getattr(args, "scan_threads", 8)) as scan_info:
            _benchmark_locked(args, scan_info)


def _benchmark_locked(args, scan_info=None):
    import torch
    import ultralytics
    from ultralytics import YOLO
    started = time.time()
    data, output = args.data.resolve(), args.output.resolve()
    manifest = verified_manifest(data)
    if sha256(args.weights) != INITIAL_SHA256:
        raise ValueError("Hardware pilot requires the pinned official initializer")
    if (output / "benchmark.json").exists() or (output / "fit").exists():
        raise FileExistsError("Each hardware pilot requires a new output directory")
    torch.set_num_threads(4)
    model = YOLO(str(args.weights.resolve()))
    measurement = BatchBenchmark(args.steps, args.warmup_steps, args.batch)

    def start_measurement(trainer):
        if len(trainer.train_loader) <= args.steps:
            raise ValueError("Pilot must stop strictly before a full training epoch")
        if trainer.device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(trainer.device)
            measurement.synchronize = lambda: torch.cuda.synchronize(trainer.device)

    model.add_callback("on_train_start", start_measurement)
    model.add_callback("on_train_batch_start", measurement.start)
    model.add_callback("on_train_batch_end", measurement.end)
    settings = fit_settings(data, output, epochs=1, batch=args.batch, workers=args.workers,
                            device=args.device, seed=args.seed, patience=20, cache=args.cache)
    settings.update(val=False, save=False, plots=False, save_period=-1)
    report = {"status": "running", "purpose": "TRAIN-only hardware pilot; no candidate or final-test metrics",
              "dataset": manifest["dataset"], "dataset_manifest_sha256": sha256(data.parent / "manifest.json"),
              "loader_setup": scan_info,
              "settings": settings, "versions": {"torch": torch.__version__, "ultralytics": ultralytics.__version__},
              "note": "Steady timings include synchronized compute plus loader/logging gaps; startup/cache cost is separately included in wall time. An exception ends the pilot before epoch validation/checkpoint saving."}
    write_json(output / "benchmark.json", report)
    try:
        model.train(**settings)
        raise RuntimeError("Hardware pilot unexpectedly reached normal training completion")
    except BenchmarkComplete:
        report.update({"status": "completed_train_only_pilot", **measurement.report(), "wall_seconds_including_setup": time.time() - started})
        if model.trainer.device.type == "cuda":
            report["peak_allocated_gib"] = torch.cuda.max_memory_allocated(model.trainer.device) / 1024**3
            report["peak_reserved_gib"] = torch.cuda.max_memory_reserved(model.trainer.device) / 1024**3
    except Exception as error:
        report.update({"status": "failed", "error": str(error), "wall_seconds_including_setup": time.time() - started})
        raise
    finally:
        write_json(output / "benchmark.json", report)
        trainer = getattr(model, "trainer", None)
        for loader in (getattr(trainer, "train_loader", None), getattr(trainer, "test_loader", None)):
            iterator = getattr(loader, "iterator", None)
            shutdown = getattr(iterator, "_shutdown_workers", None)
            if shutdown:
                shutdown()
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


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
        with label_scan_threads(getattr(args, "scan_threads", 8)) as scan_info:
            _train_locked(args, scan_info)


def _train_locked(args, scan_info=None):
    started = time.time()
    budget = runtime_budget(args.time_hours, args.deadline_utc, args.evaluation_reserve_minutes, started)
    import platform
    import torch
    import ultralytics
    from ultralytics import YOLO
    torch.set_num_threads(4)
    if budget and ultralytics.__version__ != "8.3.221":
        raise ValueError("Timed callback contract is validated against Ultralytics 8.3.221 only")
    data, out = args.data.resolve(), args.output.resolve()
    manifest = verified_manifest(data)
    initial_hash = sha256(args.weights)
    out.mkdir(parents=True, exist_ok=True)
    configuration = out / "training.json"
    previous = json.loads(configuration.read_text(encoding="utf-8")) if args.resume else None
    if args.stage_from_run:
        initialization = stage_initialization(args.stage_from_run, args.weights, sha256(data.parent / "manifest.json"), out)
    elif previous:
        initialization = previous["initialization"]
        if initial_hash != initialization["sha256"]:
            raise ValueError("Resume initializer bytes changed")
    else:
        if initial_hash != INITIAL_SHA256:
            raise ValueError("Initialization must be pinned official YOLO11n, or an explicitly declared continuation stage")
        initialization = {"sha256": initial_hash, "path": str(args.weights.resolve()),
                          "source": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
                          "training_data": "COCO train2017 (official pretrained weights)"}
    settings = fit_settings(data, out, epochs=args.epochs, batch=args.batch, workers=args.workers,
                            device=args.device, seed=args.seed, patience=args.patience, cache=args.cache)
    config = {"schema": 1, "dataset": manifest["dataset"], "settings": settings,
              "initialization": initialization,
              "dataset_manifest_sha256": sha256(data.parent / "manifest.json"),
              "versions": {"python": platform.python_version(), "torch": torch.__version__, "ultralytics": ultralytics.__version__},
              "status": "training", "limitations": manifest["limitations"]}
    if args.resume:
        check_resume(previous, config, args.resume, out)
        config = previous
        budget = resumed_budget(previous.get("runtime_budget"), budget)
        model = YOLO(str(args.resume.resolve()))
        checkpoint_args = model.ckpt.get("train_args", {})
        for key in ("data", "seed", "epochs", "batch", "optimizer", "lr0", "patience", "project"):
            if key == "epochs" and previous.get("runtime_budget") and checkpoint_args.get("time"):
                # Native timed training stores its dynamically estimated epoch horizon.
                continue
            if checkpoint_args.get(key) != previous["settings"][key]:
                raise ValueError(f"Resume checkpoint disagrees with the recorded setting: {key}")
    else:
        if configuration.exists():
            raise FileExistsError("A training run exists; choose a new output or --resume")
        model = YOLO(str(args.weights.resolve()))
        if args.stage_from_run and model.names != NAMES[manifest["dataset"]]:
            raise ValueError("Continuation checkpoint classes differ from the prepared task")
    if initialization.get("kind") == "new_optimizer_continuation_stage":
        parent_checkpoint = (torch.load(args.weights.resolve(), map_location="cpu", weights_only=False)
                             if args.resume else model.ckpt)
        initialization["parent_dev_fitness"] = stage_parent_fitness(
            parent_checkpoint, initialization.get("parent_versions", {}), ultralytics.__version__)
        initialization["selection_policy"] = "Strict child improvement in internal-dev mAP50-95(B); otherwise retain exact parent bytes"
        del parent_checkpoint
    if budget:
        if ultralytics.__version__ != "8.3.221":
            raise ValueError("Timed callback contract is validated against Ultralytics 8.3.221 only")
        budget["invocation_started_timestamp"] = started
        config["runtime_budget"] = budget
    timing = TimedRun(budget, args.workers, out, mosaic_window=settings["close_mosaic"])
    timing.attach(model)
    config["loader_setup"] = scan_info
    write_json(configuration, config)
    try:
        if args.resume:
            model.train(resume=True, device=args.device, workers=args.workers)
        else:
            model.train(**settings)
    except Exception as error:
        config.update({"status": "training_interrupted", "error": str(error),
                       "elapsed_wall_seconds": time.time() - started, "epoch_coverage_this_invocation": timing.epochs})
        write_json(configuration, config)
        raise
    checkpoint = Path(model.trainer.best)
    partial_last = bool(timing.epochs and not timing.epochs[-1]["full_epoch"])
    config.update({"status": "trained_pending_stage_selection", "completed_epochs": int(model.trainer.epoch + 1) - int(partial_last),
                   "epochs_reached": int(model.trainer.epoch + 1), "last_epoch_partial": partial_last,
                   "epoch_coverage_this_invocation": timing.epochs, "elapsed_wall_seconds": time.time() - started,
                   "best_checkpoint": str(checkpoint), "best_checkpoint_sha256": sha256(checkpoint)})
    write_json(configuration, config)
    if initialization.get("kind") == "new_optimizer_continuation_stage":
        config["stage_selection"] = select_stage_checkpoint(
            args.weights.resolve(), checkpoint, initialization["sha256"],
            initialization["parent_dev_fitness"], model.trainer.best_fitness, out)
        config["best_checkpoint_sha256"] = sha256(checkpoint)
    config["status"] = "trained_pending_final_evaluation"
    write_json(configuration, config)
    if not args.train_only:
        if budget and budget.get("deadline_timestamp") is not None and time.time() >= budget["deadline_timestamp"]:
            raise TimeoutError("Overall deadline reached; checkpoint preserved, final evaluation was not started")
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
    fit.add_argument("--scan-threads", type=int, default=8, help="Native image/label verification threads (1..48), independent of DataLoader workers")
    fit.add_argument("--seed", type=int, default=SEED)
    continuation = fit.add_mutually_exclusive_group()
    continuation.add_argument("--resume", type=Path)
    continuation.add_argument("--stage-from-run", type=Path, help="Declare a new optimizer stage from this stopped run's best.pt; requires a new output")
    fit.add_argument("--cache", choices=["none", "disk"], default="none")
    fit.add_argument("--time-hours", type=float, help="Maximum training wall hours including setup; excludes the reserved final evaluation")
    fit.add_argument("--deadline-utc", help="Overall deadline with explicit timezone, e.g. 2026-10-07T18:47:54Z")
    fit.add_argument("--evaluation-reserve-minutes", type=float, default=45.0)
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
    pilot = commands.add_parser("benchmark", help="Bounded train-only hardware pilot; no final test, export or candidate checkpoint")
    pilot.add_argument("--data", type=Path, required=True)
    pilot.add_argument("--weights", type=Path, required=True)
    pilot.add_argument("--output", type=Path, required=True)
    pilot.add_argument("--batch", type=int, default=64)
    pilot.add_argument("--workers", type=int, default=16)
    pilot.add_argument("--scan-threads", type=int, default=8, help="Native image/label verification threads (1..48), independent of DataLoader workers")
    pilot.add_argument("--device", default="0")
    pilot.add_argument("--seed", type=int, default=SEED)
    pilot.add_argument("--steps", type=int, default=64)
    pilot.add_argument("--warmup-steps", type=int, default=16)
    pilot.add_argument("--cache", choices=["none", "disk"], default="none")
    args = parser.parse_args()
    if args.command == "prepare":
        for name in args.only or ["wider", "coco"]:
            function = prepare_coco if name == "coco" else prepare_wider
            prepared_output = args.output.resolve() / name
            with run_lock(prepared_output):
                function(args.datasets.resolve(), prepared_output, seed=args.seed, mode=args.link_mode)
    elif args.command == "train":
        train(args)
    elif args.command == "benchmark":
        benchmark(args)
    elif args.command == "evaluate":
        from .public_detection_eval import evaluate
        evaluate(args.data.resolve(), args.checkpoint.resolve(), args.output.resolve(),
                 args.device, args.batch, args.workers, args.parity_images)
    else:
        from .public_detection_eval import recover_export
        recover_export(args.data.resolve(), args.checkpoint.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
