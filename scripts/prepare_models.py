"""Fetch immutable, hash-pinned runtime artifacts; never retrain during packaging."""

from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    "yolo11n.onnx",
    "face_landmarker.task",
    "face_yolov8n.onnx",
    "gaze-direction.json",
)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(root=ROOT, *, verify_only=False):
    manifest = json.loads((root / "model-manifest.json").read_text(encoding="utf-8"))
    folder = root / "models"
    folder.mkdir(exist_ok=True)
    verified = []
    for name in RUNTIME_FILES:
        entry = next((x for x in manifest["files"] if x["file"] == name), None)
        if not entry or not re.fullmatch("[0-9a-f]{64}", entry.get("sha256", "")):
            raise ValueError(f"Missing pinned model: {name}")
        path = folder / name
        if not path.is_file() or sha256(path) != entry["sha256"]:
            if verify_only:
                raise ValueError(f"MODEL_HASH_MISMATCH: {name}")
            url = entry.get("url", "")
            if not url.startswith("https://"):
                raise ValueError(f"An HTTPS artifact URL is required for {name}")
            partial = path.with_suffix(path.suffix + ".partial")
            with (
                urllib.request.urlopen(url, timeout=120) as response,
                partial.open("wb") as output,
            ):
                size = 0
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > 256 * 1024 * 1024:
                        raise ValueError("Runtime model exceeds size limit")
                    output.write(block)
            if sha256(partial) != entry["sha256"]:
                partial.unlink()
                raise ValueError(f"DOWNLOAD_HASH_MISMATCH: {name}")
            partial.replace(path)
        verified.append(
            {"file": name, "sha256": entry["sha256"], "bytes": path.stat().st_size}
        )
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(verify_only=args.verify_only), indent=2))


if __name__ == "__main__":
    main()
