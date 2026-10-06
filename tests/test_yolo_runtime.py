from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from agent.detector import YoloDetector


def test_letterbox_recovers_native_16_by_9_box_and_rejects_nonfinite_output():
    detector = YoloDetector.__new__(YoloDetector)
    detector.input = SimpleNamespace(name="images")
    detector.classes = 1
    detector.class_id = 0
    detector.confidence = 0.4
    # Native [400, 200, 600, 600] in 1280x720 becomes [200,240,300,440]
    # after scale .5 and top padding 140. The second row must be ignored.
    raw = np.array(
        [[[250, 340, 100, 200, 0.9], [10, 10, 10, 10, float("nan")]]], dtype=np.float32
    ).transpose(0, 2, 1)
    detector.session = Mock()
    detector.session.run.return_value = [raw]
    result = detector.detect(np.zeros((720, 1280, 3), dtype=np.uint8))
    assert len(result) == 1
    assert result[0]["box"] == pytest.approx([400, 200, 600, 600])
    tensor = detector.session.run.call_args.args[1]["images"]
    assert tensor.shape == (1, 3, 640, 640)
    assert np.allclose(tensor[:, :, 0, :], 114 / 255)
    assert not np.any(tensor[:, :, 140:500, :])


def test_export_with_baked_nms_is_not_silently_misread():
    detector = YoloDetector.__new__(YoloDetector)
    detector.input = SimpleNamespace(name="images")
    detector.classes = 1
    detector.session = Mock()
    detector.session.run.return_value = [np.zeros((1, 300, 6), dtype=np.float32)]
    with pytest.raises(ValueError, match="формат"):
        detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
