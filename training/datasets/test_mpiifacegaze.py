"""Regression checks that an incomplete MPIIFaceGaze tree cannot pass its audit."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.io import savemat

from .common import process_lock
from .mpiifacegaze import SUBJECTS, main, verify_raw


class MPIIFaceGazeVerificationTests(unittest.TestCase):
    def setUp(self):
        quiet_log = patch("training.datasets.mpiifacegaze.log")
        quiet_log.start()
        self.addCleanup(quiet_log.stop)

    def make_tree(self, root: Path) -> Path:
        data = root / "MPIIFaceGaze"
        for subject in SUBJECTS:
            participant = data / subject
            calibration = participant / "Calibration"
            calibration.mkdir(parents=True)
            (participant / "day01").mkdir()
            (participant / "day01" / "0001.jpg").write_bytes(b"fixture")
            (participant / f"{subject}.txt").write_text(
                "day01/0001.jpg " + " ".join(["1"] * 26) + " left\n", encoding="utf-8"
            )
            savemat(calibration / "Camera.mat", {"cameraMatrix": np.eye(3), "distCoeffs": np.zeros(5)})
            savemat(calibration / "monitorPose.mat", {"rvecs": np.zeros(3), "tvecs": np.ones(3)})
            savemat(calibration / "screenSize.mat", {
                "height_pixel": 1080, "width_pixel": 1920, "height_mm": 250, "width_mm": 400,
            })
        return data

    def test_complete_tree_and_missing_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = self.make_tree(root)
            valid = verify_raw(root)
            self.assertEqual(valid["status"], "passed")
            self.assertEqual(valid["image_count"], 15)
            self.assertTrue(valid["all_annotation_image_references_exist"])
            (data / "p08" / "day01" / "0001.jpg").unlink()
            invalid = verify_raw(root)
            self.assertEqual(invalid["status"], "failed")
            self.assertFalse(invalid["all_annotation_image_references_exist"])
            self.assertEqual(invalid["participants"]["p08"]["missing_image_references_count"], 1)

    def test_actual_archive_rotation_variable_spelling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = self.make_tree(root)
            savemat(data / "p00" / "Calibration" / "monitorPose.mat", {
                "rvects": np.zeros(3), "tvecs": np.ones(3),
            })
            valid = verify_raw(root)
            self.assertEqual(valid["status"], "passed")
            self.assertEqual(valid["participants"]["p00"]["calibration"]["monitorPose.mat"]["rotation_variable"], "rvects")

    def test_calibration_and_nonfinite_annotation_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = self.make_tree(root)
            (data / "p03" / "Calibration" / "monitorPose.mat").unlink()
            annotation = data / "p02" / "p02.txt"
            annotation.write_text(
                "day01/0001.jpg nan " + " ".join(["1"] * 25) + " left\n", encoding="utf-8"
            )
            invalid = verify_raw(root)
            self.assertEqual(invalid["status"], "failed")
            self.assertEqual(invalid["participants"]["p02"]["malformed_annotation_rows_count"], 1)
            self.assertIn("Missing calibration monitorPose.mat", invalid["participants"]["p03"]["issues"])

    def test_cli_lock_prevents_status_overwrite_and_can_be_reacquired(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "mpiifacegaze"
            dataset.mkdir()
            report = dataset / "status.json"
            sentinel = '{"status": "ready", "verification": "previous result"}\n'
            report.write_text(sentinel, encoding="utf-8")
            with patch("sys.argv", ["mpiifacegaze", "--root", str(root)]), patch(
                "training.datasets.mpiifacegaze.prepare"
            ) as prepare:
                with process_lock(dataset / ".prepare.lock"):
                    self.assertEqual(main(), 1)
                prepare.assert_not_called()
                self.assertEqual(report.read_text(encoding="utf-8"), sentinel)
                prepare.return_value = {"dataset": "MPIIFaceGaze", "status": "ready"}
                with patch("builtins.print"):
                    self.assertEqual(main(), 0)
                prepare.assert_called_once_with(root)


if __name__ == "__main__":
    unittest.main()
