"""Prepare both downloaded gaze datasets with auditable grouped splits.

python -m training.public_gaze_prepare index --data data/datasets --output data/public-gaze
python -m training.public_gaze_prepare extract --output data/public-gaze --workers 4

Index needs NumPy only. Extract needs the project's MediaPipe + OpenCV runtime.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from shared.public_gaze import SCHEMA, face_crop, mpii_direction, normalize


def json_write(path, payload):
    path = Path(path)
    tmp = path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    tmp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_splits(rows):
    groups, paths = defaultdict(set), defaultdict(set)
    counts = Counter()
    for row in rows:
        groups[row["split"]].add(row["group"])
        paths[row["split"]].add(row["source"])
        counts[row["dataset"]+"/"+row["split"]] += 1
    for left, right in (("train", "val"), ("train", "test"), ("val", "test")):
        if groups[left] & groups[right] or paths[left] & paths[right]:
            raise ValueError(f"SPLIT_LEAKAGE:{left}:{right}")
    if sum(len(v) for v in paths.values()) != len(rows):
        raise ValueError("DUPLICATE_SAMPLE")
    return {"counts": dict(counts), "groups": {k: sorted(v) for k,v in groups.items()},
            "group_overlap": 0, "path_overlap": 0}


def index(args):
    root, output = args.data.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows, annotation_hashes = [], {}
    mpii = root/"mpiifacegaze/raw/MPIIFaceGaze"
    for subject in range(15):
        group = f"p{subject:02d}"
        annotation = mpii/group/f"{group}.txt"
        annotation_hashes[str(annotation.relative_to(root))] = digest(annotation)
        split = "train" if subject <= 10 else "val" if subject <= 12 else "test"
        seen = set()
        for line in annotation.read_text("utf-8").splitlines():
            tokens = line.split()
            if len(tokens) != 28:
                raise ValueError(f"INVALID_MPII_ANNOTATION:{annotation}")
            # Some participant annotations repeat an image for left/right eye
            # evaluation. A full-face model must see each image once.
            if tokens[0] in seen:
                continue
            seen.add(tokens[0])
            path = mpii/group/tokens[0]
            gaze = mpii_direction(list(map(float, tokens[21:24])), list(map(float, tokens[24:27])))
            rows.append(dict(dataset="mpiifacegaze", split=split, group=f"mpii/{group}",
                             sequence=f"mpii/{group}/{tokens[0].split('/')[0]}",
                             source=str(path), gaze=gaze))
    gaze_root = root/"gaze360/raw"
    for split, filename in (("train", "train.txt"), ("val", "validation.txt"), ("test", "test.txt")):
        annotation = gaze_root/"splits"/filename
        annotation_hashes[str(annotation.relative_to(root))] = digest(annotation)
        for line in annotation.read_text("utf-8").splitlines():
            tokens = line.split()
            path = Path(tokens[0])
            if len(tokens) != 4 or path.is_absolute() or ".." in path.parts:
                raise ValueError("INVALID_GAZE360_ANNOTATION")
            rows.append(dict(dataset="gaze360", split=split, group="g360/"+path.parts[0],
                             sequence="g360/"+str(path.parent).replace("\\", "/"),
                             source=str(gaze_root/"imgs"/path), gaze=normalize(map(float, tokens[1:]))))
    audit = validate_splits(rows)
    for row in rows:
        row["id"] = hashlib.sha256((row["dataset"]+":"+row["source"].split("raw")[-1]).encode()).hexdigest()[:24]
    with (output/"index.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row)+"\n")
    for dataset, name in (("mpiifacegaze", "CC-BY-NC-SA-4.0.txt"), ("gaze360", "LICENSE.md")):
        shutil.copyfile(root/dataset/"sources"/name, output/f"{dataset}-LICENSE.txt")
    audit.update(schema=SCHEMA, annotation_sha256=annotation_hashes, index_sha256=digest(output/"index.jsonl"),
                 unused_gaze360="Retained in source dataset; excluded as labelled central frames.",
                 mpii_split="p00-p10 train, p11-p12 validation, p13-p14 final test; fixed before training",
                 target_axes="x camera-left, y up, z away; MPII target-face_center rotated into eye-camera ray basis",
                 images="Tight Face Mesh crop; no exact camera intrinsic perspective warp (domain approximation)")
    json_write(output/"index-audit.json", audit)
    print(json.dumps(audit["counts"]), flush=True)


_detector = None
_mp = None


def init_worker(model, disable_audio=False):
    global _detector, _mp
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    if disable_audio:
        # MediaPipe imports optional audio tasks even for vision. Its audio
        # module handles ImportError, whereas a headless PortAudio driver can
        # raise an uncaught PortAudioError. Disable audio only in this worker.
        import sys
        sys.modules["sounddevice"] = None
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    cv2.setNumThreads(1)
    _mp = mp
    _detector = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=model),
        running_mode=vision.RunningMode.IMAGE, num_faces=2,
        min_face_detection_confidence=.5, min_face_presence_confidence=.5,
        output_face_blendshapes=True))


def extract_chunk(task):
    import cv2
    output_string, chunk_id, rows = task
    output = Path(output_string)
    shard = output/"shards"/f"{chunk_id:05d}.jsonl"
    if shard.exists():
        return {"shard": chunk_id, "cached": True}
    results = []
    for row in rows:
        result = dict(row)
        image = cv2.imread(row["source"])
        if image is None:
            result["status"] = "decode_failed"
        else:
            try:
                found = _detector.detect(_mp.Image(image_format=_mp.ImageFormat.SRGB,
                                                    data=cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))
                if len(found.face_landmarks) != 1:
                    result["status"] = "face_missing_or_ambiguous"
                else:
                    crop = face_crop(image, found.face_landmarks[0])
                    blends = {v.category_name: v.score for v in found.face_blendshapes[0]}
                    if crop is None:
                        result["status"] = "face_too_small"
                    elif max(blends.get("eyeBlinkLeft", 0), blends.get("eyeBlinkRight", 0)) > .65:
                        result["status"] = "eyes_closed"
                    else:
                        relative = Path("images")/row["id"][:2]/(row["id"]+".jpg")
                        target = output/relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        ok = cv2.imwrite(str(target), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
                        if not ok:
                            raise OSError(f"CROP_WRITE_FAILED:{target}")
                        result.update(status="ok", image=relative.as_posix())
            except (RuntimeError, ValueError) as exc:
                result.update(status="landmarker_error", error=str(exc)[:200])
        results.append(result)
    temp = shard.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row)+"\n")
    temp.replace(shard)
    return {"shard": chunk_id, "counts": dict(Counter(row["status"] for row in results))}


def extract(args):
    output = args.output.resolve()
    rows = [json.loads(line) for line in (output/"index.jsonl").read_text("utf-8").splitlines()]
    if args.limit:
        # Smoke extraction includes both domains and all splits.
        rows = [row for dataset in ("mpiifacegaze", "gaze360") for split in ("train", "val", "test")
                for row in [r for r in rows if r["dataset"] == dataset and r["split"] == split][:args.limit]]
    (output/"shards").mkdir(exist_ok=True)
    contract = dict(schema=SCHEMA, index_sha256=digest(output/"index.jsonl"), model_sha256=digest(args.model),
                    chunk_size=args.chunk_size, limit_per_domain_split=args.limit, crop_size=224)
    contract_path = output/"extraction-contract.json"
    if contract_path.exists() and json.loads(contract_path.read_text("utf-8")) != contract:
        raise ValueError("EXTRACTION_CONTRACT_CHANGED: use a new output directory")
    json_write(contract_path, contract)
    tasks = [(str(output), i//args.chunk_size, rows[i:i+args.chunk_size]) for i in range(0, len(rows), args.chunk_size)]
    start = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers, initializer=init_worker, initargs=(str(args.model.resolve()), args.disable_audio)) as pool:
        for completed, result in enumerate(pool.map(extract_chunk, tasks), 1):
            print(json.dumps(dict(result, completed=completed, total=len(tasks), elapsed_seconds=round(time.monotonic()-start))), flush=True)
    retained, counts = [], Counter()
    with (output/"samples.jsonl").open("w", encoding="utf-8") as samples:
        for task in tasks:
            for line in (output/"shards"/f"{task[1]:05d}.jsonl").read_text("utf-8").splitlines():
                row = json.loads(line)
                counts[f"{row['dataset']}/{row['split']}/{row['status']}"] += 1
                if row["status"] == "ok":
                    retained.append(row)
                    samples.write(line+"\n")
    audit = validate_splits(retained)
    audit.update(schema=SCHEMA, coverage_counts=dict(counts), seconds=time.monotonic()-start,
                 samples_sha256=digest(output/"samples.jsonl"), smoke_only=bool(args.limit),
                 mediapipe_coverage="Angular metrics condition on Face Mesh availability; exclusions are not accuracy successes.")
    json_write(output/"extraction-audit.json", audit)
    print(json.dumps(audit), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("index")
    p.add_argument("--data", type=Path, default=Path("data/datasets"))
    p.add_argument("--output", type=Path, default=Path("data/public-gaze"))
    p = sub.add_parser("extract")
    p.add_argument("--output", type=Path, default=Path("data/public-gaze"))
    p.add_argument("--model", type=Path, default=Path("models/face_landmarker.task"))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--chunk-size", type=int, default=256)
    p.add_argument("--limit", type=int, default=0, help="Smoke only: rows per dataset/split; use separate output")
    p.add_argument("--disable-audio", action="store_true", help="Disable optional PortAudio import in headless vision workers only")
    args = parser.parse_args()
    (index if args.command == "index" else extract)(args)


if __name__ == "__main__":
    main()
