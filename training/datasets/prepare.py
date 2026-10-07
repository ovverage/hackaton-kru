"""Acquire research datasets or refresh the consolidated evidence registry."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .common import log, process_lock, write_json

DATASETS = ("mpiifacegaze", "gaze360", "widerface", "coco2017")


def tree_usage(directory: Path) -> tuple[dict, int]:
    # DirEntry.stat reuses FindFirstFile metadata on Windows. Turning every
    # entry into Path.stat causes hundreds of thousands of extra HDD accesses.
    sizes = {"archives_bytes": 0, "raw_bytes": 0, "sources_bytes": 0, "other_bytes": 0}
    files = 0
    pending = [(str(directory), None)]
    while pending:
        parent, category = pending.pop()
        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            group = entry.name + "_bytes"
                            pending.append((entry.path, category or (group if group in sizes else "other_bytes")))
                        elif entry.is_file(follow_symlinks=False):
                            sizes[category or "other_bytes"] += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue  # A temporary chunk may be renamed during a live snapshot.
        except FileNotFoundError:
            continue
    return sizes, files


def inventory(root: Path, *, scan_files: bool = True) -> dict:
    datasets = {}
    totals = {"archives_bytes": 0, "raw_bytes": 0, "sources_bytes": 0, "other_bytes": 0}
    for name in DATASETS:
        directory = root / name
        report = directory / "status.json"
        try:
            status = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {"status": "not_started"}
        except (OSError, ValueError) as error:
            status = {"status": "unverified", "registry_error": str(error)}
        sizes, files = tree_usage(directory) if scan_files else ({key: 0 for key in totals}, None)
        for category, size in sizes.items():
            totals[category] += size
        archive_downloads = []
        for identity in sorted((directory / "archives").glob("*.source.json")):
            archive = identity.with_name(identity.name.removesuffix(".source.json"))
            partial = archive.with_name(archive.name + ".part")
            receipt = archive.with_name(archive.name + ".download.json")
            try:
                info = json.loads(identity.read_text(encoding="utf-8"))
                if archive.exists() and receipt.exists():
                    info.update(json.loads(receipt.read_text(encoding="utf-8")))
                else:
                    info.update({"status": "partial" if partial.exists() else "pending",
                                 "path": str(archive),
                                 "downloaded_bytes": partial.stat().st_size if partial.exists() else 0})
                range_cache = archive.parent / ".ranges" / archive.name
                manifest = range_cache / "manifest.json"
                if manifest.exists():
                    transfer = json.loads(manifest.read_text(encoding="utf-8"))
                    info["range_transfer"] = {
                        "status": transfer.get("status"), "workers": transfer.get("workers"),
                        "assembled_prefix_bytes": transfer.get("assembled_prefix_bytes", 0),
                        "verified_cached_bytes": sum(p.stat().st_size for p in range_cache.glob("*.chunk") if p.is_file()),
                        "inflight_chunk_bytes": sum(p.stat().st_size for p in range_cache.glob("*.downloading") if p.is_file()),
                    }
                archive_downloads.append(info)
            except (OSError, ValueError) as error:
                archive_downloads.append({"path": str(archive), "status": "unverified", "error": str(error)})
        datasets[name] = {**status, "dataset_directory": str(directory.resolve()),
                          "status_file": str(report.resolve()), "disk_usage": sizes if scan_files else None,
                          "archive_downloads": archive_downloads,
                          "total_bytes": sum(sizes.values()) if scan_files else None, "file_count": files}
    return {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "root": str(root.resolve()), "purpose": "Qorgau research prototype dataset preparation",
            "overall_status": "ready" if all(d["status"] == "ready" for d in datasets.values()) else "incomplete",
            "ready_datasets": [name for name, data in datasets.items() if data["status"] == "ready"],
            "incomplete_datasets": [name for name, data in datasets.items() if data["status"] != "ready"],
            "training_started": False, "pretrained_weights_downloaded": False,
            "datasets": datasets, "disk_usage": {
                "scan_complete": scan_files,
                **(totals if scan_files else {key: None for key in totals}),
                "total_bytes": sum(totals.values()) if scan_files else None,
                "free_bytes": shutil.disk_usage(root).free},
            "interpretation": [
                "Gaze direction alone does not establish phone use or cheating.",
                "Duration is a later sequence/timer task; Gaze360 does not define monitor boundaries.",
                "Keep COCO images without person objects as negative examples; resolve person via categories.",
                "WIDER FACE test has no public bounding-box ground truth.",
                "ETH-XGaze and EYEDIAP were not requested and are not downloaded."
            ]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/datasets"))
    parser.add_argument("--only", nargs="+", choices=DATASETS, default=list(DATASETS))
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--range-workers", type=int, choices=range(0, 5), default=0,
                        help="Optional bounded Range connections per Gaze360/COCO worker (0 = stream)")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    args.only = list(dict.fromkeys(args.only))
    root = args.root.resolve()
    for name in DATASETS:
        for section in ("archives", "raw", "sources"):
            (root / name / section).mkdir(parents=True, exist_ok=True)
    if args.report_only:
        report = inventory(root)
        write_json(root / "DATASETS.json", report)
        log(f"Registry updated: {root / 'DATASETS.json'}")
        return
    if shutil.disk_usage(root).free < 150_000_000_000:
        log("Less than the recommended 150 GB is free; each transfer/extraction checks its actual space needs.")
    def run(name: str) -> int:
        command = [sys.executable, "-u", "-m", f"training.datasets.{name}", "--root", str(root)]
        if args.range_workers and name in {"gaze360", "coco2017"}:
            command += ["--range-workers", str(args.range_workers)]
        with (root / name / "prepare.log").open("a", encoding="utf-8") as logfile:
            return subprocess.call(command, stdout=logfile, stderr=subprocess.STDOUT)
    with process_lock(root / ".prepare.lock"):
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {name: executor.submit(run, name) for name in args.only}
            while any(not future.done() for future in futures.values()):
                write_json(root / "DATASETS.json", inventory(root, scan_files=False))
                time.sleep(20)
            exits = {name: future.result() for name, future in futures.items()}
        write_json(root / "DATASETS.json", inventory(root))
        log(f"Preparation exit codes: {exits}")
        if any(exits.values()):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
