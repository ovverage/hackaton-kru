#!/usr/bin/env python3
"""Consistent SQLite snapshot and the media referenced by that snapshot.

Backups remain on this host. Copy them off-host separately for disaster recovery.
Only this application's dated backup files are pruned, after a successful backup.
"""

import json
import os
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone


def backup(data=Path('/var/lib/qorgau'), destination=Path('/var/backups/qorgau')):
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)
    name = datetime.now(timezone.utc).strftime('qorgau-%Y%m%dT%H%M%S%fZ.tar.gz')
    output = destination / name
    partial = output.with_suffix(output.suffix + '.partial')
    with tempfile.TemporaryDirectory(prefix='.snapshot-', dir=destination) as tmp:
        snapshot = Path(tmp) / 'proctor.sqlite3'
        with closing(sqlite3.connect(f'file:{data / "proctor.sqlite3"}?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
        with closing(sqlite3.connect(snapshot)) as connection:
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Snapshot integrity check failed')
            media = [Path(row[0]).resolve() for row in connection.execute('SELECT path FROM media')]
        media_root = (data / 'media').resolve()
        for file in media:
            if not file.is_relative_to(media_root) or not file.is_file():
                raise RuntimeError('Missing media or media path outside the application directory')
        required = snapshot.stat().st_size + sum(file.stat().st_size for file in media)
        if shutil.disk_usage(destination).free < required + 512 * 1024 * 1024:
            raise RuntimeError('Insufficient disk space for backup plus 512 MiB reserve')
        try:
            with tarfile.open(partial, 'w:gz') as archive:
                archive.add(snapshot, arcname='proctor.sqlite3')
                for file in media:
                    archive.add(file, arcname='media/' + str(file.relative_to(media_root)))
            partial.replace(output)
            output.chmod(0o600)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    for old in sorted(destination.glob('qorgau-*.tar.gz'), reverse=True)[7:]:
        if old.is_file() and old.parent.resolve() == destination.resolve():
            old.unlink()
    print(json.dumps({'backup': str(output), 'bytes': output.stat().st_size, 'media': len(media)}))


if __name__ == '__main__':
    os.umask(0o077)
    backup()
