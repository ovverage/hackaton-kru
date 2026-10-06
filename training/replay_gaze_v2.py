"""Replay complete files through the runtime gaze classifier and temporal rules."""

from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--model", type=Path, required=True)
    p.add_argument("--face-model", type=Path, required=True)
    p.add_argument("--yolo-face-model", type=Path)
    p.add_argument("--split", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--fps", type=float, default=5.0)
    args = p.parse_args()
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from shared.gaze_v2 import extract_features, GazeClassifier
    from shared.rules import RuleEngine

    if args.yolo_face_model:
        from agent.detector import FaceDetector

        yolo_face = FaceDetector(args.yolo_face_model)
    else:
        yolo_face = None

    root = args.dataset.resolve()
    annotations = json.loads(
        (root / "prepared/gaze_annotation/annotations.json").read_text(
            encoding="utf-8-sig"
        )
    )
    split = json.loads(args.split.read_text(encoding="utf-8"))
    videos = [
        v
        for v in annotations["videos"]
        if v["video_id"] in split["test"] or v["category"] == "control"
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    with (args.output / "frames.jsonl").open("w", encoding="utf-8") as output:
        for index, video in enumerate(videos):
            path = (root / video["video"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Unsafe video path")
            capture = cv2.VideoCapture(str(path))
            fps = capture.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                raise ValueError("Unknown source fps")
            gaze = GazeClassifier(args.model)
            engine = RuleEngine()
            engine.start()
            options = vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(args.face_model)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=2,
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True,
            )
            frame_index = 0
            next_at = 0.0
            counts = Counter()
            events = []
            last_at = 0.0
            with vision.FaceLandmarker.create_from_options(options) as face:
                while True:
                    ok, original = capture.read()
                    if not ok:
                        break
                    at = frame_index / fps
                    frame_index += 1
                    if at + 1e-6 < next_at:
                        continue
                    next_at += 1 / args.fps
                    last_at = at
                    h, w = original.shape[:2]
                    scale = min(1.0, 960 / max(w, h))
                    frame = cv2.resize(original, (round(w * scale), round(h * scale)))
                    result = face.detect_for_video(
                        mp.Image(
                            image_format=mp.ImageFormat.SRGB,
                            data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
                        ),
                        max(1, round(at * 1000)),
                    )
                    vector = None
                    faces = max(
                        len(result.face_landmarks),
                        len(yolo_face.detect(original)) if yolo_face else 0,
                    )
                    if len(result.face_landmarks) == 1 and faces == 1:
                        vector = extract_features(
                            result.face_landmarks[0],
                            result.facial_transformation_matrixes[0],
                            {
                                b.category_name: b.score
                                for b in result.face_blendshapes[0]
                            },
                            frame.shape[1],
                            frame.shape[0],
                        )
                    obs = gaze.observe(vector)
                    events.extend(
                        engine.observe(
                            at,
                            direction=obs["direction"],
                            faces=faces,
                        )
                    )
                    truth = next(
                        (
                            s["label"]
                            for s in video["segments"]
                            if s["start_s"] <= at < s["end_s"]
                        ),
                        "uncertain",
                    )
                    counts["frames"] += 1
                    counts["unknown"] += obs["direction"] == "UNKNOWN"
                    counts["missing_face"] += len(result.face_landmarks) != 1
                    if truth in ("on_screen", "off_screen"):
                        counts[truth] += 1
                        if obs["direction"] != "UNKNOWN":
                            predicted_off = obs["direction"] != "SCREEN"
                            counts[
                                ("tp" if predicted_off else "fn")
                                if truth == "off_screen"
                                else ("fp" if predicted_off else "tn")
                            ] += 1
                    output.write(
                        json.dumps(
                            {
                                "video": video["video_id"],
                                "at": at,
                                "truth": truth,
                                **obs,
                            }
                        )
                        + "\n"
                    )
            capture.release()
            record = {
                "video": video["video_id"],
                "participant": video["participant"],
                "scenario": video["scenario"],
                "category": video["category"],
                "duration_s": last_at,
                "counts": dict(counts),
                "events": [e for e in events if not e.get("update")],
                "locks": engine.state.locks,
            }
            results.append(record)
            print(
                f"replayed {index + 1}/{len(videos)} {video['video_id']} {dict(counts)} events={len(record['events'])}",
                flush=True,
            )
    total = Counter()
    for r in results:
        total.update(r["counts"])
    report = {
        "model_sha256": hashlib.sha256(args.model.read_bytes()).hexdigest(),
        "yolo_face_sha256": hashlib.sha256(
            args.yolo_face_model.read_bytes()
        ).hexdigest()
        if args.yolo_face_model
        else None,
        "face_policy": "max YOLO/MediaPipe, as in Camera.analyze"
        if yolo_face
        else "MediaPipe only",
        "sample_fps": args.fps,
        "duration_s": sum(r["duration_s"] for r in results),
        "counts": dict(total),
        "recordings": results,
        "limitations": [
            "Development holdout was reused during model development",
            "Control clips are very short (under four seconds), so absence of five-second events there is not a long-session false-alarm test",
            "Segment labels are inferred between reviewed anchors and are preliminary",
            "Missing/unknown observations are reported, not counted as correct negatives",
        ],
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"duration_s": report["duration_s"], "counts": dict(total)}, indent=2
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
