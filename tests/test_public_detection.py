import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from training.public_detection import check_resume, dev_members, normalized_box, prepare_coco, prepare_wider, read_wider, run_lock, sha256, write_json
from training.public_detection_eval import MIN_AP50, MIN_PRECISION, MIN_RECALL, THRESHOLDS, evaluate, image_gate_failures, load_heldout_receipt, official_coco, operating_audit, operating_counts, recover_export, select_parity_images


def tiny_image(path):
    Image = pytest.importorskip("PIL.Image")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 10)).save(path)


def test_split_hash_is_order_independent_and_group_exclusive():
    groups = ["subject-a", "subject-a", "subject-b", "subject-c", "subject-d"]
    assert dev_members(groups, 0.25, 4) == dev_members(groups[::-1], 0.25, 4)
    assert len(dev_members(groups, 0.25, 4)) == 1
    assert dev_members(["only"], 0.1, 1) == set()
    with pytest.raises(ValueError):
        dev_members(groups, 0.6, 1)


def test_boxes_clip_and_do_not_remove_tiny_faces():
    assert normalized_box([-2, -3, 4, 5], 10, 10) == (0.1, 0.1, 0.2, 0.2)
    assert normalized_box([1, 2, 0.01, 0.01], 1000, 1000) is not None
    assert normalized_box([11, 1, 1, 1], 10, 10) is None
    assert normalized_box([0, 0, float("nan"), 1], 10, 10) is None


def test_coco_all_images_negatives_mapping_crowd_and_final_isolation(tmp_path):
    raw = tmp_path / "datasets" / "coco2017" / "raw"
    (raw / "annotations").mkdir(parents=True)
    for split, start, total in [("train2017", 1, 20), ("val2017", 101, 2)]:
        images = [{"id": i, "file_name": f"{i:012}.jpg", "width": 20, "height": 10} for i in range(start, start + total)]
        for image in images:
            tiny_image(raw / split / image["file_name"])
        annotations = [{"image_id": i, "category_id": 77, "bbox": [1, 1, 3, 4], "iscrowd": int(i == start)} for i in range(start, start + total, 2)]
        annotations += [{"image_id": start, "category_id": 1, "bbox": [0, 0, 10, 10], "iscrowd": 0}]
        source = {"images": images, "annotations": annotations, "categories": [{"id": 1, "name": "person"}, {"id": 77, "name": "cell phone"}]}
        (raw / "annotations" / f"instances_{split}.json").write_text(json.dumps(source))
    result = prepare_coco(tmp_path / "datasets", tmp_path / "prepared", dev_fraction=0.2)
    splits = result["splits"]
    assert splits["train"]["images"] + splits["val"]["images"] == 20
    assert splits["test"]["images"] == 2
    assert sum(splits[k]["negative_images"] for k in ("train", "val")) == 10
    assert sum(splits[k]["crowd_boxes_weak_supervision"] for k in ("train", "val")) == 1
    labels = list((tmp_path / "prepared" / "labels").rglob("000000000001.txt"))
    assert {line.split()[0] for line in labels[0].read_text().splitlines()} == {"0", "1"}
    assert result["source_category_to_model"] == {77: 0, 1: 1}
    for split, data in splits.items():
        paths = Path(data["path"]).read_text().splitlines()
        assert all((int(Path(path).stem) >= 101) == (split == "test") for path in paths)


def test_wider_event_groups_invalid_images_and_all_final_images(tmp_path):
    raw = tmp_path / "datasets" / "widerface" / "raw"
    (raw / "wider_face_split").mkdir(parents=True)
    for split, start in [("train", 1), ("val", 100)]:
        lines = []
        for i in range(start, start + 8):
            name = f"event-{i // 2}/face-{i}.jpg"
            tiny_image(raw / f"WIDER_{split}" / "images" / name)
            invalid = int(i == start + 1)
            lines += [name, "1", f"1 1 1 1 0 0 0 {invalid} 0 0"]
        (raw / "wider_face_split" / f"wider_face_{split}_bbx_gt.txt").write_text("\n".join(lines))
    result = prepare_wider(tmp_path / "datasets", tmp_path / "prepared", dev_fraction=0.25)
    assert result["splits"]["train"]["images"] + result["splits"]["val"]["images"] == 7
    assert result["splits"]["test"]["images"] == 8
    assert result["splits"]["test"]["boxes"] == 7
    train = {Path(path).parent.name for path in Path(result["splits"]["train"]["path"]).read_text().splitlines()}
    dev = {Path(path).parent.name for path in Path(result["splits"]["val"]["path"]).read_text().splitlines()}
    assert not train & dev
    assert result["exclusions"]["test_invalid_boxes"] == 1


def test_wider_zero_placeholder_and_following_entry(tmp_path):
    source = tmp_path / "labels.txt"
    source.write_text("event/a.jpg\n0\n0 0 0 0 0 0 0 0 0 0\nevent/b.jpg\n1\n0 0 1 1 0 0 0 0 0 0\n")
    assert list(read_wider(source)) == [("event/a.jpg", []), ("event/b.jpg", [[0, 0, 1, 1, 0, 0, 0, 0, 0, 0]])]


def test_operating_match_duplicates_ignore_crowd_and_inclusive_threshold():
    predictions = [{"bbox": box, "score": score} for box, score in [([0, 0, 2, 2], .8), ([0, 0, 2, 2], .9), ([20, 20, 2, 2], .8), ([50, 50, 2, 2], .79)]]
    counts = operating_counts(predictions, [[0, 0, 2, 2]], [[19, 19, 5, 5]], .8)
    assert counts["tp"] == 1
    assert counts["fp"] == 1
    assert counts["ignored_predictions"] == 1
    assert counts["fn"] == 0


def test_operating_audit_counts_zero_prediction_images(tmp_path):
    annotation = tmp_path / "source.json"
    annotation.write_text(json.dumps({"images": [{"id": 1}, {"id": 2}], "annotations": [{"image_id": 1, "category_id": 77, "bbox": [0, 0, 2, 2], "iscrowd": 0}]}))
    manifest = {"dataset": "coco", "sources": {"val2017": {"path": str(annotation)}}, "source_category_to_model": {77: 0, 1: 1}}
    result = operating_audit(manifest, [])
    assert result["cell phone"]["fn"] == 1
    assert result["cell phone"]["images"] == 2
    assert result["cell phone"]["negative_images"] == 1
    assert result["person"]["negative_images"] == 2


def test_official_coco_remaps_phone_and_person_without_scoring_crowds_as_individuals(tmp_path):
    pytest.importorskip("pycocotools.coco")
    annotation = tmp_path / "source.json"
    annotation.write_text(json.dumps({
        "images": [{"id": 1, "width": 100, "height": 100}],
        "categories": [{"id": 77, "name": "cell phone"}, {"id": 1, "name": "person"}],
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": 77, "bbox": [0, 0, 2, 2], "area": 4, "iscrowd": 0},
            {"id": 2, "image_id": 1, "category_id": 1, "bbox": [20, 20, 20, 50], "area": 1000, "iscrowd": 0},
            {"id": 3, "image_id": 1, "category_id": 1, "bbox": [60, 20, 40, 50], "area": 2000, "iscrowd": 1},
        ],
    }))
    manifest = {"sources": {"val2017": {"path": str(annotation)}}, "source_category_to_model": {77: 0, 1: 1}}
    predictions = [
        {"image_id": 1, "category_id": 1, "bbox": [0, 0, 2, 2], "score": .9},
        {"image_id": 1, "category_id": 2, "bbox": [20, 20, 20, 50], "score": .9},
        {"image_id": 1, "category_id": 2, "bbox": [65, 20, 10, 40], "score": .8},
    ]
    report = official_coco(manifest, predictions, tmp_path)
    assert report["metrics"]["cell phone"]["AP50"] > .999
    assert report["metrics"]["person"]["AP50"] > .999
    remapped = json.loads((tmp_path / "coco-original-category-predictions.json").read_text())
    assert [row["category_id"] for row in remapped] == [77, 1, 1]


def test_run_lock_refuses_concurrent_run_and_releases(tmp_path):
    with run_lock(tmp_path):
        with pytest.raises(RuntimeError, match="Another process"):
            with run_lock(tmp_path):
                pass
    with run_lock(tmp_path):
        pass


def test_resume_cannot_change_statistical_settings_or_switch_checkpoint(tmp_path):
    settings = {"seed": 1, "batch": 16, "epochs": 80, "workers": 8, "device": "0"}
    original = {"dataset": "coco", "dataset_manifest_sha256": "hash", "versions": {"torch": "2.6.0"},
                "initialization": {"sha256": "initial"}, "settings": settings}
    checkpoint = tmp_path / "fit" / "weights" / "last.pt"
    updated = {**original, "settings": {**settings, "workers": 2}}
    check_resume(original, updated, checkpoint, tmp_path)
    for key, value in [("batch", 8), ("seed", 2), ("epochs", 100)]:
        updated = {**original, "settings": {**settings, key: value}}
        with pytest.raises(ValueError, match="changed training setting"):
            check_resume(original, updated, checkpoint, tmp_path)
    with pytest.raises(ValueError, match="this run"):
        check_resume(original, original, tmp_path / "another.pt", tmp_path)
    (tmp_path / "final-evaluation.json").write_text("{}")
    with pytest.raises(ValueError, match="Final evaluation"):
        check_resume(original, original, checkpoint, tmp_path)


def saved_heldout_fixture(tmp_path):
    data = tmp_path / "prepared" / "data.yaml"
    data.parent.mkdir()
    data.write_text("names: {0: face}\n")
    source = data.parent / "original.txt"
    source.write_text("verified original annotations")
    images = []
    for index in range(4):
        image = data.parent / f"image-{index}.jpg"
        image.write_bytes(f"immutable image {index}".encode())
        images.append(str(image))
    split = data.parent / "test.txt"
    split.write_text("\n".join(images) + "\n")
    manifest = {"dataset": "wider", "data_yaml_sha256": sha256(data), "limitations": [],
                "sources": {"val": {"path": str(source), "sha256": sha256(source)}},
                "splits": {"test": {"path": str(split), "sha256": sha256(split), "images": 4}}}
    write_json(data.parent / "manifest.json", manifest)
    checkpoint = tmp_path / "best.pt"
    checkpoint.write_bytes(b"unchanged checkpoint")
    output = tmp_path / "run"
    predictions = output / "final_test" / "predictions.json"
    write_json(predictions, [{"saved": "unchanged predictions"}])
    report = {"status": "heldout_metrics_complete", "dataset": "wider", "checkpoint_sha256": sha256(checkpoint),
              "dataset_manifest_sha256": sha256(data.parent / "manifest.json"), "prediction_sha256": sha256(predictions),
              "predeclared_ap50_floors": MIN_AP50, "predeclared_precision_floors": MIN_PRECISION,
              "predeclared_recall_floors": MIN_RECALL, "fixed_operating_thresholds": THRESHOLDS,
              "parity_selection": select_parity_images(manifest, 4), "validation_images": 4,
              "diagnostic_per_class": {"face": {"AP50": .7}},
              "operating_point_audit": {"face": {"precision": .9, "recall": .6, "confidence": .55, "images": 4}}}
    write_json(output / "heldout-metrics.json", report)
    write_json(output / "final-evaluation.json", {**report, "heldout_metrics_sha256": sha256(output / "heldout-metrics.json")})
    return data, checkpoint, output, manifest, report


def test_export_failure_recovers_without_revalidating_or_changing_metrics(tmp_path, monkeypatch):
    data, checkpoint, output, _, report = saved_heldout_fixture(tmp_path)
    unchanged = (output / "heldout-metrics.json").read_bytes()
    calls = {"export": 0, "parity": 0}

    class FakeYolo:
        names = {0: "face"}

        def __init__(self, path):
            assert path == str(checkpoint)

        def val(self, **kwargs):
            pytest.fail("Export recovery must never rerun held-out inference")

        def export(self, **kwargs):
            calls["export"] += 1
            if calls["export"] == 1:
                raise RuntimeError("Recoverable missing export dependency")
            exported = tmp_path / "best.onnx"
            exported.write_bytes(b"exported graph")
            return exported

    def fake_parity(checkpoint_arg, target, manifest_arg, samples, selection=None):
        calls["parity"] += 1
        assert selection == report["parity_selection"]
        assert samples == 4
        return {"status": "passed", "images": 4}

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYolo))
    monkeypatch.setattr("training.public_detection_eval.cpu_parity", fake_parity)
    with pytest.raises(RuntimeError, match="Recoverable"):
        recover_export(data, checkpoint, output)
    assert json.loads((output / "final-evaluation.json").read_text())["status"] == "failed"
    recovered = recover_export(data, checkpoint, output)
    assert recovered["status"] == "passed_image_model_gates"
    assert (output / "heldout-metrics.json").read_bytes() == unchanged
    assert calls == {"export": 2, "parity": 1}


@pytest.mark.parametrize("change", ["checkpoint", "predictions", "image", "metrics", "data"])
def test_export_recovery_rejects_changed_evidence(tmp_path, change):
    data, checkpoint, output, _, report = saved_heldout_fixture(tmp_path)
    target = {"checkpoint": checkpoint, "predictions": output / "final_test" / "predictions.json",
              "image": Path(report["parity_selection"][0]["path"]),
              "metrics": output / "heldout-metrics.json", "data": data}[change]
    target.write_text("{}")
    with pytest.raises(ValueError):
        load_heldout_receipt(data, checkpoint, output)


def test_model_gates_reject_nan_missing_operating_metrics_and_coverage(tmp_path):
    _, _, _, manifest, report = saved_heldout_fixture(tmp_path)
    assert image_gate_failures(manifest, report) == []
    report["diagnostic_per_class"]["face"]["AP50"] = float("nan")
    assert image_gate_failures(manifest, report)
    report["diagnostic_per_class"]["face"]["AP50"] = .7
    report["operating_point_audit"] = {}
    assert image_gate_failures(manifest, report)
    report["validation_images"] = 3
    assert "Final validation image coverage is incomplete" in image_gate_failures(manifest, report)


def test_missing_prediction_artifact_never_claims_completed_metrics_or_pass(tmp_path, monkeypatch):
    data, checkpoint, output, _, _ = saved_heldout_fixture(tmp_path)
    for path in [output / "heldout-metrics.json", output / "final-evaluation.json", output / "final_test" / "predictions.json"]:
        path.unlink()
    (output / "final_test").rmdir()

    class FakeYolo:
        names = {0: "face"}

        def __init__(self, path):
            self.callback = None

        def add_callback(self, event, callback):
            assert event == "on_val_end"
            self.callback = callback

        def val(self, **kwargs):
            self.callback(SimpleNamespace(seen=4))
            return SimpleNamespace()

        def export(self, **kwargs):
            pytest.fail("Missing evaluation evidence must prevent export")

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYolo))
    with pytest.raises(FileNotFoundError, match="did not save predictions"):
        evaluate(data, checkpoint, output, parity_images=4)
    report = json.loads((output / "final-evaluation.json").read_text())
    assert report["status"] == "failed"
    assert "export" not in report
    assert not (output / "heldout-metrics.json").exists()
