"""Public enrollment is not authorization to download teacher biometrics."""
import secrets
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.proctor.app import create_app, digest
from backend.proctor.db import decode, encode
from shared.teacher_faces import MODEL_ID


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv('PROCTOR_PUBLIC_ENROLLMENT_OWNER', 'Teacher')
    app = create_app(tmp_path)
    with TestClient(app) as teacher, TestClient(app) as anonymous:
        teacher.headers['X-Requested-With'] = 'Qorgau'
        assert teacher.post('/api/auth/setup', json={'name': 'Teacher', 'password': 'fixture-pass-123'}).status_code == 200
        with app.state.db.connect(True) as c:
            owner = c.execute('SELECT id FROM users WHERE name=?', ('Teacher',)).fetchone()['id']
            c.execute('INSERT INTO teacher_faces VALUES(?,?,?,?,?,?)',
                      ('face1', owner, 'Teacher A', encode([1.] + [0.] * 127), MODEL_ID, 1))
        public = anonymous.post('/api/agent/register', json={
            'installation_secret': secrets.token_urlsafe(32), 'name': 'Unapproved public PC'}).json()
        yield SimpleNamespace(teacher=teacher, anonymous=anonymous, db=app.state.db, owner=owner,
                              public=public, headers={'Authorization': 'Bearer ' + public['token']})


def templates(site, headers=None):
    return site.anonymous.get('/api/agent/teacher-faces', headers=headers or site.headers)


def grant(site, enabled, device_id=None):
    return site.teacher.post(f"/api/devices/{device_id or site.public['device_id']}/teacher-face-access",
                             json={'enabled': enabled})


def test_anonymous_public_device_cannot_export_templates_until_teacher_grants(site):
    assert site.anonymous.get('/api/teacher-face-devices').status_code == 401
    denied = templates(site)
    assert denied.status_code == 403 and 'embedding' not in denied.text
    assert 'Ожидает разрешения' in denied.json()['detail']
    listed = site.teacher.get('/api/teacher-face-devices').json()['devices']
    assert listed == [{'id': site.public['device_id'], 'name': 'Unapproved public PC',
                       'enabled': False, 'public_enrollment': True}]
    assert grant(site, True).status_code == 200
    allowed = templates(site)
    assert allowed.status_code == 200
    assert len(allowed.json()['templates'][0]['embedding']) == 128
    assert allowed.headers['cache-control'] == 'no-store'
    assert grant(site, False).status_code == 200
    assert templates(site).status_code == 403
    with site.db.connect() as c:
        actions = [r['action'] for r in c.execute("SELECT action FROM audit WHERE action LIKE 'TEACHER_FACE_ACCESS_%'")]
    assert actions == ['TEACHER_FACE_ACCESS_GRANTED', 'TEACHER_FACE_ACCESS_REVOKED']


def test_device_token_state_and_capabilities_cannot_self_grant(site):
    path = f"/api/devices/{site.public['device_id']}/teacher-face-access"
    assert site.anonymous.post(path, headers={**site.headers, 'X-Requested-With': 'Qorgau'}, json={'enabled': True}).status_code == 401
    spoof = {'state': {'teacher_face_access': True}, 'capabilities': {'teacher_face_access': True},
             'teacher_face_access': True}
    assert site.anonymous.post('/api/agent/sync', headers=site.headers, json=spoof).status_code == 200
    assert templates(site).status_code == 403
    assert grant(site, 'true').status_code == 422
    assert grant(site, True).status_code == 200
    assert site.anonymous.post('/api/agent/sync', headers=site.headers, json={'state': {}, 'capabilities': {'teacher_face_access': False}}).status_code == 200
    assert templates(site).status_code == 200
    assert grant(site, False).status_code == 200
    assert site.anonymous.post('/api/agent/sync', headers=site.headers, json=spoof).status_code == 200
    assert templates(site).status_code == 403


def test_teacher_pairing_trust_remains_revocable(site):
    code = site.teacher.post('/api/pairings', json={}).json()['code']
    private = site.anonymous.post('/api/agent/enroll', json={'code': code, 'name': 'Paired PC'}).json()
    headers = {'Authorization': 'Bearer ' + private['token']}
    assert templates(site, headers).status_code == 200
    devices = site.teacher.get('/api/teacher-face-devices').json()['devices']
    paired = next(d for d in devices if d['id'] == private['device_id'])
    assert paired['enabled'] and not paired['public_enrollment']
    assert grant(site, False, private['device_id']).status_code == 200
    assert templates(site, headers).status_code == 403


def test_owner_scope_and_device_revocation_are_enforced(site):
    with site.db.connect(True) as c:
        c.execute('INSERT INTO devices VALUES(?,?,?,?)', ('foreign', 'other-owner', digest('foreign-token'),
                  encode({'id': 'foreign', 'name': 'Other owner PC'})))
    assert grant(site, True, 'foreign').status_code == 404
    assert all(d['id'] != 'foreign' for d in site.teacher.get('/api/teacher-face-devices').json()['devices'])
    assert grant(site, True).status_code == 200
    assert site.teacher.post(f"/api/devices/{site.public['device_id']}/revoke", json={'reason': 'Fixture revocation'}).status_code == 200
    assert templates(site).status_code in (401, 403)
    assert grant(site, True).status_code == 403
    assert site.teacher.get('/api/teacher-face-devices').json()['devices'] == []


def test_server_side_challenge_does_not_require_template_export_or_issue_an_unlock(site):
    assert templates(site).status_code == 403
    response = site.anonymous.post('/api/agent/teacher-face/challenge', headers=site.headers,
                                   json={'lock_id': 'local-lock', 'expected_version': 1})
    assert response.status_code == 200
    assert 'embedding' not in response.text and 'templates' not in response.json()
    with site.db.connect() as c:
        assert c.execute('SELECT count(*) FROM commands').fetchone()[0] == 0


def test_client_teacher_exclusion_claim_cannot_authorize_server_unlock(site):
    with site.db.connect(True) as c:
        row = c.execute('SELECT * FROM devices WHERE id=?', (site.public['device_id'],)).fetchone()
        device = decode(row)
        device['exam_id'] = 'exam1'
        device['state'].update(lifecycle='RUNNING', access='LOCKED', lock_id='lock1', version=7)
        c.execute('UPDATE devices SET body=? WHERE id=?', (encode(device), device['id']))
        c.execute('INSERT INTO exams VALUES(?,?,?)', ('exam1', site.owner,
                  encode({'id': 'exam1', 'status': 'RUNNING', 'require_camera': True})))
    forged = dict(device['state'], access='OPEN', version=8, teacher_verified=True)
    response = site.anonymous.post('/api/agent/sync', headers=site.headers,
                                   json={'exam_id': 'exam1', 'state': forged,
                                         'capabilities': {'teacher_excluded': True, 'teacher_verified': True}})
    assert response.status_code == 409
    assert 'команды преподавателя' in response.json()['detail']
