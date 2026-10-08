import base64
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.proctor.app import create_app, digest
from backend.proctor.db import encode, decode
from backend.proctor import teacher_faces as routes
from shared.teacher_faces import MODEL_ID, TeacherFaceEngine


class FakeEngine:
    match = TeacherFaceEngine.match

    def decode(self, data):
        return json.loads(data)

    def encode(self, data):
        if data.get('face_count', 1) != 1:
            raise ValueError('FACE_EXACTLY_ONE_REQUIRED')
        return {'embedding': [1., 0.] + [0.] * 126 if data.get('known', True) else [0., 1.] + [0.] * 126,
                'pose': data.get('pose', 0)}


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(routes, 'time', SimpleNamespace(time=lambda: clock[0]))
    app = create_app(tmp_path)
    app.state.teacher_face_engine = FakeEngine()
    with TestClient(app) as client:
        client.headers['X-Requested-With'] = 'Qorgau'
        assert client.post('/api/auth/setup', json={'name': 'Teacher', 'password': 'correct-pass-123'}).status_code == 200
        code = client.post('/api/pairings', json={}).json()['code']
        device = client.post('/api/agent/enroll', json={'code': code, 'name': 'PC1'}).json()
        headers = {'Authorization': 'Bearer ' + device['token']}
        yield SimpleNamespace(client=client, db=app.state.db, clock=clock, headers=headers, device=device, path=tmp_path)


def add(env, **options):
    return env.client.post('/api/teacher-faces', data={'name': 'Teacher A'}, files={'image': ('a.jpg', json.dumps({'photo': 'enrolment', **options}).encode(), 'image/jpeg')})


def issue(env, **overrides):
    return env.client.post('/api/agent/teacher-face/challenge', headers=env.headers,
                           json={'lock_id': 'local-lock-1', 'expected_version': 7, **overrides})


def verify(env, challenge, *, poses=None, known=True, same=False):
    env.clock[0] += 3
    poses = poses if poses is not None else [0, .3 * challenge['turn_sign'], 0]
    images = [json.dumps({'pose': pose, 'nonce': 0 if same else index, 'known': known}).encode() for index, pose in enumerate(poses)]
    return env.client.post('/api/agent/teacher-face/verify', headers=env.headers, json={
        'challenge_id': challenge['challenge_id'],
        'frames': [{'image_b64': base64.b64encode(image).decode(), 'captured_ms': stamp}
                   for image, stamp in zip(images, [0,800,2000])]})


def online_lock(env):
    with env.db.connect(True) as c:
        row = c.execute('SELECT * FROM devices WHERE id=?', (env.device['device_id'],)).fetchone()
        device = decode(row)
        device['exam_id'] = 'exam1'
        device['state'].update(lifecycle='RUNNING', access='LOCKED', lock_id='lock1', version=7)
        c.execute('UPDATE devices SET body=? WHERE id=?', (encode(device), device['id']))
        c.execute('INSERT INTO exams VALUES(?,?,?)', ('exam1', row['owner'], encode({'id': 'exam1', 'status': 'RUNNING', 'require_camera': True})))


def test_enrol_list_templates_and_delete_are_scoped_and_no_photos_persist(env):
    created = add(env)
    assert created.status_code == 200, created.text
    item = created.json()
    listed = env.client.get('/api/teacher-faces').json()['faces']
    assert listed == [item] and 'embedding' not in listed[0]
    payload = env.client.get('/api/agent/teacher-faces', headers=env.headers).json()
    assert payload['model'] == MODEL_ID and payload['expires_at'] == 1060
    assert payload['templates'][0]['teacher_id'] == item['id']
    assert len(payload['templates'][0]['embedding']) == 128
    with env.db.connect(True) as c:
        c.execute('INSERT INTO teacher_faces VALUES(?,?,?,?,?,?)', ('foreign', 'other-account', 'Other', encode([1.] * 128), MODEL_ID, 10))
        c.execute('INSERT INTO devices VALUES(?,?,?,?)', ('foreign-pc', 'other-account', digest('other-token'), encode({'id': 'foreign-pc'})))
    other = env.client.get('/api/agent/teacher-faces', headers={'Authorization': 'Bearer other-token'}).json()
    assert [x['teacher_id'] for x in other['templates']] == ['foreign']
    assert env.client.delete('/api/teacher-faces/foreign').status_code == 404
    assert env.client.delete('/api/teacher-faces/' + item['id']).status_code == 200
    assert issue(env).status_code == 409
    assert not list((env.path / 'media').iterdir())


def test_face_endpoints_require_authentication_and_single_visible_face(env):
    assert env.client.get('/api/agent/teacher-faces').status_code == 401
    assert add(env, face_count=2).status_code == 422
    with TestClient(env.client.app) as anonymous:
        assert anonymous.get('/api/teacher-faces').status_code == 401
    assert env.client.post('/api/teacher-faces', data={'name': 'A'}, files={'image': ('large.jpg', b'x' * (3 * 1024 * 1024 + 1))}).status_code == 422


def test_duplicate_face_enrolment_rejects_confident_match_without_breaking_existing_identity(env):
    first = add(env)
    assert first.status_code == 200
    duplicate = env.client.post('/api/teacher-faces', data={'name': 'Another name'}, files={
        'image': ('new-photo.jpg', json.dumps({'photo': 'different pixels, same face'}).encode(), 'image/jpeg')})
    assert duplicate.status_code == 409
    assert 'уже добавлен' in duplicate.json()['detail']
    assert env.client.get('/api/teacher-faces').json()['faces'] == [first.json()]
    assert verify(env, issue(env).json()).status_code == 200
    # A distinct descriptor is still accepted, even when the entered names match.
    assert add(env, known=False).status_code == 200
    assert len(env.client.get('/api/teacher-faces').json()['faces']) == 2


def test_offline_verify_is_bound_and_single_use_without_unlock_command(env):
    assert add(env).status_code == 200
    challenge = issue(env).json()
    response = verify(env, challenge)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['matched'] and result['lock_id'] == 'local-lock-1' and result['expected_version'] == 7
    assert result['exam_id'] is None and 'command' not in result
    assert verify(env, challenge).status_code == 409
    with env.db.connect() as c:
        assert c.execute('SELECT count(*) FROM commands').fetchone()[0] == 0


@pytest.mark.parametrize('options,status', [({'poses': [0,0,0]},403), ({'known': False},403), ({'poses': [0,0,0], 'same': True},422)])
def test_failure_consumes_challenge_and_never_issues_unlock(env, options, status):
    add(env)
    challenge = issue(env).json()
    response = verify(env, challenge, **options)
    assert response.status_code == status, response.text
    assert verify(env, challenge).status_code == 409
    with env.db.connect() as c:
        assert c.execute('SELECT count(*) FROM commands').fetchone()[0] == 0


def test_expired_rate_limited_and_superseded_challenges(env):
    add(env)
    previous = issue(env).json()
    current = issue(env).json()
    assert verify(env, previous).status_code == 409
    env.clock[0] = current['expires_at']
    assert verify(env, current).status_code == 409
    for _ in range(3):
        assert issue(env).status_code == 200
    assert issue(env).status_code == 429


def test_online_command_checks_state_twice_and_uses_current_templates(env):
    added = add(env).json()
    online_lock(env)
    assert issue(env, exam_id='exam1', lock_id='wrong').status_code == 409
    challenge = issue(env, exam_id='exam1', lock_id='lock1').json()
    result = verify(env, challenge)
    assert result.status_code == 200, result.text
    command = result.json()['command']
    assert command['type'] == 'UNLOCK' and command['require_camera'] is True
    assert command['lock_id'] == 'lock1' and command['expected_version'] == 7
    challenge = issue(env, exam_id='exam1', lock_id='lock1').json()
    env.client.delete('/api/teacher-faces/' + added['id'])
    assert verify(env, challenge).status_code == 403


def test_challenge_cannot_unlock_a_different_later_lock(env):
    add(env)
    online_lock(env)
    challenge = issue(env, exam_id='exam1', lock_id='lock1').json()
    with env.db.connect(True) as c:
        row = c.execute('SELECT * FROM devices WHERE id=?', (env.device['device_id'],)).fetchone()
        device = decode(row)
        device['state']['lock_id'] = 'later-lock'
        c.execute('UPDATE devices SET body=? WHERE id=?', (encode(device), device['id']))
    assert verify(env, challenge).status_code == 409
    with env.db.connect() as c:
        assert c.execute('SELECT count(*) FROM commands').fetchone()[0] == 0


def test_password_provisioning_has_no_session_side_effects_and_shared_throttle(env):
    response = env.client.post('/api/agent/teacher-password', headers=env.headers, json={'password': 'correct-pass-123'})
    assert response.status_code == 200 and response.json() == {'verified': True}
    for _ in range(5):
        assert env.client.post('/api/agent/teacher-password', headers=env.headers, json={'password': 'incorrect'}).status_code == 403
    assert env.client.post('/api/agent/teacher-password', headers=env.headers, json={'password': 'correct-pass-123'}).status_code == 429
    with env.db.connect() as c:
        assert c.execute('SELECT count(*) FROM commands').fetchone()[0] == 0
        device = decode(c.execute('SELECT * FROM devices WHERE id=?', (env.device['device_id'],)).fetchone())
        assert device['state']['lifecycle'] == 'READY'


@pytest.mark.parametrize('environment', [{'kind': 'BROWSER', 'target_id': 'qorgau-browser', 'url': 'https://example.org/exam'}, {'kind': 'DESKTOP'}])
def test_interactive_start_queues_camera_calibration_without_opening_exam_early(env, environment):
    capabilities = {'interactive_start': True, 'window_guard': True, 'desktop_monitor': True, 'camera': False, 'recording': False}
    response = env.client.post('/api/agent/sync', headers=env.headers, json={'state': {}, 'capabilities': capabilities, 'targets': []})
    assert response.status_code == 200
    created = env.client.post('/api/exams', json={'title': 'Exam', 'group': 'A', 'room': '1', 'device_ids': [env.device['device_id']], 'mode': 'GUARDED', 'environment': environment})
    assert created.status_code == 200, created.text
    command = env.client.post('/api/devices/' + env.device['device_id'] + '/commands', json={'type': 'START', 'expected_version': 0, 'exam_id': created.json()['id']})
    assert command.status_code == 200 and command.json()['status'] == 'PENDING'
    with env.db.connect() as c:
        device = decode(c.execute('SELECT * FROM devices WHERE id=?', (env.device['device_id'],)).fetchone())
        assert device['state']['lifecycle'] == 'READY'


def test_media_deadline_is_upload_time_and_duplicate_cannot_extend(env):
    online_lock(env)
    with env.db.connect(True) as c:
        c.execute('INSERT INTO events VALUES(?,?,?,?)', ('event1', 'exam1', env.device['device_id'], encode({'id': 'event1', 'created_at': 1, 'media': [], 'decision': 'PENDING'})))
    fixture = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 30
    response = env.client.post('/api/agent/media/event1', headers={**env.headers, 'Content-Type': 'video/mp4'}, content=fixture)
    assert response.status_code == 200, response.text
    mid = response.json()['id']
    with env.db.connect() as c:
        lifetime = dict(c.execute('SELECT * FROM media_lifetime WHERE id=?', (mid,)).fetchone())
    assert lifetime['expires_at'] - lifetime['uploaded_at'] == 7200
    assert lifetime['uploaded_at'] > 1
    duplicate = env.client.post('/api/agent/media/event1', headers={**env.headers, 'Content-Type': 'video/mp4'}, content=fixture)
    assert duplicate.json()['id'] == mid
    with env.db.connect(True) as c:
        assert dict(c.execute('SELECT * FROM media_lifetime WHERE id=?', (mid,)).fetchone()) == lifetime
        c.execute('UPDATE media_lifetime SET expires_at=0 WHERE id=?', (mid,))
    assert env.client.get('/api/media/' + mid).status_code == 404
    with env.db.connect() as c:
        event = decode(c.execute('SELECT * FROM events WHERE id=?', ('event1',)).fetchone())
        assert event['media'] == [] and event['decision'] == 'PENDING'
    assert not list((env.path / 'media').iterdir())
    assert env.client.post('/api/events/event1/retain', json={'reason': 'Keep', 'days': 7}).status_code == 409


def test_offline_download_redirect_uses_only_published_app_version(env):
    from shared.version import APP_VERSION
    response = env.client.get('/api/student/download-offline?url=https://attacker.test', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'] == f'https://github.com/ovverage/hackaton-kru/releases/download/v{APP_VERSION}/Qorgau-Offline.exe'
