import gzip
import hashlib
import importlib.util
import io
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tarfile

import pytest

from backend.proctor.backup_status import public_backup_status
from backend.proctor.db import Database, decode, encode

spec = importlib.util.spec_from_file_location('backup_module', Path(__file__).resolve().parents[1] / 'deploy/qorgau-backup.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def seeded(tmp_path):
    data = tmp_path / 'data'
    db = Database(data / 'proctor.sqlite3')
    (data / 'media').mkdir()
    clip = data / 'media/clip.mp4'
    clip.write_bytes(b'private fixture video')
    with db.connect(True) as c:
        c.execute('INSERT INTO users VALUES(?,?,?)', ('teacher1', 'Teacher', 'argon-fixture'))
        c.execute('INSERT INTO teacher_faces VALUES(?,?,?,?,?,?)', ('face1', 'teacher1', 'Teacher', '[1,2,3]', 'model1', 1))
        c.execute('INSERT INTO events VALUES(?,?,?,?)', ('event1', 'exam1', 'pc1', encode({'id':'event1','created_at':1,'decision':'CONFIRMED','type':'PHONE_DETECTED','media':[{'id':'clip1','url':'/api/media/clip1','sha256':'abc','clip_start':0,'clip_end':2}]})))
        c.execute('INSERT INTO media VALUES(?,?,?,?,?)', ('clip1','event1','pc1',str(clip),'video/mp4'))
        c.execute('INSERT INTO media_lifetime VALUES(?,?,?)', ('clip1',1,7201))
    return data, db, clip


def archive_database(path):
    with tarfile.open(path) as archive:
        assert archive.getnames() == ['proctor.sqlite3']
        return archive.extractfile('proctor.sqlite3').read()


def verify_database_bytes(content, tmp_path):
    restored = tmp_path / 'restored.sqlite3'
    restored.write_bytes(content)
    with closing(sqlite3.connect(restored)) as c:
        c.row_factory = sqlite3.Row
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert tuple(c.execute('SELECT * FROM users').fetchone()) == ('teacher1','Teacher','argon-fixture')
        assert c.execute('SELECT embedding FROM teacher_faces').fetchone()[0] == '[1,2,3]'
        assert c.execute('SELECT count(*) FROM media').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM media_lifetime').fetchone()[0] == 0
        event = decode(c.execute('SELECT body FROM events').fetchone())
        assert event['decision'] == 'CONFIRMED' and event['type'] == 'PHONE_DETECTED'
        assert event['media'] == [] and event['backup_media_omitted'] is True
        assert event['backup_media_metadata'] == [{'id':'clip1','sha256':'abc','clip_start':0,'clip_end':2}]


def old_archive(destination, db, clip, *, name='qorgau-20260101T010101000001Z.tar.gz', extra=None, contents=None):
    destination.mkdir(exist_ok=True)
    path = destination / name
    snapshot = destination / 'fixture.sqlite3'
    with closing(sqlite3.connect(db.path)) as live, closing(sqlite3.connect(snapshot)) as copy:
        live.backup(copy)
    with tarfile.open(path, 'w:gz') as archive:
        if contents is None:
            archive.add(snapshot, arcname='proctor.sqlite3')
        else:
            item = tarfile.TarInfo('proctor.sqlite3')
            item.size = len(contents)
            archive.addfile(item,io.BytesIO(contents))
        archive.add(clip, arcname='media/clip.mp4')
        if extra:
            archive.addfile(extra, io.BytesIO(b'X' * extra.size) if extra.isfile() else None)
    snapshot.unlink()
    return path


def test_new_backup_preserves_committed_wal_identity_and_events_without_video(tmp_path):
    data, db, clip = seeded(tmp_path)
    destination = tmp_path / 'backups'
    with closing(sqlite3.connect(db.path)) as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute("INSERT INTO audit(at,actor,action,body) VALUES(1,'teacher1','WAL_PROOF','{}')")
        live.commit()
        report = module.backup(data, destination)
        assert report['status'] == 'complete' and report['archives_retained'] == 1
    snapshot = archive_database(next(destination.glob('*.tar.gz')))
    verify_database_bytes(snapshot, tmp_path)
    with closing(sqlite3.connect(tmp_path/'restored.sqlite3')) as c:
        assert c.execute('SELECT action FROM audit').fetchone()[0] == 'WAL_PROOF'
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM media').fetchone()[0] == 1
        assert decode(c.execute('SELECT body FROM events').fetchone())['media'][0]['url'] == '/api/media/clip1'
    assert clip.read_bytes() == b'private fixture video'


def test_existing_owned_archive_is_atomically_sanitized_and_repeat_stays_metadata_only(tmp_path):
    data, db, clip = seeded(tmp_path)
    destination = tmp_path / 'backups'
    old = old_archive(destination, db, clip)
    before = hashlib.sha256(old.read_bytes()).hexdigest()
    report = module.backup(data,destination)
    assert report['archives_checked'] == report['archives_migrated'] == report['video_entries_removed'] == 1
    assert hashlib.sha256(old.read_bytes()).hexdigest() != before
    verify_database_bytes(archive_database(old), tmp_path)
    report = module.backup(data,destination)
    assert report['archives_migrated'] == report['video_entries_removed'] == 0
    for path in destination.glob('*.tar.gz'):
        archive_database(path)


@pytest.mark.parametrize('name,kind', [('../outside',tarfile.REGTYPE), ('media/link',tarfile.SYMTYPE), ('media/hard',tarfile.LNKTYPE), ('proctor.sqlite3',tarfile.REGTYPE), ('config.env',tarfile.REGTYPE)])
def test_unsafe_archives_remain_byte_exact_on_rejection(tmp_path,name,kind):
    data, db, clip = seeded(tmp_path)
    destination = tmp_path/'backups'
    member=tarfile.TarInfo(name)
    member.type=kind
    member.linkname='/etc/passwd'
    member.size=1 if kind==tarfile.REGTYPE else 0
    old=old_archive(destination,db,clip,extra=member)
    before=old.read_bytes()
    with pytest.raises(ValueError,match='UNSAFE'):
        module.backup(data,destination)
    assert old.read_bytes()==before
    status=json.loads((destination/module.STATUS_FILE).read_text())
    assert status['status']=='error' and status['failed_archives']==1
    assert clip.exists() and not (tmp_path/'outside').exists()


@pytest.mark.parametrize('kind',['invalid_sqlite','truncated_gzip','oversized'])
def test_malformed_and_oversized_original_retained(tmp_path,monkeypatch,kind):
    data,db,clip=seeded(tmp_path)
    destination=tmp_path/'backups'
    old=old_archive(destination,db,clip,contents=b'not sqlite' if kind=='invalid_sqlite' else None)
    if kind=='truncated_gzip':
        old.write_bytes(old.read_bytes()[:-6])
    if kind=='oversized':
        monkeypatch.setattr(module,'MAX_DATABASE_BYTES',128)
    before=old.read_bytes()
    with pytest.raises((ValueError,sqlite3.DatabaseError,EOFError,gzip.BadGzipFile)):
        module.backup(data,destination)
    assert old.read_bytes()==before


def test_only_exact_timestamp_names_are_modified_or_rotated(tmp_path):
    data,db,clip=seeded(tmp_path)
    destination=tmp_path/'backups'
    destination.mkdir()
    untouched=destination/'qorgau-user-notes.tar.gz'
    untouched.write_bytes(b'unrelated file')
    malformed_date=destination/'qorgau-20269999T010101000001Z.tar.gz'
    malformed_date.write_bytes(b'invalid date name')
    for index in range(8):
        old_archive(destination,db,clip,name=f'qorgau-20260101T010101{index:06d}Z.tar.gz')
    report=module.backup(data,destination)
    assert report['archives_checked']==8 and report['archives_retained']==7
    assert len([p for p in destination.iterdir() if module.is_backup_name(p.name)])==7
    assert untouched.read_bytes()==b'unrelated file'
    assert malformed_date.read_bytes()==b'invalid date name'


def test_hardlinked_owned_archive_rejected_without_touching_outside_copy(tmp_path):
    data,db,clip=seeded(tmp_path)
    destination=tmp_path/'backups'
    old=old_archive(destination,db,clip)
    outside=tmp_path/'outside.tar.gz'
    outside.hardlink_to(old)
    before=outside.read_bytes()
    with pytest.raises(ValueError,match='UNSAFE_BACKUP_FILE'):
        module.backup(data,destination)
    assert old.read_bytes()==outside.read_bytes()==before


def test_status_read_exposes_only_safe_policy_and_counter_values(tmp_path,monkeypatch):
    report=dict(policy=module.POLICY,status='complete',archives_checked=3,archives_migrated=2,video_entries_removed=4,archives_retained=4,failed_archives=0)
    path=tmp_path/'status.json'
    path.write_text(json.dumps(dict(report, secret_path='/private/name',teacher_name='private',checked_at=100)))
    monkeypatch.setenv('PROCTOR_BACKUP_POLICY_FILE',str(path))
    assert public_backup_status()==report
    path.write_text(json.dumps(dict(report,failed_archives=1)))
    assert public_backup_status()['status']=='unavailable'
    path.write_bytes(b'x'*4097)
    assert public_backup_status()['status']=='unavailable'
    path.unlink()
    assert public_backup_status()['status']=='unavailable'


def test_health_serves_migration_status_without_private_report_fields(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.proctor.app import create_app
    report=dict(policy=module.POLICY,status='complete',archives_checked=1,archives_migrated=1,video_entries_removed=2,archives_retained=2,failed_archives=0)
    path=tmp_path/'status.json'
    path.write_text(json.dumps(dict(report,private_archive='/var/backups/private.tar.gz',teacher='Private name')))
    monkeypatch.setenv('PROCTOR_BACKUP_POLICY_FILE',str(path))
    with TestClient(create_app(tmp_path/'app-data')) as client:
        response=client.get('/api/health')
        assert response.status_code==200
        assert response.json()['backup_policy_status']==report
        assert 'Private name' not in response.text and '/var/backups' not in response.text
