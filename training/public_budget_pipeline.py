"""Deadline-bound research queue; no automatic model publication.

Existing training is explicitly frozen before this queue starts. Hardware pilots
use training data only. CPU preparation/evaluation overlaps one GPU job at a time.
The independent public_resource_guard enforces the final cutoff and restores the
temporarily released Ollama model, including after a queue failure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

from training.datasets.common import process_lock, write_json


def utc_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Deadline requires an explicit timezone")
    return parsed.timestamp()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def identity(process):
    return dict(pid=process.pid, create_time=process.create_time(),
                cmdline=process.cmdline(), cwd=process.cwd())


def matching_process(record):
    try:
        process = psutil.Process(record["pid"])
        if abs(process.create_time() - record["create_time"]) > .000001:
            return None
        if process.cmdline() != record["cmdline"]:
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def stop_records(records):
    from training.public_resource_guard import stop_owned
    stopped = stop_owned(records, Path.cwd())
    if not stopped["stopped"]:
        raise RuntimeError("Owned processes did not stop: " + json.dumps(stopped["remaining"]))


def validate_schedule(cutoffs, now):
    if any(left >= right for left,right in zip(cutoffs,cutoffs[1:])):
        raise ValueError("Stage cutoffs must be strictly increasing")
    if cutoffs[-1]-cutoffs[-2] < 45*60:
        raise ValueError("At least45minutes final application/restore reserve is required")
    if now >= cutoffs[0]-600:
        raise ValueError("At least10minutes for WIDER setup/training are required")


def guard_ready(guard, root, deadline, now):
    try:
        fresh = 0 <= now-utc_timestamp(guard["updated_utc"]) <= 90
        process = matching_process(guard["guard"])
        return (guard.get("status") in {"released","monitoring"}
                and guard.get("unload_verified") is True and guard.get("restore_required") is True
                and utc_timestamp(guard["deadline_utc"]) == deadline and fresh
                and process is not None and Path(guard["guard"]["cwd"]).resolve() == root.resolve())
    except (KeyError,ValueError,TypeError,OSError):
        return False


def choose_hardware(reports):
    def acceptable(report):
        if report.get("status") != "completed_train_only_pilot":
            return False
        for key in ("images_per_second","peak_reserved_gib"):
            value=report.get(key)
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value <= 0:
                return False
        settings=report.get("settings",{})
        batch,workers=settings.get("batch"),settings.get("workers")
        return (report["peak_reserved_gib"]<=39 and type(batch) is int and batch>0
                and type(workers) is int and workers>=0)
    usable = [(name, report) for name, report in reports.items() if acceptable(report)]
    coco = [(name, report) for name, report in usable if report.get("dataset") == "coco"]
    wider = [(name, report) for name, report in usable if report.get("dataset") == "wider"]
    if not coco:
        raise RuntimeError("No successful COCO hardware pilot; refusing an unmeasured GPU configuration")
    name, best = max(coco, key=lambda pair: pair[1]["images_per_second"])
    face_batch = max(wider, key=lambda pair: pair[1]["images_per_second"])[1]["settings"]["batch"] if wider else 32
    return dict(selected_pilot=name, coco_batch=best["settings"]["batch"],
                coco_workers=best["settings"]["workers"], wider_batch=face_batch,
                wider_workers=16, gaze_batch=256, gaze_workers=12,
                reason="Train-only measured throughput with <=39 GiB allocator peak; WIDER falls back to conservative batch32 if its batch64 pilot fails.",
                data_fraction=1.0, image_size=640, test_used_for_selection=False)


def make_plan(root, args):
    prepared = root / "data/public-detection"
    runs = root / "training/runs"
    pilot_root = runs / "budget-hardware-v2"
    def job(name, resource, command, depends=(), **extra):
        return dict(name=name, resource=resource, command=command, depends=list(depends), status="pending", **extra)
    jobs = [job("extract-gaze", "cpu", ["-m", "training.public_gaze_prepare", "extract", "--output", str(root/"data/public-gaze"),
               "--model", str(args.landmarker), "--workers", "16", "--disable-audio"],
               hard_deadline=args.gaze_deadline)]
    for name, dataset, batch, workers in (("coco32", "coco", 32, 16), ("coco64", "coco", 64, 16), ("coco128", "coco", 128, 24), ("wider64", "wider", 64, 16)):
        jobs.append(job("pilot-"+name, "gpu", ["-m", "training.public_detection", "benchmark", "--data", str(prepared/dataset/"data.yaml"),
            "--weights", str(args.weights), "--output", str(pilot_root/name), "--batch", str(batch), "--workers", str(workers),
            "--steps", "48", "--warmup-steps", "12", "--cache", "none", "--scan-threads", "32"],
            ["pilot-coco64"] if name == "coco128" else [],
            require_success=False, report=str(pilot_root/name/"benchmark.json"), max_seconds=2400 if name=="coco32" else 900,
            hard_deadline=args.wider_deadline))
    jobs.append(job("select-hardware", "control", [], ["pilot-coco32", "pilot-coco64", "pilot-coco128", "pilot-wider64"], require_success=False))
    for dataset, hours, cutoff in (("wider", .65, args.wider_deadline), ("coco", 5.25, args.coco_deadline)):
        output = runs/("budget-"+dataset+"-v1")
        initial = args.wider_parent/"fit/weights/best.pt" if dataset == "wider" else args.weights
        command = ["-m", "training.public_detection", "train", "--data", str(prepared/dataset/"data.yaml"),
            "--weights", str(initial), "--output", str(output), "--epochs", "100" if dataset == "wider" else "80",
            "--patience", "15", "--batch", "PENDING", "--workers", "PENDING", "--scan-threads", "32", "--device", "0", "--train-only",
            "--time-hours", str(hours), "--deadline-utc", cutoff, "--evaluation-reserve-minutes", "10" if dataset == "wider" else "15"]
        if dataset == "wider":
            command += ["--stage-from-run", str(args.wider_parent)]
        jobs.append(job("train-"+dataset, "gpu", command, ["select-hardware"] + (["train-wider"] if dataset == "coco" else []),
                        require_success=False, hard_deadline=cutoff))
        jobs.append(job("evaluate-"+dataset, "cpu", ["-m", "training.public_detection", "evaluate",
            "--data", str(prepared/dataset/"data.yaml"), "--checkpoint", str(output/"fit/weights/best.pt"),
            "--output", str(output), "--device", "cpu", "--batch", "8", "--workers", "8", "--parity-images", "32"],
            ["train-"+dataset], hard_deadline=args.gaze_deadline))
    jobs.append(job("train-gaze", "gpu", ["-m", "training.public_gaze_train", "--data", str(root/"data/public-gaze"),
        "--output", str(runs/"budget-gaze-v1"), "--device", "cuda:0", "--batch", "256", "--workers", "12",
        "--epochs", "40", "--patience", "8", "--cpu-threads", "4", "--max-hours", "2.15",
        "--deadline-utc", args.gaze_deadline, "--finalize-reserve-minutes", "20"],
        ["extract-gaze", "train-coco"], require_success=False, hard_deadline=args.gaze_deadline))
    return {job["name"]: job for job in jobs}


TERMINAL = {"complete", "failed", "blocked", "skipped", "timed_out"}


def ready(job, jobs, active):
    if job["status"] != "pending" or any(jobs[name]["status"] not in TERMINAL for name in job["depends"]):
        return False
    if job.get("require_success", True) and any(jobs[name]["status"] != "complete" for name in job["depends"]):
        job["status"] = "blocked"
        return False
    if job["name"] == "train-gaze" and jobs["extract-gaze"]["status"] != "complete":
        job["status"] = "blocked"
        return False
    if job["name"].startswith("train-") and "select-hardware" in job["depends"] and jobs["select-hardware"]["status"] != "complete":
        job["status"] = "blocked"
        return False
    limit = 1 if job["resource"] == "gpu" else 2
    return sum(jobs[name]["resource"] == job["resource"] for name in active) < limit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--landmarker", type=Path, required=True)
    parser.add_argument("--wider-parent", type=Path, required=True)
    parser.add_argument("--deadline-utc", required=True)
    parser.add_argument("--wider-deadline", required=True)
    parser.add_argument("--coco-deadline", required=True)
    parser.add_argument("--gaze-deadline", required=True)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    args.root, args.weights, args.landmarker, args.wider_parent = (p.resolve() for p in (args.root,args.weights,args.landmarker,args.wider_parent))
    root = args.root
    cutoffs = [utc_timestamp(getattr(args, key)) for key in ("wider_deadline","coco_deadline","gaze_deadline","deadline_utc")]
    validate_schedule(cutoffs,time.time())
    jobs = make_plan(root, args)
    if args.plan_only:
        print(json.dumps(jobs,indent=2))
        return
    os.chdir(root)
    for name in ("widerface","coco2017","mpiifacegaze","gaze360"):
        if json.loads((root/"data/datasets"/name/"status.json").read_text("utf-8"))["status"] != "ready":
            raise ValueError("All four verified datasets must be ready before the budget queue starts")
    transition = json.loads((root/"budget-transition.json").read_text("utf-8"))
    if transition.get("status") != "superseded_by_ten_hour_budget":
        raise ValueError("Original queue takeover was not confirmed")
    from training.public_gaze_prepare import digest
    audit = json.loads((root/"data/public-gaze/index-audit.json").read_text("utf-8"))
    if audit["index_sha256"] != digest(root/"data/public-gaze/index.jsonl"):
        raise ValueError("Prepared gaze index failed integrity check")
    logs = root/"logs/budget-training"
    logs.mkdir(parents=True,exist_ok=True)
    receipt = root/"budget-training-status.json"
    with process_lock(root/".budget-training.lock"):
        if receipt.exists():
            raise FileExistsError("A budget journal already exists; inspect and recover explicitly")
        state = dict(status="running", started_utc=utc_now(), deadline_utc=args.deadline_utc,
            parent=identity(psutil.Process()), jobs=jobs, processes=[], heartbeat_utc=utc_now(),
            source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("public_*.py")},
            purpose="User ten-hour limit; full datasets, development-only selection, no automatic model promotion",
            service_preservation="ai-mektep and WSL remain running; Qorgau children use BELOW_NORMAL CPU priority; no WSL/service changes")
        active, known = {}, {}
        environment={**os.environ,"PYTHONUTF8":"1","PYTHONUNBUFFERED":"1","YOLO_AUTOINSTALL":"false",
                     "OMP_NUM_THREADS":"4","MKL_NUM_THREADS":"4","OPENBLAS_NUM_THREADS":"1"}
        def persist():
            state.update(heartbeat_utc=utc_now(),processes=list(known.values()))
            write_json(receipt,state)
        try:
            while True:
                for name,(process,stream,started) in list(active.items()):
                    code=process.poll()
                    job=jobs[name]
                    try:
                        parent=matching_process(job["process"]) if code is None else None
                        if parent is None and code is None:
                            code=process.poll()
                            if code is None:
                                raise RuntimeError("Active child identity no longer matches its recorded launch")
                        if parent is None:
                            raise psutil.NoSuchProcess(process.pid)
                        for child in [parent,*parent.children(recursive=True)]:
                            record=identity(child)
                            known[(record["pid"],record["create_time"])]=record
                    except (psutil.NoSuchProcess,psutil.AccessDenied):
                        pass
                    if code is None and (time.time() >= utc_timestamp(job["hard_deadline"]) or time.monotonic()-started > job.get("max_seconds",999999)):
                        own=[record for record in known.values() if matching_process(record) is not None]
                        # Kill only this active job's tree; other independent CPU jobs continue.
                        parent=matching_process(job["process"])
                        try:
                            pids={process.pid,*[child.pid for child in parent.children(recursive=True)]} if parent else set()
                        except psutil.NoSuchProcess:
                            pids=set()
                        stop_records([dict(job["process"],queue_parent=True),*[record for record in own if record["pid"] in pids]])
                        code=process.wait(timeout=10)
                        job["status"]="timed_out"
                    if code is not None:
                        stream.close()
                        job.update(status=job["status"] if job["status"]=="timed_out" else ("complete" if code==0 else "failed"),
                                   exit_code=code,finished_utc=utc_now())
                        if name=="train-gaze" and code==0:
                            model=json.loads((root/"training/runs/budget-gaze-v1/gaze-public.json").read_text("utf-8"))
                            job["quality_checks"]=model["release_checks"]
                            job["model_gate_status"]="passed" if model.get("deployment_eligible") is True else "failed"
                            if job["model_gate_status"]=="failed":
                                job.update(status="failed",reason="Training/export finished but gaze quality gates failed")
                        del active[name]
                if time.time() >= cutoffs[-1]:
                    state["status"]="failed"
                    state["stop_reason"]="absolute_deadline"
                    break
                guard_path=root/"resource-guard-status.json"
                guard=json.loads(guard_path.read_text("utf-8")) if guard_path.exists() else {}
                for job in jobs.values():
                    if not ready(job,jobs,active):
                        continue
                    if job["resource"]=="gpu" and not guard_ready(guard,root,cutoffs[-1],time.time()):
                        continue
                    if job["name"]=="pilot-coco128":
                        prior_path=Path(jobs["pilot-coco64"]["report"])
                        prior=json.loads(prior_path.read_text("utf-8")) if prior_path.exists() else {}
                        if prior.get("status")!="completed_train_only_pilot" or prior.get("peak_reserved_gib",999)>20:
                            job.update(status="skipped",reason="batch64 pilot leaves insufficient tested headroom for batch128")
                            continue
                    if job["name"]=="select-hardware":
                        reports={name:json.loads(Path(item["report"]).read_text("utf-8")) for name,item in jobs.items() if "report" in item and Path(item["report"]).exists()}
                        try:
                            hardware=choose_hardware(reports)
                        except RuntimeError as error:
                            # A failed detector pilot must not discard completed
                            # CPU preparation or prevent the independent gaze fit.
                            job.update(status="failed",reason=str(error),finished_utc=utc_now())
                            continue
                        state["hardware_selection"]=hardware
                        write_json(root/"hardware-selection.json",hardware)
                        for dataset in ("wider","coco"):
                            command=jobs["train-"+dataset]["command"]
                            for flag,key in (("--batch",dataset+"_batch"),("--workers",dataset+"_workers")):
                                command[command.index(flag)+1]=str(hardware[key])
                        job.update(status="complete",finished_utc=utc_now())
                        continue
                    if time.time() >= utc_timestamp(job["hard_deadline"]):
                        job.update(status="timed_out",reason="stage window expired before start")
                        continue
                    stream=(logs/(job["name"]+".log")).open("wb")
                    options={"creationflags":subprocess.BELOW_NORMAL_PRIORITY_CLASS} if os.name=="nt" else {}
                    process=subprocess.Popen([sys.executable,"-u",*job["command"]],cwd=root,env=environment,stdout=stream,stderr=subprocess.STDOUT,**options)
                    active[job["name"]]=(process,stream,time.monotonic())
                    record=identity(psutil.Process(process.pid))
                    known[(record["pid"],record["create_time"])]=record
                    job.update(status="running",process=record,log=str(logs/(job["name"]+".log")),started_utc=utc_now())
                    persist()
                if all(job["status"] in TERMINAL for job in jobs.values()):
                    required=[job for name,job in jobs.items() if not name.startswith("pilot-")]
                    state["status"]="complete" if all(job["status"]=="complete" for job in required) else "failed"
                    state["finished_utc"]=utc_now()
                    break
                persist()
                time.sleep(10)
        except Exception as error:
            state.update(status="failed",error=str(error),finished_utc=utc_now())
            raise
        finally:
            try:
                stop_records(list(known.values()))
            finally:
                for _,stream,_ in active.values():
                    stream.close()
                persist()


if __name__=="__main__":
    main()
