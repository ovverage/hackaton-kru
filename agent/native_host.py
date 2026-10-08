"""Bound Chrome/Edge native messaging; no automatic account discovery."""
import argparse
import json
import struct
import sys
import time
from pathlib import Path
from shared.storage import atomic_json
from agent.profile import load_binding, read_object


def exchange(folder, message, now=None):
    now = time.time() if now is None else now
    journal = read_object(folder / "journal.json")
    state = journal.get("state", {})
    bridge = read_object(folder / "bridge.json")
    from .browser_tabs import safe_tabs
    instance = message.get('browser_instance')
    # serve() has already checked the registered browser profile identity.
    roster = safe_tabs(message.get('tabs'), instance)
    request = read_object(folder / 'browser-tabs-request.json')
    tab_request = 0 < request.get('until', 0) - now <= 60
    tab_request = tab_request or (0 <= now - bridge.get('at', 0) < 6
        and (journal.get('environment') or {}).get('kind') == 'BROWSER_TAB'
        and state.get('lifecycle') in ('READY', 'RUNNING'))
    if tab_request and message.get('type') == 'tabs' and roster:
        atomic_json(folder / ('browser-tabs-' + roster[0]['browser_instance'] + '.json'),
                    {'at': now, 'browser_instance': instance, 'tabs': roster})
    command = read_object(folder / 'browser-tab-command.json')
    activation = (command if command.get('browser_instance') == instance
                  and 0 < command.get('expires_at', 0) - now <= 5 else None)
    if (activation and message.get('type') == 'activation-result'
            and message.get('nonce') == activation.get('nonce')):
        from .browser_tabs import native_browser_owner
        matches = all(message.get(key) == activation.get(key) for key in ('tab_id', 'window_id', 'url'))
        owner = native_browser_owner() if message.get('ok') is True and matches else None
        atomic_json(folder / 'browser-tab-ack.json',
                    {'nonce': activation['nonce'], 'browser_instance': instance,
                     'ok': message.get('ok') is True and matches and owner is not None,
                     'owner': owner, **{key: message.get(key) for key in ('tab_id', 'window_id', 'url')},
                     'error': str(message.get('error', '') or ('Окно браузера не подтверждено' if not owner else ''))[:180]})
    active = (0 <= now - bridge.get("at", 0) < 6
              and bridge.get("exam_id") == journal.get("exam_id")
              and state.get("lifecycle") == "RUNNING"
              and (journal.get("environment") or {}).get("kind") in ("BROWSER", "BROWSER_TAB"))
    if active and message.get("type") == "observation":
        if message.get("binding") == bridge.get("binding") and bridge.get("binding"):
            item = message.get("observation", {})
            if isinstance(item, dict) and isinstance(item.get("focused"), bool):
                atomic_json(folder / "browser.json", {
                    "at": now, "exam_id": journal["exam_id"], "binding": bridge["binding"],
                    "observation": {"url": str(item.get("url", ""))[:2048],
                                    "focused": item["focused"], 'tab_id': item.get('tab_id'),
                                    'window_id': item.get('window_id'), 'browser_instance': instance},
                })
    return {
        "active": bool(active), "access": state.get("access", "OPEN"),
        "environment": journal.get("environment") if active else None,
        "device_id": read_object(folder / "config.json").get("device_id"),
        "session_id": journal.get("exam_id") if active else None,
        "binding": bridge.get("binding") if active else None, "mode": "OBSERVE",
        'request_tabs': tab_request, 'activate_tab': activation,
    }


def serve(folder, source, sink, browser_instance=None):
    while True:
        header = source.read(4)
        if len(header) != 4:
            return
        size = struct.unpack("=I", header)[0]
        if not 0 < size <= 65536:
            return
        data = source.read(size)
        if len(data) != size:
            return
        try:
            message = json.loads(data)
        except (ValueError, UnicodeError):
            return
        if not isinstance(message, dict):
            return
        reply = ({"active": False, "error": "BROWSER_PROFILE_MISMATCH"}
                 if browser_instance is not None and message.get("browser_instance") != browser_instance
                 else exchange(folder, message))
        payload = json.dumps(reply).encode()
        sink.write(struct.pack("=I", len(payload)) + payload)
        sink.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", type=Path, default=Path(sys.executable).parent / "binding.json" if getattr(sys, "frozen", False) else None)
    parser.add_argument("origin")
    args, _browser_args = parser.parse_known_args()
    if args.binding is None:
        parser.error("--binding is required outside a packaged installation")
    try:
        folder = load_binding(args.binding, args.origin)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return
    if sys.platform == "win32":
        import msvcrt
        import os
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    instance = read_object(args.binding).get("browser_instance")
    if not instance:
        print("BROWSER_PROFILE_NOT_BOUND", file=sys.stderr)
        return
    serve(folder, sys.stdin.buffer, sys.stdout.buffer, instance)


if __name__ == "__main__":
    main()
