import json
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from agent.client import Agent, atomic_json
from backend.proctor.app import create_app


def login(client, setup=False):
    response = client.post('/api/auth/' + ('setup' if setup else 'login'), json={
        'name': 'Teacher', 'password': 'test-password',
    })
    assert response.status_code == 200


def test_production_hides_old_demo_and_disables_simulation(tmp_path):
    # Explicitly opt into fixtures only when seeding an old installation.
    with TestClient(create_app(tmp_path, allow_demo=True), headers={'X-Requested-With': 'Qorgau'}) as old:
        login(old, setup=True)
        old.post('/api/demo/devices', json={})
        demo = old.get('/api/snapshot').json()['devices'][0]
        body = dict(title='Old demo', group='A', room='1', device_ids=[demo['id']],
                    environment={'kind': 'BROWSER', 'target_id': 'chrome', 'url': 'https://example.com'})
        assert old.post('/api/exams', json=body).status_code == 200
    with TestClient(create_app(tmp_path), headers={'X-Requested-With': 'Qorgau'}) as client:
        login(client)
        snap = client.get('/api/snapshot').json()
        assert not snap['devices'] and not snap['exams'] and not snap['events']
        assert client.post('/api/demo/devices', json={}).status_code == 404
        assert client.post('/api/devices/' + demo['id'] + '/simulate', json={'scenario': 'PHONE'}).status_code == 404
        assert client.post('/api/exams', json=body).status_code == 409


def test_desktop_session_needs_live_heartbeat_and_launches_no_program(tmp_path):
    app = create_app(tmp_path / 'server')
    with TestClient(app, headers={'X-Requested-With': 'Qorgau'}) as server:
        login(server, setup=True)
        code = server.post('/api/pairings', json={}).json()['code']
        identity = server.post('/api/agent/enroll', json={'code': code, 'name': 'Real PC'}).json()
        folder = tmp_path / 'agent'
        atomic_json(folder / 'config.json', {**identity, 'server': 'http://testserver'})
        def forward(request):
            return server.request(request.method, request.url.path, content=request.content, headers=dict(request.headers))
        agent = Agent(folder, transport=httpx.MockTransport(forward))
        agent.capabilities.update(desktop_monitor=True, window_guard=True, camera=True, recording=True)
        body = dict(title='Desktop', group='A', room='1', device_ids=[identity['device_id']], mode='GUARDED')
        assert not server.get('/api/snapshot').json()['devices'][0]['online']
        assert server.post('/api/exams', json=body).status_code == 409
        agent.sync()
        assert server.get('/api/snapshot').json()['devices'][0]['online']
        created = server.post('/api/exams', json=body)
        assert created.status_code == 200, created.text
        assert created.json()['environment'] == {'kind': 'DESKTOP', 'guarded': True}
        agent.sync()
        agent.camera, agent.recorder = Mock(), Mock()
        agent.observe()
        command_url = '/api/devices/' + identity['device_id'] + '/commands'
        assert server.post(command_url, json={'type': 'START', 'expected_version': 0}).status_code == 200
        with patch('agent.client.subprocess.Popen') as launched:
            agent.sync()
            launched.assert_not_called()
        assert agent.engine.state.lifecycle == 'RUNNING'
        assert agent.guard_target == {'desktop': True}
        agent.sync()
        agent.observe(phone_confidence=.99)
        agent.observe(phone_confidence=.99)
        agent.sync()
        assert agent.engine.state.access == 'LOCKED'
        assert server.get('/api/snapshot').json()['events'][0]['type'] == 'PHONE_DETECTED'
        # Expiry and revocation must never leave a green presence indicator.
        with app.state.db.connect(True) as c:
            d = json.loads(c.execute('SELECT body FROM devices WHERE id=?', (identity['device_id'],)).fetchone()[0])
            d['last_heartbeat_at'] = time.time() - 7
            c.execute('UPDATE devices SET body=? WHERE id=?', (json.dumps(d), d['id']))
        assert not server.get('/api/snapshot').json()['devices'][0]['online']
        agent.sync()
        assert server.get('/api/snapshot').json()['devices'][0]['online']
        with app.state.db.connect(True) as c:
            d = json.loads(c.execute('SELECT body FROM devices WHERE id=?', (identity['device_id'],)).fetchone()[0])
            d['revoked_at'] = time.time()
            c.execute('UPDATE devices SET body=? WHERE id=?', (json.dumps(d), d['id']))
        assert not server.get('/api/snapshot').json()['devices'][0]['online']
        agent.http.close()


def test_desktop_guard_allows_normal_work_but_only_lock_overlays_when_locked():
    from agent.windows_guard import WindowsGuard
    guard = WindowsGuard.__new__(WindowsGuard)
    guard.desktop, guard.target, guard.locked = True, None, False
    guard.overlay_handles, guard.attempted = set(), False
    guard.u = SimpleNamespace(GetAncestor=lambda hwnd, _: hwnd, GetSystemMetrics=lambda _: 0,
                              GetForegroundWindow=lambda: 11, OpenClipboard=Mock(),
                              SetForegroundWindow=Mock())
    assert guard.allowed(11)
    assert guard.tick(locked=False, overlays=[22]) is None
    guard.u.OpenClipboard.assert_not_called()
    guard.u.OpenClipboard.return_value = False
    assert guard.tick(locked=True, overlays=[22]) == 'ENVIRONMENT_ATTEMPT'
    assert not guard.allowed(11) and guard.allowed(22)
    guard.u.SetForegroundWindow.assert_called_once_with(22)
    assert guard.tick(locked=False, overlays=[22]) is None
    assert guard.allowed(11)
