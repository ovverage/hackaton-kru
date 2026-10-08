"""Native activation acknowledgements cannot cross profile, nonce, or expiry."""
import io
import json
import struct
from types import SimpleNamespace

import pytest

from agent.native_host import serve
from agent.browser_tabs import activate
from agent.profile import read_object
from agent.windows_guard import WindowTarget
from shared.storage import atomic_json


BOUND = "dd505740-53b0-4018-bb7c-33aaea1f19c6"
OTHER = "2cc2086b-6813-448d-9c14-c6da66c6dfdc"
TARGET = {"kind": "BROWSER_TAB", "id": BOUND + ":1", "browser_instance": BOUND,
          "tab_id": 1, "window_id": 2, "url": "https://exam.test/", "title": "Exam"}
WINDOW = WindowTarget(42, 73, "Exam - Chrome", "C:/browser/chrome.exe", 99)


@pytest.mark.parametrize("change", [
    {"browser_instance": OTHER}, {"nonce": "other-request"}, {"expired": True},
])
def test_activation_ack_rejects_wrong_owner_nonce_or_expired_command(tmp_path, monkeypatch, change):
    monkeypatch.setattr("agent.native_host.time", SimpleNamespace(time=lambda: 100.))
    atomic_json(tmp_path / "browser-tab-command.json", {
        "browser_instance": BOUND, "nonce": "request", "expires_at": 99 if change.get("expired") else 104,
        "tab_id": 1, "window_id": 2, "url": "https://exam.test/",
    })
    message = {"type": "activation-result", "nonce": "request", "browser_instance": BOUND, "ok": True}
    message.update({key: value for key, value in change.items() if key != "expired"})
    data = json.dumps(message).encode()
    output = io.BytesIO()
    serve(tmp_path, io.BytesIO(struct.pack("=I", len(data)) + data), output, BOUND)
    assert not (tmp_path / "browser-tab-ack.json").exists()
    reply = json.loads(output.getvalue()[4:])
    if change.get("browser_instance") == OTHER:
        assert reply == {"active": False, "error": "BROWSER_PROFILE_MISMATCH"}


def test_owned_fresh_activation_ack_is_linked_to_only_that_request(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.native_host.time", SimpleNamespace(time=lambda: 100.))
    monkeypatch.setattr("agent.browser_tabs.native_browser_owner", lambda: WINDOW.public())
    atomic_json(tmp_path / "browser-tab-command.json", {
        "browser_instance": BOUND, "nonce": "request", "expires_at": 104,
        "tab_id": 1, "window_id": 2, "url": "https://exam.test/",
    })
    data = json.dumps({**TARGET, "type": "activation-result", "nonce": "request", "ok": True}).encode()
    serve(tmp_path, io.BytesIO(struct.pack("=I", len(data)) + data), io.BytesIO(), BOUND)
    assert read_object(tmp_path / "browser-tab-ack.json") == {
        "nonce": "request", "browser_instance": BOUND, "ok": True, "error": "", "owner": WINDOW.public(),
        "tab_id": 1, "window_id": 2, "url": "https://exam.test/",
    }


@pytest.mark.parametrize("change", [{"tab_id": 9}, {"window_id": 9}, {"url": "https://other.test/"}, {"ok": False}])
def test_native_ack_does_not_accept_mismatched_actual_tab_even_with_correct_nonce(tmp_path, monkeypatch, change):
    monkeypatch.setattr("agent.native_host.time", SimpleNamespace(time=lambda: 100.))
    monkeypatch.setattr("agent.browser_tabs.native_browser_owner", lambda: WINDOW.public())
    atomic_json(tmp_path / "browser-tab-command.json", {**TARGET, "nonce": "request", "expires_at": 104})
    data = json.dumps({**TARGET, "type": "activation-result", "nonce": "request", "ok": True, **change}).encode()
    serve(tmp_path, io.BytesIO(struct.pack("=I", len(data)) + data), io.BytesIO(), BOUND)
    assert read_object(tmp_path / "browser-tab-ack.json")["ok"] is False


@pytest.mark.parametrize("changed", [None, "hwnd", "pid", "started", "executable", "tab_id", "window_id", "url"])
def test_activation_requires_acknowledged_window_identity_and_exact_tab(tmp_path, monkeypatch, changed):
    monkeypatch.setattr("agent.browser_tabs.available", lambda _: [TARGET])
    monkeypatch.setattr("agent.browser_tabs.uuid4", lambda: SimpleNamespace(hex="request"))
    monkeypatch.setattr("agent.windows_guard.WindowsGuard", lambda: SimpleNamespace(
        info=lambda _: WINDOW, u=SimpleNamespace(GetForegroundWindow=lambda: WINDOW.hwnd)))
    reply = {**TARGET, "nonce": "request", "ok": True, "owner": WINDOW.public()}
    if changed in ("hwnd", "pid", "started"):
        reply["owner"][changed] += 1
    elif changed == "executable":
        reply["owner"][changed] = "C:/different/chrome.exe"
    elif changed in ("tab_id", "window_id"):
        reply[changed] += 1
    elif changed == "url":
        reply[changed] = "https://other.test/"
    atomic_json(tmp_path / "browser-tab-ack.json", reply)
    if changed is None:
        selected, window = activate(tmp_path, TARGET)
        assert selected == TARGET and window is WINDOW
    else:
        with pytest.raises(ValueError, match="Браузер"):
            activate(tmp_path, TARGET)
