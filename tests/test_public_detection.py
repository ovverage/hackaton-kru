import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from training.public_detection import BatchBenchmark, BenchmarkComplete, INITIAL_SHA256, TimedRun, benchmark_worker_settings, check_resume, dev_members, fit_settings, label_scan_threads, normalized_box, prepare_coco, prepare_wider, read_wider, resumed_budget, run_lock, runtime_budget, select_stage_checkpoint, sha256, stage_initialization, stage_parent_fitness, train_only_pilot_trainer, write_json
from training.public_detection_eval import MIN_AP50, MIN_PRECISION, MIN_RECALL, THRESHOLDS, evaluate, image_gate_failures, load_heldout_receipt, official_coco, operating_audit, operating_counts, recover_export, select_parity_images


def tiny_image(path):
    Image = pytest.importorskip("PIL.Image")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (20, 10)).save(path)


@pytest.mark.parametrize("platform,requested,cap,effective", [
    ("nt", 16, 4, 4), ("nt", 24, 4, 4), ("nt", 16, 0, 16),
    ("nt", 2, 4, 2), ("nt", 0, 4, 0), ("posix", 16, 4, 16),
])
def test_benchmark_windows_worker_cap_preserves_other_platforms_and_explicit_disable(platform, requested, cap, effective):
    result = benchmark_worker_settings(requested, cap, platform)
    assert result["requested_workers"] == requested
    assert result["effective_workers"] == effective
    assert result["windows_worker_cap"] == cap
    assert result["reason"]


@pytest.mark.parametrize("batch,platform,requested,cap,effective", [
    (32, "nt", 16, None, 4), (64, "nt", 16, None, 4),
    (128, "nt", 24, None, 8), (256, "nt", 24, None, 8),
    (128, "nt", 24, 4, 4), (32, "nt", 16, 8, 8),
    (128, "nt", 24, 0, 24), (128, "posix", 24, None, 24),
])
def test_benchmark_automatic_batch_worker_candidates_and_explicit_overrides(batch, platform, requested, cap, effective):
    result = benchmark_worker_settings(requested, cap, platform, batch=batch)
    assert result["effective_workers"] == effective
    assert result["requested_windows_worker_cap"] == cap
    assert result["worker_cap_policy"] == ("automatic_batch_candidate" if cap is None else "explicit")
    assert result["candidate_batch"] == batch
    if cap is None:
        assert result["windows_worker_cap"] == (8 if batch >= 128 else 4)
        assert "Automatic" in result["reason"]


@pytest.mark.parametrize("requested,cap", [(-1, 4), (16, -1), (True, 4), (16, 1.5)])
def test_benchmark_worker_cap_rejects_invalid_scheduler_counts(requested, cap):
    with pytest.raises(ValueError, match="non-negative integers"):
        benchmark_worker_settings(requested, cap, "nt")


def test_train_only_pilot_real_cpu_batches_skip_poisoned_dev_and_never_save(tmp_path, monkeypatch):
    ultralytics = pytest.importorskip("ultralytics")
    if ultralytics.__version__ != "8.3.221":
        pytest.skip("Native pilot contract is pinned to Ultralytics 8.3.221")
    import torch
    from PIL import Image
    from ultralytics.data import utils as data_utils
    from ultralytics.utils import checks
    monkeypatch.setattr(checks, "check_pip_update_available", lambda: False)
    monkeypatch.setattr(checks, "check_font", lambda *args, **kwargs: None)
    monkeypatch.setattr(data_utils, "check_font", lambda *args, **kwargs: None)
    train = tmp_path / "images" / "train"
    labels = tmp_path / "labels" / "train"
    train.mkdir(parents=True)
    labels.mkdir(parents=True)
    for index in range(10):
        Image.new("RGB", (64, 64), (100, index, 50)).save(train / f"{index}.jpg")
        (labels / f"{index}.txt").write_text("0 .5 .5 .3 .3\n")
    dev = tmp_path / "images" / "val"
    dev.mkdir()
    (dev / "poison.jpg").write_bytes(b"not an image: must never be scanned")
    data = tmp_path / "data.yaml"
    data.write_text(f"path: {tmp_path.as_posix()}\ntrain: images/train\nval: images/val\nnames: {{0: object}}\n")
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        model = ultralytics.YOLO("yolo11n.yaml")
        counter = BatchBenchmark(2, 0, 2)
        model.add_callback("on_train_batch_start", counter.start)
        model.add_callback("on_train_batch_end", counter.end)
        with pytest.raises(BenchmarkComplete):
            model.train(trainer=train_only_pilot_trainer(), data=str(data), imgsz=64, batch=2,
                        workers=0, device="cpu", epochs=1, amp=False, pretrained=False,
                        val=False, save=False, plots=False, project=str(tmp_path / "pilot"), name="fit",
                        optimizer="AdamW", verbose=False)
        assert counter.count == 2
        assert model.trainer.test_loader is None
        assert model.trainer.validator.dataloader is None
        assert model.trainer.pilot_skipped_dev_loader
        assert not list((tmp_path / "pilot").rglob("*.pt"))
        assert not (tmp_path / "labels" / "val.cache").exists()
        for forbidden in (model.trainer.validate, model.trainer.final_eval, model.trainer.save_model):
            with pytest.raises(RuntimeError, match="unexpectedly"):
                forbidden()
    finally:
        torch.set_num_threads(previous_threads)


def test_scan_pool_is_bounded_versioned_and_restored_after_failure():
    module = SimpleNamespace(NUM_THREADS=8, DATASET_CACHE_VERSION="1.0.3")
    with pytest.raises(RuntimeError, match="simulated"):
        with label_scan_threads(32, module, "8.3.221") as receipt:
            assert module.NUM_THREADS == 32
            assert receipt["cache_version"] == "1.0.3"
            raise RuntimeError("simulated scan failure")
    assert module.NUM_THREADS == 8
    for count in (0, 49, -1, 3.5, True):
        with pytest.raises(ValueError, match="between 1 and 48"):
            with label_scan_threads(count, module, "8.3.221"):
                pytest.fail("Invalid pool bound accepted")
    for version, cache in (("8.3.222", "1.0.3"), ("8.3.221", "1.0.4")):
        module.DATASET_CACHE_VERSION = cache
        with pytest.raises(ValueError, match="requires Ultralytics"):
            with label_scan_threads(32, module, version):
                pytest.fail("Unvalidated library contract accepted")
    assert module.NUM_THREADS == 8


def test_parallel_native_label_cache_matches_original_verification(tmp_path):
    ultralytics = pytest.importorskip("ultralytics")
    if ultralytics.__version__ != "8.3.221":
        pytest.skip("Native cache contract is pinned to Ultralytics 8.3.221")
    from PIL import Image
    import numpy as np
    from ultralytics.data import dataset
    from ultralytics.utils.metrics import Metric

    metric = Metric()
    metric.mean_results = lambda: [0.91, 0.72, 0.63, 0.48]
    assert metric.fitness() == 0.48  # Exact 8.3.221 selection contract.

    files, labels = [], []
    # Includes a negative image, duplicate, tiny valid box and rejected bad class.
    content = ["", "0 .5 .5 .2 .2\n0 .5 .5 .2 .2\n", "1 .4 .4 .0001 .0001\n", "2 .5 .5 .2 .2\n"]
    for index, value in enumerate(content):
        image, label = tmp_path / f"{index}.jpg", tmp_path / f"{index}.txt"
        Image.new("RGB", (32, 24), (index, 0, 0)).save(image)
        label.write_text(value)
        files.append(str(image))
        labels.append(str(label))
    source = SimpleNamespace(im_files=files[::-1], label_files=labels[::-1], prefix="parity: ",
                             use_keypoints=False, data={"names": {0: "phone", 1: "person"}}, single_cls=False)
    original = dataset.NUM_THREADS
    with label_scan_threads(8):
        baseline = dataset.YOLODataset.cache_labels(source, tmp_path / "baseline.cache")
    with label_scan_threads(32):
        parallel = dataset.YOLODataset.cache_labels(source, tmp_path / "parallel.cache")
    assert dataset.NUM_THREADS == original
    assert {k: v for k, v in baseline.items() if k != "labels"} == {k: v for k, v in parallel.items() if k != "labels"}
    assert baseline["results"] == (4, 0, 1, 1, 4)
    assert [row["im_file"] for row in parallel["labels"]] == files[-2::-1]
    for before, after in zip(baseline["labels"], parallel["labels"], strict=True):
        for key in before:
            if isinstance(before[key], np.ndarray):
                np.testing.assert_array_equal(before[key], after[key])
            else:
                assert before[key] == after[key]


def test_parent_stage_fitness_requires_preserved_matching_dev_metric_and_version():
    checkpoint = {"version": "8.3.221", "best_fitness": 0.48076,
                  "train_metrics": {"fitness": 0.48076, "metrics/mAP50-95(B)": 0.48076}}
    versions = {"ultralytics": "8.3.221"}
    assert stage_parent_fitness(checkpoint, versions, "8.3.221") == 0.48076
    for value in (None, float("nan"), float("inf"), -0.1, 0.5):
        with pytest.raises(ValueError):
            stage_parent_fitness({**checkpoint, "best_fitness": value}, versions, "8.3.221")
    with pytest.raises(ValueError, match="8.3.221"):
        stage_parent_fitness(checkpoint, {"ultralytics": "8.3.220"}, "8.3.221")


@pytest.mark.parametrize("child_fitness,selected", [(0.4, "parent"), (0.48076, "parent"), (0.48077, "child")])
def test_stage_selection_preserves_child_and_never_replaces_parent_with_worse_dev(tmp_path, child_fitness, selected):
    parent, child = tmp_path / "parent.pt", tmp_path / "best.pt"
    parent.write_bytes(b"frozen unstripped parent")
    child.write_bytes(b"new child candidate")
    original_parent_sha = sha256(parent)
    result = select_stage_checkpoint(parent, child, original_parent_sha, 0.48076, child_fitness, tmp_path)
    assert result["selected_source"] == selected
    assert result["status"] == "selected_before_final_evaluation"
    assert (tmp_path / "child-best.pt").read_bytes() == b"new child candidate"
    assert child.read_bytes() == (b"frozen unstripped parent" if selected == "parent" else b"new child candidate")
    assert sha256(parent) == original_parent_sha
    assert result["selected_sha256"] == sha256(child)
    assert json.loads((tmp_path / "stage-selection.json").read_text()) == result


def test_stage_selection_rejects_changed_parent_and_missing_child_metric_before_mutation(tmp_path):
    parent, child = tmp_path / "parent.pt", tmp_path / "best.pt"
    parent.write_bytes(b"parent")
    child.write_bytes(b"child")
    with pytest.raises(ValueError, match="identity changed"):
        select_stage_checkpoint(parent, child, "wrong hash", 0.48, 0.45, tmp_path)
    with pytest.raises(ValueError, match="finite"):
        select_stage_checkpoint(parent, child, sha256(parent), 0.48, None, tmp_path)
    assert child.read_bytes() == b"child"
    assert not (tmp_path / "child-best.pt").exists()


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


def test_timed_budget_reserves_evaluation_and_includes_setup(tmp_path):
    budget = runtime_budget(time_hours=4, deadline_utc="1970-01-01T03:00:00Z", reserve_minutes=60, now=0)
    assert budget["training_cutoff_timestamp"] == 7200
    trainer = SimpleNamespace(args=SimpleNamespace(workers=8, time=None))
    timer = TimedRun(budget, 16, tmp_path)
    timer.clock = lambda: 1800
    timer.before_setup(trainer)
    timer.start(trainer)
    assert trainer.args.workers == 16
    assert trainer.args.time == 1.5
    assert budget["setup_seconds_this_invocation"] == 1800
    assert runtime_budget(now=0) is None


@pytest.mark.parametrize("kwargs", [
    {"time_hours": -1}, {"time_hours": float("inf")},
    {"deadline_utc": "1970-01-01T00:00:00"},
    {"time_hours": 1, "reserve_minutes": -1},
])
def test_timing_rejects_ambiguous_or_invalid_constraints(kwargs):
    with pytest.raises(ValueError):
        runtime_budget(now=0, **kwargs)


def test_setup_exhaustion_stops_before_training_and_resume_cannot_extend_budget(tmp_path):
    previous = runtime_budget(time_hours=1, deadline_utc="1970-01-01T02:00:00Z", now=0)
    newer = runtime_budget(time_hours=5, deadline_utc="1970-01-01T08:00:00Z", now=1200)
    merged = resumed_budget(previous, newer)
    assert merged["training_cutoff_timestamp"] == previous["training_cutoff_timestamp"]
    assert merged["deadline_timestamp"] == previous["deadline_timestamp"]
    assert resumed_budget(previous, None) == previous
    timer = TimedRun(merged, 16, tmp_path)
    timer.clock = lambda: 3601
    with pytest.raises(TimeoutError, match="exhausted"):
        timer.start(SimpleNamespace(args=SimpleNamespace(time=None)))
    with pytest.raises(TimeoutError):
        runtime_budget(deadline_utc="1970-01-01T01:00:00Z", reserve_minutes=60, now=0)


def test_timed_stop_does_not_count_a_partial_epoch_as_complete(tmp_path):
    timer = TimedRun(None, 8, tmp_path)
    trainer = SimpleNamespace(epoch=2, train_loader=range(5))
    timer.epoch_start(trainer)
    timer.batch_start(trainer)
    timer.batch_start(trainer)
    timer.epoch_end(trainer)
    assert timer.epochs[-1] == {"epoch": 3, "training_batches": 2, "expected_batches": 5, "full_epoch": False}
    trainer.epoch = 3
    timer.epoch_start(trainer)
    for _ in range(5):
        timer.batch_start(trainer)
    timer.epoch_end(trainer)
    assert timer.epochs[-1]["full_epoch"] is True


def test_timed_mosaic_closes_once_after_dynamic_horizon_skips_equality(tmp_path):
    calls = []
    trainer = SimpleNamespace(epoch=10, epochs=25, args=SimpleNamespace(close_mosaic=10),
                              train_loader=SimpleNamespace(reset=lambda: calls.append("reset")),
                              _close_dataloader_mosaic=lambda: calls.append("close"))
    budget = runtime_budget(time_hours=1, now=0)
    timer = TimedRun(budget, 8, tmp_path)
    timer.epoch_start(trainer)
    assert calls == []
    # The estimate moves 25 -> 20, while epoch moves 10 -> 11; equality was missed.
    trainer.epoch, trainer.epochs = 11, 20
    timer.epoch_start(trainer)
    assert calls == ["close", "reset"]
    assert trainer.args.close_mosaic == 0
    assert budget["mosaic_closed_epoch"] == 12
    timer.epoch_start(trainer)
    assert len(calls) == 2
    # A fresh loader in resumed training must not re-enable Mosaic.
    resumed = TimedRun(dict(budget), 8, tmp_path)
    trainer.epoch, trainer.epochs = 12, 100
    resumed.epoch_start(trainer)
    assert calls == ["close", "reset", "close", "reset"]
    assert resumed.budget["mosaic_close_restored_on_resume"] is True


def test_untimed_mosaic_remains_under_native_control(tmp_path):
    timer = TimedRun(None, 8, tmp_path)
    timer.epoch_start(SimpleNamespace(epoch=90, epochs=100))
    assert timer.mosaic_closed is False


def test_benchmark_stops_at_batch_boundary_without_an_epoch_and_discards_warmup():
    timer = BatchBenchmark(steps=4, warmup_steps=2, batch_size=8)
    ticks = iter(range(8))
    timer.clock = lambda: next(ticks)
    for _ in range(3):
        timer.start(None)
        timer.end(None)
    timer.start(None)
    with pytest.raises(BenchmarkComplete):
        timer.end(None)
    report = timer.report()
    assert report["completed_batches"] == 4
    assert report["measured_batches"] == 2
    assert report["measured_images"] == 16
    assert report["images_per_second"] == 4


def test_declared_stage_requires_new_run_same_data_parent_unopened_final_test(tmp_path):
    parent = tmp_path / "parent"
    weights = parent / "fit" / "weights" / "best.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"parent dev checkpoint")
    source = {"dataset_manifest_sha256": "dataset-sha", "initialization": {"sha256": INITIAL_SHA256}, "settings": {"batch": 16}}
    write_json(parent / "training.json", source)
    new = tmp_path / "stage2"
    result = stage_initialization(parent, weights, "dataset-sha", new)
    assert result["kind"] == "new_optimizer_continuation_stage"
    assert result["sha256"] == sha256(weights)
    assert result["original_pretraining_sha256"] == INITIAL_SHA256
    assert result["parent_requested_settings"] == {"batch": 16}
    with pytest.raises(ValueError, match="new output"):
        stage_initialization(parent, weights, "dataset-sha", parent)
    with pytest.raises(ValueError, match="switch"):
        stage_initialization(parent, weights, "changed-sha", new)
    with run_lock(parent):
        with pytest.raises(RuntimeError, match="Another process"):
            stage_initialization(parent, weights, "dataset-sha", new)
    (parent / "final-evaluation.json").write_text("{}")
    with pytest.raises(ValueError, match="final test"):
        stage_initialization(parent, weights, "dataset-sha", new)


def test_default_fit_settings_keep_untimed_protocol_and_cache_is_explicit():
    kwargs = dict(epochs=80, batch=16, workers=8, device="0", seed=20261007, patience=20)
    old = fit_settings(Path("data.yaml"), Path("run"), cache="none", **kwargs)
    assert old["cache"] is False and "time" not in old
    assert old["lr0"] == .001 and old["imgsz"] == 640 and old["mosaic"] == .75
    cached = fit_settings(Path("data.yaml"), Path("run"), cache="disk", **kwargs)
    assert cached == {**old, "cache": "disk"}
