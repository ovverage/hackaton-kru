"""Reproduce the supplied public behavior experiment without relabeling it as phones."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--epochs", type=int, default=10)
    p.add_argument("--fraction", type=float, default=1.0)
    p.add_argument("--imgsz", type=int, default=320)
    p.add_argument("--output", type=Path, default=Path("training/runs/behavior"))
    p.add_argument("--device", default="cpu")
    args = p.parse_args()
    if not 0 < args.fraction <= 1 or args.epochs < 1:
        p.error("Invalid training configuration")
    root = args.dataset.resolve()
    subprocess.run(
        [sys.executable, str(root / "scripts/validate_package.py")], check=True
    )
    args.output.mkdir(parents=True, exist_ok=True)
    data = json.loads((root / "configs/behavior.json").read_text(encoding="utf-8-sig"))
    data["path"] = str(root / data["path"])
    config = args.output.resolve() / "data.yaml"
    config.write_text(json.dumps(data), encoding="utf-8")
    import torch
    import ultralytics
    from ultralytics import YOLO

    torch.set_num_threads(4)
    model = YOLO("models/yolo11n.pt")
    model.train(
        data=str(config),
        epochs=args.epochs,
        fraction=args.fraction,
        imgsz=args.imgsz,
        batch=8,
        workers=0,
        device=args.device,
        seed=20261006,
        fliplr=0,
        flipud=0,
        project=str(args.output.resolve()),
        name="fit",
        exist_ok=False,
        cache=False,
        plots=False,
        deterministic=True,
    )
    best = YOLO(str(model.trainer.best))
    evaluation = best.val(
        data=str(config),
        split="test",
        imgsz=args.imgsz,
        batch=8,
        device=args.device,
        workers=0,
        plots=False,
        project=str(args.output.resolve()),
        name="held_out_test",
    )
    report = {
        "run_type": "experimental_fine_tuning",
        "epochs": args.epochs,
        "train_fraction": args.fraction,
        "seed": 20261006,
        "imgsz": args.imgsz,
        "ultralytics": ultralytics.__version__,
        "classes": data["names"],
        "source": "https://zenodo.org/records/14606173",
        "test_images": 718,
        "metrics": {k: float(v) for k, v in evaluation.results_dict.items()},
        "limitations": [
            "Native behavior boxes are NOT phone/face boxes",
            "Short CPU experiment, not production validation",
            "Recording-group split inferred from filenames, not confirmed identities",
        ],
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
