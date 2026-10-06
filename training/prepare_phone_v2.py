"""Build a single-class phone set from COCO boxes and reviewed classroom photos."""

from __future__ import annotations
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import random
import shutil
import time
import urllib.request
import zipfile

SEED = 20261006
ANNOTATIONS = "http://images.cocodataset.org/annotations/annotations_trainval2017.zip"


def fetch(url, path):
    if path.is_file() and path.stat().st_size > 0:
        return
    temp = path.with_suffix(path.suffix + ".partial")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as src, temp.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            temp.replace(path)
            return
        except Exception:
            if attempt == 3:
                raise
            time.sleep(attempt + 1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--review", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cache", type=Path, default=Path("data/coco-cache"))
    args = p.parse_args()
    from PIL import Image

    root = args.dataset.resolve()
    out = args.output.resolve()
    cache = args.cache.resolve()
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / "annotations_trainval2017.zip"
    print("Downloading official COCO annotations", flush=True)
    fetch(ANNOTATIONS, archive)
    manifest = []
    jobs = []
    rng = random.Random(SEED)
    with zipfile.ZipFile(archive) as z:
        for subset, target, negative_count in [
            ("train2017", "train", 500),
            ("val2017", "val", 100),
        ]:
            data = json.loads(z.read(f"annotations/instances_{subset}.json"))
            phone = next(
                c["id"] for c in data["categories"] if c["name"] == "cell phone"
            )
            hard_ids = {
                c["id"]
                for c in data["categories"]
                if c["name"] in ("person", "book", "remote", "laptop")
            }
            boxes = defaultdict(list)
            hard = set()
            crowd = set()
            for a in data["annotations"]:
                if a["category_id"] == phone:
                    if a.get("iscrowd"):
                        crowd.add(a["image_id"])
                    boxes[a["image_id"]].append(a["bbox"])
                if a["category_id"] in hard_ids:
                    hard.add(a["image_id"])
            positives = set(boxes) - crowd
            negatives = rng.sample(sorted(hard - set(boxes)), negative_count)
            selected = positives | set(negatives)
            images = [im for im in data["images"] if im["id"] in selected]
            for im in images:
                name = f"coco_{subset}_{im['id']:012d}"
                image = out / "images" / target / (name + ".jpg")
                label = out / "labels" / target / (name + ".txt")
                image.parent.mkdir(parents=True, exist_ok=True)
                label.parent.mkdir(parents=True, exist_ok=True)
                w, h = im["width"], im["height"]
                normalized = []
                for x, y, bw, bh in boxes[im["id"]]:
                    x0, y0, x1, y1 = (
                        max(0, x),
                        max(0, y),
                        min(w, x + bw),
                        min(h, y + bh),
                    )
                    if x1 > x0 and y1 > y0:
                        normalized.append(
                            [
                                0,
                                (x0 + x1) / 2 / w,
                                (y0 + y1) / 2 / h,
                                (x1 - x0) / w,
                                (y1 - y0) / h,
                            ]
                        )
                label.write_text(
                    "\n".join(" ".join(map(str, b)) for b in normalized) + "\n",
                    encoding="utf-8",
                )
                url = f"http://images.cocodataset.org/{subset}/{im['file_name']}"
                row = {
                    "source": "COCO2017",
                    "coco_id": im["id"],
                    "source_split": subset,
                    "split": target,
                    "image": str(image),
                    "label": str(label),
                    "license_id": im["license"],
                    "url": url,
                    "boxes": len(normalized),
                    "group": name,
                    "reviewed": "official COCO annotations",
                }
                manifest.append(row)
                jobs.append((url, image))
    failures = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = {pool.submit(fetch, url, path): (url, path) for url, path in jobs}
        for i, future in enumerate(as_completed(pending), 1):
            url, path = pending[future]
            try:
                future.result()
                with Image.open(path) as im:
                    im.verify()
            except Exception as e:
                failures.append({"url": url, "error": str(e)})
            if i % 100 == 0:
                print(
                    f"COCO images {i}/{len(jobs)}; errors {len(failures)}", flush=True
                )
    if failures:
        (out / "download-errors.json").write_text(
            json.dumps(failures, indent=2), encoding="utf-8"
        )
        raise RuntimeError("COCO download incomplete; rerun to resume cached images")
    reviews = json.loads(args.review.read_text(encoding="utf-8"))
    excluded = []
    for item in reviews:
        if not item["reviewed"]:
            raise ValueError("Unreviewed image")
        rel = item["image"]
        participant = rel.split("/")[0]
        n = int(Path(rel).stem.rsplit("_", 1)[1])
        if item["excluded"]:
            excluded.append(rel)
            continue
        if participant == "person_02":
            target = "test"
        elif "/with_phone/" in rel:
            if 61 <= n <= 70 or 91 <= n <= 100:
                excluded.append(rel)
                continue
            target = "val" if 71 <= n <= 90 else "train"
        else:
            if 26 <= n <= 30:
                excluded.append(rel)
                continue
            target = "val" if n >= 31 else "train"
        original = (root / rel).resolve()
        if not original.is_relative_to(root):
            raise ValueError("Unsafe private image path")
        if hashlib.sha256(original.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Private image changed after review")
        image = out / "images" / target / (Path(rel).stem + ".jpg")
        label = out / "labels" / target / (Path(rel).stem + ".txt")
        image.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, image)
        w, h = item["width"], item["height"]
        boxes = [
            [0, (a + c) / 2 / w, (b + d) / 2 / h, (c - a) / w, (d - b) / h]
            for a, b, c, d in item["boxes"]
        ]
        label.write_text(
            "\n".join(" ".join(map(str, b)) for b in boxes) + "\n", encoding="utf-8"
        )
        manifest.append(
            {
                "source": "private_classroom",
                "participant": participant,
                "split": target,
                "image": str(image),
                "label": str(label),
                "boxes": len(boxes),
                "group": participant
                + "_"
                + ("phone" if "/with_phone/" in rel else "negative"),
                "reviewed": item["reviewer"],
                "sha256": item["sha256"],
            }
        )
    # Exact duplicates never cross splits. Fail instead of silently inflating test.
    hashes = {}
    for row in manifest:
        digest = hashlib.sha256(Path(row["image"]).read_bytes()).hexdigest()
        if digest in hashes and hashes[digest] != row["split"]:
            raise ValueError("Exact duplicate across splits")
        hashes[digest] = row["split"]
        row["sha256"] = digest
    config = {
        "path": str(out),
        "train": "images/train",
        "val": "images/val",
        "test": "images/test",
        "names": ["cell phone"],
    }
    (out / "data.yaml").write_text(json.dumps(config), encoding="utf-8")
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "seed": SEED,
                "items": manifest,
                "excluded": excluded,
                "annotations_source": ANNOTATIONS,
                "annotations_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "review_sha256": hashlib.sha256(args.review.read_bytes()).hexdigest(),
                "split_policy": "COCO official train/val; P01 separated contiguous blocks with temporal gaps; P02 entirely test",
                "limitations": [
                    "Personal validation shares identity and room with train",
                    "Personal test is one unseen participant with limited phone models",
                    "Private boxes were AI-assisted visually reviewed, no independent double annotation",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                s: sum(r["split"] == s for r in manifest)
                for s in ("train", "val", "test")
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
