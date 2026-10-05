import json
import os
import struct
import subprocess
import sys
from agent.client import atomic_json


def exchange(folder, message):
    payload = json.dumps(message).encode()
    result = subprocess.run(
        [sys.executable, "-m", "agent.native_host"],
        input=struct.pack("=I", len(payload)) + payload,
        capture_output=True,
        env={**os.environ, "QORGAU_AGENT_DATA": str(folder)},
        check=True,
    )
    length = struct.unpack("=I", result.stdout[:4])[0]
    return json.loads(result.stdout[4 : 4 + length])


def test_native_protocol_has_no_observation_outside_active_exam(tmp_path):
    atomic_json(
        tmp_path / "journal.json",
        {"state": {"lifecycle": "READY"}, "environment": None},
    )
    reply = exchange(
        tmp_path,
        {
            "type": "observation",
            "observation": {
                "url": "https://unrelated.example/private",
                "focused": True,
            },
        },
    )
    assert not reply["active"]
    assert not (tmp_path / "browser.json").exists()
    atomic_json(
        tmp_path / "journal.json",
        {
            "state": {"lifecycle": "RUNNING", "access": "OPEN"},
            "environment": {"kind": "BROWSER", "url": "https://exam.example"},
        },
    )
    reply = exchange(
        tmp_path,
        {
            "type": "observation",
            "observation": {"url": "https://exam.example", "focused": True},
        },
    )
    assert reply["active"] and reply["mode"] == "OBSERVE"
    assert (tmp_path / "browser.json").exists()
