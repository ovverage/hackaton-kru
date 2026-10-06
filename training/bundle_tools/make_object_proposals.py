"""Create unreviewed phone or face proposals; never promote them to ground truth."""

import argparse
import json
from pathlib import Path

from validate_package import ROOT, read_json, safe_path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--component", choices=["phone", "face"], required=True)
    p.add_argument(
        "--review", type=Path, default=ROOT / "annotations/phone_face_review.json"
    )
    p.add_argument(
        "--output", type=Path, default=ROOT / "annotations/phone_face_proposals.json"
    )
    p.add_argument("--source", choices=["personal", "public", "all"], default="all")
    p.add_argument(
        "--model", choices=["yolo11n.pt", "yolov8n.pt"], default="yolo11n.pt"
    )
    p.add_argument("--confidence", type=float, default=0.15)
    p.add_argument("--device", default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.limit < 0 or not 0 < args.confidence <= 1:
        p.error("limit must be nonnegative and confidence in (0, 1]")
    review = read_json(args.review)
    if review["names"] != ["cell_phone", "face"]:
        p.error("Unexpected target class names")
    items = [
        r
        for r in review["items"]
        if not r["reviewed"] and (args.source == "all" or r["source"] == args.source)
    ]
    if args.limit:
        items = items[: args.limit]
    if args.dry_run:
        print(
            json.dumps(
                {
                    "component": args.component,
                    "candidate_images": len(items),
                    "output": str(args.output),
                }
            )
        )
        return
    target_class = 0 if args.component == "phone" else 1
    mesh = None
    try:
        if args.component == "phone":
            from ultralytics import YOLO

            model = YOLO(args.model)
            if model.names.get(67) != "cell phone":
                p.error("Phone proposals require COCO cell phone class 67")
        else:
            import cv2
            import numpy as np
            import mediapipe as mp

            if not hasattr(mp, "solutions"):
                p.error(
                    "This script uses Face Mesh. Install requirements-facemesh.txt in the Face Mesh environment"
                )
            mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True,
                max_num_faces=4,
                refine_landmarks=True,
                min_detection_confidence=0.5,
            )
        for index, item in enumerate(items, 1):
            path = safe_path(ROOT, item["image"])
            boxes, scores = [], []
            if args.component == "phone":
                result = model.predict(
                    source=str(path),
                    classes=[67],
                    conf=args.confidence,
                    device=args.device,
                    verbose=False,
                )[0]
                for xywh, score in zip(
                    result.boxes.xywhn.tolist(), result.boxes.conf.tolist()
                ):
                    boxes.append([0] + xywh)
                    scores.append(float(score))
            else:
                frame = cv2.imdecode(
                    np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR
                )
                if frame is None:
                    raise ValueError(f"Cannot decode {path}")
                faces = (
                    mesh.process(
                        cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    ).multi_face_landmarks
                    or []
                )
                for face in faces:
                    points = face.landmark[:468]
                    x0 = max(0.0, min(p.x for p in points))
                    x1 = min(1.0, max(p.x for p in points))
                    y0 = max(0.0, min(p.y for p in points))
                    y1 = min(1.0, max(p.y for p in points))
                    if x1 > x0 and y1 > y0:
                        boxes.append(
                            [1, (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0]
                        )
                        scores.append(None)
            item["boxes"] = [
                b for b in item["boxes"] if int(b[0]) != target_class
            ] + boxes
            item.setdefault("proposal_metadata", {})[args.component] = {
                "method": args.model + ":COCO67"
                if args.component == "phone"
                else "MediaPipe Face Mesh landmark extent",
                "scores": scores,
                "requires_visual_review": True,
            }
            # No detections is still unreviewed; it never means a verified negative.
            item["reviewed"] = False
            if index % 200 == 0:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(review, indent=2), encoding="utf-8")
                print(f"Processed {index}/{len(items)}", flush=True)
    except ImportError as e:
        p.error(
            f"Missing dependency: {e}. Install the requirements for the selected component"
        )
    finally:
        if mesh:
            mesh.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(review, indent=2), encoding="utf-8")
    print(f"Saved {len(items)} unreviewed {args.component} proposals to {args.output}")


if __name__ == "__main__":
    main()
