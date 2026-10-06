import json

import pytest

from scripts.train_phone import validate_dataset


def dataset(tmp_path):
    items = []
    for session, split in (("S01", "train"), ("S02", "val"), ("S03", "test")):
        for i in range(20):
            stem = f"{session}-{i}"
            (tmp_path / f"{stem}.jpg").write_bytes(stem.encode())
            (tmp_path / f"{stem}.txt").write_text("0 0.5 0.5 0.2 0.4\n")
            items.append({"subject": "P01", "session": session, "split": split,
                          "consent": True, "image": stem + ".jpg", "labels": stem + ".txt"})
    return {"split_policy": "session", "items": items}


def check(tmp_path, data):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return validate_dataset(path)


def test_one_person_requires_explicit_independent_session_split(tmp_path):
    data = dataset(tmp_path)
    _, counts = check(tmp_path, data)
    assert counts == {"train": 20, "val": 20, "test": 20}
    del data["split_policy"]
    with pytest.raises(ValueError, match="DATA_LEAKAGE"):
        check(tmp_path, data)


@pytest.mark.parametrize("leak", ["session", "copy", "video"])
def test_sessions_cannot_hide_data_leakage(tmp_path, leak):
    data = dataset(tmp_path)
    a, b = data["items"][0], data["items"][20]
    if leak == "session":
        b["session"] = a["session"]
    elif leak == "copy":
        b["image"] = a["image"]
    else:
        a["source_video"] = b["source_video"] = "same-recording.mp4"
    with pytest.raises(ValueError, match="DATA_LEAKAGE"):
        check(tmp_path, data)
