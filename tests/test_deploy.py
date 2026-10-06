"""Deployment must reject unsafe input and restore production after failed startup."""

import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location('apply_release', Path(__file__).parents[1] / 'deploy/apply_release.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


def bundle(tmp_path, extra=()):
    path = tmp_path / 'release.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        for name in ('backend/proctor/app.py', 'shared/version.py', 'web/dist/index.html', 'requirements-core.lock'):
            item = tarfile.TarInfo(name)
            item.size = 2
            archive.addfile(item, io.BytesIO(b'ok'))
        for item in extra:
            archive.addfile(item, io.BytesIO(b'x' * item.size) if item.isfile() else None)
    return path


def test_extract_valid_release(tmp_path):
    target = tmp_path / 'extracted'
    target.mkdir()
    deployment.extract(bundle(tmp_path), target)
    assert (target / 'web/dist/index.html').read_bytes() == b'ok'


@pytest.mark.parametrize('name,kind', [
    ('backend/../../escape', tarfile.REGTYPE),
    ('/etc/passwd', tarfile.REGTYPE),
    ('backend/link', tarfile.SYMTYPE),
    ('backend/link', tarfile.LNKTYPE),
    ('backend/device', tarfile.CHRTYPE),
    ('server.env', tarfile.REGTYPE),
    ('backend/proctor/app.py', tarfile.REGTYPE),
])
def test_extract_rejects_unsafe_entries(tmp_path, name, kind):
    item = tarfile.TarInfo(name)
    item.type = kind
    item.linkname = '/etc/passwd'
    target = tmp_path / 'extracted'
    target.mkdir()
    with pytest.raises(ValueError):
        deployment.extract(bundle(tmp_path, [item]), target)
    assert not (tmp_path / 'escape').exists()


def test_receive_requires_matching_checksum(tmp_path):
    data = b'archive'
    destination = tmp_path / 'upload'
    deployment.receive(io.BytesIO(data), destination, hashlib.sha256(data).hexdigest())
    assert destination.read_bytes() == data
    with pytest.raises(ValueError, match='SHA-256'):
        deployment.receive(io.BytesIO(data), tmp_path / 'bad-upload', '0' * 64)


def test_receive_stops_oversized_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(deployment, 'MAX_UPLOAD', 3)
    with pytest.raises(ValueError, match='size limit'):
        deployment.receive(io.BytesIO(b'too large'), tmp_path / 'upload', '0' * 64)


@pytest.mark.parametrize('command', ['id', 'deploy ../main abc', 'deploy ' + 'a' * 40 + ' ' + 'b' * 64 + '; id'])
def test_forced_command_rejects_shell_commands(command):
    with pytest.raises(ValueError, match='Only deploy'):
        deployment.deploy(command, io.BytesIO())


def test_failed_health_check_restores_previous_release(monkeypatch, tmp_path):
    previous, release = tmp_path / 'old', tmp_path / 'new'
    calls = []
    monkeypatch.setattr(deployment, 'ROOT', tmp_path)
    monkeypatch.setattr(deployment, 'run', lambda *args: calls.append(args))
    monkeypatch.setattr(deployment, 'atomic_link', lambda link, target: calls.append(('link', link, target)))

    def fail(_):
        raise RuntimeError('unhealthy')

    monkeypatch.setattr(deployment, 'healthcheck', fail)
    with pytest.raises(RuntimeError, match='unhealthy'):
        deployment.activate(release, previous)
    assert calls == [
        ('systemctl', 'start', 'qorgau-backup.service'),
        ('link', tmp_path / 'current', release),
        ('systemctl', 'restart', 'qorgau.service'),
        ('link', tmp_path / 'current', previous),
        ('systemctl', 'restart', 'qorgau.service'),
    ]


def test_backup_failure_does_not_switch_release(monkeypatch, tmp_path):
    def fail(*args):
        raise RuntimeError('backup failed')

    monkeypatch.setattr(deployment, 'run', fail)
    monkeypatch.setattr(deployment, 'atomic_link', lambda *args: pytest.fail('must not activate without a backup'))
    with pytest.raises(RuntimeError, match='backup failed'):
        deployment.activate(tmp_path / 'new', tmp_path / 'old')


def test_server_only_release_preserves_existing_installer(tmp_path):
    previous, release = tmp_path / 'old', tmp_path / 'new'
    (previous / 'dist').mkdir(parents=True)
    release.mkdir()
    (previous / 'dist/Qorgau-Student.exe').write_bytes(b'MZ-old')
    deployment.preserve_downloads(release, previous)
    assert (release / 'dist/Qorgau-Student.exe').read_bytes() == b'MZ-old'


def test_windows_release_replaces_exe_without_modifying_previous(tmp_path):
    previous, release = tmp_path / 'old', tmp_path / 'new'
    for directory in (previous, release):
        (directory / 'dist').mkdir(parents=True)
    (previous / 'dist/Qorgau-Student.exe').write_bytes(b'MZ-old')
    (previous / 'dist/release-manifest.json').write_text('old checksums')
    (release / 'dist/Qorgau-Student.exe').write_bytes(b'MZ-new')
    deployment.preserve_downloads(release, previous)
    assert (previous / 'dist/Qorgau-Student.exe').read_bytes() == b'MZ-old'
    assert (release / 'dist/Qorgau-Student.exe').read_bytes() == b'MZ-new'
    assert not (release / 'dist/release-manifest.json').exists()
