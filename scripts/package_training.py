"""Package only experiment weights and aggregate reports, never source media/features."""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/Qorgau-Training-Experiments.zip")
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    files = {
        "models/gaze-baseline.json": root
        / "training/runs/gaze-runtime/gaze-baseline.json",
        "models/behavior-best.pt": root
        / "training/runs/behavior-full/fit/weights/best.pt",
        "reports/behavior-learning-curve.csv": root
        / "training/runs/behavior-full/fit/results.csv",
        "README.md": root / "training/README.md",
    }
    for folder in ("reports", "provenance"):
        for path in (root / "training" / folder).glob("*.json"):
            files[f"{folder}/{path.name}"] = path
    if not all(path.is_file() for path in files.values()):
        parser.error("Run and finish both experiments before packaging")
    if not (root / "training/reports/behavior-full.json").is_file():
        parser.error(
            "Copy the completed full-training evaluation into training/reports first"
        )
    manifest = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            manifest.append(
                {
                    "path": name,
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
            archive.write(path, name)
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
    print(args.output, hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
