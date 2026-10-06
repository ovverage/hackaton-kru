import hashlib
import io
import json
from unittest.mock import patch

import pytest

from scripts.prepare_models import RUNTIME_FILES, prepare


def test_bad_download_does_not_replace_existing_model(tmp_path):
    directory = tmp_path / "models"
    directory.mkdir()
    (directory / RUNTIME_FILES[0]).write_bytes(b"old model")
    manifest = {
        "files": [
            {
                "file": name,
                "sha256": hashlib.sha256(b"correct").hexdigest(),
                "url": "https://models.example/" + name,
            }
            for name in RUNTIME_FILES
        ]
    }
    (tmp_path / "model-manifest.json").write_text(json.dumps(manifest))
    with patch(
        "urllib.request.urlopen", return_value=io.BytesIO(b"corrupted download")
    ):
        with pytest.raises(ValueError, match="DOWNLOAD_HASH_MISMATCH"):
            prepare(tmp_path)
    assert (directory / RUNTIME_FILES[0]).read_bytes() == b"old model"
    assert not list(directory.glob("*.partial"))


def test_verified_models_are_usable_without_network(tmp_path):
    directory = tmp_path / "models"
    directory.mkdir()
    rows = []
    for name in RUNTIME_FILES:
        data = name.encode()
        (directory / name).write_bytes(data)
        rows.append({"file": name, "sha256": hashlib.sha256(data).hexdigest()})
    (tmp_path / "model-manifest.json").write_text(json.dumps({"files": rows}))
    with patch("urllib.request.urlopen", side_effect=AssertionError("No network")):
        assert len(prepare(tmp_path, verify_only=True)) == 4
