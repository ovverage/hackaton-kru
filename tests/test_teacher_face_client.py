import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import httpx
import numpy as np
import pytest

from agent.offline import OfflineAgent
from agent.teacher_exclusion import count_students


@pytest.mark.parametrize('change', [None, {'matched': False}, {'expected_version': 99},
                                   {'challenge_id': 'another'}, {'lock_id': 'another'}, {'exam_id': 'other'}])
def test_offline_face_unlock_requires_bound_positive_server_result(tmp_path, monkeypatch, change):
    agent = OfflineAgent(tmp_path)
    agent.engine.start()
    agent.engine.lock('TEACHER_LOCK')
    agent.journal['exam_id'] = 'local-fixture'
    lock_id, version = agent.engine.state.lock_id, agent.engine.state.version
    clock = [time.monotonic()]
    monkeypatch.setattr('agent.teacher_access.time.monotonic', lambda: clock[0])
    messages = []
    frames = iter([20, 100, 180])

    def packet():
        value = next(frames)
        clock[0] += .6
        return SimpleNamespace(sequence=value, captured_at=clock[0], frame=np.full((100, 100, 3), value, np.uint8))

    agent.camera = SimpleNamespace(latest_preview=packet)
    agent.teacher_engine = lambda: SimpleNamespace(
        encode=lambda frame: {'pose': .3 if 90 <= int(frame[0, 0, 0]) <= 110 else 0},
        decode=lambda content: cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR))
    agent.refresh_teacher_templates = Mock()

    def request(req):
        body = json.loads(req.content)
        messages.append((req.url.path, body))
        if req.url.path.endswith('challenge'):
            assert body['exam_id'] is None and body['lock_id'] == lock_id
            return httpx.Response(200, json={'turn_sign': 1, 'challenge_id': 'challenge', 'expires_at': time.time() + 45})
        assert len(body['frames']) == 3 and body['frames'][-1]['captured_ms'] >= 1000
        result = dict(matched=True, challenge_id='challenge', lock_id=lock_id, exam_id=None, expected_version=version)
        result.update(change or {})
        return httpx.Response(200, json=result)

    agent._face_http = httpx.Client(base_url='https://fixture.test', transport=httpx.MockTransport(request))
    if change:
        with pytest.raises(ValueError, match='другой блокировке'):
            agent.teacher_face_unlock()
        assert agent.engine.state.access == 'LOCKED'
    else:
        agent.teacher_face_unlock()
        assert agent.engine.state.access == 'OPEN'
    assert len(messages) == 2 and not agent.teacher_face_scan
    assert not list(tmp_path.glob('**/*.jpg'))
    agent._face_http.close()
    agent.http.close()


def test_teacher_exclusion_never_subtracts_one_teacher_twice():
    primary = [{'box': [0, 0, 100, 100]}, {'box': [200, 0, 300, 100]}]
    teacher = dict(box=[0, 0, .25, .5], embedding='known')
    engine = SimpleNamespace(match=lambda value, _: {'teacher_id': 'teacher'} if value == 'known' else None)
    assert count_students(2, primary, [teacher], 400, 200, engine, [{}]) == 1
    assert count_students(2, primary, [teacher, teacher], 400, 200, engine, [{}]) == 1
    unknown = dict(box=[.5, 0, .25, .5], embedding=None)
    assert count_students(2, primary, [teacher, teacher, unknown], 400, 200, engine, [{}]) == 2
    assert count_students(2, primary, [dict(teacher, box=[.4, .5, .2, .2])], 400, 200, engine, [{}]) == 2


def test_teacher_exclusion_ambiguous_primary_correspondence_keeps_faces():
    primary = [{'box': [0, 0, 100, 100]}, {'box': [10, 0, 110, 100]}]
    engine = SimpleNamespace(match=lambda *_: {'teacher_id': 'teacher'})
    assert count_students(2, primary, [dict(box=[0, 0, .25, .5], embedding='known')],
                          400, 200, engine, [{}]) == 2
