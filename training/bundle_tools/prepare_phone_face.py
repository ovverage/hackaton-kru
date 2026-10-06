"""Export reviewed object boxes from both sources, with explicit split and group checks."""

import argparse
import collections
import json
import shutil
from pathlib import Path

from validate_package import ROOT, boxes_from_text, read_json, safe_path, sha256


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--review", type=Path, default=ROOT / "annotations/phone_face_review.json"
    )
    p.add_argument("--output", type=Path, default=ROOT / "prepared/phone_face")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    review = read_json(args.review)
    if review["names"] != ["cell_phone", "face"]:
        p.error("Expected class 0 cell_phone and class 1 face")
    items = [r for r in review["items"] if r["reviewed"] is True]
    if not items:
        p.error(
            "No reviewed annotations; empty proposals are not negative ground truth"
        )
    native = {
        r["sample_id"]: r
        for r in read_json(ROOT / "metadata/public_manifest.json")["files"]
    }
    personal = {
        Path(r["dataset_relative_path"]).stem: r
        for r in read_json(ROOT / "file_manifest.json")["files"]
        if r["media_type"] == "photo"
    }
    groups, hashes, ids = {}, {}, set()
    summary = {
        s: {
            "images": 0,
            "sources": collections.Counter(),
            "boxes": collections.Counter(),
        }
        for s in ("train", "val", "test")
    }
    for item in items:
        sid, source, split, group = (
            item["sample_id"],
            item["source"],
            item["split"],
            item["group_id"],
        )
        if sid in ids or not sid.replace("_", "").isalnum():
            p.error(f"Duplicate or unsafe sample ID: {sid}")
        ids.add(sid)
        if split not in summary or not group:
            p.error(
                f"{sid}: reviewed items require a split and a recording/series group"
            )
        truth = (native if source == "public" else personal).get(sid)
        if source not in ("public", "personal") or truth is None:
            p.error(f"{sid}: unknown source")
        if source == "public" and (
            truth["status"] != "ready"
            or truth["split"] != split
            or truth["group_id"] != group
        ):
            p.error(
                f"{sid}: cannot change the public holdout split or include quarantined/unlabeled data"
            )
        if (
            truth["dataset_relative_path"] != item["image"]
            or truth["sha256"] != item["sha256"]
        ):
            p.error(f"{sid}: review source identity differs from the manifest")
        path = safe_path(ROOT, item["image"])
        if sha256(path) != item["sha256"]:
            p.error(f"{sid}: image changed")
        group_key = source + ":" + str(group)
        for key, mapping in ((group_key, groups), (item["sha256"], hashes)):
            if key in mapping and mapping[key] != split:
                p.error(f"{sid}: recording group or duplicate crosses splits")
            mapping[key] = split
        text = "\n".join(" ".join(str(v) for v in b) for b in item["boxes"])
        try:
            boxes = boxes_from_text(text, 2)
        except ValueError as e:
            p.error(f"{sid}: {e}")
        summary[split]["images"] += 1
        summary[split]["sources"][source] += 1
        summary[split]["boxes"].update(int(b[0]) for b in boxes)
    if set(summary["train"]["sources"]) != {"personal", "public"}:
        p.error(
            "Combined training requires reviewed personal and public samples in train"
        )
    if any(s["images"] == 0 for s in summary.values()):
        p.error("Reviewed train, val and test sets must all be nonempty")
    if not all(summary["train"]["boxes"][c] for c in (0, 1)):
        p.error("Training requires reviewed examples of both phone and face")
    print(json.dumps(summary, indent=2))
    if args.dry_run:
        return
    output = args.output.resolve()
    if not output.is_relative_to(ROOT) or output == ROOT:
        p.error("--output must be a subdirectory of the dataset")
    if any(output.rglob("*.jpg")) or any(output.rglob("*.txt")):
        p.error(
            "Output already contains prepared samples; choose a new output directory"
        )
    for split in summary:
        for folder in ("images", "labels"):
            (output / folder / split).mkdir(parents=True, exist_ok=True)
    for item in items:
        sid, split = item["sample_id"], item["split"]
        shutil.copy2(
            safe_path(ROOT, item["image"]), output / "images" / split / (sid + ".jpg")
        )
        text = "\n".join(" ".join(str(v) for v in b) for b in item["boxes"])
        (output / "labels" / split / (sid + ".txt")).write_text(
            text + ("\n" if text else ""), encoding="utf-8"
        )
    config = {
        "path": output.relative_to(ROOT).as_posix(),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "nc": 2,
        "names": ["cell_phone", "face"],
    }
    (ROOT / "configs/phone_face.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    (ROOT / "configs/phone_face.yaml").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )
    (output / "reviewed_manifest.json").write_text(
        json.dumps(
            {"names": review["names"], "summary": summary, "items": items}, indent=2
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
