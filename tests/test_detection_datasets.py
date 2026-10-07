import json
from pathlib import Path

import pytest

pytest.importorskip("requests", reason="Dataset preparation uses optional dataset dependencies")

from training.datasets import coco2017
from training.datasets.coco2017 import resolve_metadata, verify_coco
from training.datasets.widerface import read_bbox_annotations, verify_wider


def test_wider_zero_face_placeholder_and_invalid_boxes(tmp_path: Path):
    annotation = tmp_path / "annotations.txt"
    annotation.write_text(
        "event/a.jpg\n0\n0 0 0 0 0 0 0 0 0 0\n"
        "event/b.jpg\n1\n1 2 3 4 0 0 0 1 0 0\n"
        "event/c.jpg\n0\n",
        encoding="utf-8",
    )
    result = read_bbox_annotations(annotation)
    assert result["images"] == {"event/a.jpg": 0, "event/b.jpg": 1, "event/c.jpg": 0}
    assert result["bbox_count"] == 1
    assert result["zero_face_images"] == 2
    assert result["invalid_flagged_boxes_retained"] == 1


@pytest.mark.parametrize("content", ["../bad.jpg\n0\n", "event/a.jpg\n1\n", "event/a.jpg\n0\nevent/a.jpg\n0\n"])
def test_wider_rejects_malformed_annotations(tmp_path: Path, content: str):
    annotation = tmp_path / "annotations.txt"
    annotation.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        read_bbox_annotations(annotation)


def test_wider_verifies_test_filelist_without_inventing_boxes(tmp_path: Path):
    images = tmp_path / "WIDER_test" / "images" / "event"
    images.mkdir(parents=True)
    (images / "a.jpg").touch()
    split = tmp_path / "wider_face_split"
    split.mkdir()
    (split / "wider_face_test_filelist.txt").write_text("event/a.jpg\n", encoding="utf-8")
    result = verify_wider(tmp_path, {"test": 1})
    assert result["splits"]["test"]["bbox_public"] is False
    (split / "wider_face_test_filelist.txt").write_text("event/missing.jpg\n", encoding="utf-8")
    with pytest.raises(ValueError, match="file list"):
        verify_wider(tmp_path, {"test": 1})


def make_tiny_coco(root: Path):
    (root / "train2017").mkdir()
    (root / "train2017" / "a.jpg").touch()
    (root / "train2017" / "b.jpg").touch()
    annotations = root / "annotations"
    annotations.mkdir()
    images = [{"id": 3, "file_name": "a.jpg"}, {"id": 4, "file_name": "b.jpg"}]
    for kind in ("instances", "captions", "person_keypoints"):
        data = {"images": images, "annotations": [{"id": 10, "image_id": 3, "category_id": 17}]}
        if kind != "captions":
            data["categories"] = [{"id": 17, "name": "person"}]
        (annotations / f"{kind}_train2017.json").write_text(json.dumps(data), encoding="utf-8")


def test_coco_person_category_comes_from_json_and_keeps_negatives(tmp_path: Path):
    make_tiny_coco(tmp_path)
    result = verify_coco(tmp_path, {"train2017": 2})
    person = result["splits"]["train2017"]["person"]
    assert person["category_id"] == 17
    assert person["images_with_person"] == 1
    assert person["images_without_annotated_person"] == 1


def test_coco_rejects_dangling_annotation_reference(tmp_path: Path):
    make_tiny_coco(tmp_path)
    path = tmp_path / "annotations" / "captions_train2017.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["annotations"][0]["image_id"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid image references"):
        verify_coco(tmp_path, {"train2017": 2})


def test_coco_official_fallback_has_pinned_checksums_when_hf_is_down(tmp_path: Path, monkeypatch):
    def unavailable(*args):
        raise ConnectionError("HF unavailable")

    monkeypatch.setattr(coco2017, "fetch_hf", unavailable)
    metadata, source, warnings = resolve_metadata(tmp_path)
    assert source == "captured_pinned_lfs_metadata"
    assert set(metadata) == set(coco2017.FILES)
    assert all(item["revision"] == coco2017.REVISION for item in metadata.values())
    assert metadata["train2017.zip"]["size"] == 19336861798
    assert warnings[0]["error"] == "HF unavailable"
    assert (tmp_path / "captured-pinned-lfs-metadata.json").is_file()


def test_coco_reuses_valid_pinned_metadata_without_network(tmp_path: Path, monkeypatch):
    siblings = [
        {"rfilename": name, "lfs": {"size": size, "sha256": digest}}
        for name, (size, digest) in coco2017.PINNED_ARCHIVES.items()
    ]
    (tmp_path / "huggingface-metadata.json").write_text(json.dumps({"response": {
        "sha": coco2017.REVISION, "id": coco2017.REPOSITORY, "siblings": siblings,
    }}), encoding="utf-8")

    def unexpected_network(*args):
        pytest.fail("A pinned metadata cache should not require network access")

    monkeypatch.setattr(coco2017, "fetch_hf", unexpected_network)
    metadata, source, warnings = resolve_metadata(tmp_path)
    assert source == "saved_pinned_hf_metadata"
    assert not warnings
    assert set(metadata) == set(coco2017.FILES)


def test_coco_incomplete_download_never_claims_retained_data(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(coco2017, "resolve_metadata", lambda _: (
        {name: {"url": "https://unavailable.test/" + name, "size": 10, "sha256": "a" * 64, "revision": coco2017.REVISION}
         for name in coco2017.FILES}, "fixture", [],
    ))

    def unavailable(*args, **kwargs):
        raise ConnectionError("Unavailable fixture")

    monkeypatch.setattr(coco2017, "download", unavailable)
    result = coco2017.prepare(tmp_path)
    assert result["status"] == "incomplete"
    notes = json.loads((tmp_path / "coco2017" / "sources" / "usage-notes.json").read_text(encoding="utf-8"))
    assert notes["all_original_images_and_annotations_retained"] is False


def test_coco_preparation_uses_official_hosts_when_hf_is_unavailable(tmp_path: Path, monkeypatch):
    official_calls = []

    def hf_unavailable(*args):
        raise ConnectionError("HF unavailable")

    def fake_download(url, destination, **kwargs):
        if "huggingface.co" in url:
            raise ConnectionError("HF unavailable")
        if url.startswith("http://images.cocodataset.org/"):
            filename = url.rsplit("/", 1)[-1]
            size, digest = coco2017.PINNED_ARCHIVES[filename]
            assert kwargs["expected_sha256"] == digest
            assert kwargs["expected_size"] == size
            assert destination.name == "official-" + filename
            official_calls.append(filename)
        return {"url": url, "path": str(destination), "status": "verified"}

    monkeypatch.setattr(coco2017, "fetch_hf", hf_unavailable)
    monkeypatch.setattr(coco2017, "download", fake_download)
    monkeypatch.setattr(coco2017, "extract_zip_verified", lambda *_: {"status": "verified", "crc_verified": True})
    monkeypatch.setattr(coco2017, "verify_coco", lambda *_: {"status": "verified"})
    result = coco2017.prepare(tmp_path)
    assert result["status"] == "ready"
    assert set(official_calls) == set(coco2017.FILES)
    assert result["errors"] == []
    assert result["warnings"]
