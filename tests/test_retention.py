from backend.proctor.db import Database, encode, decode
from backend.proctor.retention import expire_media


def test_retention_preserves_active_and_extended_media(tmp_path):
    db = Database(tmp_path / 'proctor.sqlite3')
    media = tmp_path / 'media'
    media.mkdir()
    with db.connect(True) as c:
        for key, status, until in [('expired', 'COMPLETED', 0), ('active', 'RUNNING', 0), ('extended', 'COMPLETED', 1000000)]:
            c.execute('INSERT INTO exams VALUES(?,?,?)', (key, 'teacher', encode({'status': status})))
            c.execute('INSERT INTO events VALUES(?,?,?,?)', (key, key, 'pc', encode({'id': key, 'created_at': 0, 'retain_until': until, 'media': [{'id': key}]})))
            path = media / (key + '.mp4')
            path.write_bytes(b'fixture')
            c.execute('INSERT INTO media VALUES(?,?,?,?,?)', (key, key, 'pc', str(path), 'video/mp4'))
    assert expire_media(tmp_path, now=800000) == ['expired']
    assert (media / 'expired.mp4').exists()
    assert expire_media(tmp_path, now=800000, apply=True) == ['expired']
    assert not (media / 'expired.mp4').exists()
    assert (media / 'active.mp4').exists() and (media / 'extended.mp4').exists()
    with db.connect() as c:
        event = decode(c.execute("SELECT body FROM events WHERE id='expired'").fetchone())
        assert event['media'] == [] and event['media_expired_at'] == 800000
        assert c.execute('SELECT count(*) FROM audit').fetchone()[0] == 1
