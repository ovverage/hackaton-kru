"""Fetch and verify original WIDER FACE images and official bbox annotations."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath

from .common import download, extract_zip_verified, fetch_hf, log, process_lock, write_json


REPOSITORY = "wider_face"
REVISION = "db171f1b7fedf4d3453e81297ff02f9915356d19"
EXPECTED_COUNTS = {"train": 12880, "val": 3226, "test": 16097}
IMAGE_FILES = ("data/WIDER_train.zip", "data/WIDER_val.zip", "data/WIDER_test.zip")
ANNOTATIONS = "data/wider_face_split.zip"
OFFICIAL_ANNOTATIONS = "http://shuoyang1213.me/WIDERFACE/support/bbx_annotation/wider_face_split.zip"


def read_bbox_annotations(path: Path) -> dict:
    """Parse all official text entries, including optional zero-face dummy rows."""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cursor = 0
    images = {}
    box_count = 0
    zero_face_images = 0
    invalid_boxes = 0
    while cursor < len(lines):
        image_name = lines[cursor]
        image_path = PurePosixPath(image_name)
        if image_path.is_absolute() or ".." in image_path.parts or image_path.suffix.lower() != ".jpg":
            raise ValueError(f"{path.name}:{cursor + 1}: invalid image path {image_name!r}")
        if image_name in images:
            raise ValueError(f"{path.name}: duplicate image {image_name}")
        cursor += 1
        if cursor >= len(lines):
            raise ValueError(f"{path.name}: missing face count for {image_name}")
        number = int(lines[cursor])
        cursor += 1
        if number < 0:
            raise ValueError(f"{path.name}: negative face count for {image_name}")
        if cursor + number > len(lines):
            raise ValueError(f"{path.name}: truncated boxes for {image_name}")
        for row in lines[cursor:cursor + number]:
            values = [int(value) for value in row.split()]
            if len(values) != 10:
                raise ValueError(f"{path.name}: expected ten bbox attributes for {image_name}")
            invalid_boxes += bool(values[7])
        cursor += number
        if number == 0:
            zero_face_images += 1
            # The published text format may include a ten-zero placeholder.
            if cursor < len(lines) and not lines[cursor].lower().endswith(".jpg"):
                dummy = lines[cursor].split()
                if len(dummy) != 10 or any(int(value) != 0 for value in dummy):
                    raise ValueError(f"{path.name}: invalid zero-face placeholder for {image_name}")
                cursor += 1
        images[image_name] = number
        box_count += number
    return {
        "images": images,
        "bbox_count": box_count,
        "zero_face_images": zero_face_images,
        "invalid_flagged_boxes_retained": invalid_boxes,
    }


def verify_wider(raw: Path, expected_counts: dict[str, int] | None = None) -> dict:
    counts = EXPECTED_COUNTS if expected_counts is None else expected_counts
    results = {}
    split_dir = raw / "wider_face_split"
    for split, expected in counts.items():
        image_dir = raw / f"WIDER_{split}" / "images"
        actual = {path.relative_to(image_dir).as_posix() for path in image_dir.rglob("*.jpg")}
        if len(actual) != expected:
            raise ValueError(f"WIDER {split}: expected {expected} JPEGs; found {len(actual)}")
        result = {"images": len(actual)}
        if split in ("train", "val"):
            annotation_file = split_dir / f"wider_face_{split}_bbx_gt.txt"
            annotation = read_bbox_annotations(annotation_file)
            annotated = set(annotation.pop("images"))
            if annotated != actual:
                raise ValueError(
                    f"WIDER {split}: annotation images differ: missing={len(annotated - actual)}, "
                    f"unannotated={len(actual - annotated)}"
                )
            result.update(annotation)
            result.update({"annotation_path": str(annotation_file), "missing_images": 0, "bbox_public": True})
        else:
            annotation_file = split_dir / "wider_face_test_filelist.txt"
            filelist = [line.strip() for line in annotation_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(set(filelist)) != len(filelist) or set(filelist) != actual:
                raise ValueError("WIDER test: file list and extracted JPEGs differ")
            result.update({"filelist_path": str(annotation_file), "missing_images": 0, "bbox_public": False})
        results[split] = result
        log(f"WIDER FACE {split}: all {len(actual):,} images and official references verified")
    return {"status": "verified", "splits": results, "original_splits_retained": True}


def prepare(root: Path) -> dict:
    base = root.resolve() / "widerface"
    archives, raw, sources = (base / name for name in ("archives", "raw", "sources"))
    for directory in (archives, raw, sources):
        directory.mkdir(parents=True, exist_ok=True)
    status = {
        "name": "WIDER FACE",
        "status": "preparing",
        "source": {"repository": REPOSITORY, "requested_revision": "main", "type": "officially_linked_hf_mirror"},
        "extracted_path": str(raw),
        "archives": {},
        "extraction": {},
        "sources": [],
        "errors": [],
        "limitations": ["Test bounding boxes are not publicly released; use train/val for labeled training and evaluation."],
        "license": {
            "official_site": "CC BY-NC-ND (official page does not state a version)",
            "hf_dataset_card": "CC BY-NC-ND 4.0",
            "restrictions": "Attribution, noncommercial use, no distribution of adaptations; retain original dataset and source notices.",
            "intended_use": "research prototype",
        },
    }

    def save() -> None:
        write_json(base / "status.json", status)

    save()
    try:
        metadata = fetch_hf(REPOSITORY, REVISION, [*IMAGE_FILES, ANNOTATIONS], sources)
        revision = metadata[IMAGE_FILES[0]]["revision"]
        status["source"]["revision"] = revision
    except Exception as error:
        status["status"] = "blocked"
        status["errors"].append({"stage": "hf_metadata", "error": str(error)})
        save()
        return status
    source_urls = {
        "official-description-and-license.html": "http://shuoyang1213.me/WIDERFACE/",
        "mirror-README.md": f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{revision}/README.md",
        "CC-BY-NC-ND-4.0-legalcode.txt": "https://creativecommons.org/licenses/by-nc-nd/4.0/legalcode.txt",
    }
    for filename, url in source_urls.items():
        try:
            status["sources"].append(download(url, sources / filename))
        except Exception as error:
            status["errors"].append({"stage": "source", "file": filename, "error": str(error)})
        save()
    status["status"] = "downloading"
    save()
    for filename in (ANNOTATIONS, *IMAGE_FILES):
        item = metadata[filename]
        basename = PurePosixPath(filename).name
        archive = archives / basename
        url = OFFICIAL_ANNOTATIONS if filename == ANNOTATIONS else item["url"]
        status["current_file"] = basename
        save()
        try:
            try:
                record = download(url, archive, expected_sha256=item["sha256"], expected_size=item["size"])
            except Exception as official_error:
                if filename != ANNOTATIONS:
                    raise
                archive = archives / ("hf-" + basename)
                record = download(item["url"], archive, expected_sha256=item["sha256"], expected_size=item["size"])
                record["official_source_error"] = str(official_error)
            record["hf_revision"] = revision
            record["expected_lfs_sha256"] = item["sha256"]
            status["archives"][basename] = record
            status["status"] = "extracting"
            save()
            status["extraction"][basename] = extract_zip_verified(archive, raw)
            status["status"] = "downloading"
        except Exception as error:
            status["errors"].append({"stage": "archive", "file": filename, "error": str(error)})
        save()
    status.pop("current_file", None)
    status["status"] = "verifying"
    save()
    try:
        status["verification"] = verify_wider(raw)
    except Exception as error:
        status["errors"].append({"stage": "verification", "error": str(error)})
    status["status"] = "ready" if not status["errors"] else "incomplete"
    write_json(sources / "usage-notes.json", {
        **status["license"],
        "test_bbox": "Not public. Test image file list is verified; no bbox labels are fabricated.",
        "source_evidence": source_urls,
        "annotation_source": OFFICIAL_ANNOTATIONS,
    })
    save()
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/datasets"))
    args = parser.parse_args()
    with process_lock(args.root / "widerface" / ".prepare.lock"):
        status = prepare(args.root)
    log(f"WIDER FACE: {status['status']}")
    raise SystemExit(0 if status["status"] == "ready" else 1)


if __name__ == "__main__":
    main()
