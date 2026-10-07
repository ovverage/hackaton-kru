"""Clean builds fetch every model selected by the manifest runtime profile."""

import hashlib
import io
import json
from unittest.mock import patch

import pytest

from scripts.prepare_models import PUBLIC_GAZE_FILES, RUNTIME_FILES, prepare


def manifest(root, *, profile="public-gaze-v1", present=False):
    files = RUNTIME_FILES + PUBLIC_GAZE_FILES
    bodies = {name: ("pinned model fixture: " + name).encode() for name in files}
    document = {"runtime_gaze": profile, "files": [
        {"file": name, "sha256": hashlib.sha256(body).hexdigest(),
         "url": "https://models.example/" + name}
        for name, body in bodies.items()
    ]}
    (root / "model-manifest.json").write_text(json.dumps(document), encoding="utf-8")
    if present:
        folder = root / "models"
        folder.mkdir()
        for name, body in bodies.items():
            (folder / name).write_bytes(body)
    return document, bodies


def test_clean_public_profile_downloads_and_checks_all_six_files(tmp_path):
    _, bodies = manifest(tmp_path)

    def download(url, timeout):
        assert timeout == 120
        return io.BytesIO(bodies[url.removeprefix("https://models.example/")])

    with patch("urllib.request.urlopen", side_effect=download) as fetch:
        result = prepare(tmp_path)
    assert [item["file"] for item in result] == list(RUNTIME_FILES + PUBLIC_GAZE_FILES)
    assert fetch.call_count == 6
    for item in result:
        assert item == {"file": item["file"], "bytes": len(bodies[item["file"]]),
                        "sha256": hashlib.sha256(bodies[item["file"]]).hexdigest()}
        assert (tmp_path / "models" / item["file"]).read_bytes() == bodies[item["file"]]
    with patch("urllib.request.urlopen", side_effect=AssertionError("No network during verification")):
        assert prepare(tmp_path, verify_only=True) == result


@pytest.mark.parametrize("name", PUBLIC_GAZE_FILES)
@pytest.mark.parametrize("damage", ["missing", "wrong_hash"])
def test_verify_only_rejects_missing_or_modified_public_pair(tmp_path, name, damage):
    manifest(tmp_path, present=True)
    path = tmp_path / "models" / name
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"changed")
    with patch("urllib.request.urlopen", side_effect=AssertionError("No network during verification")):
        with pytest.raises(ValueError, match="MODEL_HASH_MISMATCH: " + name):
            prepare(tmp_path, verify_only=True)


@pytest.mark.parametrize("name", PUBLIC_GAZE_FILES)
@pytest.mark.parametrize("damage", ["entry_missing", "hash_invalid"])
def test_public_profile_requires_both_hash_pinned_manifest_entries(tmp_path, name, damage):
    document, _ = manifest(tmp_path, present=True)
    if damage == "entry_missing":
        document["files"] = [entry for entry in document["files"] if entry["file"] != name]
    else:
        next(entry for entry in document["files"] if entry["file"] == name)["sha256"] = "not a sha256"
    (tmp_path / "model-manifest.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="Missing pinned model: " + name):
        prepare(tmp_path, verify_only=True)


@pytest.mark.parametrize("profile", ["public-gaze-v2", "PUBLIC-GAZE-V1", "", None, 3])
def test_unknown_runtime_profile_fails_before_network_or_model_directory_creation(tmp_path, profile):
    manifest(tmp_path, profile=profile)
    with patch("urllib.request.urlopen", side_effect=AssertionError("Unknown profile must not fetch")):
        with pytest.raises(ValueError, match="UNKNOWN_RUNTIME_GAZE_PROFILE"):
            prepare(tmp_path)
    assert not (tmp_path / "models").exists()


@pytest.mark.parametrize("name", PUBLIC_GAZE_FILES)
def test_wrong_public_download_never_replaces_existing_file(tmp_path, name):
    manifest(tmp_path, present=True)
    path = tmp_path / "models" / name
    path.write_bytes(b"previous file")
    with patch("urllib.request.urlopen", return_value=io.BytesIO(b"wrong downloaded bytes")):
        with pytest.raises(ValueError, match="DOWNLOAD_HASH_MISMATCH: " + name):
            prepare(tmp_path)
    assert path.read_bytes() == b"previous file"
    assert not path.with_suffix(path.suffix + ".partial").exists()


@pytest.mark.parametrize("explicit_profile", [False, True])
def test_legacy_profile_still_selects_only_original_four_files(tmp_path, explicit_profile):
    document, bodies = manifest(tmp_path, profile="legacy", present=True)
    if not explicit_profile:
        del document["runtime_gaze"]
        (tmp_path / "model-manifest.json").write_text(json.dumps(document), encoding="utf-8")
    for name in PUBLIC_GAZE_FILES:
        (tmp_path / "models" / name).unlink()
    with patch("urllib.request.urlopen", side_effect=AssertionError("Legacy profile must not fetch gaze pair")):
        result = prepare(tmp_path, verify_only=True)
    assert [entry["file"] for entry in result] == list(RUNTIME_FILES)
    assert all(entry["bytes"] == len(bodies[entry["file"]]) for entry in result)


@pytest.mark.parametrize("name", PUBLIC_GAZE_FILES)
def test_public_download_still_requires_https_source(tmp_path, name):
    document, _ = manifest(tmp_path, present=True)
    (tmp_path / "models" / name).unlink()
    next(entry for entry in document["files"] if entry["file"] == name)["url"] = "http://models.example/" + name
    (tmp_path / "model-manifest.json").write_text(json.dumps(document), encoding="utf-8")
    with patch("urllib.request.urlopen", side_effect=AssertionError("Do not request insecure URL")):
        with pytest.raises(ValueError, match="An HTTPS artifact URL is required for " + name):
            prepare(tmp_path)
