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
        "wider64": report(dataset="wider", speed=800., memory=20., batch=64, workers=4),
    })
    assert selected["selected_pilot"] == "safe128"
    assert selected["coco_batch"] == 128
    assert selected["coco_workers"] == 24
    assert selected["wider_batch"] == 64
    assert selected["wider_workers"] == 4
    assert selected["data_fraction"] == 1.0
    assert selected["test_used_for_selection"] is False


def test_failed_wider_pilot_uses_conservative_batch_and_coco_is_required():
    selected = pipeline.choose_hardware({"coco64": report(), "wider64": report(dataset="wider", status="failed")})
    assert selected["wider_batch"] == 32
    assert selected["wider_workers"] == 4
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


def reusable_fixture(tmp_path, monkeypatch):
    from training import public_detection as detection
    args = arguments(tmp_path)
    args.weights.write_bytes(b"fixture official initializer")
    monkeypatch.setattr(detection, "INITIAL_SHA256", detection.sha256(args.weights))
    jobs = pipeline.make_plan(tmp_path, args)
    job = jobs["pilot-coco64"]
    data = tmp_path / "data/public-detection/coco/data.yaml"
    data.parent.mkdir(parents=True)
    data.write_text("fixture data yaml")
    detection.write_json(data.parent/"manifest.json", {"dataset": "coco", "data_yaml_sha256": detection.sha256(data), "sources": {}, "splits": {}})
    output = tmp_path / "training/runs/budget-hardware-v3/coco64"
    output.mkdir(parents=True)
    workers = detection.benchmark_worker_settings(16, 4, batch=64)["effective_workers"]
    settings = detection.fit_settings(data, output, epochs=1, batch=64, workers=workers, device="0", seed=20261007, patience=20, cache="none")
    settings.update(val=False, save=False, plots=False, save_period=-1)
    versions = {"torch": "2.6.0+cu124", "ultralytics": "8.3.221"}
    value = report(settings=settings, speed=64., memory=8., versions=versions,
                   dataset_manifest_sha256=detection.sha256(data.parent/"manifest.json"),
                   initialization={"path": str(args.weights.resolve()), "sha256": detection.INITIAL_SHA256},
                   completed_batches=48, warmup_batches=12, measured_batches=36, measured_images=2304,
                   measured_seconds=36., peak_allocated_gib=4., wall_seconds_including_setup=40.,
                   mean_synchronized_batch_seconds=.8, mean_loader_and_logging_gap_seconds=.2)
    detection.write_json(output/"benchmark.json", value)
    return args, jobs, job, value, versions


def test_reuse_completed_pilot_binds_exact_evidence_and_only_new_w8_stays_pending(tmp_path, monkeypatch):
    args, jobs, job, _, versions = reusable_fixture(tmp_path, monkeypatch)
    pipeline.reuse_completed_pilots(jobs, args.weights, versions)
    assert job["status"] == "complete"
    assert job["reused_completed_pilot"]["report_sha256"]
    assert jobs["pilot-coco128-w8"]["status"] == "pending"
    command = jobs["pilot-coco128-w8"]["command"]
    assert command[command.index("--windows-worker-cap")+1] == "8"
    assert command[command.index("--workers")+1] == "8"
    assert "pilot-coco128-w8" in jobs["select-hardware"]["depends"]


@pytest.mark.parametrize("change", [
    {"dataset": "wider"}, {"dataset_manifest_sha256": "changed"},
    {"versions": {"torch": "2.6.0+cu124", "ultralytics": "8.3.222"}},
    {"initialization": {"path": "other.pt", "sha256": "other"}},
    {"settings": {}}, {"completed_batches": 47}, {"warmup_batches": 0},
    {"measured_images": 2303}, {"images_per_second": float("nan")},
    {"peak_reserved_gib": float("inf")}, {"peak_reserved_gib": 40.},
    {"images_per_second": 65.},
])
def test_reuse_rejects_changed_dataset_versions_initializer_protocol_and_measurements(tmp_path, monkeypatch, change):
    args, _, job, value, versions = reusable_fixture(tmp_path, monkeypatch)
    from training.public_detection import write_json
    value.update(change)
    write_json(pipeline.Path(job["report"]), value)
    with pytest.raises(ValueError):
        pipeline.validate_reusable_pilot(job, args.weights, versions)


@pytest.mark.parametrize("value", [[], {"status": "running"}, {"status": "failed"}])
def test_reuse_never_accepts_running_failed_or_malformed_report(tmp_path, monkeypatch, value):
    args, _, job, _, versions = reusable_fixture(tmp_path, monkeypatch)
    from training.public_detection import write_json
    write_json(pipeline.Path(job["report"]), value)
    with pytest.raises(FileExistsError, match="operator"):
        pipeline.validate_reusable_pilot(job, args.weights, versions)


def test_legacy_reuse_requires_native_initializer_binding(tmp_path, monkeypatch):
    args, _, job, value, versions = reusable_fixture(tmp_path, monkeypatch)
    from training.public_detection import write_json
    value.pop("initialization")
    path = pipeline.Path(job["report"])
    write_json(path, value)
    native = path.parent/"fit/args.yaml"
    native.parent.mkdir()
    native.write_text("model: " + args.weights.as_posix())
    assert "legacy" in pipeline.validate_reusable_pilot(job, args.weights, versions)["initializer_binding"]
    native.write_text("model: other.pt")
    with pytest.raises(ValueError, match="initializer"):
        pipeline.validate_reusable_pilot(job, args.weights, versions)


@pytest.mark.parametrize("memory,allowed", [(20., True), (20.01, False), (0., False), (float("nan"), False), (float("inf"), False)])
def test_both_128_candidates_require_finite_batch64_headroom(memory, allowed):
    assert pipeline.batch128_has_headroom(report(memory=memory)) is allowed
