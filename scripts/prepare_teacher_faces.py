"""Fetch separate hash-pinned teacher face models and their upstream notices."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def prepare(root=ROOT, *, verify_only=False, folder=None):
    manifest = json.loads((root / 'teacher-face-manifest.json').read_text(encoding='utf-8'))
    folder = Path(folder) if folder is not None else root / 'models' / 'teacher-faces'
    folder.mkdir(parents=True, exist_ok=True)
    verified = []
    for entry in manifest['files'] + manifest['licenses']:
        name = entry['file']
        if Path(name).name != name or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            raise ValueError('INVALID_TEACHER_FACE_MANIFEST')
        path = folder / name
        def valid():
            if not path.is_file():
                return False
            with path.open('rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256']
        if not valid():
            if verify_only:
                raise ValueError(f'TEACHER_FACE_HASH_MISMATCH:{name}')
            if not entry['url'].startswith('https://'):
                raise ValueError('HTTPS_REQUIRED')
            partial = path.with_suffix(path.suffix + '.partial')
            try:
                with urllib.request.urlopen(entry['url'], timeout=120) as source, partial.open('wb') as dest:
                    total = 0
                    while block := source.read(1024 * 1024):
                        total += len(block)
                        if total > 64 * 1024 * 1024:
                            raise ValueError('TEACHER_FACE_DOWNLOAD_TOO_LARGE')
                        dest.write(block)
                with partial.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != entry['sha256']:
                        raise ValueError(f'TEACHER_FACE_DOWNLOAD_HASH_MISMATCH:{name}')
                partial.replace(path)
            finally:
                partial.unlink(missing_ok=True)
        verified.append({'file': name, 'sha256': entry['sha256'], 'bytes': path.stat().st_size})
    return verified


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--folder', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(verify_only=args.verify_only, folder=args.folder), indent=2))
