"""Download official pretrained assets for explicitly requested setup/builds."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

MODELS = {
    "yolo11n.pt": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}
HASHES = {
    "yolo11n.pt": "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1",
    "face_landmarker.task": "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff",
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=Path("models"))
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, url in MODELS.items():
        path = args.output / name
        if not path.exists():
            with urlopen(url, timeout=60) as response:
                data = response.read(100_000_000)
            if hashlib.sha256(data).hexdigest() != HASHES[name]:
                raise ValueError(f"Unexpected model SHA-256: {name}")
            temp = path.with_suffix(".download")
            temp.write_bytes(data)
            temp.replace(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != HASHES[name]:
            raise ValueError(f"Existing model hash differs from the release: {name}")
        manifest.append(
            {
                "file": name,
                "source": url,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
