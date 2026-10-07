"""Focused tests for multipart TAR extraction and Gaze360 correspondence checks."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
    from scipy.io import savemat
    from training.datasets.gaze360 import SequentialParts, extract_heads, selected_member, verify_annotations
except ImportError:
    HAVE_DATASET_DEPENDENCIES = False
else:
    HAVE_DATASET_DEPENDENCIES = True

@unittest.skipUnless(HAVE_DATASET_DEPENDENCIES, "Install the datasets extra for dataset checks")
class Gaze360DatasetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def split_archive(self, contents):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, data in contents:
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        data = buffer.getvalue()
        # Boundaries inside both headers and payloads exercise true concatenation.
        boundaries = (0, 17, 519, 1193, 2049, 3101, len(data))
        parts = []
        for number, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            path = self.root / f"part{number}"
            path.write_bytes(data[start:end])
            parts.append(path)
        return parts, data

    def test_sequential_reader_crosses_arbitrary_part_boundaries(self):
        parts, expected = self.split_archive([("metadata.mat", b"metadata")])
        with SequentialParts(parts) as source:
            self.assertEqual(source.read(511) + source.read(), expected)
            self.assertEqual(source.bytes_read, len(expected))

    def test_selection_excludes_body_and_rejects_traversal(self):
        self.assertEqual(selected_member("./metadata.mat"), "metadata.mat")
        self.assertEqual(selected_member("imgs/rec_001/head/000001/000002.jpg"),
                         "imgs/rec_001/head/000001/000002.jpg")
        self.assertIsNone(selected_member("imgs/rec_001/body/000001/000002.jpg"))
        for path in ("../metadata.mat", "/metadata.mat", "C:/metadata.mat", "imgs\\metadata.mat"):
            with self.assertRaises(ValueError):
                selected_member(path)

    def test_tar_extracts_all_heads_without_body(self):
        jpeg = io.BytesIO()
        Image.new("RGB", (12, 9), (20, 40, 60)).save(jpeg, format="JPEG")
        parts, original = self.split_archive([
            ("metadata.mat", b"metadata"),
            ("imgs/rec_001/head/000001/000000.jpg", jpeg.getvalue()),
            ("imgs/rec_001/head/000001/000001.jpg", jpeg.getvalue()),
            ("imgs/rec_001/body/000001/000000.jpg", b"body is intentionally not decoded"),
        ])
        result = extract_heads(parts, self.root / "raw")
        self.assertEqual(result["head_images"], 2)
        self.assertEqual(result["body_images_skipped"], 1)
        self.assertEqual(result["body_images_extracted"], 0)
        self.assertEqual(result["stream_bytes_read"], len(original))
        self.assertTrue(result["tar_headers_verified"])

    def test_tar_rejects_corrupt_selected_jpeg(self):
        parts, _ = self.split_archive([
            ("metadata.mat", b"metadata"),
            ("imgs/rec_001/head/000001/000001.jpg", b"not a JPEG"),
        ])
        with self.assertRaisesRegex(ValueError, "Image verification failed"):
            extract_heads(parts, self.root / "raw")

    def annotation_fixture(self):
        raw = self.root / "raw"
        (raw / "splits").mkdir(parents=True)
        savemat(raw / "metadata.mat", {
            "recordings": np.array(["rec_001"], dtype=object),
            "recording": np.zeros(4, dtype=int),
            "person_identity": np.ones(4, dtype=int),
            "frame": np.arange(4),
            "split": np.arange(4),
            "gaze_dir": np.tile([0.0, 0.0, -1.0], (4, 1)),
        })
        for index in range(4):
            path = raw / "imgs" / "rec_001" / "head" / "000001" / f"{index:06d}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"already verified during extraction")
        for index, split in enumerate(("train", "validation", "test")):
            (raw / "splits" / f"{split}.txt").write_text(
                f"rec_001/head/000001/{index:06d}.jpg 0 0 -1\n", encoding="utf-8"
            )
        return raw

    def test_metadata_verification_includes_unused_frames(self):
        raw = self.annotation_fixture()
        result = verify_annotations(raw)
        self.assertEqual(result["metadata_rows"], 4)
        self.assertEqual(result["unused_frames_preserved"], 1)
        (raw / "imgs/rec_001/head/000001/000003.jpg").unlink()
        with self.assertRaisesRegex(ValueError, "missing head images"):
            verify_annotations(raw)

    def test_metadata_rejects_mismatched_split_gaze(self):
        raw = self.annotation_fixture()
        (raw / "splits/train.txt").write_text("rec_001/head/000001/000000.jpg 1 0 0\n")
        with self.assertRaisesRegex(ValueError, "gaze differs"):
            verify_annotations(raw)


if __name__ == "__main__":
    unittest.main()
