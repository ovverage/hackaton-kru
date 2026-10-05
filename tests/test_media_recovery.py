import httpx
from agent.client import Agent
from agent.recording import ClipRecorder
from shared.storage import atomic_json


def test_restarted_agent_recovers_unjournaled_clip_and_persists_upload_ack(tmp_path):
    atomic_json(tmp_path / 'config.json', {'server': 'http://testserver', 'token': 'fixture', 'device_id': 'pc1'})
    clips = tmp_path / 'clips'
    recorder = ClipRecorder(clips)
    path = clips / 'fixture.mp4'
    path.write_bytes(b'fixture upload bytes')
    recorder.ready = {'event1': [{'event_id': 'event1', 'path': str(path), 'start': 0, 'end': 2}]}
    recorder.save()
    recorder.close()
    requests = []
    def receive(request):
        requests.append((request.url.path, request.read()))
        return httpx.Response(200, json={'ok': True})
    agent = Agent(tmp_path, transport=httpx.MockTransport(receive))
    assert len(agent.journal['media']) == 1
    agent.upload_media()
    assert not agent.journal['media'] and not path.exists()
    agent.recorder.close()
    agent.http.close()
    recovered = ClipRecorder(clips)
    assert not recovered.completed(float('inf'))
    recovered.close()
    assert requests == [('/api/agent/media/event1', b'fixture upload bytes')]
