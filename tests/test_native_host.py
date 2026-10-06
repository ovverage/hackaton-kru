import io
import json
import struct
import time
import pytest
from shared.storage import atomic_json
from agent.native_host import exchange, serve
from agent.profile import load_binding, profile_identity


def setup(folder):
    atomic_json(folder / 'config.json', {'server': 'https://server.example', 'account_id': 'teacher', 'device_id': 'pc1'})
    atomic_json(folder / 'journal.json', {'exam_id': 'exam1', 'state': {'lifecycle': 'RUNNING', 'access': 'OPEN'}, 'environment': {'kind': 'BROWSER'}})
    atomic_json(folder / 'bridge.json', {'exam_id': 'exam1', 'at': 100, 'binding': 'nonce'})


def test_scoped_profile_binding_and_isolation(tmp_path):
    scoped = tmp_path / '.qorgau' / 'accounts' / 'teacher1'
    setup(scoped)
    binding = tmp_path / 'binding.json'
    origin = 'chrome-extension://' + 'a' * 32 + '/'
    atomic_json(binding, {'folder': str(scoped), 'identity': profile_identity(scoped), 'origin': origin})
    assert load_binding(binding, origin) == scoped.resolve()
    assert exchange(scoped, {'type': 'status'}, now=101)['session_id'] == 'exam1'
    with pytest.raises(ValueError, match='ORIGIN_MISMATCH'):
        load_binding(binding, origin.replace('aaa', 'bbb'))
    atomic_json(scoped / 'config.json', {'server': 'https://other.example', 'device_id': 'pc2'})
    with pytest.raises(ValueError, match='BINDING_CHANGED'):
        load_binding(binding, origin)


def test_stale_session_and_inactive_browser_do_not_collect(tmp_path):
    setup(tmp_path)
    msg = {'type': 'observation', 'binding': 'old', 'observation': {'url': 'https://private.example', 'focused': True}}
    assert exchange(tmp_path, msg, now=101)['active']
    assert not (tmp_path / 'browser.json').exists()
    msg['binding'] = 'nonce'
    exchange(tmp_path, msg, now=101)
    assert json.loads((tmp_path / 'browser.json').read_text())['exam_id'] == 'exam1'
    (tmp_path / 'browser.json').unlink()
    assert not exchange(tmp_path, msg, now=107)['active']
    assert not (tmp_path / 'browser.json').exists()
    atomic_json(tmp_path / 'journal.json', {'state': {'lifecycle': 'READY'}})
    assert not exchange(tmp_path, msg, now=101)['active']


@pytest.mark.parametrize('data', [b'', b'xx', struct.pack('=I', 65537), struct.pack('=I', 2) + b'[]', struct.pack('=I', 1) + b'{'])
def test_malformed_native_input_exits_without_reply(tmp_path, data):
    output = io.BytesIO()
    serve(tmp_path, io.BytesIO(data), output)
    assert output.getvalue() == b''


def test_native_wire_protocol(tmp_path):
    setup(tmp_path)
    atomic_json(tmp_path / 'bridge.json', {'exam_id': 'exam1', 'at': time.time(), 'binding': 'nonce'})
    data = b'{"type":"status"}'
    output = io.BytesIO()
    serve(tmp_path, io.BytesIO(struct.pack('=I', len(data)) + data), output)
    packet = output.getvalue()
    assert struct.unpack('=I', packet[:4])[0] == len(packet) - 4
    assert json.loads(packet[4:])['device_id'] == 'pc1'


def test_another_browser_profile_gets_no_account_or_session_metadata(tmp_path):
    setup(tmp_path)
    data = b'{"type":"status", "browser_instance":"another-profile"}'
    output = io.BytesIO()
    serve(tmp_path, io.BytesIO(struct.pack('=I', len(data)) + data), output, 'bound-profile')
    reply = json.loads(output.getvalue()[4:])
    assert reply == {'active': False, 'error': 'BROWSER_PROFILE_MISMATCH'}
