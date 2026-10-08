import json
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest

from agent.client import Agent
from agent.offline import OfflineAgent
from agent.local_access import LocalAccess
from agent.profile import read_object
from agent.native_host import exchange
from agent.browser_tabs import available, safe_tabs
from shared.storage import atomic_json


def make_agent(tmp_path, transport=None):
    atomic_json(tmp_path / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture'})
    agent = Agent(tmp_path, transport=transport)
    agent.journal['exam_id'] = 'exam'
    return agent


def command(agent, kind='START'):
    return dict(id='start', type=kind, exam_id=agent.journal['exam_id'],
                expected_version=agent.engine.state.version, expires_at=time.time() + 60)


def test_interactive_start_never_launches_or_arms_before_calibration(tmp_path):
    agent = make_agent(tmp_path)
    agent.capabilities['interactive_start'] = True
    agent.camera = SimpleNamespace(screen_calibration_required=True, requires_gaze_reference=True)
    with patch.object(agent, 'launch_environment') as launch:
        agent.apply(command(agent))
        assert agent.journal['acks'][-1]['ok']
        token = agent.start_pending['id']
        assert agent.engine.state.lifecycle == 'READY' and not launch.called
        with pytest.raises(ValueError):
            agent.prepare_start(token)
        agent.recorder = Mock()
        agent.last_observation_at = time.monotonic()
        agent.last_observation = {'faces': 1, 'phone_confidence': 0}
        agent.camera.requires_gaze_reference = False
        with patch('agent.program_check.running_tools', return_value=[]):
            agent.prepare_start(token)
        assert launch.call_count == 1 and agent.engine.state.lifecycle == 'READY'
        agent.complete_start(token)
        assert launch.call_count == 1 and agent.engine.state.lifecycle == 'RUNNING'
        assert agent.start_pending is None
    agent.http.close()


def test_cancelled_or_superseded_pending_cannot_start(tmp_path):
    agent = make_agent(tmp_path)
    agent.capabilities['interactive_start'] = True
    agent.apply(command(agent))
    token = agent.start_pending['id']
    agent.cancel_start(token)
    with pytest.raises(ValueError):
        agent.complete_start(token)
    assert agent.engine.state.lifecycle == 'READY'
    agent.http.close()


def test_remote_program_preflight_leaves_exam_unarmed(tmp_path):
    agent = make_agent(tmp_path)
    agent.queue_start(command(agent))
    agent.camera, agent.recorder = SimpleNamespace(requires_gaze_reference=False), Mock()
    agent.last_observation_at = time.monotonic()
    with patch('agent.program_check.running_tools', return_value=['AnyDesk']):
        with pytest.raises(ValueError, match='AnyDesk'):
            agent.prepare_start(agent.start_pending['id'])
    assert agent.engine.state.lifecycle == 'READY'


def test_local_password_is_salted_and_not_plaintext(tmp_path):
    access = LocalAccess(tmp_path)
    access.set_password('teacher-password')
    assert access.ready
    content = (tmp_path / 'local-access.json').read_text()
    assert 'teacher-password' not in content and '$argon2' in content
    access.verify('teacher-password')
    with pytest.raises(ValueError):
        access.verify('wrong-password')


def test_offline_no_transport_and_end_deletes_only_local_evidence(tmp_path):
    requests = []
    agent = OfflineAgent(tmp_path, transport=httpx.MockTransport(lambda req: requests.append(req)))
    agent.setup_password('teacher-password')
    agent.engine.start()
    agent.journal['exam_id'] = 'local-test'
    for sub in ('evidence', 'clips'):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / 'owned.data').write_bytes(b'local')
    agent.sync()
    agent.upload_media()
    agent.teacher_unlock('teacher-password', 'END_AND_RELEASE')
    assert not requests
    assert not (tmp_path / 'evidence').exists() and not (tmp_path / 'clips').exists()
    assert (tmp_path / 'config.json').exists() and agent.local_access.ready
    assert agent.engine.state.lifecycle == 'COMPLETED'
    agent.http.close()


def test_online_password_never_bypasses_server_release_authority(tmp_path):
    agent = make_agent(tmp_path)
    agent.local_access.set_password('teacher-password')
    agent.engine.start()
    agent.engine.lock('TEACHER_LOCK')
    agent.camera = SimpleNamespace()
    with patch.object(agent, '_online_teacher_unlock', side_effect=ValueError('WRONG_PASSWORD')):
        with pytest.raises(ValueError, match='WRONG_PASSWORD'):
            agent.teacher_unlock('teacher-password')
    assert agent.engine.state.access == 'LOCKED'
    with patch.object(agent, '_online_teacher_unlock', side_effect=httpx.ConnectError('offline')):
        with pytest.raises(ValueError, match='Нет связи'):
            agent.teacher_unlock('teacher-password')
    assert agent.engine.state.access == 'LOCKED'
    agent.http.close()


def test_roster_only_during_request_and_expires_and_preserves_exact_tab_ids(tmp_path):
    instance = '25d28b1e-127c-482e-8cb5-fcd1445a8540'
    tabs = [{'tab_id': 123, 'window_id': 4, 'title': 'exam', 'url': 'https://school.test/1'},
            {'tab_id': 124, 'window_id': 4, 'title': 'exam', 'url': 'https://school.test/1'}]
    exchange(tmp_path, {'type': 'tabs', 'tabs': tabs, 'browser_instance': instance}, now=100)
    assert not list(tmp_path.glob('browser-tabs-*.json'))
    assert available(tmp_path, now=100) == []
    exchange(tmp_path, {'type': 'tabs', 'tabs': tabs, 'browser_instance': instance}, now=101)
    found = available(tmp_path, now=102)
    assert len(found) == 2 and found[0]['id'] != found[1]['id']
    assert not available(tmp_path, now=108)
    assert safe_tabs(tabs, '../../bad') == []
    assert safe_tabs([dict(tabs[0], url='file:///secret')], instance) == []


def test_same_origin_different_tab_is_detected_and_lock_does_not_spam(tmp_path):
    agent = make_agent(tmp_path)
    agent.environment = dict(kind='BROWSER_TAB', guarded=True, url='https://school.test/exam',
                             tab_id=1, window_id=2, browser_instance='instance')
    agent.engine.start()
    atomic_json(tmp_path / 'browser.json', dict(at=time.time(), exam_id='exam', binding=agent.bridge_binding,
        observation=dict(url='https://school.test/other', focused=True, tab_id=3, window_id=2, browser_instance='instance')))
    agent.read_browser()
    assert agent.engine.state.access == 'LOCKED'
    assert agent.journal['events'][-1]['detail'] == 'OTHER_TAB'
    before = len(agent.journal['events'])
    agent.security_event('ANOTHER_REASON')
    agent.read_browser()
    assert len(agent.journal['events']) == before
    assert read_object(tmp_path / 'journal.json')['state']['access'] == 'LOCKED'
    assert json.loads((tmp_path / 'journal.json').read_text())
    agent.http.close()
