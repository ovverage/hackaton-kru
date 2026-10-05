import json
import shutil
import subprocess
import pytest


def test_real_clip_container_timestamps_and_playback_range(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from agent.resources import ffmpeg_executable
    from agent.vision import ClipRecorder

    recorder = ClipRecorder(tmp_path)
    try:
        for i in range(151):
            at = i / 10
            frame = np.full((480, 640, 3), (35, 65, 45), dtype=np.uint8)
            cv2.putText(
                frame,
                f"SYNTHETIC TEST ONLY  {at:.1f}s",
                (30, 235),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (220, 245, 200),
                2,
            )
            recorder.push(at, frame)
            if i == 100:
                recorder.mark({"id": "fixture", "at": at})
        result = recorder.completed(float("inf"))
        assert len(result) == 1 and result[0]["start"] == 0 and result[0]["end"] == 15
        probe = subprocess.run(
            [
                ffmpeg_executable(),
                "-v",
                "error",
                "-i",
                result[0]["path"],
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert probe.returncode == 0
        capture = cv2.VideoCapture(result[0]["path"])
        capture.set(cv2.CAP_PROP_POS_MSEC, 10000)
        ok, frame = capture.read()
        capture.release()
        assert ok and frame.shape[:2] == (480, 640)
    finally:
        recorder.close()
