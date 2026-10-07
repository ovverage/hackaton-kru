"""Prepare original-resolution Ultralytics disk caches for verified train/dev lists.

Pilot: python -m training.public_cache --data PATH/data.yaml --output pilot.json \
    --workers 24 --limit 1024

No training, resizing, label edits, or final-test image reads are performed.
Run while the dataset is idle: the lock excludes other instances of this CLI,
not an independently running Ultralytics loader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

import cv2
import numpy as np
from PIL import Image

from training.public_detection import SEED, sha256, stable_order, verified_manifest

KIND = "qorgau-public-detection-cache"
GIB = 1 << 30
MIN_RESERVE = 50 * GIB
OVERHEAD = 8192  # NPY header, provenance JSON, and filesystem block rounding.


@contextmanager
def process_lock(path: Path):
    """Non-blocking OS lock; retain its inode and release automatically on exit."""
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
            raise RuntimeError(f"Another cache process is using {path}") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Item:
    split: str
    path: Path
    key: str


def selected_items(data: Path, manifest: dict, limit: int = 0, seed: int = SEED) -> tuple[list[Item], list[Item]]:
    """Only the verified train and internal-dev lists can introduce filenames."""
    if limit < 0:
        raise ValueError("Limit cannot be negative")
    root = data.parent.resolve()
    items, seen, targets = [], set(), set()
    for split in ("train", "val"):
        row = manifest["splits"][split]
        split_file = Path(row["path"])
        # Check the bytes used for selection, not just an earlier verification.
        content = split_file.read_bytes()
        if hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise ValueError("Prepared split changed during cache preparation")
        group = []
        for line in content.decode("utf-8").splitlines():
            if not line.strip():
                continue
            source = Path(line.strip())
            path = source.resolve(strict=True)
            allowed = root / "images" / split
            if not source.is_absolute() or not path.is_relative_to(allowed):
                raise ValueError(f"Image outside its prepared {split} directory: {source}")
            if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp"}:
                raise ValueError(f"Unsupported image (JPEG/PNG/BMP only): {path}")
            target = path.with_suffix(".npy")
            if path in seen or target in targets:
                raise ValueError(f"Duplicate image/cache target in prepared lists: {path}")
            seen.add(path)
            targets.add(target)
            key = path.relative_to(root).as_posix()
            group.append(Item(split, path, key))
        if len(group) != row["images"]:
            raise ValueError(f"Manifest image count disagrees with {split} list")
        items.extend(sorted(group, key=lambda item: (stable_order(item.key, seed), item.key)))
    return items, items[:limit] if limit else items


def bounded_results(function, items, workers, stop: threading.Event | None = None):
    """At most two jobs per worker are queued, even for a full COCO dataset."""
    iterator = iter(items)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {}
        for item in iterator:
            pending[executor.submit(function, item)] = item
            if len(pending) == workers * 2:
                break
        try:
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    item = pending.pop(future)
                    yield item, future.result()
                    next_item = next(iterator, None) if stop is None or not stop.is_set() else None
                    if next_item is not None:
                        pending[executor.submit(function, next_item)] = next_item
        finally:
            for future in pending:
                future.cancel()


def estimate_bytes(items: list[Item], manifest: dict, workers: int) -> dict[str, int]:
    """Use verified COCO metadata or image headers; never decode test images."""
    metadata = {}
    if manifest.get("dataset") == "coco" and "train2017" in manifest.get("sources", {}):
        source = manifest["sources"]["train2017"]
        content = Path(source["path"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != source["sha256"]:
            raise ValueError("COCO source changed during cache preparation")
        metadata = {row["file_name"]: (row["width"], row["height"]) for row in json.loads(content)["images"]}

    def estimate(item):
        size = metadata.get(item.path.name)
        if size is None:
            with Image.open(item.path) as image:
                size = image.size  # Header only; EXIF rotation preserves the pixel count.
        width, height = size
        if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
            raise ValueError(f"Invalid source dimensions: {item.path}")
        return width * height * 3 + OVERHEAD

    return {item.key: size for item, size in bounded_results(estimate, items, workers)}


class DiskBudget:
    """Reserve space for simultaneous writes as well as the free-space floor."""

    def __init__(self, root: Path, reserve: int = MIN_RESERVE):
        self.root, self.reserve = root, reserve
        self.lock = threading.Lock()
        self.inflight = 0

    def check(self, required: int):
        free = shutil.disk_usage(self.root).free
        if free - self.inflight - required < self.reserve:
            raise OSError(f"Insufficient disk space: free={free}, pending={self.inflight}, "
                          f"required={required}, reserved_floor={self.reserve} bytes")

    @contextmanager
    def writing(self, required: int):
        with self.lock:
            self.check(required)
            self.inflight += required
        try:
            yield
        finally:
            with self.lock:
                self.inflight -= required


def _temporary(target: Path) -> Path:
    return target.with_name(f".{target.name}.qorgau-cache-{uuid.uuid4().hex}.tmp")


def _publish_new(temporary: Path, target: Path):
    # An atomic, exclusive hard-link publication never overwrites an unexpected
    # concurrent writer's file (unlike POSIX rename/replace).
    os.link(temporary, target)


def _json_file(target: Path, value: dict, replace_owned: bool = False):
    temporary = _temporary(target)
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if replace_owned and target.exists():
            if target.is_symlink():
                raise ValueError(f"Refusing to replace a symlink journal: {target}")
            previous = json.loads(target.read_text(encoding="utf-8"))
            if previous.get("kind") != KIND or previous.get("data") != value.get("data"):
                raise ValueError(f"Refusing to replace an unrelated journal: {target}")
            os.replace(temporary, target)
        else:
            _publish_new(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _quarantine(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Refusing to move a symlink or non-file cache: {path}")
    target = path.with_name(f"{path.name}.qorgau-quarantine-{uuid.uuid4().hex}")
    # Destination is exclusive; remove only the original entry after preserving
    # its inode under a distinct name in the same selected image directory.
    os.link(path, target)
    path.unlink()
    return str(target)


def cache_one(item: Item, budget: DiskBudget) -> dict:
    started = time.monotonic()
    source = item.path
    before = source.stat()
    content = source.read_bytes()
    source_hash = hashlib.sha256(content).hexdigest()
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"Cannot decode original BGR image: {source}")
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"Source changed while reading: {source}")
    target = source.with_suffix(".npy")
    sidecar = target.with_name(target.name + ".qorgau-cache.json")
    identity = {"kind": KIND, "schema": 1, "source": str(source), "source_sha256": source_hash,
                "source_bytes": len(content), "shape": list(image.shape), "dtype": "uint8",
                "decoder": "cv2.imdecode(IMREAD_COLOR)", "opencv_version": cv2.__version__}
    quarantined = []
    status, old_metadata, valid = "created", None, False
    if target.is_symlink() or sidecar.is_symlink():
        raise ValueError(f"Refusing a symlink cache/provenance file: {target}")
    if sidecar.exists():
        try:
            old_metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            pass
    if target.exists():
        cached = None
        try:
            cached = np.load(target, mmap_mode="r", allow_pickle=False)
            # Pixel verification also permits safe adoption of an Ultralytics
            # cache that predates these sidecars, without changing its contents.
            valid = cached.shape == image.shape and cached.dtype == np.uint8 and np.array_equal(cached, image)
        except (ValueError, OSError, EOFError, AttributeError, TypeError):
            valid = False
        finally:
            if isinstance(cached, np.memmap):
                cached._mmap.close()
        if valid:
            cache_hash = sha256(target)
            if isinstance(old_metadata, dict) and all(old_metadata.get(k) == v for k, v in identity.items()) and old_metadata.get("cache_sha256") == cache_hash:
                status = "reused"
            else:
                status = "adopted"
        else:
            # Check BEFORE moving an existing entry. Its quarantined bytes stay
            # on disk, and the new cache requires its full additional size.
            with budget.writing(image.nbytes + OVERHEAD):
                quarantined.append(_quarantine(target))
                if sidecar.exists():
                    quarantined.append(_quarantine(sidecar))
                _save_array(target, image)
            cache_hash = sha256(target)
    else:
        with budget.writing(image.nbytes + OVERHEAD):
            _save_array(target, image)
        cache_hash = sha256(target)
    if status != "reused":
        with budget.writing(OVERHEAD):
            if sidecar.exists():
                quarantined.append(_quarantine(sidecar))
            _json_file(sidecar, {**identity, "cache_sha256": cache_hash, "cache_bytes": target.stat().st_size,
                                 "created_utc": datetime.now(timezone.utc).isoformat()})
    return {"split": item.split, "source": str(source), "cache": str(target), "sidecar": str(sidecar),
            "status": status, "source_bytes": len(content), "cache_bytes": target.stat().st_size,
            "cache_sha256": cache_hash, "source_sha256": source_hash, "quarantined": quarantined,
            "duration_seconds": round(time.monotonic() - started, 6)}


def _save_array(target: Path, image: np.ndarray):
    temporary = _temporary(target)
    try:
        # Passing an open stream prevents np.save appending another .npy suffix.
        with temporary.open("xb") as stream:
            np.save(stream, image, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_new(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_cache(data: Path, output: Path, workers: int = 24, limit: int = 0,
                  reserve_bytes: int = MIN_RESERVE, seed: int = SEED) -> dict:
    if not 1 <= workers <= 64 or limit < 0 or reserve_bytes < MIN_RESERVE:
        raise ValueError("Use 1–64 workers, a nonnegative limit, and at least 50 GiB reserve")
    if output.is_symlink():
        raise ValueError("Journal cannot be a symlink")
    data, output = data.resolve(), output.resolve()
    # Keep journal writes out of the image/label trees, including held-out test.
    for directory in (data.parent / "images", data.parent / "labels"):
        if output.is_relative_to(directory):
            raise ValueError("Journal must be outside the dataset image/label directories")
    if output.is_symlink():
        raise ValueError("Journal cannot be a symlink")
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous.get("kind") != KIND or previous.get("data") != str(data):
            raise ValueError("Refusing to replace an unrelated journal")
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    run_id = uuid.uuid4().hex
    receipts = output.with_name(f"{output.stem}.{run_id}.images.jsonl")
    summary = {"kind": KIND, "schema": 1, "data": str(data), "run_id": run_id,
               "started_utc": datetime.now(timezone.utc).isoformat(), "workers": workers,
               "limit": limit, "seed": seed, "reserve_bytes": reserve_bytes, "status": "preflight",
               "selection": "train first, then internal dev (val); SHA256(seed:relative_path)",
               "final_test_images_read": 0, "receipts": str(receipts), "counts": {},
               "source_bytes": 0, "cache_bytes": 0, "quarantined_files": 0}

    def journal():
        summary["duration_seconds"] = round(time.monotonic() - started, 3)
        _json_file(output, summary, replace_owned=True)

    with process_lock(data.parent / ".public-cache.lock"):
        try:
            manifest = verified_manifest(data)
            all_items, selected = selected_items(data, manifest, limit, seed)
            summary.update({"dataset": manifest["dataset"], "manifest_sha256": sha256(data.parent / "manifest.json"),
                            "available_images": len(all_items), "selected_images": len(selected),
                            "selected_by_split": dict(Counter(item.split for item in selected))})
            journal()
            estimates = estimate_bytes(all_items, manifest, workers)
            required = sum(estimates[item.key] for item in selected)
            summary.update({"estimated_full_train_dev_bytes": sum(estimates.values()),
                            "estimated_selected_bytes": required,
                            "estimate_semantics": "Conservative additional original BGR arrays plus 8192 bytes/image; existing caches are not subtracted.",
                            "free_bytes_before": shutil.disk_usage(data.parent).free})
            budget = DiskBudget(data.parent, reserve_bytes)
            budget.check(required)
            summary["status"] = "caching"
            journal()
            cv2.setNumThreads(1)
            counts = Counter()
            stop = threading.Event()
            first_error = None

            def guarded(item):
                if stop.is_set():
                    return {"split": item.split, "source": str(item.path), "status": "cancelled"}
                try:
                    return cache_one(item, budget)
                except Exception as error:
                    stop.set()
                    return {"split": item.split, "source": str(item.path), "status": "failed",
                            "error": f"{type(error).__name__}: {error}"}

            with receipts.open("x", encoding="utf-8") as stream:
                for _, result in bounded_results(guarded, selected, workers, stop):
                    stream.write(json.dumps(result, ensure_ascii=False) + "\n")
                    stream.flush()
                    counts[result["status"]] += 1
                    summary["counts"] = dict(counts)
                    summary["source_bytes"] += result.get("source_bytes", 0)
                    summary["cache_bytes"] += result.get("cache_bytes", 0)
                    summary["quarantined_files"] += len(result.get("quarantined", []))
                    if result["status"] == "failed":
                        first_error = first_error or result["error"]
                    if sum(counts.values()) % 128 == 0:
                        journal()
                        print(json.dumps({"completed": sum(counts.values()), "selected": len(selected),
                                          "duration_seconds": summary["duration_seconds"]}), flush=True)
                os.fsync(stream.fileno())
            if first_error:
                raise RuntimeError(first_error)
            summary["status"] = "completed"
            summary["free_bytes_after"] = shutil.disk_usage(data.parent).free
            journal()
            return summary
        except BaseException as error:
            summary["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
            summary["error"] = f"{type(error).__name__}: {error}"
            journal()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="Verified prepared detection data.yaml")
    parser.add_argument("--output", type=Path, required=True, help="Atomic summary JSON; unique per-run JSONL receipts alongside")
    parser.add_argument("--workers", type=int, default=24, help="CPU threads (24 or 32 recommended); OpenCV uses one thread each")
    parser.add_argument("--limit", type=int, default=0, help="Deterministic train-first pilot size; 0 caches all train/dev")
    parser.add_argument("--reserve-gib", type=int, default=50, help="Free disk floor, minimum 50 GiB")
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    result = prepare_cache(args.data, args.output, args.workers, args.limit, args.reserve_gib * GIB, args.seed)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
