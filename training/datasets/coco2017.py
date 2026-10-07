"""Fetch and verify the complete, original COCO 2017 train/val dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath

from .common import download, extract_zip_verified, fetch_hf, log, process_lock, write_json


REPOSITORY = "pcuenq/coco-2017-mirror"
REVISION = "a4cd8b69bd45a35e9a0a9c8692f3a7f4f23321fe"
FILES = ("annotations_trainval2017.zip", "val2017.zip", "train2017.zip")
EXPECTED_COUNTS = {"train2017": 118287, "val2017": 5000}
# Observed from this exact pinned revision's public LFS API on 2026-10-07.
# These let the official-host fallback remain verifiable when HF is unavailable.
PINNED_ARCHIVES = {
    "annotations_trainval2017.zip": (252907541, "113a836d90195ee1f884e704da6304dfaaecff1f023f49b6ca93c4aaae470268"),
    "val2017.zip": (815585330, "4f7e2ccb2866ec5041993c9cf2a952bbed69647b115d0f74da7ce8f4bef82f05"),
    "train2017.zip": (19336861798, "69a8bb58ea5f8f99d24875f21416de2e9ded3178e903f1f7603e283b9e06d929"),
}
OFFICIAL_URLS = {
    name: f"http://images.cocodataset.org/{'annotations' if name.startswith('annotations') else 'zips'}/{name}"
    for name in FILES
}


def resolve_metadata(sources: Path) -> tuple[dict, str, list[dict]]:
    """Use the pinned metadata cache, then HF, then captured pinned LFS hashes."""
    warnings = []
    cached = sources / "huggingface-metadata.json"
    if cached.exists():
        try:
            payload = json.loads(cached.read_text(encoding="utf-8"))["response"]
            if payload["sha"] != REVISION or payload["id"] != REPOSITORY:
                raise ValueError("Cached metadata belongs to a different repository or revision")
            entries = {item["rfilename"]: item for item in payload["siblings"]}
            metadata = {}
            for filename, (size, digest) in PINNED_ARCHIVES.items():
                entry = entries[filename]["lfs"]
                if entry["size"] != size or entry["sha256"] != digest:
                    raise ValueError(f"Cached LFS metadata disagrees with pinned values for {filename}")
                metadata[filename] = {
                    "url": f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{REVISION}/{filename}",
                    "size": size, "sha256": digest, "revision": REVISION,
                }
            return metadata, "saved_pinned_hf_metadata", warnings
        except (ValueError, KeyError, TypeError) as error:
            warnings.append({"stage": "cached_hf_metadata", "error": str(error)})
    try:
        return fetch_hf(REPOSITORY, REVISION, list(FILES), sources), "live_pinned_hf_metadata", warnings
    except Exception as error:
        warnings.append({"stage": "pinned_hf_metadata", "error": str(error), "fallback": "captured_pinned_lfs_metadata"})
        metadata = {
            filename: {
                "url": f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{REVISION}/{filename}",
                "size": size, "sha256": digest, "revision": REVISION,
            }
            for filename, (size, digest) in PINNED_ARCHIVES.items()
        }
        write_json(sources / "captured-pinned-lfs-metadata.json", {
            "repository": REPOSITORY,
            "revision": REVISION,
            "observed_on": "2026-10-07",
            "source_url": f"https://huggingface.co/api/datasets/{REPOSITORY}/revision/{REVISION}?blobs=true",
            "files": metadata,
            "note": "Pinned LFS metadata captured during implementation; the HF API was unavailable on this run.",
        })
        return metadata, "captured_pinned_lfs_metadata", warnings


def verify_coco(raw: Path, expected_counts: dict[str, int] | None = None) -> dict:
    """Validate all six original annotation JSONs against all extracted images.

    Person IDs come from categories, never from an assumed YOLO class index.
    All images are kept, including the images with no annotated person.
    """
    counts = EXPECTED_COUNTS if expected_counts is None else expected_counts
    results = {}
    for split, expected_count in counts.items():
        image_dir = raw / split
        actual_names = {path.name for path in image_dir.glob("*.jpg")}
        if len(actual_names) != expected_count:
            raise ValueError(f"{split}: expected {expected_count} JPEGs; found {len(actual_names)}")
        checks = {}
        person_result = {}
        for kind in ("instances", "captions", "person_keypoints"):
            annotation_file = raw / "annotations" / f"{kind}_{split}.json"
            with annotation_file.open(encoding="utf-8") as source:
                data = json.load(source)
            images = data["images"]
            image_ids = {item["id"] for item in images}
            image_names = {item["file_name"] for item in images}
            if len(image_ids) != len(images) or len(image_names) != len(images):
                raise ValueError(f"{annotation_file.name}: duplicate image IDs or filenames")
            if any(PurePosixPath(name).name != name for name in image_names):
                raise ValueError(f"{annotation_file.name}: unexpected non-flat image filename")
            if image_names != actual_names:
                raise ValueError(
                    f"{annotation_file.name}: images differ: missing={len(image_names - actual_names)}, "
                    f"unreferenced={len(actual_names - image_names)}"
                )
            annotations = data["annotations"]
            annotation_ids = {item["id"] for item in annotations}
            if len(annotation_ids) != len(annotations):
                raise ValueError(f"{annotation_file.name}: duplicate annotation IDs")
            invalid_refs = sum(item["image_id"] not in image_ids for item in annotations)
            if invalid_refs:
                raise ValueError(f"{annotation_file.name}: {invalid_refs} invalid image references")
            categories = data.get("categories")
            if categories is not None:
                category_ids = {item["id"] for item in categories}
                if len(category_ids) != len(categories):
                    raise ValueError(f"{annotation_file.name}: duplicate category IDs")
                if any(item["category_id"] not in category_ids for item in annotations):
                    raise ValueError(f"{annotation_file.name}: unknown annotation category IDs")
            if kind == "instances":
                person_ids = [item["id"] for item in categories if item["name"] == "person"]
                if len(person_ids) != 1:
                    raise ValueError(f"{annotation_file.name}: expected exactly one person category")
                person_id = person_ids[0]
                person_annotations = [item for item in annotations if item["category_id"] == person_id]
                positive_ids = {item["image_id"] for item in person_annotations}
                person_result = {
                    "category_id": person_id,
                    "category_id_source": "categories[name=person].id",
                    "images_with_person": len(positive_ids),
                    "images_without_annotated_person": len(image_ids - positive_ids),
                    "person_annotations": len(person_annotations),
                    "negative_images_retained": True,
                }
                del person_annotations
            checks[kind] = {
                "path": str(annotation_file.resolve()),
                "images": len(images),
                "annotations": len(annotations),
                "missing_images": 0,
                "invalid_image_references": 0,
            }
            del data, annotations, images, categories
        results[split] = {"images": len(actual_names), "annotations": checks, "person": person_result}
        log(f"COCO {split}: all {len(actual_names):,} images and three annotation JSONs verified")
    return {"status": "verified", "splits": results, "full_annotations_retained": True}


def prepare(root: Path, range_workers: int = 0) -> dict:
    base = root.resolve() / "coco2017"
    archives, raw, sources = (base / name for name in ("archives", "raw", "sources"))
    for directory in (archives, raw, sources):
        directory.mkdir(parents=True, exist_ok=True)
    status = {
        "name": "COCO 2017",
        "status": "preparing",
        "source": {"repository": REPOSITORY, "revision": REVISION, "type": "community_mirror"},
        "extracted_path": str(raw),
        "archives": {},
        "extraction": {},
        "sources": [],
        "errors": [],
        "warnings": [],
        "license": {
            "annotations": "CC BY 4.0",
            "images": "Individual Flickr image licenses; consult the licenses/image license fields in original JSONs.",
            "terms_url": "https://cocodataset.org/#termsofuse",
            "intended_use": "research prototype",
        },
    }

    def save() -> None:
        write_json(base / "status.json", status)

    def download_archive(url: str, archive: Path, item: dict) -> dict:
        if range_workers:
            from .ranges import download_ranges

            return download_ranges(
                url, archive, expected_sha256=item["sha256"],
                expected_size=item["size"], workers=range_workers,
            )
        return download(url, archive, expected_sha256=item["sha256"], expected_size=item["size"])

    save()
    source_urls = {
        "terms-of-use.html": "https://raw.githubusercontent.com/cocodataset/cocodataset.github.io/master/dataset/termsofuse.htm",
        "official-downloads.html": "https://raw.githubusercontent.com/cocodataset/cocodataset.github.io/master/dataset/download.htm",
        "mirror-README.md": f"https://huggingface.co/datasets/{REPOSITORY}/resolve/{REVISION}/README.md",
        "CC-BY-4.0-legalcode.txt": "https://creativecommons.org/licenses/by/4.0/legalcode.txt",
    }
    for filename, url in source_urls.items():
        try:
            status["sources"].append(download(url, sources / filename))
        except Exception as error:
            bucket = "warnings" if filename == "mirror-README.md" else "errors"
            status[bucket].append({"stage": "source", "file": filename, "error": str(error)})
        save()
    metadata, metadata_source, metadata_warnings = resolve_metadata(sources)
    status["source"]["checksum_metadata_source"] = metadata_source
    status["warnings"].extend(metadata_warnings)
    status["status"] = "downloading"
    save()
    for filename in FILES:
        item = metadata[filename]
        archive = archives / filename
        status["current_file"] = filename
        save()
        try:
            try:
                record = download_archive(item["url"], archive, item)
            except Exception as mirror_error:
                # Separate paths ensure bytes from different URLs are never silently mixed.
                fallback = archives / ("official-" + filename)
                record = download_archive(OFFICIAL_URLS[filename], fallback, item)
                record["mirror_error"] = str(mirror_error)
                record["fallback_of"] = item["url"]
                archive = fallback
            record["hf_revision"] = item["revision"]
            record["expected_lfs_sha256"] = item["sha256"]
            status["archives"][filename] = record
            status["status"] = "extracting"
            save()
            status["extraction"][filename] = extract_zip_verified(archive, raw)
            status["status"] = "downloading"
        except Exception as error:
            status["errors"].append({"stage": "archive", "file": filename, "error": str(error)})
        save()
    status.pop("current_file", None)
    status["status"] = "verifying"
    save()
    try:
        status["verification"] = verify_coco(raw)
    except Exception as error:
        status["errors"].append({"stage": "verification", "error": str(error)})
    verified = status.get("verification", {}).get("status") == "verified"
    retained = verified and len(status["archives"]) == len(FILES) and len(status["extraction"]) == len(FILES)
    status["status"] = "ready" if retained and not status["errors"] else "incomplete"
    write_json(sources / "usage-notes.json", {
        **status["license"],
        "all_original_images_and_annotations_retained": retained,
        "person_category_mapping": "Read categories[name=person].id; category_id is not a YOLO class index.",
        "negative_examples": "Images without person annotations remain in both original splits.",
        "source_evidence": source_urls,
    })
    save()
    return status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/datasets"))
    parser.add_argument("--range-workers", type=int, choices=range(9), default=0,
                        help="Parallel verified HTTP Range connections per archive; 0 uses one resumable stream")
    args = parser.parse_args()
    with process_lock(args.root / "coco2017" / ".prepare.lock"):
        status = prepare(args.root, range_workers=args.range_workers)
    log(f"COCO 2017: {status['status']}")
    raise SystemExit(0 if status["status"] == "ready" else 1)


if __name__ == "__main__":
    main()
