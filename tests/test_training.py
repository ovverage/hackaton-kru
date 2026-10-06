import zipfile
import pytest

from shared.gaze import predict_offscreen
from training.import_bundle import extract


def test_bundle_import_rejects_escape_and_existing_destination(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("qorgau_dataset/../../escape", "bad")
    with pytest.raises(ValueError, match="Unsafe"):
        extract(archive, tmp_path / "data")
    assert not (tmp_path / "escape").exists()
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("qorgau_dataset/example.txt", "ok")
    target = extract(good, tmp_path / "data")
    assert (target / "example.txt").read_text() == "ok"
    with pytest.raises(ValueError, match="already exists"):
        extract(good, tmp_path / "data")


def test_exported_forest_uses_all_trees_and_rejects_cycles():
    tree = {
        "left": [1, -1, -1],
        "right": [2, -1, -1],
        "feature": [0, -2, -2],
        "threshold": [0.5, -2, -2],
        "off_probability": [0.5, 0.1, 0.9],
    }
    model = {"schema": 1, "trees": [tree, tree]}
    assert predict_offscreen(model, [0.2, 0, 0, 0]) == 0.1
    assert predict_offscreen(model, [0.8, 0, 0, 0]) == 0.9
    tree["left"][0] = 0
    with pytest.raises(ValueError, match="INVALID_GAZE_TREE"):
        predict_offscreen(model, [0.2, 0, 0, 0])
