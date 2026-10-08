import hashlib
import io
import json

import pytest

from scripts import prepare_teacher_faces as script


def manifest(tmp_path):
    entries = [{'file': name, 'url': 'https://fixtures.test/' + name, 'sha256': hashlib.sha256(name.encode()).hexdigest()} for name in ['detector.onnx', 'recognizer.onnx', 'license.txt']]
    (tmp_path / 'teacher-face-manifest.json').write_text(json.dumps({'files': entries[:2], 'licenses': entries[2:]}))
    return entries


def test_clean_download_verifies_every_model_and_license(tmp_path, monkeypatch):
    entries = manifest(tmp_path)
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return io.BytesIO(url.rsplit('/',1)[1].encode())
    monkeypatch.setattr(script.urllib.request, 'urlopen', fetch)
    result = script.prepare(tmp_path)
    assert len(result) == 3 and len(calls) == 3
    assert script.prepare(tmp_path, verify_only=True) == result
    assert len(calls) == 3
    (tmp_path / 'models/teacher-faces' / entries[1]['file']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='HASH_MISMATCH:recognizer'):
        script.prepare(tmp_path, verify_only=True)


def test_bad_download_does_not_publish_or_leave_partial(tmp_path, monkeypatch):
    manifest(tmp_path)
    monkeypatch.setattr(script.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(b'bad bytes'))
    with pytest.raises(ValueError, match='DOWNLOAD_HASH_MISMATCH'):
        script.prepare(tmp_path)
    assert not list((tmp_path / 'models/teacher-faces').iterdir())


def test_missing_assets_fail_verify_only_without_network(tmp_path, monkeypatch):
    manifest(tmp_path)
    monkeypatch.setattr(script.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('No download during verification'))
    with pytest.raises(ValueError, match='HASH_MISMATCH:detector'):
        script.prepare(tmp_path, verify_only=True)
