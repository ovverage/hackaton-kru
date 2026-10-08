"""Exercise the real server face runtime in an isolated backend-only release layout.

Uses synthetic blank pixels only: no camera, teacher photos, or production data.
Run with the server's core lock installed, without the desktop OpenCV package.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
import cv2
import numpy as np
from fastapi.testclient import TestClient
from backend.proctor.app import create_app, ROOT
from shared.teacher_faces import TeacherFaceEngine

root = Path(sys.argv[1]).resolve()
assert ROOT.resolve() == root
assert not (root / 'teacher-face-manifest.json').exists()
assert not (root / 'models').exists()
assert not (root / 'agent').exists()
app = create_app(root / 'probe-db')
with TestClient(app) as client:
    client.headers['X-Requested-With'] = 'Qorgau'
    created = client.post('/api/auth/setup', json={'name': 'Runtime probe', 'password': 'ephemeral-probe-123'})
    assert created.status_code == 200, created.text
    ok, encoded = cv2.imencode('.jpg', np.zeros((480, 640, 3), np.uint8))
    assert ok
    response = client.post('/api/teacher-faces', data={'name': 'Synthetic blank'},
        files={'image': ('blank.jpg', encoded.tobytes(), 'image/jpeg')})
    assert response.status_code == 422, response.text
    assert 'FACE_EXACTLY_ONE_REQUIRED' in response.json()['detail'], response.text
    engine = app.state.teacher_face_engine
    assert isinstance(engine, TeacherFaceEngine)
    assert engine.detect(np.zeros((480, 640, 3), np.uint8)) == []
    feature = engine.recognizer.feature(np.zeros((112, 112, 3), np.uint8))
    assert feature.shape == (1, 128) and np.isfinite(feature).all()
    assert client.get('/api/teacher-faces').json()['faces'] == []
    assert not list((root / 'probe-db/media').iterdir())
print(json.dumps({'status': 'passed', 'python': sys.version.split()[0],
    'platform': sys.platform, 'opencv': cv2.__version__, 'numpy': np.__version__,
    'backend_only_layout': True, 'fallback_manifest_loaded': True,
    'blank_image_status': response.status_code, 'face_descriptor_shape': list(feature.shape),
    'camera_opened': False, 'teacher_photos_used': False}))
'''


def verify(models_dir=None, *, root=ROOT):
    models_dir = Path(models_dir) if models_dir else root / 'backend/proctor/assets/teacher-faces'
    manifest_path = root / 'teacher-face-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    hashes = {}
    for item in manifest['files'] + manifest['licenses']:
        name = item['file']
        if Path(name).name != name:
            raise ValueError('TEACHER_FACE_MANIFEST_PATH')
        with (models_dir / name).open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != item['sha256']:
            raise ValueError(f'TEACHER_FACE_HASH_MISMATCH:{name}')
        hashes[name] = actual
    with tempfile.TemporaryDirectory(prefix='qorgau-server-face-') as temporary:
        release = Path(temporary).resolve()
        for directory in ('backend', 'shared'):
            shutil.copytree(root / directory, release / directory,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'assets'))
        assets = release / 'backend/proctor/assets/teacher-faces'
        assets.mkdir(parents=True)
        for name in hashes:
            shutil.copyfile(models_dir / name, assets / name)
        shutil.copyfile(manifest_path, assets / manifest_path.name)
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        env.pop('PROCTOR_TEACHER_FACE_MODELS', None)
        env.update(PROCTOR_DATA=str(release / 'default-db'), PROCTOR_SECURE_COOKIE='0')
        result = subprocess.run([sys.executable, '-I', '-c', PROBE, str(release)],
                                cwd=release, env=env, text=True, capture_output=True, timeout=90)
        if result.returncode:
            raise RuntimeError('Server face runtime failed:\n' + result.stdout + result.stderr)
        report = json.loads(result.stdout.strip())
    report['asset_sha256'] = hashes
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = verify(args.models_dir)
    text = json.dumps(report, indent=2)
    if args.report:
        args.report.write_text(text + '\n', encoding='utf-8')
    print(text)
