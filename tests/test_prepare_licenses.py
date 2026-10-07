"""Clean packaging retains research conditions only for the selected runtime."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import prepare_licenses


def notice_sources(root):
    folder = root / "docs/licenses"
    folder.mkdir(parents=True)
    bodies = {
        name: ("Retained license and citation: " + name + "\n").encode()
        for name in prepare_licenses.RESEARCH_NOTICES
    }
    for name, body in bodies.items():
        (folder / name).write_bytes(body)
    # This must never enter the package, even when located next to notices.
    (folder / "private-training-data.json").write_text("not a notice", encoding="utf-8")
    return bodies


def test_public_profile_packages_only_tracked_notices_without_private_data(tmp_path):
    bodies = notice_sources(tmp_path)
    output = tmp_path / "dist/third-party"
    assert not (tmp_path / "data").exists()
    copied = prepare_licenses.copy_research_notices(
        tmp_path, output, {"runtime_gaze": "public-gaze-v1"}
    )
    assert set(copied) == set(bodies)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == bodies


@pytest.mark.parametrize("manifest", [{}, {"runtime_gaze": "legacy"}])
def test_legacy_profile_clears_only_stale_research_notices(tmp_path, manifest):
    output = tmp_path / "dist/third-party"
    output.mkdir(parents=True)
    for name in prepare_licenses.RESEARCH_NOTICES:
        (output / name).write_text("previous research build", encoding="utf-8")
    retained = output / "MediaPipe-Apache-2.0.txt"
    retained.write_text("unrelated existing notice", encoding="utf-8")
    assert prepare_licenses.copy_research_notices(tmp_path, output, manifest) == []
    assert list(output.iterdir()) == [retained]
    assert retained.read_text(encoding="utf-8") == "unrelated existing notice"


def test_missing_public_notice_aborts_before_partial_notice_package(tmp_path):
    notice_sources(tmp_path)
    name = "MPIIFaceGaze-CC-BY-NC-SA-4.0.txt"
    (tmp_path / "docs/licenses" / name).unlink()
    output = tmp_path / "dist/third-party"
    with pytest.raises(ValueError, match="MISSING_RESEARCH_NOTICE: " + name):
        prepare_licenses.copy_research_notices(
            tmp_path, output, {"runtime_gaze": "public-gaze-v1"}
        )
    assert not output.exists()


@pytest.mark.parametrize("profile", [None, "", "public-gaze-v2", "PUBLIC-GAZE-V1"])
def test_unknown_profile_cannot_silently_omit_research_notices(tmp_path, profile):
    output = tmp_path / "dist/third-party"
    with pytest.raises(ValueError, match="UNKNOWN_RUNTIME_GAZE_PROFILE"):
        prepare_licenses.copy_research_notices(tmp_path, output, {"runtime_gaze": profile})
    assert not output.exists()


def test_main_wires_research_notices_and_manifest_into_clean_build(tmp_path, monkeypatch):
    bodies = notice_sources(tmp_path)
    manifest = {"runtime_gaze": "public-gaze-v1", "files": []}
    (tmp_path / "model-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text("All component notices", encoding="utf-8")
    monkeypatch.setattr(prepare_licenses.metadata, "distributions", lambda: [])
    monkeypatch.setattr(prepare_licenses, "ffmpeg_executable", lambda: "ffmpeg-fixture")
    monkeypatch.setattr(prepare_licenses.subprocess, "check_output", lambda *a, **kw: "FFmpeg terms")
    requests = []

    def get(url, **kwargs):
        requests.append(url)
        return SimpleNamespace(text="Unrelated upstream notice", raise_for_status=lambda: None)

    monkeypatch.setattr(prepare_licenses.httpx, "get", get)
    prepare_licenses.main(tmp_path)
    output = tmp_path / "dist/third-party"
    for name, body in bodies.items():
        assert (output / name).read_bytes() == body
    assert json.loads((output / "model-manifest.json").read_text(encoding="utf-8")) == manifest
    assert (output / "README.md").read_text(encoding="utf-8") == "All component notices"
    assert (output / "FFmpeg-build-and-license.txt").is_file()
    assert not any("gaze" in url.lower() for url in requests)
    assert not (output / "private-training-data.json").exists()


def test_checked_in_notice_set_contains_full_research_terms_and_attribution(tmp_path):
    root = Path(__file__).resolve().parents[1]
    prepare_licenses.copy_research_notices(root, tmp_path, {"runtime_gaze": "public-gaze-v1"})
    gaze_license = (tmp_path / "Gaze360-Research-License.md").read_text(encoding="utf-8")
    mpii_license = (tmp_path / "MPIIFaceGaze-CC-BY-NC-SA-4.0.txt").read_text(encoding="utf-8")
    assert "LICENSE AGREEMENT FOR USE OF GAZE360 DATABASE AND MODELS" in gaze_license
    assert "Section 6" in gaze_license
    assert "models trained on dataset" in gaze_license
    assert "Attribution-NonCommercial-ShareAlike 4.0 International" in mpii_license
    assert "Section 8" in mpii_license
    citations = (tmp_path / "Research-Gaze-Notice.md").read_text(encoding="utf-8")
    assert "10.1109/ICCV.2019.00701" in citations
    assert "10.1109/CVPRW.2017.284" in citations
    assert "10.18419/DARUS-3240" in citations
