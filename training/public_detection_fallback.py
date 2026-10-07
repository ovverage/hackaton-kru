"""Freeze a failed stage's dev-selected fallback; never overwrite its history.

Preparation does not evaluate data. Evaluation is explicit and uses the same
one-shot held-out evaluator/export recovery contract as normal detection runs.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shutil

from .public_detection import INITIAL_SHA256, run_lock, sha256, stage_parent_fitness, verified_manifest, write_json

MARKER = "final-selection-marker.json"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_checkpoint(path):
    import torch
    return torch.load(path, map_location="cpu", weights_only=False)


def require_runtime_versions(versions):
    from importlib.metadata import version
    if any(version(name) != versions[name] for name in ("torch", "ultralytics")):
        raise ValueError("Fallback runtime does not match the frozen training versions")


def finite_checkpoint_fitness(checkpoint, versions):
    # Native strip_optimizer removes best_fitness but preserves the best file's
    # original train_metrics. Only accept the exact native stripped signature.
    if checkpoint.get("best_fitness") is None:
        if (checkpoint.get("epoch") != -1 or checkpoint.get("optimizer") is not None
                or checkpoint.get("ema") is not None or checkpoint.get("model") is None):
            raise ValueError("Missing checkpoint fitness without a valid native stripped signature")
        checkpoint = {**checkpoint, "best_fitness": checkpoint.get("train_metrics", {}).get("fitness")}
    return stage_parent_fitness(checkpoint, versions, "8.3.221")


def unopened(run):
    for name in (MARKER, "heldout-metrics.json", "final-evaluation.json", "final_test"):
        if (run/name).exists():
            raise FileExistsError(f"Final selection/evaluation already started: {run/name}")


def stopped_stage_audit(state, job_name, root, *, process_factory=None):
    """Read-only proof: any surviving recorded PID must belong to another job."""
    import psutil
    from .public_resource_guard import identity_state, process_record

    factory = psutil.Process if process_factory is None else process_factory
    job = state["jobs"][job_name]
    if job.get("status") not in {"failed", "timed_out"}:
        raise ValueError("Fallback requires a definitively failed or timed-out queue stage")
    target, records = job.get("process"), state.get("processes")
    if not isinstance(target, dict) or not isinstance(records, list) or target not in records:
        raise ValueError("Stopped-stage audit requires the recorded job identity and process inventory")
    status, _, reason = identity_state(target, root, process_factory=factory)
    if status != "gone":
        raise ValueError(f"Failed stage process is alive or unverified: {status}: {reason}")

    def key(record):
        return (record["pid"], record["create_time"], tuple(record["cmdline"]),
                str(Path(record["cwd"]).resolve()))

    other_live = {}
    for name, other in state["jobs"].items():
        if name == job_name or other.get("status") != "running":
            continue
        record = other.get("process")
        if not isinstance(record, dict):
            raise ValueError("Other running job lacks a verifiable process identity")
        status, process, reason = identity_state(record, root, process_factory=factory)
        if status == "gone":
            continue
        if status != "verified":
            raise ValueError(f"Other running job identity is unverified: {name}: {reason}")
        try:
            tree = [process, *process.children(recursive=True)]
            for child in tree:
                try:
                    current = process_record(child)
                    current_status, _, current_reason = identity_state(current, root, process_factory=factory)
                    if current_status == "verified":
                        other_live[key(current)] = name
                    elif current_status != "gone":
                        raise ValueError(f"Other job descendant is unverified: {current_reason}")
                except psutil.NoSuchProcess:
                    continue
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as error:
            raise ValueError(f"Cannot verify other running job tree: {name}: {error}") from error

    permitted = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Stopped-stage inventory contains an invalid process identity")
        status, _, reason = identity_state(record, root, process_factory=factory)
        if status == "gone":
            continue
        if status != "verified" or key(record) not in other_live:
            raise ValueError(f"Recorded process is still alive outside another job tree, or unverified: {record.get('pid')}: {reason}")
        permitted.append({"pid": record["pid"], "create_time": record["create_time"], "job": other_live[key(record)]})
    return {"status": "recorded_stage_processes_confirmed_gone", "recorded_processes_checked": len(records),
            "other_job_processes_allowed": permitted,
            "scope": "Recorded queue identities only; OS run locks separately exclude active checkpoint writers."}


def prepare_fallback(data, stage, journal, output, *, job_name="train-wider", checkpoint_reader=read_checkpoint):
    data, stage, journal, output = (Path(p).resolve() for p in (data, stage, journal, output))
    job = read_json(journal)["jobs"][job_name]
    if job.get("status") not in {"failed", "timed_out"}:
        raise ValueError("Fallback requires a definitively failed or timed-out queue stage")
    command = job["command"]
    if Path(command[command.index("--output")+1]).resolve() != stage or Path(command[command.index("--data")+1]).resolve() != data:
        raise ValueError("Queue job is not the requested stage and dataset")
    stage_receipt = stage/"training.json"
    initial = read_json(stage_receipt)["initialization"]
    if initial.get("kind") != "new_optimizer_continuation_stage":
        raise ValueError("Fallback applies only to an explicitly declared continuation stage")
    parent = Path(initial["parent_run"]).resolve()
    if output == stage or output == parent or output.is_relative_to(stage) or output.is_relative_to(parent):
        raise ValueError("Fallback requires a separate output outside both historical runs")
    with ExitStack() as locks:
        for run in (parent, stage, output):
            locks.enter_context(run_lock(run))
        unopened(parent)
        unopened(stage)
        unopened(output)
        state = read_json(journal)
        if state["jobs"][job_name]["command"] != command:
            raise ValueError("Queue job changed while acquiring selection locks")
        process_audit = stopped_stage_audit(state, job_name, journal.parent)
        if (output/"selection.json").exists() or (output/"selected.pt").exists():
            raise FileExistsError("Fallback output already contains a selection")
        manifest = verified_manifest(data)
        manifest_sha = sha256(data.parent/"manifest.json")
        parent_receipt = parent/"training.json"
        parent_config, stage_config = read_json(parent_receipt), read_json(stage_receipt)
        if stage_config["initialization"] != initial:
            raise ValueError("Stage initialization changed while acquiring selection locks")
        for config in (parent_config, stage_config):
            if config["dataset_manifest_sha256"] != manifest_sha or config["dataset"] != manifest["dataset"]:
                raise ValueError("Fallback training receipt dataset/manifest mismatch")
            if config["versions"].get("ultralytics") != "8.3.221":
                raise ValueError("Fallback requires pinned training versions")
        if parent_config["versions"] != stage_config["versions"]:
            raise ValueError("Parent and child runtime versions differ")
        if checkpoint_reader is read_checkpoint:
            require_runtime_versions(stage_config["versions"])
        if initial["parent_training_receipt_sha256"] != sha256(parent_receipt):
            raise ValueError("Parent training receipt changed after the declared stage")
        origin = initial.get("original_pretraining_sha256")
        parent_origin = parent_config["initialization"].get("original_pretraining_sha256", parent_config["initialization"].get("sha256"))
        if origin != INITIAL_SHA256 or parent_origin != INITIAL_SHA256:
            raise ValueError("Fallback initializer lineage is not the pinned official model")
        parent_path = parent/"fit/weights/best.pt"
        if Path(initial["path"]).resolve() != parent_path or sha256(parent_path) != initial["sha256"]:
            raise ValueError("Fallback parent checkpoint identity changed")
        parent_checkpoint = checkpoint_reader(parent_path)
        if Path(parent_checkpoint.get("train_args", {}).get("data", "")).resolve() != data:
            raise ValueError("Parent checkpoint development data path is inconsistent")
        parent_fitness = finite_checkpoint_fitness(parent_checkpoint, parent_config["versions"])
        candidate = stage/"fit/weights/child-best.pt"
        if not candidate.exists():
            candidate = stage/"fit/weights/best.pt"
        child = None
        if candidate.exists():
            child_sha = sha256(candidate)
            child_checkpoint = checkpoint_reader(candidate)
            if Path(child_checkpoint.get("train_args", {}).get("data", "")).resolve() != data:
                raise ValueError("Child checkpoint development data path is inconsistent")
            child_fitness = finite_checkpoint_fitness(child_checkpoint, stage_config["versions"])
            if sha256(candidate) != child_sha:
                raise ValueError("Child checkpoint changed during selection")
            child = {"path": str(candidate), "sha256": child_sha, "dev_fitness": child_fitness}
        improved = child is not None and child["dev_fitness"] > parent_fitness
        chosen = child if improved else {"path": str(parent_path), "sha256": initial["sha256"], "dev_fitness": parent_fitness}
        selected = output/"selected.pt"
        shutil.copy2(chosen["path"], selected)
        if sha256(selected) != chosen["sha256"]:
            raise ValueError("Selected checkpoint copy failed SHA256 verification")
        result = {"status": "checkpoint_frozen_before_heldout", "dataset": manifest["dataset"],
                  "data": str(data), "dataset_manifest_sha256": manifest_sha,
                  "parent_run": str(parent), "stage_run": str(stage), "stage_job_status": job["status"],
                  "queue_journal": str(journal), "queue_journal_sha256_at_selection": sha256(journal),
                  "stopped_stage_process_audit": process_audit,
                  "parent_training_receipt_sha256": sha256(parent_receipt), "stage_training_receipt_sha256": sha256(stage_receipt),
                  "versions": stage_config["versions"], "policy": "Strict internal-dev mAP50-95 improvement; ties retain parent; no final-test feedback",
                  "parent": {"path": str(parent_path), "sha256": initial["sha256"], "dev_fitness": parent_fitness},
                  "child": child, "selected_source": "child" if improved else "parent",
                  "selected_checkpoint": str(selected), "selected_sha256": chosen["sha256"],
                  "note": "Failed/timed-out stage remains failed in its original history. No completed-epoch or test-quality claim is added."}
        selection_path = output/"selection.json"
        write_json(selection_path, result)
        marker = {"selection": str(selection_path), "selection_sha256": sha256(selection_path),
                  "selected_checkpoint": str(selected), "selected_sha256": chosen["sha256"]}
        # Exclusive, append-only markers freeze both histories. A partial failure
        # is fail-closed; evaluation below requires both identical markers.
        for run in (parent, stage):
            with (run/MARKER).open("x", encoding="utf-8") as stream:
                json.dump(marker, stream, indent=2)
        return result


def verify_frozen_selection(output):
    output = Path(output).resolve()
    receipt = output/"selection.json"
    result = read_json(receipt)
    marker = {"selection": str(receipt), "selection_sha256": sha256(receipt),
              "selected_checkpoint": str(output/"selected.pt"), "selected_sha256": result["selected_sha256"]}
    for key in ("parent_run", "stage_run"):
        if read_json(Path(result[key])/MARKER) != marker:
            raise ValueError("Frozen selection marker changed or is incomplete")
        hash_key = "parent_training_receipt_sha256" if key == "parent_run" else "stage_training_receipt_sha256"
        if sha256(Path(result[key])/"training.json") != result[hash_key]:
            raise ValueError("Frozen historical training receipt changed")
    if result["status"] != "checkpoint_frozen_before_heldout" or result["selected_checkpoint"] != marker["selected_checkpoint"]:
        raise ValueError("Fallback selection receipt is inconsistent")
    if sha256(output/"selected.pt") != result["selected_sha256"]:
        raise ValueError("Frozen fallback checkpoint changed")
    data = Path(result["data"])
    verified_manifest(data)
    if sha256(data.parent/"manifest.json") != result["dataset_manifest_sha256"]:
        raise ValueError("Frozen fallback data changed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    for name in ("data", "stage", "journal", "output"):
        prepare.add_argument("--"+name, type=Path, required=True)
    prepare.add_argument("--job", default="train-wider")
    score = commands.add_parser("evaluate")
    score.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_fallback(args.data, args.stage, args.journal, args.output, job_name=args.job)
    else:
        from .public_detection_eval import _evaluate_locked
        with run_lock(args.output.resolve()):
            result = verify_frozen_selection(args.output)
            require_runtime_versions(result["versions"])
            result = _evaluate_locked(Path(result["data"]), Path(result["selected_checkpoint"]), args.output.resolve(),
                                      device="cpu", batch=8, workers=4, parity_images=32)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
