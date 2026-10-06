"""Exercise the downloaded Windows EXE against an isolated real HTTP server.

No camera or test application is started. Qt uses its offscreen platform.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from backend.proctor.app import create_app


def until(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise RuntimeError("Timed out waiting for the downloaded EXE")


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        # One-file PyInstaller has a bootloader parent and an application child.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        process.terminate()
    process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--exe", type=Path, default=ROOT / "dist" / "Qorgau-Student.exe"
    )
    parser.add_argument("--plain", action="store_true", help="Test the ordinary installer/EXE without any enrollment trailer")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ["PROCTOR_STUDENT_EXE"] = str(args.exe.resolve())
    if args.plain:
        os.environ["PROCTOR_PUBLIC_ENROLLMENT_OWNER"] = "Smoke Teacher"
    processes = []
    with tempfile.TemporaryDirectory(prefix="qorgau-exe-smoke-") as directory:
        folder = Path(directory)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        address = f"http://127.0.0.1:{listener.getsockname()[1]}"
        app = create_app(folder / "server")
        server = uvicorn.Server(
            uvicorn.Config(app, log_level="error", access_log=False)
        )
        worker = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        worker.start()
        until(lambda: server.started)
        try:
            with httpx.Client(
                base_url=address, headers={"X-Requested-With": "Qorgau"}, timeout=30
            ) as client:
                client.post(
                    "/api/auth/setup",
                    json={
                        "name": "Smoke Teacher",
                        "password": "isolated-test-password",
                    },
                ).raise_for_status()
                downloaded = args.exe.resolve()
                if not args.plain:
                    response = client.post(
                        "/api/student-packages",
                        json={"room": "Smoke", "server": address, "max_devices": 2},
                    )
                    response.raise_for_status()
                    package = response.json()
                    downloaded = folder / "Qorgau-Classroom.exe"
                    with client.stream("GET", package["download_path"]) as response:
                        response.raise_for_status()
                        with downloaded.open("wb") as output:
                            for chunk in response.iter_bytes():
                                output.write(chunk)

                def launch(name):
                    print("Launching " + name, flush=True)
                    process = subprocess.Popen(
                        [str(downloaded), "--data", str(folder / name)] + (["--server", address] if args.plain else []),
                        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW
                        if os.name == "nt"
                        else 0,
                    )
                    processes.append(process)

                    def registered():
                        if process.poll() is not None:
                            output = process.communicate()[0].decode(
                                "utf-8", errors="replace"
                            )
                            raise RuntimeError(
                                f"EXE exited before registration: {process.returncode}\n{output}"
                            )
                        return (folder / name / "config.json").exists()

                    until(registered)
                    print("Registered " + name, flush=True)
                    config = json.loads(
                        (folder / name / "config.json").read_text(encoding="utf-8")
                    )
                    registered_at = time.time()
                    until(
                        lambda: any(
                            d["id"] == config["device_id"]
                            and d["last_seen"] > registered_at
                            for d in client.get("/api/snapshot").json()["devices"]
                        )
                    )
                    assert process.poll() is None
                    print("Synced " + name, flush=True)
                    return process, config

                first, config1 = launch("pc1")
                _, config2 = launch("pc2")
                assert config1["device_id"] != config2["device_id"]
                assert config1["token"] != config2["token"]
                stop_process(first)
                # Wait until the worker process has released the per-profile lock.
                time.sleep(1)
                _, restarted = launch("pc1")
                assert restarted == config1
                assert len(client.get("/api/snapshot").json()["devices"]) == 2
                packages = client.get("/api/student-packages").json()["packages"]
                if args.plain:
                    assert packages == []
                else:
                    assert packages[0]["used_devices"] == 2
                print(
                    "PASS: downloaded EXE, automatic GUI enrollment for two profiles, individual tokens, live sync, restart without duplicate registration"
                )
                if args.output:
                    from shared.version import APP_VERSION
                    from shared.bootstrap import DEFAULT_SERVER
                    args.output.write_text(json.dumps({
                        "status": "passed", "version": APP_VERSION,
                        "mode": "ordinary executable without bootstrap trailer" if args.plain else "classroom package",
                        "default_server": DEFAULT_SERVER,
                        "profiles": 2, "distinct_tokens": True, "live_sync": True,
                        "restart_without_duplicate": True,
                        "scope": "isolated HTTP fixture via CLI server override; no webcam or input hooks",
                    }, indent=2), encoding="utf-8")
        finally:
            for process in processes:
                stop_process(process)
            server.should_exit = True
            worker.join(timeout=10)
            listener.close()


if __name__ == "__main__":
    main()
