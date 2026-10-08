import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.proctor.app import create_app
from backend.proctor.db import encode
from shared.rules import State


@pytest.fixture
def history(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.headers['X-Requested-With'] = 'Qorgau'
        response = client.post('/api/auth/setup', json={'name': 'admin', 'password': 'admin'})
        owner = response.json()['user']['id']
        state = State(lifecycle='COMPLETED').public()
        video = tmp_path / 'media' / 'clip.mp4'
        video.write_bytes(b'private recording')
        with app.state.db.connect(True) as c:
            device = {'id': 'pc', 'name': 'PC', 'exam_id': 'exam', 'state': state, 'capabilities': {}}
            c.execute('INSERT INTO devices VALUES(?,?,?,?)', ('pc', owner, 'token', encode(device)))
            exam = {'id': 'exam', 'status': 'COMPLETED', 'participants': {'pc': {'state': state}}}
            c.execute('INSERT INTO exams VALUES(?,?,?)', ('exam', owner, encode(exam)))
            c.execute('INSERT INTO events VALUES(?,?,?,?)', ('event', 'exam', 'pc', encode({'created_at': time.time(), 'media': [{'id': 'clip'}]})))
            c.execute('INSERT INTO media VALUES(?,?,?,?,?)', ('clip', 'event', 'pc', str(video), 'video/mp4'))
            c.execute('INSERT INTO media_lifetime VALUES(?,?,?)', ('clip', time.time(), time.time() + 7200))
            c.execute('INSERT INTO commands VALUES(?,?,?)', ('command', 'pc', encode({'exam_id': 'exam'})))
            c.execute('INSERT INTO teacher_faces VALUES(?,?,?,?,?,?)', ('face', owner, 'Teacher', '[1,0]', 'model', time.time()))
            c.execute('INSERT INTO exams VALUES(?,?,?)', ('foreign', 'other-owner', encode(exam)))
        yield client, app.state.db, video


def cleanup(client, **kwargs):
    return client.post('/api/history/cleanup', json={'exam_ids': ['exam'], 'password': 'admin', **kwargs})


def test_preview_then_remove_preserves_account_and_registration(history):
    client, db, video = history
    preview = cleanup(client)
    assert preview.status_code == 200
    assert preview.json() == {'applied': False, 'exams': 1, 'events': 1, 'videos': 1, 'commands': 1, 'devices_reset': 1}
    assert video.exists()
    result = cleanup(client, apply=True)
    assert result.status_code == 200 and result.json()['applied'] is True
    assert not video.exists()
    with db.connect() as c:
        for table in ('events', 'media', 'media_lifetime', 'commands'):
            assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
        assert c.execute('SELECT id FROM exams').fetchone()[0] == 'foreign'
        assert c.execute('SELECT count(*) FROM teacher_faces').fetchone()[0] == 1
        assert c.execute('SELECT count(*) FROM users').fetchone()[0] == 1
        device = json.loads(c.execute('SELECT body FROM devices').fetchone()[0])
        assert device['exam_id'] is None and device['state']['lifecycle'] == 'READY'
        assert c.execute('SELECT token FROM devices').fetchone()[0] == 'token'
        audit = c.execute("SELECT body FROM audit WHERE action='COMPLETED_HISTORY_REMOVED'").fetchone()[0]
        assert 'password' not in audit
    assert client.get('/api/auth/status').json()['user']['name'] == 'admin'


@pytest.mark.parametrize('ids', [['foreign'], ['exam', 'missing'], ['exam', 'foreign']])
def test_foreign_or_stale_selection_is_atomic(history, ids):
    client, db, video = history
    assert cleanup(client, exam_ids=ids, apply=True).status_code == 404
    assert video.exists()
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM events').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['exam', 'device', 'recording'])
def test_running_or_recording_cannot_be_removed(history, kind):
    client, db, video = history
    with db.connect(True) as c:
        if kind == 'exam':
            c.execute("UPDATE exams SET body=? WHERE id='exam'", (encode({'status': 'RUNNING'}),))
        else:
            device = json.loads(c.execute("SELECT body FROM devices WHERE id='pc'").fetchone()[0])
            if kind == 'device':
                device['state']['lifecycle'] = 'RUNNING'
            else:
                device['capabilities']['recording_tail'] = True
            c.execute("UPDATE devices SET body=? WHERE id='pc'", (encode(device),))
    assert cleanup(client, apply=True).status_code == 409
    assert video.exists()


def test_cleanup_requires_cookie_csrf_and_password(history):
    client, db, video = history
    cookie = client.cookies.get('qorgau_session')
    client.cookies.clear()
    assert cleanup(client, apply=True).status_code == 401
    client.cookies.set('qorgau_session', cookie)
    del client.headers['X-Requested-With']
    assert cleanup(client, apply=True).status_code == 403
    client.headers['X-Requested-With'] = 'Qorgau'
    for _ in range(5):
        assert cleanup(client, password='wrong', apply=True).status_code == 403
    assert cleanup(client, apply=True).status_code == 429
    assert video.exists()


def test_media_path_outside_directory_blocks_whole_cleanup(history, tmp_path):
    client, db, video = history
    unrelated = tmp_path / 'unrelated.mp4'
    unrelated.write_bytes(b'keep')
    with db.connect(True) as c:
        c.execute("UPDATE media SET path=? WHERE id='clip'", (str(unrelated),))
    assert cleanup(client, apply=True).status_code == 409
    assert unrelated.read_bytes() == b'keep' and video.exists()
