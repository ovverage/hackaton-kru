"""Normal-stage parent freezing never reads held-out metrics or needs Torch."""
import json

import pytest

from training.public_detection import INITIAL_SHA256, check_resume, freeze_stage_parent, run_lock, select_stage_checkpoint, sha256, stage_initialization, write_json


def selected_stage(tmp_path, child_fitness=.4):
    parent, stage = tmp_path/"parent", tmp_path/"stage"
    for run in (parent, stage):
        (run/"fit/weights").mkdir(parents=True)
    weights, candidate = parent/"fit/weights/best.pt", stage/"fit/weights/best.pt"
    weights.write_bytes(b"original parent")
    candidate.write_bytes(b"child candidate")
    common = {"dataset": "wider", "dataset_manifest_sha256": "same-prepared-manifest",
              "versions": {"ultralytics": "8.3.221", "torch": "2.6.0+cu124", "python": "3.11.9"},
              "settings": {"batch": 16}}
    write_json(parent/"training.json", {**common, "initialization": {"sha256": INITIAL_SHA256}})
    initial = {"kind": "new_optimizer_continuation_stage", "parent_run": str(parent),
               "parent_training_receipt_sha256": sha256(parent/"training.json"),
               "original_pretraining_sha256": INITIAL_SHA256, "sha256": sha256(weights),
               "parent_dev_fitness": .48076}
    write_json(stage/"training.json", {**common, "initialization": initial, "status": "trained_pending_final_evaluation"})
    select_stage_checkpoint(weights, candidate, sha256(weights), .48076, child_fitness, stage)
    return parent, stage


@pytest.mark.parametrize("child_fitness", [.4, .48076, .5])
def test_normal_stage_freezes_parent_while_child_evaluator_holds_lock(tmp_path, child_fitness):
    parent, stage = selected_stage(tmp_path, child_fitness)
    history = {run: sha256(run/"training.json") for run in (parent, stage)}
    # Invalid JSON intentionally proves final-test receipt content is not read.
    (stage/"final-evaluation.json").write_text("currently evaluating; do not read")
    with run_lock(stage):
        marker = freeze_stage_parent(parent, stage)
    marker_path = parent/"final-selection-marker.json"
    marker_bytes = marker_path.read_bytes()
    assert marker["selection_sha256"] == sha256(stage/"stage-selection.json")
    assert marker["selected_sha256"] == sha256(stage/"fit/weights/best.pt")
    assert freeze_stage_parent(parent, stage) == marker
    assert marker_path.read_bytes() == marker_bytes
    for run in (parent, stage):
        assert sha256(run/"training.json") == history[run]
    with pytest.raises(ValueError, match="selection.*frozen"):
        check_resume({}, {}, parent/"fit/weights/last.pt", parent)
    with pytest.raises(ValueError, match="selection.*frozen"):
        stage_initialization(parent, parent/"fit/weights/best.pt", "unused", tmp_path/"new-stage")


def test_normal_freeze_requires_parent_lock_and_never_overwrites_another_marker(tmp_path):
    parent, stage = selected_stage(tmp_path)
    with run_lock(parent), pytest.raises(RuntimeError, match="Another process"):
        freeze_stage_parent(parent, stage)
    write_json(parent/"final-selection-marker.json", {"selection": "another frozen output"})
    with pytest.raises(FileExistsError, match="another frozen"):
        freeze_stage_parent(parent, stage)


@pytest.mark.parametrize("change", ["parent_weights", "child_weights", "selected_weights", "parent_history", "dataset", "version", "wrong_winner"])
def test_normal_freeze_rejects_changed_source_provenance_and_selection(tmp_path, change):
    parent, stage = selected_stage(tmp_path)
    binary = {"parent_weights": parent/"fit/weights/best.pt", "child_weights": stage/"fit/weights/child-best.pt",
              "selected_weights": stage/"fit/weights/best.pt"}
    if change in binary:
        binary[change].write_bytes(b"changed checkpoint")
    elif change == "wrong_winner":
        path = stage/"stage-selection.json"
        value = json.loads(path.read_text())
        value["selected_source"] = "child"
        write_json(path, value)
    else:
        path = (parent if change == "parent_history" else stage)/"training.json"
        value = json.loads(path.read_text())
        if change == "dataset":
            value["dataset_manifest_sha256"] = "other"
        elif change == "version":
            value["versions"]["ultralytics"] = "8.3.222"
        else:
            value["edited"] = True
        write_json(path, value)
    with pytest.raises(ValueError):
        freeze_stage_parent(parent, stage)
    assert not (parent/"final-selection-marker.json").exists()
