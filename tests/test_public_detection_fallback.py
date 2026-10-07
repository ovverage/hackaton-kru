"""Fallback selection tests need no Torch, GPU, test data or running processes."""
import json
from contextlib import nullcontext
from pathlib import Path

import pytest

from training.public_detection import INITIAL_SHA256, check_resume, run_lock, sha256, stage_initialization, write_json
from training.public_detection_fallback import MARKER, prepare_fallback, stopped_stage_audit, verify_frozen_selection

psutil = pytest.importorskip("psutil")
pytest.importorskip("requests")


def fixture(tmp_path, child_fitness=.4, status="timed_out", stripped=False):
    data = tmp_path/"prepared/data.yaml"
    data.parent.mkdir()
    data.write_text("prepared fixture")
    write_json(data.parent/"manifest.json", {"dataset": "wider", "data_yaml_sha256": sha256(data), "sources": {}, "splits": {}})
    parent, stage, output = (tmp_path/name for name in ("parent", "stage", "fallback"))
    for run in (parent, stage):
        (run/"fit/weights").mkdir(parents=True)
    parent_path = parent/"fit/weights/best.pt"
    parent_path.write_bytes(b"parent exact bytes")
    child_path = stage/"fit/weights/best.pt"
    if child_fitness is not None:
        child_path.write_bytes(b"child exact bytes")
    versions = {"python": "3.11.9", "torch": "2.6.0+cu124", "ultralytics": "8.3.221"}
    common = {"dataset": "wider", "dataset_manifest_sha256": sha256(data.parent/"manifest.json"), "versions": versions, "settings": {"batch": 16}}
    write_json(parent/"training.json", {**common, "initialization": {"sha256": INITIAL_SHA256}})
    initial = {"kind": "new_optimizer_continuation_stage", "parent_run": str(parent), "path": str(parent_path),
               "sha256": sha256(parent_path), "parent_training_receipt_sha256": sha256(parent/"training.json"),
               "original_pretraining_sha256": INITIAL_SHA256}
    write_json(stage/"training.json", {**common, "initialization": initial, "status": "training"})
    journal = tmp_path/"queue.json"
    exited = {"pid": 2147483647, "create_time": 1., "cmdline": ["python", "train-wider"], "cwd": str(tmp_path)}
    assert not psutil.pid_exists(exited["pid"])
    write_json(journal, {"processes": [exited], "jobs": {"train-wider": {"status": status, "process": exited,
                                                                      "command": ["train", "--output", str(stage), "--data", str(data)]}}})
    def reader(path):
        score = .48076 if Path(path) == parent_path else child_fitness
        checkpoint = {"version": "8.3.221", "best_fitness": score,
                      "train_metrics": {"fitness": score, "metrics/mAP50-95(B)": score},
                      "train_args": {"data": str(data)}}
        if stripped and Path(path) != parent_path:
            checkpoint.update(best_fitness=None, epoch=-1, optimizer=None, ema=None, model=object())
        return checkpoint
    return data, parent, stage, journal, output, reader


@pytest.mark.parametrize("child_score,selected", [(.4, "parent"), (.48076, "parent"), (.49, "child"), (None, "parent")])
def test_fallback_strict_dev_selection_preserves_history_and_freezes_both_runs(tmp_path, child_score, selected):
    data, parent, stage, journal, output, reader = fixture(tmp_path, child_score)
    before = {run: sha256(run/"training.json") for run in (parent, stage)}
    result = prepare_fallback(data, stage, journal, output, checkpoint_reader=reader)
    assert result["selected_source"] == selected
    assert (output/"selected.pt").read_bytes() == (b"child exact bytes" if selected == "child" else b"parent exact bytes")
    assert verify_frozen_selection(output) == result
    for run in (parent, stage):
        assert sha256(run/"training.json") == before[run]
        assert (run/MARKER).exists()
        with pytest.raises(ValueError, match="selection.*frozen"):
            check_resume({}, {}, run/"fit/weights/last.pt", run)
    with pytest.raises(ValueError, match="selection.*frozen"):
        stage_initialization(parent, parent/"fit/weights/best.pt", "unused", tmp_path/"future-stage")
    with pytest.raises(FileExistsError, match="already started"):
        prepare_fallback(data, stage, journal, tmp_path/"another-output", checkpoint_reader=reader)
    assert not (output/"heldout-metrics.json").exists()


def test_stripped_native_child_metrics_can_select_child_without_retuning(tmp_path):
    data, _, stage, journal, output, reader = fixture(tmp_path, .6, stripped=True)
    assert prepare_fallback(data, stage, journal, output, checkpoint_reader=reader)["selected_source"] == "child"


@pytest.mark.parametrize("status", ["running", "pending", "complete", "blocked"])
def test_only_definitive_failed_stages_are_eligible(tmp_path, status):
    data, _, stage, journal, output, reader = fixture(tmp_path, status=status)
    with pytest.raises(ValueError, match="failed or timed-out"):
        prepare_fallback(data, stage, journal, output, checkpoint_reader=reader)


def test_active_training_lock_prevents_selection(tmp_path):
    data, _, stage, journal, output, reader = fixture(tmp_path)
    with run_lock(stage), pytest.raises(RuntimeError, match="Another process"):
        prepare_fallback(data, stage, journal, output, checkpoint_reader=reader)


@pytest.mark.parametrize("change", ["parent_bytes", "parent_receipt", "stage_manifest", "stage_version", "test_started"])
def test_changed_provenance_or_opened_test_refuses_fallback(tmp_path, change):
    data, parent, stage, journal, output, reader = fixture(tmp_path)
    if change == "parent_bytes":
        (parent/"fit/weights/best.pt").write_bytes(b"different")
    elif change == "test_started":
        write_json(stage/"final-evaluation.json", {"status": "evaluating"})
    else:
        path = (parent if change == "parent_receipt" else stage)/"training.json"
        value = json.loads(path.read_text())
        if change == "stage_manifest":
            value["dataset_manifest_sha256"] = "different"
        elif change == "stage_version":
            value["versions"]["ultralytics"] = "8.3.222"
        else:
            value["edited"] = True
        write_json(path, value)
    with pytest.raises((ValueError, FileExistsError)):
        prepare_fallback(data, stage, journal, output, checkpoint_reader=reader)


def test_nonfinite_dev_fitness_and_changed_checkpoint_data_are_rejected(tmp_path):
    data, _, stage, journal, output, reader = fixture(tmp_path, float("nan"))
    with pytest.raises(ValueError, match="finite"):
        prepare_fallback(data, stage, journal, output, checkpoint_reader=reader)
    def changed(path):
        value = reader(path)
        value["train_args"]["data"] = str(tmp_path/"other.yaml")
        return value
    with pytest.raises(ValueError, match="data path"):
        prepare_fallback(data, stage, journal, output, checkpoint_reader=changed)


@pytest.mark.parametrize("target", ["checkpoint", "marker", "history"])
def test_frozen_selection_integrity_is_checked_before_final_evaluation(tmp_path, target):
    data, parent, stage, journal, output, reader = fixture(tmp_path)
    prepare_fallback(data, stage, journal, output, checkpoint_reader=reader)
    path = {"checkpoint": output/"selected.pt", "marker": stage/MARKER, "history": parent/"training.json"}[target]
    path.write_text("{}")
    with pytest.raises(ValueError):
        verify_frozen_selection(output)


class AuditProcess:
    def __init__(self, record, children=()):
        self.record = record
        self.pid = record["pid"]
        self.descendants = children

    def oneshot(self):
        return nullcontext()

    def create_time(self):
        return self.record["create_time"]

    def cmdline(self):
        return self.record["cmdline"]

    def cwd(self):
        return self.record["cwd"]

    def is_running(self):
        return True

    def status(self):
        return psutil.STATUS_RUNNING

    def children(self, recursive):
        assert recursive
        return list(self.descendants)


def audit_fixture(tmp_path):
    records = [{"pid": pid, "create_time": float(pid), "cmdline": ["python", name], "cwd": str(tmp_path)}
               for pid, name in ((101, "train-wider"), (102, "orphan-worker"), (201, "train-coco"), (202, "coco-worker"))]
    state = {"jobs": {"train-wider": {"status": "timed_out", "process": records[0]}}, "processes": records[:1]}
    processes = {}

    def factory(pid):
        result = processes.get(pid)
        if isinstance(result, BaseException):
            raise result
        if result is None:
            raise psutil.NoSuchProcess(pid)
        return result

    return state, records, processes, factory


@pytest.mark.parametrize("target", ["stage", "inventory"])
def test_stopped_stage_access_denied_never_means_gone(tmp_path, target):
    state, records, processes, factory = audit_fixture(tmp_path)
    record = records[0] if target == "stage" else records[1]
    if target == "inventory":
        state["processes"].append(record)
    processes[record["pid"]] = psutil.AccessDenied(record["pid"])
    with pytest.raises(ValueError, match="unverified"):
        stopped_stage_audit(state, "train-wider", tmp_path, process_factory=factory)


def test_orphan_worker_blocks_fallback_even_after_stage_parent_exits(tmp_path):
    state, records, processes, factory = audit_fixture(tmp_path)
    state["processes"].append(records[1])
    processes[102] = AuditProcess(records[1])
    with pytest.raises(ValueError, match="outside another job tree"):
        stopped_stage_audit(state, "train-wider", tmp_path, process_factory=factory)


def test_other_running_coco_tree_is_allowed_without_stopping_it(tmp_path):
    state, records, processes, factory = audit_fixture(tmp_path)
    state["jobs"]["train-coco"] = {"status": "running", "process": records[2]}
    state["processes"].extend(records[2:])
    processes[202] = AuditProcess(records[3])
    processes[201] = AuditProcess(records[2], children=[processes[202]])
    result = stopped_stage_audit(state, "train-wider", tmp_path, process_factory=factory)
    assert {row["pid"] for row in result["other_job_processes_allowed"]} == {201, 202}
    assert {row["job"] for row in result["other_job_processes_allowed"]} == {"train-coco"}


def test_unrelated_running_job_cannot_whitelist_orphan_worker(tmp_path):
    state, records, processes, factory = audit_fixture(tmp_path)
    state["jobs"]["train-coco"] = {"status": "running", "process": records[2]}
    state["processes"].extend(records[1:3])
    processes[102] = AuditProcess(records[1])
    processes[201] = AuditProcess(records[2])
    with pytest.raises(ValueError, match="outside another job tree"):
        stopped_stage_audit(state, "train-wider", tmp_path, process_factory=factory)


def test_reused_pid_counts_as_original_process_gone(tmp_path):
    state, records, processes, factory = audit_fixture(tmp_path)
    processes[101] = AuditProcess({**records[0], "create_time": 999.})
    result = stopped_stage_audit(state, "train-wider", tmp_path, process_factory=factory)
    assert result["other_job_processes_allowed"] == []


@pytest.mark.parametrize("missing", ["process", "processes"])
def test_missing_stopped_process_proof_is_rejected(tmp_path, missing):
    state, _, _, factory = audit_fixture(tmp_path)
    if missing == "process":
        del state["jobs"]["train-wider"]["process"]
    else:
        del state[missing]
    with pytest.raises(ValueError, match="identity and process inventory"):
        stopped_stage_audit(state, "train-wider", tmp_path, process_factory=factory)


def test_unverified_process_inventory_prevents_any_fallback_freeze(tmp_path, monkeypatch):
    data, parent, stage, journal, output, reader = fixture(tmp_path)
    state, records, processes, factory = audit_fixture(tmp_path)
    original = json.loads(journal.read_text())
    original["jobs"]["train-wider"]["process"] = records[0]
    original["processes"] = state["processes"]
    write_json(journal, original)
    processes[101] = psutil.AccessDenied(101)
    monkeypatch.setattr(psutil, "Process", factory)
    with pytest.raises(ValueError, match="unverified"):
        prepare_fallback(data, stage, journal, output, checkpoint_reader=reader)
    assert not (output/"selected.pt").exists()
    assert not (output/"selection.json").exists()
    assert not (parent/MARKER).exists()
    assert not (stage/MARKER).exists()
