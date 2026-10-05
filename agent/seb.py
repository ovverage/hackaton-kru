"""Experimental SEB adapter. It does not constitute verified STRICT capability.

The broker stays alive independently of CV and locks SEB on an expired agent lease.
No arbitrary command or externally supplied executable is accepted over HTTP.
"""
import argparse
import base64
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import plistlib
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
from .profile import read_object


def seb_file(settings):
    return gzip.compress(b"plnd" + gzip.compress(plistlib.dumps(settings, sort_keys=True), mtime=0), mtime=0)


def settings_for(url, quit_hash, *, server_url=None, secret=None, exam_id=None):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("SEB requires a valid HTTPS exam URL")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", quit_hash):
        raise ValueError("A SHA-256 staff quit password hash is required")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    values = {
        "startURL": url, "sebConfigPurpose": 0, "sebMode": 1 if server_url else 0,
        "createNewDesktop": True, "killExplorerShell": False,
        "sebServicePolicy": 2, "sebServiceIgnore": False,
        "allowQuit": True, "hashedQuitPassword": quit_hash.upper(),
        "hashedAdminPassword": quit_hash.upper(), "clipboardPolicy": 1,
        "hookKeys": True, "enableAltTab": False, "enableAltEsc": False,
        "enableAltF4": False, "enableEsc": False, "enableStartMenu": False,
        "enablePrintScreen": False, "enableRightMouse": False,
        "allowDeveloperConsole": False, "allowDownloads": False, "allowUploads": False,
        "allowPrint": False, "downloadAndOpenSebConfig": False,
        "browserWindowAllowAddressBar": False, "newBrowserWindowAllowAddressBar": False,
        "newBrowserWindowByLinkPolicy": 0, "browserViewMode": 1,
        "URLFilterEnable": True, "URLFilterEnableContentFilter": False,
        "URLFilterRules": [{"active": True, "regex": True, "action": 1,
                            "expression": "^" + re.escape(origin) + r"(?:/|$)"}],
        "insideSebEnableStartTaskManager": False, "insideSebEnableSwitchUser": False,
        "insideSebEnableLockThisComputer": False, "insideSebEnableLogOff": False,
        "insideSebEnableChangeAPassword": False, "insideSebEnableEaseOfAccess": False,
        "allowScreenSharing": False, "allowVirtualMachine": False,
        "examSessionReconfigureAllow": False, "sebServerFallback": False,
        "sebServerFallbackAttempts": 3, "sebServerFallbackTimeout": 1000,
        "sebServerFallbackAttemptInterval": 500, "enableSessionVerification": True,
        "removeBrowserProfile": True, "examSessionClearCookiesOnEnd": True,
        "examSessionClearCookiesOnStart": True,
    }
    if server_url:
        values.update(sebServerURL=server_url, sebServerConfiguration={
            "apiDiscovery": "api", "clientName": "qorgau", "clientSecret": secret,
            "institution": "qorgau", "exam": exam_id, "pingInterval": 1000,
        })
    return values


class Broker:
    def __init__(self, folder, exam_id, configuration):
        self.folder, self.exam_id, self.configuration = folder, exam_id, configuration
        self.secret = secrets.token_urlsafe(32)
        self.token = secrets.token_urlsafe(32)
        self.connection = secrets.token_urlsafe(32)
        self.locked = False
        self.last_ping = None
        self.instruction = None
        self.pending_confirmation = None
        self.mutex = threading.Lock()

    def ping(self, confirmation=None, now=None):
        with self.mutex:
            now = time.time() if now is None else now
            self.last_ping = now
            journal = read_object(self.folder / "journal.json")
            lease = read_object(self.folder / "bridge.json")
            state = journal.get("state", {})
            stale = not 0 <= now - lease.get("at", 0) <= 3 or lease.get("exam_id") != self.exam_id
            if journal.get("exam_id") != self.exam_id:
                stale = True
            if state.get("lifecycle") == "COMPLETED" and not stale:
                desired = "SEB_QUIT"
            elif stale or state.get("access") == "LOCKED" or state.get("lifecycle") != "RUNNING":
                desired = "SEB_FORCE_LOCK_SCREEN"
            else:
                desired = "NOTIFICATION_CONFIRM" if self.locked else ""
            if self.pending_confirmation and confirmation == self.pending_confirmation:
                self.pending_confirmation = None
                if self.instruction == "NOTIFICATION_CONFIRM":
                    self.locked = False
                    desired = ""
            if desired != self.instruction:
                self.instruction = desired
                self.pending_confirmation = secrets.token_hex(12) if desired else None
            if desired == "SEB_FORCE_LOCK_SCREEN":
                self.locked = True
            attributes = {"instruction-confirm": self.pending_confirmation or ""}
            if desired == "SEB_FORCE_LOCK_SCREEN":
                attributes["message"] = "Qorgau: контроль приостановлен. Дождитесь преподавателя." if not stale else "Qorgau: нет связи с агентом. Требуется сотрудник."
            if desired == "NOTIFICATION_CONFIRM":
                attributes.update(type="lockscreen", id=0)
            return {"instruction": desired, "attributes": attributes}

    def handler(self):
        broker = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def dispatch(self):
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 65536:
                    self.send_error(413)
                    return
                content = self.rfile.read(length) if length else b""
                path = urlsplit(self.path).path
                headers = {}
                if path == "/api" and self.command == "GET":
                    result = {"api-versions": [{"name": "v1", "endpoints": [
                        {"name": name, "location": endpoint} for name, endpoint in (
                            ("access-token-endpoint", "token"), ("seb-configuration-endpoint", "configuration"),
                            ("seb-handshake-endpoint", "handshake"), ("seb-log-endpoint", "log"), ("seb-ping-endpoint", "ping"))]}]}
                elif path == "/token":
                    expected = "Basic " + base64.b64encode(("qorgau:" + broker.secret).encode()).decode()
                    if not secrets.compare_digest(self.headers.get("Authorization", ""), expected):
                        self.send_error(401)
                        return
                    result = {"access_token": broker.token, "token_type": "bearer", "expires_in": 86400}
                else:
                    if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + broker.token):
                        self.send_error(401)
                        return
                    if path == "/handshake" and self.command == "POST":
                        result = [{"examId": broker.exam_id, "name": "Qorgau · проверка защищённой среды", "url": broker.configuration["startURL"], "lmsType": "Qorgau"}]
                        headers["SEBConnectionToken"] = broker.connection
                    else:
                        if not secrets.compare_digest(self.headers.get("SEBConnectionToken", ""), broker.connection):
                            self.send_error(401)
                            return
                        if path == "/configuration":
                            result = seb_file(broker.configuration)
                        elif path == "/ping":
                            from urllib.parse import parse_qs
                            values = parse_qs(content.decode())
                            result = broker.ping(values.get("instruction-confirm", [None])[0])
                        elif path in ("/handshake", "/log"):
                            result = {}
                        else:
                            self.send_error(404)
                            return
                payload = result if isinstance(result, bytes) else json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/seb" if isinstance(result, bytes) else "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(payload)
            do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = dispatch
        return Handler


def main():
    parser = argparse.ArgumentParser(description="Experimental SEB validation harness; not verified STRICT")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--quit-hash-file", type=Path, required=True)
    args = parser.parse_args()
    journal = read_object(args.data / "journal.json")
    if not journal.get("exam_id") or (journal.get("environment") or {}).get("kind") != "BROWSER":
        parser.error("Сначала назначьте браузерный сеанс этому компьютеру")
    quit_hash = args.quit_hash_file.read_text().strip()
    config = settings_for(journal["environment"]["url"], quit_hash)
    broker = Broker(args.data, journal["exam_id"], config)
    server = ThreadingHTTPServer(("127.0.0.1", 0), broker.handler())
    bootstrap = settings_for(config["startURL"], quit_hash, server_url=f"http://127.0.0.1:{server.server_port}/",
                             secret=broker.secret, exam_id=journal["exam_id"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(seb_file(bootstrap))
    print("Экспериментальная конфигурация готова:", args.output)
    print("Не закрывайте этот процесс во время проверки. Сначала завершите SEB с паролем сотрудника.")
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
