"""Budget planning tests use no GPU, subprocess creation, or Ollama calls."""
from argparse import Namespace
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("psutil")
pytest.importorskip("requests")
pipeline = pytest.importorskip("training.public_budget_pipeline")


def report(dataset="coco", speed=100., memory=20., batch=64, workers=16, **extra):
    return {"status": "completed_train_only_pilot", "dataset": dataset,
            "images_per_second": speed, "peak_reserved_gib": memory,
            "settings": {"batch": batch, "workers": workers}, **extra}


def arguments(root, **updates):
    result = Namespace(root=root, weights=root / "initializer.pt", landmarker=root / "landmarker.task",
                       wider_parent=root / "prior-wider", wider_deadline="2026-10-07T10:00:00Z",
                       coco_deadline="2026-10-07T14:30:00Z", gaze_deadline="2026-10-07T17:47:54Z",
                       deadline_utc="2026-10-07T18:47:54Z", plan_only=True)
    vars(result).update(updates)
    return result


def cli_plan(monkeypatch, args):
    argv = ["public_budget_pipeline", "--plan-only"]
    for key, value in vars(args).items():
        if key != "plan_only":
            argv += ["--" + key.replace("_", "-"), str(value)]
    monkeypatch.setattr(pipeline.sys, "argv", argv)
    monkeypatch.setattr(pipeline.time, "time", lambda: pipeline.utc_timestamp("2026-10-07T08:47:54Z"))
    pipeline.main()


def test_hardware_selects_measured_throughput_inside_memory_budget():
    selected = pipeline.choose_hardware({
        "safe64": report(speed=190., memory=21.),
        "safe128": report(speed=245., memory=39., batch=128, workers=24),
        "oom-risk": report(speed=1000., memory=39.01, batch=256),
        "failed": report(speed=2000., status="failed"),
        "wider64": report(dataset="wider", speed=800., memory=20., batch=64),
    })
    assert selected["selected_pilot"] == "safe128"
    assert selected["coco_batch"] == 128
    assert selected["coco_workers"] == 24
    assert selected["wider_batch"] == 64
    assert selected["data_fraction"] == 1.0
    assert selected["test_used_for_selection"] is False


def test_failed_wider_pilot_uses_conservative_batch_and_coco_is_required():
    selected = pipeline.choose_hardware({"coco64": report(), "wider64": report(dataset="wider", status="failed")})
    assert selected["wider_batch"] == 32
    with pytest.raises(RuntimeError, match="COCO"):
        pipeline.choose_hardware({"wider64": report(dataset="wider")})


@pytest.mark.parametrize("invalid", [
    {"speed": 0.}, {"speed": -1.}, {"speed": float("nan")}, {"speed": float("inf")},
    {"memory": float("nan")}, {"memory": float("inf")}, {"memory": -1.},
    {"batch": 0}, {"batch": -1}, {"workers": -1},
])
def test_invalid_hardware_measurements_cannot_win(invalid):
    with pytest.raises(RuntimeError, match="COCO"):
        pipeline.choose_hardware({"invalid": report(**invalid)})


def test_malformed_report_does_not_override_valid_pilot():
    selected = pipeline.choose_hardware({"valid": report(), "incomplete": report(speed=500., settings={})})
    assert selected["selected_pilot"] == "valid"


def test_dependency_waits_then_blocks_evaluation_after_failed_train(tmp_path):
    jobs = pipeline.make_plan(tmp_path, arguments(tmp_path))
    evaluation = jobs["evaluate-coco"]
    jobs["train-coco"]["status"] = "running"
    assert pipeline.ready(evaluation, jobs, {}) is False
    assert evaluation["status"] == "pending"
    jobs["train-coco"]["status"] = "failed"
    assert pipeline.ready(evaluation, jobs, {}) is False
    assert evaluation["status"] == "blocked"


def test_independent_coco_can_follow_failed_wider_but_not_failed_hardware(tmp_path):
    jobs = pipeline.make_plan(tmp_path, arguments(tmp_path))
    jobs["select-hardware"]["status"] = "complete"
    jobs["train-wider"]["status"] = "failed"
    assert pipeline.ready(jobs["train-coco"], jobs, {}) is True
    jobs["select-hardware"]["status"] = "failed"
    assert pipeline.ready(jobs["train-coco"], jobs, {}) is False
    assert jobs["train-coco"]["status"] == "blocked"


def test_gaze_never_starts_after_failed_extraction(tmp_path):
    jobs = pipeline.make_plan(tmp_path, arguments(tmp_path))
    jobs["extract-gaze"]["status"] = "timed_out"
    jobs["train-coco"]["status"] = "complete"
    assert pipeline.ready(jobs["train-gaze"], jobs, {}) is False
    assert jobs["train-gaze"]["status"] == "blocked"


def test_one_gpu_and_two_cpu_jobs_are_allowed_to_overlap(tmp_path):
    jobs = pipeline.make_plan(tmp_path, arguments(tmp_path))
    assert pipeline.ready(jobs["pilot-wider64"], jobs, {"pilot-coco64": None}) is False
    assert pipeline.ready(jobs["extract-gaze"], jobs, {"pilot-coco64": None}) is True
    jobs["train-coco"]["status"] = "complete"
    jobs["train-wider"]["status"] = "complete"
    assert pipeline.ready(jobs["evaluate-coco"], jobs, {"extract-gaze": None}) is True
    assert pipeline.ready(jobs["evaluate-coco"], jobs, {"extract-gaze": None, "evaluate-wider": None}) is False


def test_control_waits_for_all_pilots_and_accepts_terminal_failure(tmp_path):
    jobs = pipeline.make_plan(tmp_path, arguments(tmp_path))
    for name in jobs["select-hardware"]["depends"]:
        if name != "pilot-wider64":
            jobs[name]["status"] = "complete"
    jobs["pilot-coco128"]["status"] = "skipped"
    assert pipeline.ready(jobs["select-hardware"], jobs, {}) is False
    jobs["pilot-wider64"]["status"] = "failed"
    assert pipeline.ready(jobs["select-hardware"], jobs, {}) is True


def test_timeplan_preserves_absolute_deadline_and_final_hour(tmp_path, monkeypatch, capsys):
    args = arguments(tmp_path)
    cli_plan(monkeypatch, args)
    jobs = json.loads(capsys.readouterr().out)
    assert pipeline.utc_timestamp(args.deadline_utc) - pipeline.utc_timestamp(args.gaze_deadline) == 3600
    assert jobs["train-gaze"]["hard_deadline"] == args.gaze_deadline
    assert jobs["evaluate-coco"]["hard_deadline"] == args.gaze_deadline
    assert "--train-only" in jobs["train-coco"]["command"]
    assert "--train-only" in jobs["train-wider"]["command"]
    assert "--limit" not in jobs["extract-gaze"]["command"]
    assert all("test" not in job["command"] for job in jobs.values() if job["name"].startswith("pilot-"))


@pytest.mark.parametrize("updates", [
    {"wider_deadline": "2026-10-07T08:55:00Z"},
    {"wider_deadline": "2026-10-07T15:00:00Z"},
    {"coco_deadline": "2026-10-07T19:00:00Z"},
    {"gaze_deadline": "2026-10-07T19:00:00Z"},
    {"deadline_utc": "2026-10-07T18:47:54"},
    {"coco_deadline": "2026-10-07T10:00:00Z"},
    {"gaze_deadline": "2026-10-07T18:47:54Z"},
    {"gaze_deadline": "2026-10-07T18:03:00Z"},
])
def test_invalid_or_unreserved_timeplan_is_rejected(tmp_path, monkeypatch, updates):
    with pytest.raises(ValueError):
        cli_plan(monkeypatch, arguments(tmp_path, **updates))


def test_pid_reuse_with_same_command_is_not_our_process(monkeypatch):
    fake = SimpleNamespace(create_time=lambda: 100.005, cmdline=lambda: ["python", "train.py"])
    monkeypatch.setattr(pipeline.psutil, "Process", lambda _: fake)
    assert pipeline.matching_process({"pid": 101, "create_time": 100., "cmdline": ["python", "train.py"]}) is None


def test_schedule_keeps_exact_minimum_reserve_and_rejects_less():
    pipeline.validate_schedule([1000, 2000, 3000, 5700], 0)
    with pytest.raises(ValueError, match="reserve"):
        pipeline.validate_schedule([1000, 2000, 3000, 5699], 0)
    with pytest.raises(ValueError):
        pipeline.validate_schedule([1000, 2000, 3000, 5700], 400)


def active_guard(tmp_path):
    return {"status": "monitoring", "unload_verified": True, "restore_required": True,
            "updated_utc": "2026-10-07T10:00:00Z", "deadline_utc": "2026-10-07T18:47:54Z",
            "guard": {"pid": 123, "create_time": 10., "cwd": str(tmp_path),
                      "cmdline": ["python", "-m", "training.public_resource_guard"]}}


def test_gpu_admission_requires_current_verified_resource_lease(tmp_path, monkeypatch):
    guard = active_guard(tmp_path)
    monkeypatch.setattr(pipeline, "matching_process", lambda _: object())
    now = pipeline.utc_timestamp("2026-10-07T10:00:30Z")
    deadline = pipeline.utc_timestamp("2026-10-07T18:47:54Z")
    assert pipeline.guard_ready(guard, tmp_path, deadline, now) is True
    monkeypatch.setattr(pipeline, "matching_process", lambda _: None)
    assert pipeline.guard_ready(guard, tmp_path, deadline, now) is False


@pytest.mark.parametrize("change", [
    {"status": "restoring"}, {"status": "restored"},
    {"unload_verified": False}, {"restore_required": False},
    {"updated_utc": "2026-10-07T09:55:00Z"},
    {"updated_utc": "2026-10-07T10:02:00Z"},
    {"updated_utc": "invalid"},
    {"deadline_utc": "2026-10-07T19:47:54Z"},
    {"guard": {}},
])
def test_gpu_rejects_stale_unreleased_or_mismatched_guard(tmp_path, monkeypatch, change):
    guard = active_guard(tmp_path)
    guard.update(change)
    monkeypatch.setattr(pipeline, "matching_process", lambda _: object())
    assert pipeline.guard_ready(guard, tmp_path, pipeline.utc_timestamp("2026-10-07T18:47:54Z"),
                                pipeline.utc_timestamp("2026-10-07T10:00:30Z")) is False


def test_gpu_rejects_guard_for_another_workspace(tmp_path, monkeypatch):
    guard = active_guard(tmp_path / "unrelated")
    monkeypatch.setattr(pipeline, "matching_process", lambda _: object())
    assert pipeline.guard_ready(guard, tmp_path, pipeline.utc_timestamp("2026-10-07T18:47:54Z"),
                                pipeline.utc_timestamp("2026-10-07T10:00:30Z")) is False
