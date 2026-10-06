"""Short mixed-traffic smoke test. Isolated data; synthetic video, not 50 webcams."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import httpx
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shared.rules import State
from agent.resources import ffmpeg_executable


async def exercise(base, clip, seconds):
    latencies, failures = [], []
    video_sem = asyncio.Semaphore(2)
    uploads, reconnects, repeated_commands = 0, 0, 0
    tasks = []
    content = clip.read_bytes()
    targets = [{'id': 'fixture', 'kind': 'BROWSER', 'name': 'Synthetic target'}]
    async with httpx.AsyncClient(base_url=base, headers={'X-Requested-With': 'Qorgau'}, timeout=20,
                                 limits=httpx.Limits(max_connections=110, max_keepalive_connections=100)) as http:
        for _ in range(100):
            try:
                if (await http.get('/api/health')).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.1)
        (await http.post('/api/auth/setup', json={'name': 'Mixed test', 'password': secrets.token_urlsafe(24)})).raise_for_status()
        devices = []
        for n in range(50):
            code = (await http.post('/api/pairings', json={})).json()['code']
            response = await http.post('/api/agent/enroll', json={'code': code, 'name': f'SYNTHETIC-{n}'})
            response.raise_for_status()
            d = response.json()
            d['headers'] = {'Authorization': 'Bearer ' + d.pop('token')}
            d['state'] = State().public()
            (await http.post('/api/agent/sync', headers=d['headers'], json={'state': d['state'], 'targets': targets})).raise_for_status()
            devices.append(d)
        response = await http.post('/api/exams', json={'title': 'SYNTHETIC MIXED LOAD', 'group': 'Test', 'room': 'One physical host',
                                  'device_ids': [d['device_id'] for d in devices], 'require_camera': False,
                                  'environment': {'kind': 'BROWSER', 'target_id': 'fixture', 'url': 'https://example.org/test'}})
        response.raise_for_status()
        exam = response.json()['id']
        for d in devices:
            (await http.post('/api/devices/'+d['device_id']+'/commands', json={'type': 'START', 'expected_version': 0})).raise_for_status()
        began = time.perf_counter()

        async def upload(device, ident):
            nonlocal uploads
            async with video_sem:
                try:
                    response = await http.post('/api/agent/media/'+ident, headers={**device['headers'],
                                               'Content-Type': 'video/mp4', 'X-Clip-Start': '0', 'X-Clip-End': '30'}, content=content)
                    response.raise_for_status()
                    uploads += 1
                except Exception as error:
                    failures.append('video: '+str(error))

        async def worker(device):
            nonlocal repeated_commands
            seen = set()
            ack = []
            burst = False
            pending = []
            while time.perf_counter()-began < seconds:
                started = time.perf_counter()
                elapsed = started-began
                if elapsed >= 10 and not burst:
                    # A simultaneous event from every workstation; retry IDs remain stable.
                    pending = [{'id': str(uuid4()), 'type': 'SECOND_FACE_REVIEW', 'at': elapsed,
                                'start': max(0, elapsed-1), 'end': elapsed, 'created_at': time.time()}]
                    burst = True
                try:
                    response = await http.post('/api/agent/sync', headers=device['headers'],
                                               json={'exam_id': exam, 'state': device['state'], 'targets': targets,
                                                     'acknowledgements': ack, 'events': pending})
                    response.raise_for_status()
                    if pending:
                        tasks.append(asyncio.create_task(upload(device, pending[0]['id'])))
                        pending = []
                    ack = []
                    for command in response.json()['commands']:
                        if command['id'] in seen:
                            repeated_commands += 1
                            ack.append({'id': command['id'], 'ok': True})
                        else:
                            seen.add(command['id'])
                            device['state'].update(lifecycle='RUNNING', version=1)
                            # Deliberately lose the first ACK; the server must redeliver.
                except Exception as error:
                    failures.append('sync: '+type(error).__name__)
                latencies.append((time.perf_counter()-started)*1000)
                await asyncio.sleep(max(0, 2-(time.perf_counter()-started)))

        async def dashboard():
            while time.perf_counter()-began < seconds:
                started = time.perf_counter()
                try:
                    (await http.get('/api/snapshot')).raise_for_status()
                except Exception as error:
                    failures.append('snapshot: '+type(error).__name__)
                latencies.append((time.perf_counter()-started)*1000)
                await asyncio.sleep(5)

        async def websocket():
            nonlocal reconnects
            while time.perf_counter()-began < seconds:
                try:
                    async with connect(base.replace('http:', 'ws:')+'/ws/teacher', origin=base,
                                       additional_headers={'Cookie': 'qorgau_session='+http.cookies.get('qorgau_session')}) as ws:
                        data = json.loads(await asyncio.wait_for(ws.recv(), 10))
                        assert len(data['devices']) == 50
                        reconnects += 1
                except Exception as error:
                    failures.append('websocket: '+type(error).__name__)
                await asyncio.sleep(10)

        await asyncio.gather(*(worker(d) for d in devices), dashboard(), websocket())
        await asyncio.gather(*tasks)
        snapshot = (await http.get('/api/snapshot')).json()
        assert len(snapshot['events']) == 50
        assert all(c['status'] == 'APPLIED' for c in snapshot['commands'])
        latencies.sort()
        p95 = round(latencies[int((len(latencies)-1)*.95)], 2)
        return {'kind': 'isolated synthetic mixed traffic; one physical host; no webcam or real-clip bitrate claim',
                'seconds': round(time.perf_counter()-began, 2), 'clients': 50, 'heartbeat_seconds': 2,
                'ordinary_requests': len(latencies), 'ordinary_p95_ms': p95,
                'failures': failures[:100], 'failure_count': len(failures), 'event_burst': 50,
                'uploaded_clips': uploads, 'clip_bytes': len(content), 'uploaded_bytes': uploads*len(content),
                'max_parallel_uploads': 2, 'websocket_reconnections': reconnects,
                'redelivered_commands': repeated_commands,
                'status': 'PASS' if not failures and uploads == 50 and repeated_commands >= 50 and reconnects >= 2 and p95 <= 500 else 'FAIL'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--output', type=Path, default=Path('.local/evidence/mixed-load.json'))
    args = parser.parse_args()
    if args.seconds < 30:
        parser.error('At least 30 seconds required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qorgau-mixed-') as name:
        folder = Path(name)
        clip = folder/'synthetic.mp4'
        subprocess.run([ffmpeg_executable(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=15',
                        '-t', '30', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '25', '-pix_fmt', 'yuv420p',
                        '-movflags', '+faststart', str(clip)], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'PROCTOR_DATA': str(folder/'data'), 'PROCTOR_ALLOWED_ORIGINS': base, 'PROCTOR_SECURE_COOKIE': '0'}
        with args.output.with_suffix('.server.log').open('w') as log:
            process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.proctor.app:app', '--host', '127.0.0.1',
                                        '--port', str(port), '--no-access-log'], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                result = asyncio.run(exercise(base, clip, args.seconds))
                if os.name == 'nt':
                    command = (f'$qorgauIds = @({process.pid}); $qorgauAll = @(Get-CimInstance Win32_Process); '
                               '$qorgauMore = $true; while ($qorgauMore) { '
                               '$qorgauNew = @($qorgauAll | Where-Object { $_.ParentProcessId -in $qorgauIds -and $_.ProcessId -notin $qorgauIds } | Select-Object -ExpandProperty ProcessId); '
                               '$qorgauMore = $qorgauNew.Count -gt 0; $qorgauIds += $qorgauNew }; '
                               'ConvertTo-Json -Compress -InputObject @(Get-Process -Id $qorgauIds | Select-Object Id,CPU,WorkingSet64,PeakWorkingSet64)')
                    result['server_process_tree'] = json.loads(subprocess.check_output(['powershell.exe', '-NoProfile', '-Command', command], text=True))
                    result['resource_note'] = 'Includes venv launcher and descendants; CPU cumulative seconds; working set at finish and per-process peak, not 30-minute agent metrics'
                result['host'] = {'system': platform.platform(), 'cpu': platform.processor(), 'logical_cpus': os.cpu_count()}
                result['data_bytes'] = sum(p.stat().st_size for p in (folder/'data').rglob('*') if p.is_file())
                args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
                print(json.dumps(result, indent=2))
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    main()
