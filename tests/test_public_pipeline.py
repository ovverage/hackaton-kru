import json
from pathlib import Path

from training.public_pipeline import plan, runnable


def test_training_is_gated_on_verified_data_and_one_gpu_slot(tmp_path):
    jobs = {j["name"]: dict(j, status="pending") for j in plan(tmp_path, Path("init.pt"), Path("mesh.task"))}
    assert not runnable(jobs["prepare-wider"], jobs, tmp_path, {})
    status = tmp_path/"data/datasets/widerface/status.json"
    status.parent.mkdir(parents=True)
    status.write_text(json.dumps({"status": "ready"}))
    assert runnable(jobs["prepare-wider"], jobs, tmp_path, {})
    assert not runnable(jobs["train-wider"], jobs, tmp_path, {})
    jobs["prepare-wider"]["status"] = "complete"
    assert runnable(jobs["train-wider"], jobs, tmp_path, {})
    assert not runnable(jobs["train-wider"], jobs, tmp_path, {"train-coco": object()})
    jobs["prepare-wider"]["status"] = "failed"
    assert not runnable(jobs["train-wider"], jobs, tmp_path, {})


def test_queue_uses_both_gaze_datasets_and_preserves_long_training(tmp_path):
    jobs = {j["name"]: j for j in plan(tmp_path, Path("init.pt"), Path("mesh.task"))}
    assert set(jobs["index-gaze"]["datasets"]) == {"mpiifacegaze", "gaze360"}
    assert jobs["train-gaze"]["depends"] == ["extract-gaze"]
    for name, epochs in (("train-coco", "80"), ("train-wider", "100"), ("train-gaze", "40")):
        command = jobs[name]["command"]
        assert command[command.index("--epochs")+1] == epochs
        assert "--allow-smoke" not in command
