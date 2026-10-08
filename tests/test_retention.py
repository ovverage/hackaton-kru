import pytest
from backend.proctor.db import Database, encode, decode
from backend.proctor.retention import expire_media


def seed(tmp_path, uploaded=100, *, outside=False):
    db = Database(tmp_path / 'proctor.sqlite3')
    media = tmp_path / 'media'
    media.mkdir(exist_ok=True)
    with db.connect(True) as c:
        for key, status, until in [('expired', 'COMPLETED', 0), ('active', 'RUNNING', 0), ('extended', 'COMPLETED', 1000000)]:
            c.execute('INSERT INTO exams VALUES(?,?,?)', (key, 'teacher', encode({'status': status})))
            c.execute('INSERT INTO events VALUES(?,?,?,?)', (key, key, 'pc', encode({'id': key, 'created_at': 0, 'retain_until': until, 'decision': 'CONFIRMED', 'media': [{'id': key}]})))
            path = (tmp_path if outside else media) / (key + '.mp4')
            path.write_bytes(b'fixture')
            c.execute('INSERT INTO media VALUES(?,?,?,?,?)', (key, key, 'pc', str(path), 'video/mp4'))
            if uploaded is not None:
                c.execute('INSERT INTO media_lifetime VALUES(?,?,?)', (key, uploaded, uploaded + 7200))
    return db


def test_two_hours_from_upload_preserves_event_even_active_or_previously_extended(tmp_path):
    db = seed(tmp_path)
    assert expire_media(tmp_path, now=7299, apply=True) == []
    assert expire_media(tmp_path, now=7300) == ['expired', 'active', 'extended']
    assert (tmp_path / 'media/expired.mp4').exists()
    assert expire_media(tmp_path, now=7300, apply=True) == ['expired', 'active', 'extended']
    assert not list((tmp_path / 'media').iterdir())
    with db.connect() as c:
        event = decode(c.execute("SELECT body FROM events WHERE id='expired'").fetchone())
        assert event['media'] == [] and event['media_expired_at'] == 7300
        assert event['decision'] == 'CONFIRMED'
        assert c.execute('SELECT count(*) FROM events').fetchone()[0] == 3
        assert c.execute('SELECT count(*) FROM audit').fetchone()[0] == 3
        assert c.execute('SELECT count(*) FROM media_lifetime').fetchone()[0] == 0
    assert expire_media(tmp_path, now=7400, apply=True) == []


def test_legacy_video_uses_event_time_without_extending_on_migration(tmp_path):
    seed(tmp_path, uploaded=None)
    assert len(expire_media(tmp_path, days=30, now=7200, apply=True)) == 3


def test_retention_never_unlinks_outside_media_directory(tmp_path):
    seed(tmp_path, outside=True)
    with pytest.raises(ValueError, match='OUTSIDE'):
        expire_media(tmp_path, now=7300, apply=True)
    assert (tmp_path / 'expired.mp4').read_bytes() == b'fixture'
