"""Real HTTP load against an isolated local server; never uses production accounts."""
import argparse
import asyncio
from collections import deque
import json
import os
from pathlib import Path
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import httpx


async def run(args, url, output):
    from shared.rules import State
    latencies, errors = deque(maxlen=100000), []
    requests = 0
    async with httpx.AsyncClient(base_url=url, headers={"X-Requested-With": "Qorgau"}, timeout=15) as teacher:
        for _ in range(100):
            try:
                if (await teacher.get("/api/health")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(.1)
        setup = await teacher.post("/api/auth/setup", json={"name": "Synthetic load test", "password": secrets.token_urlsafe(24)})
        setup.raise_for_status()
        devices = []
        targets = [{"id": "fixture", "kind": "BROWSER", "name": "Synthetic test browser"}]
        for i in range(args.clients):
            code = (await teacher.post("/api/pairings", json={})).json()["code"]
            enrolled = await teacher.post("/api/agent/enroll", json={"code": code, "name": f"SYNTHETIC-{i+1:02d}"})
            enrolled.raise_for_status()
            device = enrolled.json()
            device["state"] = State().public()
            device["headers"] = {"Authorization": "Bearer " + device.pop("token")}
            response = await teacher.post("/api/agent/sync", headers=device["headers"], json={"exam_id": None, "state": device["state"], "targets": targets})
            response.raise_for_status()
            devices.append(device)
        exam_response = await teacher.post("/api/exams", json={"title": "SYNTHETIC HTTP LOAD", "group": "Test only", "room": "One computer, emulated clients", "device_ids": [d["device_id"] for d in devices], "require_camera": False, "environment": {"kind": "BROWSER", "target_id": "fixture", "url": "https://example.org/test"}})
        exam_response.raise_for_status()
        exam = exam_response.json()["id"]
        for device in devices:
            response = await teacher.post(f"/api/devices/{device['device_id']}/commands", json={"type": "START", "expected_version": 0})
            response.raise_for_status()
        began = time.perf_counter()
        http = httpx.AsyncClient(base_url=url, timeout=10, limits=httpx.Limits(max_connections=100, max_keepalive_connections=100))

        def report():
            ordered = sorted(latencies)
            return {"kind": "synthetic HTTP load; one physical computer; no CV accuracy or 50-camera claim", "clients": args.clients,
                    "target_seconds": args.seconds, "elapsed_seconds": round(time.perf_counter()-began, 2),
                    "requests": requests, "errors": errors[:100], "error_count": len(errors),
                    "latency_sample_count": len(ordered), "latency_window": "last 100000 requests",
                    "p95_ms": round(ordered[int((len(ordered)-1)*.95)]*1000, 2) if ordered else None,
                    "error_rate": len(errors)/requests if requests else None,
                    "heartbeat_seconds": args.interval, "video_load": False,
                    "status": "RUNNING"}

        async def client(device):
            nonlocal requests
            ack = []
            if True:
                while time.perf_counter() - began < args.seconds:
                    start = time.perf_counter()
                    try:
                        response = await http.post("/api/agent/sync", headers=device["headers"], json={"exam_id": exam, "state": device["state"], "targets": targets, "events": [], "acknowledgements": ack})
                        response.raise_for_status()
                        ack = []
                        for command in response.json()["commands"]:
                            if command["type"] == "START":
                                device["state"].update(lifecycle="RUNNING", version=1)
                                ack.append({"id": command["id"], "ok": True})
                    except Exception as error:
                        errors.append(type(error).__name__)
                    requests += 1
                    latencies.append(time.perf_counter()-start)
                    await asyncio.sleep(max(0, args.interval-(time.perf_counter()-start)))

        async def dashboard():
            nonlocal requests
            while time.perf_counter() - began < args.seconds:
                start = time.perf_counter()
                try:
                    (await teacher.get("/api/snapshot")).raise_for_status()
                except Exception as error:
                    errors.append(type(error).__name__)
                requests += 1
                latencies.append(time.perf_counter()-start)
                output.write_text(json.dumps(report(), indent=2), encoding="utf-8")
                await asyncio.sleep(5)

        await asyncio.gather(*(client(device) for device in devices), dashboard())
        await http.aclose()
        final = report()
        final["status"] = "PASS" if final["error_rate"] < .01 and final["p95_ms"] <= 500 else "FAIL"
        output.write_text(json.dumps(final, indent=2), encoding="utf-8")
        print(json.dumps(final, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=7200)
    parser.add_argument("--clients", type=int, default=50)
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--output", type=Path, default=Path(".local/evidence/load-50.json"))
    args = parser.parse_args()
    if not 1 <= args.clients <= 100 or args.seconds < 1 or args.interval < .1:
        parser.error("Invalid load parameters")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="qorgau-load-") as folder:
        env = {**os.environ, "PROCTOR_DATA": folder, "PROCTOR_SECURE_COOKIE": "0"}
        log = args.output.with_suffix(".server.log")
        with log.open("w") as stream:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.proctor.app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], env=env, stdout=stream, stderr=stream)
            try:
                asyncio.run(run(args, f"http://127.0.0.1:{port}", args.output))
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == "__main__":
    main()
