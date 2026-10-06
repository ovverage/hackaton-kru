import gzip
import plistlib
import pytest
from agent.seb import settings_for, seb_file, Broker
from shared.storage import atomic_json


def test_seb_configuration_requires_service_clipboard_block_and_staff_exit():
    config = settings_for('https://exam.example/test', 'a' * 64)
    decoded = plistlib.loads(gzip.decompress(gzip.decompress(seb_file(config))[4:]))
    assert decoded['sebServicePolicy'] == 2 and decoded['clipboardPolicy'] == 1
    assert decoded['createNewDesktop'] and not decoded['enableAltTab']
    assert decoded['hashedQuitPassword'] == 'A' * 64
    with pytest.raises(ValueError):
        settings_for('file:///C:/Windows', 'a' * 64)


def test_seb_broker_stale_agent_locks_and_completion_quits(tmp_path):
    broker = Broker(tmp_path, 'exam1', {})
    assert broker.ping(now=100)['instruction'] == 'SEB_FORCE_LOCK_SCREEN'
    atomic_json(tmp_path / 'journal.json', {'exam_id': 'exam1', 'state': {'lifecycle': 'RUNNING', 'access': 'OPEN'}})
    atomic_json(tmp_path / 'bridge.json', {'exam_id': 'exam1', 'at': 100})
    unlock = broker.ping(now=100)
    assert unlock['instruction'] == 'NOTIFICATION_CONFIRM'
    assert broker.ping(unlock['attributes']['instruction-confirm'], now=100)['instruction'] == ''
    assert broker.ping(now=104)['instruction'] == 'SEB_FORCE_LOCK_SCREEN'
    atomic_json(tmp_path / 'journal.json', {'exam_id': 'exam1', 'state': {'lifecycle': 'COMPLETED', 'access': 'OPEN'}})
    atomic_json(tmp_path / 'bridge.json', {'exam_id': 'exam1', 'at': 105})
    assert broker.ping(now=105)['instruction'] == 'SEB_QUIT'


def test_seb_http_authentication_and_connection_token(tmp_path):
    import base64
    import threading
    import httpx
    from http.server import ThreadingHTTPServer
    broker = Broker(tmp_path, 'exam1', {'startURL': 'https://exam.example/'})
    server = ThreadingHTTPServer(('127.0.0.1', 0), broker.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{server.server_port}') as client:
            assert client.get('/api').status_code == 200
            assert client.post('/token').status_code == 401
            encoded = base64.b64encode(('qorgau:' + broker.secret).encode()).decode()
            response = client.post('/token', headers={'Authorization': 'Basic ' + encoded})
            client.headers['Authorization'] = 'Bearer ' + response.json()['access_token']
            assert client.post('/ping').status_code == 401
            response = client.post('/handshake')
            client.headers['SEBConnectionToken'] = response.headers['SEBConnectionToken']
            assert client.post('/ping').json()['instruction'] == 'SEB_FORCE_LOCK_SCREEN'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
