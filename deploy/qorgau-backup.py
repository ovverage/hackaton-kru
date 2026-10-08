#!/usr/bin/env python3
"""Metadata-only Qorgau snapshots; atomically remove video from owned old backups."""

from contextlib import closing
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time

POLICY = 'database_only_v1'
STATUS_FILE = 'backup-policy-status.json'
ARCHIVE_NAME = re.compile(r'qorgau-(\d{8}T\d{6}(?:\d{6})?Z)\.tar\.gz\Z')
MAX_ARCHIVE_BYTES = 4 * 1024 ** 3
MAX_EXPANDED_BYTES = 8 * 1024 ** 3
MAX_DATABASE_BYTES = 256 * 1024 ** 2
MAX_MEMBERS = 20000
MAX_ARCHIVES = 500
RESERVE_BYTES = 512 * 1024 ** 2


def is_backup_name(name):
    match = ARCHIVE_NAME.fullmatch(name)
    if not match:
        return False
    try:
        datetime.strptime(match[1], '%Y%m%dT%H%M%S%fZ' if len(match[1]) == 22 else '%Y%m%dT%H%M%SZ')
        return True
    except ValueError:
        return False


def checked_file(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & 0x400
            or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid())):
        raise ValueError('UNSAFE_BACKUP_FILE')
    if info.st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('BACKUP_SIZE_LIMIT')
    return info


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def ensure_space(folder, required):
    if shutil.disk_usage(folder).free < required + RESERVE_BYTES:
        raise RuntimeError('BACKUP_DISK_RESERVE')


def sanitize_database(snapshot):
    """Only a copied snapshot is opened writable. Preserve identity/event tables."""
    if snapshot.stat().st_size > MAX_DATABASE_BYTES:
        raise ValueError('BACKUP_DATABASE_LIMIT')
    with closing(sqlite3.connect(snapshot)) as connection:
        connection.execute('PRAGMA trusted_schema=OFF')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('BACKUP_DATABASE_INTEGRITY')
        if connection.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]:
            raise ValueError('BACKUP_DATABASE_UNEXPECTED_TRIGGER')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'media' not in tables:
            raise ValueError('BACKUP_DATABASE_SCHEMA')
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.execute('PRAGMA secure_delete=ON')
        removed = connection.execute('SELECT count(*) FROM media').fetchone()[0]
        connection.execute('DELETE FROM media')
        if 'media_lifetime' in tables:
            connection.execute('DELETE FROM media_lifetime')
        if 'events' in tables:
            for event_id, body in connection.execute('SELECT id,body FROM events').fetchall():
                event = json.loads(body)
                if not isinstance(event, dict) or not isinstance(event.get('media', []), list):
                    raise ValueError('BACKUP_EVENT_FORMAT')
                if event.get('media'):
                    safe_fields = {'id','mime','size','sha256','clip_start','clip_end','gaps','complete','uploaded_at','expires_at'}
                    if any(not isinstance(item, dict) for item in event['media']):
                        raise ValueError('BACKUP_EVENT_FORMAT')
                    metadata = [{key: value for key, value in item.items() if key in safe_fields} for item in event['media']]
                    event.update(media=[], backup_media_omitted=True, backup_media_metadata=metadata)
                    connection.execute('UPDATE events SET body=? WHERE id=?', (json.dumps(event, ensure_ascii=False, separators=(',', ':')), event_id))
        connection.commit()
        connection.execute('VACUUM')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('BACKUP_DATABASE_INTEGRITY')
    return removed


def write_archive(snapshot, destination):
    with tarfile.open(destination, 'x:gz') as archive:
        archive.add(snapshot, arcname='proctor.sqlite3')
    with destination.open('r+b') as stream:
        os.fsync(stream.fileno())
    destination.chmod(0o600)


def sanitize_archive(path, destination):
    """Fully validate an archive before atomically replacing it; never extract video."""
    initial = checked_file(path)
    with tempfile.TemporaryDirectory(prefix='.sanitize-', dir=destination) as tmp:
        temporary = Path(tmp)
        snapshot = temporary / 'proctor.sqlite3'
        seen, total, videos = set(), 0, 0
        with gzip.open(path, 'rb') as decoded, tarfile.open(fileobj=decoded, mode='r|') as archive:
            for member in archive:
                name = PurePosixPath(member.name)
                if (member.name in seen or name.is_absolute() or '..' in name.parts
                        or '\\' in member.name or member.name != name.as_posix()
                        or not member.isfile() or member.issparse()
                        or not (member.name == 'proctor.sqlite3' or
                                (len(name.parts) > 1 and name.parts[0] == 'media'))):
                    raise ValueError('UNSAFE_BACKUP_ENTRY')
                seen.add(member.name)
                total += member.size
                if member.size < 0 or total > MAX_EXPANDED_BYTES or len(seen) > MAX_MEMBERS:
                    raise ValueError('BACKUP_CONTENT_LIMIT')
                if member.name == 'proctor.sqlite3':
                    if member.size > MAX_DATABASE_BYTES:
                        raise ValueError('BACKUP_DATABASE_LIMIT')
                    ensure_space(destination, member.size * 3)
                    with archive.extractfile(member) as source, snapshot.open('xb') as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                    if snapshot.stat().st_size != member.size:
                        raise ValueError('BACKUP_DATABASE_TRUNCATED')
                else:
                    videos += 1
            # Consume the gzip footer to validate CRC/truncation before replacing anything.
            tail_bytes = 0
            while block := decoded.read(1024 * 1024):
                tail_bytes += len(block)
                if total + tail_bytes > MAX_EXPANDED_BYTES:
                    raise ValueError('BACKUP_CONTENT_LIMIT')
        if not snapshot.is_file():
            raise ValueError('BACKUP_DATABASE_MISSING')
        references = sanitize_database(snapshot)
        if identity(checked_file(path)) != identity(initial):
            raise ValueError('BACKUP_CHANGED_DURING_CHECK')
        # Rewrite even already-clean inputs, so unlisted trailing bytes cannot survive.
        replacement = temporary / 'metadata.tar.gz'
        write_archive(snapshot, replacement)
        if identity(checked_file(path)) != identity(initial):
            raise ValueError('BACKUP_CHANGED_DURING_CHECK')
        replacement.replace(path)
        return {'migrated': bool(videos or references), 'video_entries_removed': videos}


def write_status(destination, report):
    path = destination / STATUS_FILE
    if path.exists() or path.is_symlink():
        checked_file(path)
    with tempfile.NamedTemporaryFile(prefix='.policy-', dir=destination, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(json.dumps(dict(report, checked_at=time.time()), separators=(',', ':')).encode())
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def backup(data=Path('/var/lib/qorgau'), destination=Path('/var/backups/qorgau')):
    data, destination = Path(data), Path(destination)
    if destination.is_symlink() or getattr(destination, 'is_junction', lambda: False)():
        raise ValueError('UNSAFE_BACKUP_DIRECTORY')
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve(strict=True)
    destination.chmod(0o700)
    report = dict(policy=POLICY, status='pending', archives_checked=0, archives_migrated=0,
                  video_entries_removed=0, archives_retained=0, failed_archives=0)
    write_status(destination, report)
    try:
        previous = sorted((path for path in destination.iterdir() if is_backup_name(path.name)), reverse=True)
        if len(previous) > MAX_ARCHIVES:
            raise ValueError('BACKUP_ARCHIVE_COUNT_LIMIT')
        for path in previous:
            report['archives_checked'] += 1
            try:
                result = sanitize_archive(path, destination)
            except Exception:
                report['failed_archives'] += 1
                raise
            report['archives_migrated'] += int(result['migrated'])
            report['video_entries_removed'] += result['video_entries_removed']
        name = datetime.now(timezone.utc).strftime('qorgau-%Y%m%dT%H%M%S%fZ.tar.gz')
        output = destination / name
        if output.exists():
            raise ValueError('BACKUP_NAME_COLLISION')
        with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=destination) as tmp:
            snapshot = Path(tmp) / 'proctor.sqlite3'
            live = (data / 'proctor.sqlite3').resolve(strict=True)
            with closing(sqlite3.connect(live.as_uri() + '?mode=ro', uri=True)) as source:
                size = source.execute('PRAGMA page_count').fetchone()[0] * source.execute('PRAGMA page_size').fetchone()[0]
                if size > MAX_DATABASE_BYTES:
                    raise ValueError('BACKUP_DATABASE_LIMIT')
                ensure_space(destination, size * 3)
                with closing(sqlite3.connect(snapshot)) as target:
                    source.backup(target)
            sanitize_database(snapshot)
            pending = Path(tmp) / 'new.tar.gz'
            write_archive(snapshot, pending)
            pending.replace(output)
        # Only exact owned timestamp names, all already validated, participate in rotation.
        retained = sorted([*previous, output], reverse=True)
        for old in retained[7:]:
            checked_file(old)
            old.unlink()
        report.update(status='complete', archives_retained=min(7, len(retained)))
    except Exception:
        report['status'] = 'error'
        write_status(destination, report)
        print(json.dumps(report), flush=True)
        raise
    write_status(destination, report)
    print(json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    os.umask(0o077)
    backup()
