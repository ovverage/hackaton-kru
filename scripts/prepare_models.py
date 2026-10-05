"""Download pinned upstream models and export YOLO11n for the CPU-only runtime."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "yolo11n.pt": "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
    "face_landmarker.task": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    from ultralytics import YOLO
    folder = ROOT / "models"
    folder.mkdir(exist_ok=True)
    manifest_path = ROOT / "model-manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    entries = []
    for name, url in SOURCES.items():
        destination = folder / name
        if not destination.exists():
            partial = destination.with_suffix(".download")
            urllib.request.urlretrieve(url, partial)
            partial.replace(destination)
        digest = sha256(destination)
        expected = next((x["sha256"] for x in previous.get("files", []) if x["file"] == name), None)
        if expected and expected != digest:
            raise ValueError(f"UPSTREAM_HASH_MISMATCH: {name}")
        entries.append({"file": name, "url": url, "sha256": digest,
                        "license": "AGPL-3.0" if name.endswith(".pt") else "Apache-2.0"})
    model = YOLO(str(folder / "yolo11n.pt"))
    output = Path(model.export(format="onnx", imgsz=640, opset=17, simplify=False, dynamic=False, nms=False, device="cpu"))
    entries.append({"file": output.name, "sha256": sha256(output), "license": "AGPL-3.0",
                    "source": "yolo11n.pt", "export": "onnx,640,opset17,float32,batch1,no-nms"})
    import ultralytics, torch, onnx
    manifest = {"version": "2026.10.06.1", "pretrained": True, "class_id": 67,
                "class_name": "cell phone", "architecture": "YOLO11n / COCO80",
                "training": "Upstream pretrained; no local classroom fine-tuning claimed",
                "exporter": {"ultralytics": ultralytics.__version__, "torch": torch.__version__, "onnx": onnx.__version__},
                "files": entries}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
