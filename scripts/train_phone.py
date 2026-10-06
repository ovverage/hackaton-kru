"""Fine-tune only an explicitly supplied, separated and consented local dataset."""
import argparse
import hashlib
import json
from pathlib import Path


def validate_dataset(path):
    dataset = json.loads(path.read_text(encoding="utf-8"))
    items = dataset.get("items", [])
    if not items:
        raise ValueError("Нет размеченных данных. Обучение не проводилось.")
    subjects, groups, hashes = set(), {}, {}
    policy = dataset.get("split_policy", "subject")
    if policy not in ("subject", "session"):
        raise ValueError("split_policy должен быть subject или session")
    sources = {}
    counts = {"train": 0, "val": 0, "test": 0}
    for item in items:
        split, subject = item["split"], item["subject"]
        if split not in counts or not subject or item.get("consent") is not True:
            raise ValueError("Нужны split train/val/test, обезличенный subject и подтверждённое consent")
        subjects.add(subject)
        session = item.get("session")
        if policy == "session" and not session:
            raise ValueError("Для разделения по сеансам нужен session у каждого изображения")
        group = subject if policy == "subject" else (subject, session)
        if group in groups and groups[group] != split:
            raise ValueError("DATA_LEAKAGE: участник или сеанс попал в разные выборки")
        groups[group] = split
        if item.get("source_video"):
            source = item["source_video"]
            if source in sources and sources[source] != split:
                raise ValueError("DATA_LEAKAGE: кадры одного видео попали в разные выборки")
            sources[source] = split
        image = (path.parent / item["image"]).resolve()
        labels = (path.parent / item["labels"]).resolve()
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        if digest in hashes:
            raise ValueError("DATA_LEAKAGE: повтор изображения")
        hashes[digest] = split
        for line in labels.read_text().splitlines():
            parts = line.split()
            if len(parts) != 5 or parts[0] != "0" or any(not 0 <= float(x) <= 1 for x in parts[1:]):
                raise ValueError("Нужны YOLO labels: 0 cx cy width height в диапазоне 0..1")
        counts[split] += 1
    if min(counts.values()) < 20 or (policy == "subject" and len(subjects) < 5):
        raise ValueError("Нужно минимум 20 изображений в каждой выборке и 5 разных участников; это минимум для запуска, не доказательство качества")
    if policy == "session" and len(groups) < 3:
        raise ValueError("Нужны как минимум три независимых сеанса")
    return dataset, counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path, help="Consented dataset manifest, see docs/CV_EVALUATION.md")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output", type=Path, default=Path(".local/training"))
    args = parser.parse_args()
    dataset, counts = validate_dataset(args.dataset)
    from ultralytics import YOLO
    import yaml
    import shutil
    folder = args.output.resolve()
    if folder.exists():
        raise ValueError("Выберите новый каталог вывода, чтобы сохранить прежний эксперимент")
    for item in dataset["items"]:
        split = item["split"]
        for kind, field, suffix in (("images", "image", Path(item["image"]).suffix), ("labels", "labels", ".txt")):
            source = (args.dataset.parent / item[field]).resolve()
            target = folder / "dataset" / kind / split / (hashlib.sha256(item["image"].encode()).hexdigest() + suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    config = folder / "dataset.yaml"
    config.write_text(yaml.safe_dump({"path": str(folder / "dataset"), "train": "images/train", "val": "images/val", "test": "images/test", "names": {0: "cell phone"}}))
    root = Path(__file__).resolve().parents[1]
    model = YOLO(str(root / "models/yolo11n.pt"))
    model.train(data=str(config), epochs=args.epochs, imgsz=640, batch=8, workers=0,
                device=args.device, seed=42, deterministic=True, project=str(folder), name="fit")
    best = YOLO(str(folder / "fit/weights/best.pt"))
    metrics = best.val(data=str(config), split="test", device=args.device)
    artifact = best.export(format="onnx", imgsz=640, opset=17, simplify=False, dynamic=False, nms=False)
    report = {"counts": counts, "split_policy": dataset.get("split_policy", "subject"),
              "participants": len({item["subject"] for item in dataset["items"]}),
              "scope": "Session split measures familiar participants only; no claim about unseen people" if dataset.get("split_policy") == "session" else "Held-out participants",
              "test": metrics.results_dict, "candidate": artifact,
              "promoted_to_release": False, "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest()}
    (folder / "report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
