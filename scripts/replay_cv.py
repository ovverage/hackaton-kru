"""Replay consented local recordings through the production CV and rule pipeline."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--calibration", type=Path, required=True,
                        help="JSON {centres: {SCREEN: [four features], ...}} or intervals per calibration position")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recording-id", required=True, help="Anonymous ID used in ground truth")
    parser.add_argument("--fps", type=float, default=5)
    args = parser.parse_args()
    if not 2.5 <= args.fps <= 15:
        parser.error("Replay FPS must be 2.5..15 for the continuous rule thresholds")
    if args.output.exists():
        parser.error("Choose a new output directory to preserve previous results")
    import cv2
    import numpy as np
    from agent.vision import Camera
    from agent.resources import verified_models
    from agent.behavior import POSITIONS
    from shared.rules import RuleEngine
    from shared.version import APP_VERSION, MODEL_VERSION, RULE_VERSION

    models = verified_models()
    camera = Camera(str(args.video.resolve()), models[0], models[1], calibrate=False)
    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    source_fps = camera.capture.get(cv2.CAP_PROP_FPS)
    if not np.isfinite(source_fps) or source_fps <= 0:
        raise ValueError("Recording has no valid frame rate")
    try:
        if "centres" in calibration:
            camera.centres = {key: np.asarray(value, dtype=float) for key, value in calibration["centres"].items()}
        else:
            # Calibration intervals belong to this recording and must be excluded
            # from scored intervals in the independent ground truth.
            intervals = calibration["intervals"]
            samples = {key: [] for key, _ in POSITIONS}
            frame_number = 0
            while True:
                ok, frame = camera.capture.read()
                if not ok:
                    break
                at = frame_number / source_fps
                frame_number += 1
                keys = [key for key, interval in intervals.items() if interval[0] <= at <= interval[1]]
                if not keys:
                    continue
                faces, features = camera.face_features(cv2.resize(frame, (640, 480)), int(at*1000))
                if faces == 1 and features is not None:
                    for key in keys:
                        samples[key].append(features)
            if any(len(values) < 25 for values in samples.values()):
                raise ValueError("Each of the eight calibration intervals needs 25 valid frames")
            camera.centres = {key: np.median(values, axis=0) for key, values in samples.items()}
        camera.validate_calibration()
        camera.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        # The underlying MediaPipe VIDEO clock must remain increasing across replay.
        clock_offset = camera.timestamp / 1000 + 1
        args.output.mkdir(parents=True)
        (args.output / "calibration.json").write_text(json.dumps({"centres": {k: v.tolist() for k, v in camera.centres.items()}}, indent=2))
        engine = RuleEngine()
        engine.start()
        events, durations = {}, []
        number, next_at, last_at = 0, 0., 0.
        start = float(calibration.get("score_from", 0))
        with (args.output / "observations.jsonl").open("w", encoding="utf-8") as output:
            while True:
                ok, frame = camera.capture.read()
                if not ok:
                    break
                at = number / source_fps
                number += 1
                if at + 1e-6 < next_at or at < start:
                    continue
                next_at = at + 1 / args.fps
                tick = time.perf_counter()
                observation = camera.analyze(frame, at + clock_offset)
                durations.append((time.perf_counter()-tick)*1000)
                output.write(json.dumps({"t": at, **observation}) + "\n")
                last_at = at
                for event in engine.observe(at, **observation):
                    if event.get("update"):
                        events[event["id"]].update(event)
                    else:
                        events[event["id"]] = event
        predictions = [{"recording": args.recording_id, "type": event["type"],
                        "start": event["start"], "end": event.get("end", last_at if event.get("ongoing") else event["at"])}
                       for event in events.values()]
        (args.output / "predictions.json").write_text(json.dumps(predictions, indent=2), encoding="utf-8")
        report = {"app": APP_VERSION, "model": MODEL_VERSION, "rules": RULE_VERSION,
                  "frames": len(durations), "source_fps": source_fps, "sample_fps": args.fps,
                  "input_sha256": hashlib.file_digest(args.video.open("rb"), "sha256").hexdigest(),
                  "inference_p95_ms": float(np.percentile(durations, 95)) if durations else None,
                  "note": "Offline inference, no live camera, OS security or accuracy claim. Rule locks are preserved; review/unlock commands are not replayed."}
        (args.output / "run.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    finally:
        camera.close()


if __name__ == "__main__":
    main()
