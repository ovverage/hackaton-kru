#!/usr/bin/python3 -I
"""Root-owned SSH receiver. Install with install-cicd.sh; never run from an upload."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

ROOT = Path('/opt/qorgau')
PUBLIC_URL = 'https://212.19.134.23'
MAX_UPLOAD = 600 * 1024 * 1024
MAX_EXTRACTED = 900 * 1024 * 1024
COMMAND = re.compile(r'deploy ([0-9a-f]{40}) ([0-9a-f]{64})')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def receive(stream, destination, expected):
    digest = hashlib.sha256()
    size = 0
    with destination.open('xb') as output:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise ValueError('Upload exceeds size limit')
            output.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('Upload SHA-256 mismatch')


def extract(archive, destination):
    """Allow only regular application files, with no links or path traversal."""
    total = 0
    seen = set()
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            parts = path.parts
            allowed = (
                bool(parts) and parts[0] in {'backend', 'shared', 'deploy'}
                or parts[:2] == ('web', 'dist')
                or member.name in {'requirements-core.lock', 'pyproject.toml',
                                   'dist', 'dist/Qorgau-Student.exe'}
            )
            if (not allowed or path.is_absolute() or '..' in parts
                    or '\\' in member.name or member.name in seen
                    or not (member.isdir() or member.isfile())):
                raise ValueError(f'Unsafe archive entry: {member.name}')
            seen.add(member.name)
            total += member.size
            if total > MAX_EXTRACTED or len(seen) > 10000:
                raise ValueError('Extracted archive exceeds size limit')
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with bundle.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o644)
    for required in ('backend/proctor/app.py', 'shared/version.py',
                     'web/dist/index.html', 'requirements-core.lock'):
        if not (destination / required).is_file():
            raise ValueError(f'Missing release file: {required}')


def atomic_link(link, target):
    temporary = link.with_name(link.name + '.next')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target, target_is_directory=True)
    temporary.replace(link)


def prepare_environment(release, previous):
    requirements = release / 'requirements-core.lock'
    previous_lock = previous / 'requirements-core.lock'
    if previous_lock.is_file() and sha256(requirements) == sha256(previous_lock):
        environment = (previous / '.venv').resolve() if (previous / '.venv').exists() else ROOT / 'venv'
    else:
        environment = ROOT / 'environments' / sha256(requirements)
        environment.parent.mkdir(exist_ok=True, mode=0o755)
        if not (environment / '.ready').exists():
            # Build as an unprivileged account; uploaded code never runs as root.
            environment.mkdir(exist_ok=True, mode=0o755)
            shutil.chown(environment, 'qorgau-deploy', 'qorgau-deploy')
            run('runuser', '-u', 'qorgau-deploy', '--', '/usr/bin/python3', '-m', 'venv', str(environment))
            run('runuser', '-u', 'qorgau-deploy', '--', str(environment / 'bin/python'), '-m', 'pip',
                'install', '--disable-pip-version-check', '--only-binary=:all:', '-r', str(requirements))
            (environment / '.ready').touch()
            run('chown', '-R', 'root:root', str(environment))
    run('runuser', '-u', 'qorgau-deploy', '--', str(environment / 'bin/python'), '-I', '-m', 'pip', 'check')
    (release / '.venv').symlink_to(environment, target_is_directory=True)


def preserve_downloads(release, previous):
    downloads = release / 'dist'
    downloads.mkdir(exist_ok=True, mode=0o755)
    replaced = (downloads / 'Qorgau-Student.exe').exists()
    old_downloads = previous / 'dist'
    for old in old_downloads.iterdir():
        target = downloads / old.name
        if old.is_file() and not target.exists() and not (replaced and old.name == 'release-manifest.json'):
            os.link(old, target)
    if not (downloads / 'Qorgau-Student.exe').is_file():
        raise ValueError('No verified Windows executable available')


def healthcheck(release, attempts=18):
    expected = json.loads((release / 'web/dist/deployment.json').read_text())
    last_error = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(PUBLIC_URL + '/api/health', timeout=8) as response:
                if json.load(response).get('status') != 'ok':
                    raise ValueError('Unhealthy API')
            with urllib.request.urlopen(PUBLIC_URL + '/deployment.json', timeout=8) as response:
                if json.load(response).get('commit') != expected['commit']:
                    raise ValueError('The public website is serving a different commit')
            request = urllib.request.Request(PUBLIC_URL + '/api/student/download', headers={'Range': 'bytes=0-1'})
            with urllib.request.urlopen(request, timeout=8) as response:
                if response.status != 206 or response.read(2) != b'MZ':
                    raise ValueError('Windows download failed')
            return
        except (OSError, ValueError) as error:
            last_error = error
            time.sleep(3)
    raise RuntimeError(f'Public health check failed: {last_error}')


def activate(release, previous):
    run('systemctl', 'start', 'qorgau-backup.service')
    atomic_link(ROOT / 'current', release)
    try:
        run('systemctl', 'restart', 'qorgau.service')
        healthcheck(release)
    except BaseException:
        atomic_link(ROOT / 'current', previous)
        run('systemctl', 'restart', 'qorgau.service')
        print(f'ROLLED BACK to {previous.name}; database backup retained', flush=True)
        raise


def deploy(command, stream):
    match = COMMAND.fullmatch(command)
    if not match:
        raise ValueError('Only deploy <commit SHA> <archive SHA-256> is permitted')
    commit, checksum = match.groups()
    import fcntl
    with (ROOT / '.deploy.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = (ROOT / 'current').resolve(strict=True)
        if previous.parent != ROOT / 'releases':
            raise ValueError('Current release is outside the releases directory')
        if shutil.disk_usage(ROOT).free < MAX_EXTRACTED + 512 * 1024 * 1024:
            raise RuntimeError('Insufficient free disk space for deployment')
        incoming = ROOT / 'incoming'
        incoming.mkdir(exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=incoming) as tmp:
            archive = Path(tmp) / 'release.tar.gz'
            receive(stream, archive, checksum)
            release = ROOT / 'releases' / (time.strftime('%Y%m%dT%H%M%S', time.gmtime()) + '-' + commit[:12])
            release.mkdir(mode=0o755)
            extract(archive, release)
            preserve_downloads(release, previous)
            prepare_environment(release, previous)
            manifest = {'commit': commit, 'archive_sha256': checksum,
                        'previous_release': previous.name,
                        'student_sha256': sha256(release / 'dist/Qorgau-Student.exe')}
            (release / 'web/dist/deployment.json').write_text(json.dumps(manifest) + '\n')
            activate(release, previous)
            print(json.dumps({'status': 'deployed', 'release': release.name, **manifest}), flush=True)


if __name__ == '__main__':
    os.umask(0o022)
    if os.geteuid() != 0 or len(sys.argv) != 2:
        sys.exit('Run through the installed forced SSH command')
    deploy(sys.argv[1], sys.stdin.buffer)
