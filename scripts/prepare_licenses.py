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


RESEARCH_NOTICES = (
    'Gaze360-Research-License.md',
    'Gaze360-Dataset-Citation.md',
    'MPIIFaceGaze-CC-BY-NC-SA-4.0.txt',
    'MPIIFaceGaze-Attribution.md',
    'Research-Gaze-Notice.md',
)


def copy_research_notices(root, output, manifest):
    """Package tracked research terms without fetching data or granting rights."""
    profile = manifest.get('runtime_gaze', 'legacy')
    if profile not in ('legacy', 'public-gaze-v1'):
        raise ValueError(f'UNKNOWN_RUNTIME_GAZE_PROFILE: {profile}')
    if profile == 'legacy':
        # Avoid carrying these notices into a later legacy build in the same dist.
        for name in RESEARCH_NOTICES:
            (output / name).unlink(missing_ok=True)
        return []
    sources = [root / 'docs/licenses' / name for name in RESEARCH_NOTICES]
    for source in sources:
        if not source.is_file():
            raise ValueError(f'MISSING_RESEARCH_NOTICE: {source.name}')
    output.mkdir(parents=True, exist_ok=True)
    for source in sources:
        shutil.copyfile(source, output / source.name)
    return list(RESEARCH_NOTICES)


def main(root=ROOT):
    manifest = json.loads((root / 'model-manifest.json').read_text(encoding='utf-8'))
    output = root / 'dist/third-party'
    copy_research_notices(root, output, manifest)
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
    shutil.copyfile(root / 'THIRD_PARTY_NOTICES.md', output / 'README.md')
    shutil.copyfile(root / 'model-manifest.json', output / 'model-manifest.json')
    print(f'Notices collected for {len(packages)} installed distributions')


if __name__ == '__main__':
    main()
