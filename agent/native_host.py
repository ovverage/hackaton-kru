"""Chrome native messaging: policy/status out, limited browser observations in."""

import json
import os
import struct
import sys
import time
from pathlib import Path
from agent.client import atomic_json


def main():
    folder = Path(os.getenv("QORGAU_AGENT_DATA", str(Path.home() / ".qorgau")))
    while True:
        header = sys.stdin.buffer.read(4)
        if len(header) != 4:
            return
        size = struct.unpack("=I", header)[0]
        if size > 65536:
            return
        data = sys.stdin.buffer.read(size)
        if len(data) != size:
            return
        message = json.loads(data)
        try:
            journal = json.loads((folder / "journal.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            journal = {}
        state = journal.get("state", {})
        active = (
            state.get("lifecycle") == "RUNNING"
            and (journal.get("environment") or {}).get("kind") == "BROWSER"
        )
        if active and message.get("type") == "observation":
            item = message.get("observation", {})
            atomic_json(
                folder / "browser.json",
                {
                    "at": time.time(),
                    "observation": {
                        "url": str(item.get("url", ""))[:2048],
                        "focused": bool(item.get("focused")),
                    },
                },
            )
        reply = {
            "active": active,
            "access": state.get("access", "OPEN"),
            "environment": journal.get("environment") if active else None,
            "mode": "OBSERVE",
        }
        payload = json.dumps(reply).encode()
        sys.stdout.buffer.write(struct.pack("=I", len(payload)) + payload)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
