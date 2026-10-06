"""Collect actual installed distribution notices alongside exact model sources."""
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.resources import ffmpeg_executable


def main():
    output = ROOT / 'dist/third-party'
    output.mkdir(parents=True, exist_ok=True)
    packages = []
    for distribution in metadata.distributions():
        name = distribution.metadata['Name']
        if name.lower().startswith('qostanai'):
            continue
        files = []
        for relative in distribution.files or []:
            lowered = str(relative).lower()
            if '..' in relative.parts or not any(x in lowered for x in ('license', 'copying', 'notice')):
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file() or source.suffix.lower() in ('.py', '.pyc', '.exe', '.dll', '.pyd'):
                continue
            target = output / 'packages' / name / str(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            files.append(str(target.relative_to(output)))
        packages.append({'name': name, 'version': distribution.version, 'notices': files,
                         'metadata_license': distribution.metadata.get('License-Expression') or distribution.metadata.get('License')})
    for name, url in (
        ('YOLO-AGPL-3.0.txt', 'https://raw.githubusercontent.com/ultralytics/ultralytics/v8.3.221/LICENSE'),
        ('MediaPipe-Apache-2.0.txt', 'https://raw.githubusercontent.com/google-ai-edge/mediapipe/v0.10.32/LICENSE'),
        ('Bingsu-adetailer-model-card.md', 'https://huggingface.co/Bingsu/adetailer/raw/53cc19de382014514d9d4038601d261a7faa9b7b/README.md')):
        response = httpx.get(url, follow_redirects=True, timeout=30)
        response.raise_for_status()
        (output / name).write_text(response.text, encoding='utf-8')
    executable = ffmpeg_executable()
    lines = [subprocess.check_output([executable, argument], stderr=subprocess.STDOUT, text=True)
             for argument in ('-version', '-L')]
    (output / 'FFmpeg-build-and-license.txt').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'package-inventory.json').write_text(json.dumps(packages, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copyfile(ROOT / 'THIRD_PARTY_NOTICES.md', output / 'README.md')
    shutil.copyfile(ROOT / 'model-manifest.json', output / 'model-manifest.json')
    print(f'Notices collected for {len(packages)} installed distributions')


if __name__ == '__main__':
    main()
