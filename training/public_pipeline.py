"""Durable three-model research training queue; never publishes model artifacts.

Run this module from an OS-supervised process on the training host. Dataset
acquisition is separate. Only verified dataset status files unlock preparation.
One GPU job runs at a time, with up to two independent CPU preparation jobs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

def plan(root: Path, weights: Path, landmarker: Path):
    datasets = root / "data/datasets"
    prepared = root / "data/public-detection"
    gaze = root / "data/public-gaze"
    runs = root / "training/runs"
    jobs = []
    for name, source, epochs in (("wider", "widerface", 100), ("coco", "coco2017", 80)):
        jobs.append(dict(name=f"prepare-{name}", resource="cpu", datasets=[source], depends=[],
            command=["-m", "training.public_detection", "prepare", "--datasets", str(datasets),
                     "--output", str(prepared), "--only", name]))
        jobs.append(dict(name=f"train-{name}", resource="gpu", datasets=[], depends=[f"prepare-{name}"],
            command=["-m", "training.public_detection", "train", "--data", str(prepared/name/"data.yaml"),
                     "--weights", str(weights), "--output", str(runs/f"public-{name}-v1"),
                     "--epochs", str(epochs), "--patience", "20", "--batch", "16", "--workers", "8", "--device", "0"]))
    jobs += [
        dict(name="index-gaze", resource="cpu", datasets=["mpiifacegaze", "gaze360"], depends=[],
             command=["-m", "training.public_gaze_prepare", "index", "--data", str(datasets), "--output", str(gaze)]),
        dict(name="extract-gaze", resource="cpu", datasets=[], depends=["index-gaze"],
             command=["-m", "training.public_gaze_prepare", "extract", "--output", str(gaze),
                      "--model", str(landmarker), "--workers", "8", "--disable-audio"]),
        dict(name="train-gaze", resource="gpu", datasets=[], depends=["extract-gaze"],
             command=["-m", "training.public_gaze_train", "--data", str(gaze),
                      "--output", str(runs/"public-gaze-v1"), "--device", "cuda:0", "--batch", "128",
                      "--workers", "6", "--epochs", "40", "--patience", "8"]),
    ]
    return jobs


def dataset_ready(root: Path, name: str):
    try:
        return json.loads((root/"data/datasets"/name/"status.json").read_text("utf-8"))["status"] == "ready"
    except (OSError, ValueError, KeyError):
        return False


def runnable(job, jobs, root, active):
    if job["status"] != "pending":
        return False
    if any(jobs[name]["status"] != "complete" for name in job["depends"]):
        return False
    if not all(dataset_ready(root, name) for name in job["datasets"]):
        return False
    limit = 1 if job["resource"] == "gpu" else 2
    return sum(jobs[name]["resource"] == job["resource"] for name in active) < limit


def main():
    from training.datasets.common import process_lock, write_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--landmarker", type=Path, required=True)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    schedule = plan(root, args.weights.resolve(), args.landmarker.resolve())
    if args.plan_only:
        print(json.dumps(schedule, indent=2))
        return
    logs = root/"logs/public-training"
    logs.mkdir(parents=True, exist_ok=True)
    receipt = root/"public-training-status.json"
    with process_lock(root/".public-training.lock"):
        if receipt.exists():
            raise FileExistsError("This run already has a journal. Inspect it and resume individual interrupted jobs explicitly.")
        jobs = {job["name"]: dict(job, status="pending") for job in schedule}
        state = dict(status="running", pid=os.getpid(), started_utc=datetime.now(timezone.utc).isoformat(),
                     purpose="Research training and final evaluation; no automatic publication or production promotion",
                     source_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in Path(__file__).parent.glob("public_*.py")}, jobs=jobs)
        environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1", "YOLO_AUTOINSTALL": "false",
                       "OMP_NUM_THREADS": "4", "MKL_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "1"}
        active = {}
        while True:
            for name, (process, stream) in list(active.items()):
                code = process.poll()
                if code is not None:
                    stream.close()
                    jobs[name].update(status="complete" if code == 0 else "failed", exit_code=code,
                                      finished_utc=datetime.now(timezone.utc).isoformat())
                    del active[name]
            for job in jobs.values():
                if job["status"] == "pending" and any(jobs[name]["status"] in ("failed", "blocked") for name in job["depends"]):
                    job["status"] = "blocked"
                if runnable(job, jobs, root, active):
                    log = logs/(job["name"]+".log")
                    stream = log.open("wb")
                    process = subprocess.Popen([sys.executable, "-u", *job["command"]], cwd=root, env=environment,
                                               stdout=stream, stderr=subprocess.STDOUT)
                    active[job["name"]] = (process, stream)
                    job.update(status="running", pid=process.pid, log=str(log),
                               started_utc=datetime.now(timezone.utc).isoformat())
            if all(j["status"] in ("complete", "failed", "blocked") for j in jobs.values()):
                state["status"] = "complete" if all(j["status"] == "complete" for j in jobs.values()) else "failed"
            state["updated_utc"] = datetime.now(timezone.utc).isoformat()
            write_json(receipt, state)
            if state["status"] != "running":
                break
            time.sleep(15)


if __name__ == "__main__":
    main()
