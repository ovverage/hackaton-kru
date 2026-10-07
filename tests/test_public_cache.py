import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from training.public_detection import sha256

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
cache = pytest.importorskip("training.public_cache")


def prepared(tmp_path, train=5, val=2):
    root = tmp_path / "prepared"
    root.mkdir()
    splits = {}
    paths = {}
    for split, count in (("train", train), ("val", val), ("test", 1)):
        paths[split] = []
        for index in range(count):
            image = root / "images" / split / f"изображение-{index}.jpg"
            image.parent.mkdir(parents=True, exist_ok=True)
            # Distinct B/R channels expose an accidental RGB conversion.
            Image.new("RGB", (37 + index, 19 + index), (240, index * 15, 12)).save(image)
            paths[split].append(image)
        listing = root / f"{split}.txt"
        listing.write_text("\n".join(str(p) for p in paths[split]) + "\n", encoding="utf-8")
        splits[split] = {"path": str(listing), "images": count, "sha256": sha256(listing)}
    data = root / "data.yaml"
    data.write_text("path: prepared\n", encoding="utf-8")
    manifest = {"dataset": "wider", "sources": {}, "splits": splits, "data_yaml_sha256": sha256(data)}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return data, manifest, paths


@pytest.fixture
def ample_disk(monkeypatch):
    monkeypatch.setattr(cache.shutil, "disk_usage", lambda _: SimpleNamespace(free=1000 * cache.GIB))


def item(path):
    return cache.Item("train", path, path.name)


def test_pilot_is_deterministic_train_first_without_touching_heldout(tmp_path, ample_disk, monkeypatch):
    data, manifest, paths = prepared(tmp_path)
    snapshots = {split: Path(row["path"]).read_bytes() for split, row in manifest["splits"].items()}
    # A final-test image must never even be decoded/read for dimensions.
    paths["test"][0].write_bytes(b"not an image")
    original_read = Path.read_bytes

    def no_test_reads(path):
        assert path != paths["test"][0]
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", no_test_reads)
    all_items, selected = cache.selected_items(data, manifest, limit=3)
    reversed_manifest = dict(manifest)
    for split in ("train", "val"):
        listing = Path(manifest["splits"][split]["path"])
        listing.write_text("\n".join(str(p) for p in reversed(paths[split])) + "\n", encoding="utf-8")
        reversed_manifest["splits"][split]["sha256"] = sha256(listing)
    assert selected == cache.selected_items(data, reversed_manifest, limit=3)[1]
    for split, content in snapshots.items():
        Path(manifest["splits"][split]["path"]).write_bytes(content)
    # Restore manifest hashes altered through the shallow-copy lists above.
    manifest = json.loads((data.parent / "manifest.json").read_text())
    result = cache.prepare_cache(data, tmp_path / "pilot.json", workers=3, limit=3)
    assert result["status"] == "completed"
    assert result["counts"] == {"created": 3}
    assert result["selected_by_split"] == {"train": 3}
    assert result["estimated_full_train_dev_bytes"] > result["estimated_selected_bytes"]
    assert len(all_items) == 7
    assert not any(p.with_suffix(".npy").exists() for p in paths["val"] + paths["test"])
    assert {split: Path(row["path"]).read_bytes() for split, row in manifest["splits"].items()} == snapshots
    receipts = [json.loads(line) for line in Path(result["receipts"]).read_text(encoding="utf-8").splitlines()]
    assert len(receipts) == 3
    assert {row["source"] for row in receipts} == {str(i.path) for i in selected}
    assert result["cache_bytes"] == sum(row["cache_bytes"] for row in receipts)
    assert "accuracy" not in json.dumps(result)


def test_original_bgr_array_and_source_identity_reuse(tmp_path, ample_disk):
    _, _, paths = prepared(tmp_path, train=1, val=0)
    source = paths["train"][0]
    budget = cache.DiskBudget(source.parent)
    first = cache.cache_one(item(source), budget)
    actual = np.load(first["cache"], allow_pickle=False)
    expected = cv2.imdecode(np.frombuffer(source.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    assert actual.shape == (19, 37, 3)
    assert np.array_equal(actual, expected)
    assert actual[0, 0, 2] > actual[0, 0, 0]
    stamp = Path(first["cache"]).stat().st_mtime_ns
    second = cache.cache_one(item(source), budget)
    assert second["status"] == "reused"
    assert Path(first["cache"]).stat().st_mtime_ns == stamp
    metadata = json.loads(Path(first["sidecar"]).read_text())
    assert metadata["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert metadata["cache_sha256"] == sha256(Path(first["cache"]))
    assert metadata["dtype"] == "uint8"
    assert not list(source.parent.glob("*.tmp*"))


@pytest.mark.parametrize("bad", [b"broken npy", "wrong_shape", "wrong_dtype", "wrong_pixels"])
def test_corrupt_or_stale_cache_preserved_and_replaced(tmp_path, ample_disk, bad):
    _, _, paths = prepared(tmp_path, train=1, val=0)
    source = paths["train"][0]
    target = source.with_suffix(".npy")
    if isinstance(bad, bytes):
        target.write_bytes(bad)
    else:
        shape = (10, 10, 3) if bad == "wrong_shape" else (19, 37, 3)
        np.save(target, np.zeros(shape, dtype=np.float32 if bad == "wrong_dtype" else np.uint8))
    original = target.read_bytes()
    result = cache.cache_one(item(source), cache.DiskBudget(source.parent))
    assert result["status"] == "created"
    assert len(result["quarantined"]) == 1
    saved = Path(result["quarantined"][0])
    assert saved.parent == target.parent
    assert saved.read_bytes() == original
    assert np.load(target).shape == (19, 37, 3)


def test_existing_exact_array_is_adopted_without_rewriting(tmp_path, ample_disk):
    _, _, paths = prepared(tmp_path, train=1, val=0)
    source = paths["train"][0]
    target = source.with_suffix(".npy")
    np.save(target, cv2.imdecode(np.fromfile(source, np.uint8), cv2.IMREAD_COLOR))
    before = target.stat().st_mtime_ns
    result = cache.cache_one(item(source), cache.DiskBudget(source.parent))
    assert result["status"] == "adopted"
    assert result["quarantined"] == []
    assert target.stat().st_mtime_ns == before


def test_changed_source_invalidates_previous_provenance(tmp_path, ample_disk):
    _, _, paths = prepared(tmp_path, train=1, val=0)
    source = paths["train"][0]
    budget = cache.DiskBudget(source.parent)
    first = cache.cache_one(item(source), budget)
    Image.new("RGB", (37, 19), (10, 200, 40)).save(source)
    second = cache.cache_one(item(source), budget)
    assert second["source_sha256"] != first["source_sha256"]
    assert second["status"] == "created"
    assert len(second["quarantined"]) == 2


def test_low_space_fails_before_mutating_any_cache_and_journals(tmp_path, monkeypatch):
    data, _, paths = prepared(tmp_path)
    target = paths["train"][0].with_suffix(".npy")
    target.write_bytes(b"preserve")
    monkeypatch.setattr(cache.shutil, "disk_usage", lambda _: SimpleNamespace(free=cache.MIN_RESERVE + 100))
    output = tmp_path / "low-space.json"
    with pytest.raises(OSError, match="Insufficient disk space"):
        cache.prepare_cache(data, output, workers=2, limit=3)
    assert target.read_bytes() == b"preserve"
    assert len(list(data.parent.rglob("*.npy"))) == 1
    assert json.loads(output.read_text())["status"] == "failed"


def test_disk_reservations_include_concurrent_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(cache.shutil, "disk_usage", lambda _: SimpleNamespace(free=cache.MIN_RESERVE + 100))
    budget = cache.DiskBudget(tmp_path)
    with budget.writing(60):
        with pytest.raises(OSError, match="Insufficient disk space"):
            with budget.writing(41):
                pytest.fail("Reserve breached")
        with budget.writing(40):
            assert budget.inflight == 100
    assert budget.inflight == 0


def test_tampered_split_is_rejected_before_image_writes(tmp_path, ample_disk):
    data, manifest, _ = prepared(tmp_path)
    Path(manifest["splits"]["train"]["path"]).write_text("tampered\n")
    with pytest.raises(ValueError, match="split list changed"):
        cache.prepare_cache(data, tmp_path / "result.json", workers=1)
    assert not list(data.parent.rglob("*.npy"))


@pytest.mark.parametrize("reason", ["test", "duplicate", "collision"])
def test_even_hashed_list_cannot_introduce_heldout_duplicate_or_colliding_cache(tmp_path, reason):
    data, manifest, paths = prepared(tmp_path, train=1, val=0)
    source = paths["train"][0]
    if reason == "test":
        sources = [paths["test"][0]]
    elif reason == "duplicate":
        sources = [source, source]
    else:
        alternate = source.with_suffix(".png")
        Image.new("RGB", (10, 10)).save(alternate)
        sources = [source, alternate]
    row = manifest["splits"]["train"]
    listing = Path(row["path"])
    listing.write_text("\n".join(str(p) for p in sources), encoding="utf-8")
    row.update(sha256=sha256(listing), images=len(sources))
    with pytest.raises(ValueError, match="outside|Duplicate"):
        cache.selected_items(data, manifest)


def test_atomic_publish_does_not_overwrite_concurrent_file(tmp_path, monkeypatch):
    target = tmp_path / "image.npy"
    original_publish = cache._publish_new

    def race(temporary, destination):
        destination.write_bytes(b"concurrent user file")
        original_publish(temporary, destination)

    monkeypatch.setattr(cache, "_publish_new", race)
    with pytest.raises(FileExistsError):
        cache._save_array(target, np.zeros((3, 4, 3), dtype=np.uint8))
    assert target.read_bytes() == b"concurrent user file"
    assert list(tmp_path.iterdir()) == [target]


def test_coordinator_drains_inflight_receipts_on_failure(tmp_path, ample_disk, monkeypatch):
    data, _, _ = prepared(tmp_path, train=12, val=0)
    completed = []

    def failing(item, _):
        if not completed:
            completed.append(item.key)
            raise OSError("hardware write failure")
        completed.append(item.key)
        return {"source": str(item.path), "status": "created", "cache_bytes": 10}

    monkeypatch.setattr(cache, "cache_one", failing)
    output = tmp_path / "failed.json"
    with pytest.raises(RuntimeError, match="hardware write failure"):
        cache.prepare_cache(data, output, workers=1)
    summary = json.loads(output.read_text())
    receipts = [json.loads(line) for line in Path(summary["receipts"]).read_text().splitlines()]
    assert summary["status"] == "failed"
    assert summary["counts"]["failed"] == 1
    assert len(receipts) <= 2
    assert len([row for row in receipts if row["status"] != "cancelled"]) == len(completed)


def test_journal_will_not_overwrite_unrelated_file_and_cannot_lower_floor(tmp_path, ample_disk):
    data, _, _ = prepared(tmp_path)
    output = tmp_path / "user.json"
    output.write_text('{"keep":true}')
    with pytest.raises(ValueError, match="unrelated journal"):
        cache.prepare_cache(data, output)
    assert output.read_text() == '{"keep":true}'
    with pytest.raises(ValueError, match="50 GiB"):
        cache.prepare_cache(data, tmp_path / "never-created.json", reserve_bytes=49 * cache.GIB)


def test_coco_estimate_uses_verified_training_metadata_without_opening_images(tmp_path, monkeypatch):
    data, manifest, _ = prepared(tmp_path)
    all_items, _ = cache.selected_items(data, manifest)
    source = tmp_path / "train-annotations.json"
    source.write_text(json.dumps({"images": [{"file_name": i.path.name, "width": 900, "height": 800} for i in all_items]}))
    manifest.update(dataset="coco", sources={"train2017": {"path": str(source), "sha256": sha256(source)}})

    def forbidden(_):
        pytest.fail("COCO estimate should use annotation dimensions")

    monkeypatch.setattr(cache.Image, "open", forbidden)
    estimates = cache.estimate_bytes(all_items, manifest, workers=2)
    assert set(estimates.values()) == {900 * 800 * 3 + cache.OVERHEAD}
    source.write_text("{}")
    with pytest.raises(ValueError, match="COCO source changed"):
        cache.estimate_bytes(all_items, manifest, workers=2)


def test_disk_drop_does_not_quarantine_existing_cache(tmp_path, monkeypatch):
    _, _, paths = prepared(tmp_path, train=1, val=0)
    source = paths["train"][0]
    target = source.with_suffix(".npy")
    target.write_bytes(b"preserved despite low disk")
    monkeypatch.setattr(cache.shutil, "disk_usage", lambda _: SimpleNamespace(free=cache.MIN_RESERVE))
    with pytest.raises(OSError, match="Insufficient disk space"):
        cache.cache_one(item(source), cache.DiskBudget(source.parent))
    assert target.read_bytes() == b"preserved despite low disk"
    assert not list(target.parent.glob("*.qorgau-quarantine-*"))


def test_dataset_lock_is_nonblocking_and_released(tmp_path):
    lock = tmp_path / ".public-cache.lock"
    with cache.process_lock(lock):
        with pytest.raises(RuntimeError, match="Another cache process"):
            with cache.process_lock(lock):
                pytest.fail("Concurrent cache process entered")
    with cache.process_lock(lock):
        assert lock.exists()


def test_cache_matches_pinned_ultralytics_reader(tmp_path, ample_disk):
    patches = pytest.importorskip("ultralytics.utils.patches")
    _, _, paths = prepared(tmp_path, train=1, val=0)
    source = paths["train"][0]
    result = cache.cache_one(item(source), cache.DiskBudget(source.parent))
    assert np.array_equal(np.load(result["cache"]), patches.imread(str(source)))
