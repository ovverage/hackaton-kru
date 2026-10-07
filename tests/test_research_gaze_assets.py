import hashlib
import json

import pytest

from agent import resources


def bundle(tmp_path, monkeypatch, *, research=False):
    monkeypatch.setattr(resources, 'resource_root', lambda: tmp_path)
    models = tmp_path / 'models'
    models.mkdir()
    names = resources.RUNTIME_MODELS + (resources.PUBLIC_GAZE_MODELS if research else ())
    manifest = {'files': []}
    if research:
        manifest['runtime_gaze'] = 'public-gaze-v1'
    for name in names:
        content = name.encode()
        (models / name).write_bytes(content)
        manifest['files'].append({'file': name, 'sha256': hashlib.sha256(content).hexdigest()})
    (tmp_path / 'model-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return models


def test_private_bundle_verifies_new_weights_and_metadata(tmp_path, monkeypatch):
    models = bundle(tmp_path, monkeypatch, research=True)
    assert tuple(path.name for path in resources.verified_assets()) == resources.RUNTIME_MODELS + resources.PUBLIC_GAZE_MODELS
    assert resources.verified_models() == (models/'yolo11n.onnx', models/'face_landmarker.task')
    (models/'gaze-public.json').write_text('modified', encoding='utf-8')
    with pytest.raises(ValueError, match='Повреждена модель'):
        resources.verified_assets()


def test_private_bundle_requires_both_assets(tmp_path, monkeypatch):
    models = bundle(tmp_path, monkeypatch, research=True)
    (models/'gaze-public.onnx').unlink()
    with pytest.raises(ValueError, match='проверенного комплекта'):
        resources.verified_assets()


def test_production_profile_rejects_unregistered_research_weights(tmp_path, monkeypatch):
    models = bundle(tmp_path, monkeypatch)
    assert len(resources.verified_assets()) == 4
    (models/'gaze-public.onnx').write_bytes(b'not-registered')
    with pytest.raises(ValueError, match='не зарегистрирована'):
        resources.verified_assets()
