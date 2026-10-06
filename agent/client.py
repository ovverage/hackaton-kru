"""Local proctoring agent; optional Windows guard is owned by the GUI thread."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx

from shared.rules import RuleEngine, State


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.parent.chmod(0o700)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        if os.name != "nt":
            os.fchmod(stream.fileno(), 0o600)
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def inventory(config):
    """Only locally provisioned executables may be launched. Never use a shell."""
    entries = config.get("targets", [])
    found = []
    for entry in entries:
        path = Path(entry.get("executable", ""))
        if path.is_file() and entry.get("kind") in ("APP", "BROWSER"):
            found.append({**entry, "executable": str(path.resolve())})
    for ident, name, commands in (
        (
            "chrome",
            "Google Chrome / Chromium",
            ["google-chrome", "chromium", "chromium-browser"],
        ),
        ("edge", "Microsoft Edge", ["microsoft-edge", "msedge"]),
    ):
        if any(t["id"] == ident for t in found):
            continue
        executable = next((shutil.which(c) for c in commands if shutil.which(c)), None)
        if os.name == "nt":
            suffix = (
                "Google/Chrome/Application/chrome.exe"
                if ident == "chrome"
                else "Microsoft/Edge/Application/msedge.exe"
            )
            executable = next(
                (
                    str(Path(root) / suffix)
                    for root in [
                        os.getenv("PROGRAMFILES", ""),
                        os.getenv("PROGRAMFILES(X86)", ""),
                        os.getenv("LOCALAPPDATA", ""),
                    ]
                    if root and (Path(root) / suffix).is_file()
                ),
                executable,
            )
        if executable:
            found.append(
                {"id": ident, "name": name, "kind": "BROWSER", "executable": executable}
            )
    return found


class Agent:
    def __init__(self, folder: Path, server: str | None = None, transport=None):
        self.folder = folder
        self.config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        from .provision import server_address

        self.server = server_address(
            server or self.config["server"], testing=transport is not None
        )
        self.http = httpx.Client(
            base_url=self.server,
            headers={"Authorization": "Bearer " + self.config["token"]},
            timeout=3,
            transport=transport,
        )
        self.journal_path = folder / "journal.json"
        self.journal = (
            json.loads(self.journal_path.read_text(encoding="utf-8"))
            if self.journal_path.exists()
            else {
                "exam_id": None,
                "state": State().public(),
                "events": [],
                "acks": [],
                "processed": {},
                "reviews": {},
                "clock": 0,
                "media": [],
            }
        )
        self.engine = RuleEngine(
            State(
                **{
                    k: v
                    for k, v in self.journal["state"].items()
                    if k in State.__dataclass_fields__
                }
            )
        )
        self.origin = time.monotonic() - self.journal.get("clock", 0)
        self.targets = inventory(self.config)
        self.environment = self.journal.get("environment")
        self.status = "Подключение к серверу"
        self.last_synced_at = None
        self.camera_preparing = False
        self.session = self.journal.get("session")
        self.capabilities = {
            "strict": False,
            "camera": False,
            "recording": False,
            "platform": os.name,
        }
        self.camera = None
        self.recorder = None
        self.camera_fault = False
        self.browser_seen = None
        self.last_browser_event = {}
        self.mutex = threading.RLock()
        self.sync_mutex = threading.RLock()
        self.guard_target = None
        self.guard_started_at = None
        self.last_security_event = {}
        # A crashed/restarted active agent must not silently resume an unobserved exam.
        if self.engine.state.lifecycle == "RUNNING":
            self.engine.lock("AGENT_RESTARTED")
            self.save()

    def save(self):
        self.journal["state"] = self.engine.state.public()
        self.journal["clock"] = max(0, time.monotonic() - self.origin)
        self.journal["environment"] = self.environment
        self.journal["session"] = self.session
        atomic_json(self.journal_path, self.journal)

    def apply(self, command, now=None):
        now = time.time() if now is None else now
        ident = command["id"]
        if ident in self.journal["processed"]:
            self.journal["acks"].append(self.journal["processed"][ident])
            return
        ok, error = True, ""
        try:
            if command["exam_id"] != self.journal["exam_id"]:
                raise ValueError("SESSION_MISMATCH")
            if command["expires_at"] < now:
                raise ValueError("COMMAND_EXPIRED")
            kind = command["type"]
            if (
                kind != "REVIEW"
                and command.get("expected_version") != self.engine.state.version
            ):
                raise ValueError("STATE_CONFLICT")
            if kind == "START":
                if self.camera_preparing:
                    raise ValueError("CAMERA_PREPARING")
                if self.guarded:
                    if not self.capabilities.get("window_guard"):
                        raise ValueError("WINDOW_GUARD_UNAVAILABLE")
                    if not self.camera or not self.recorder:
                        raise ValueError("CAMERA_REQUIRED")
                self.launch_environment()
                self.engine.start()
                self.guard_started_at = time.monotonic()
            elif kind == "LOCK":
                self.engine.lock("TEACHER_LOCK")
            elif kind == "UNLOCK":
                if self.camera_fault:
                    raise ValueError("CAMERA_UNAVAILABLE")
                if self.guarded and not self.capabilities.get("guard_active"):
                    raise ValueError("WINDOW_GUARD_UNAVAILABLE")
                if self.guarded and self.capabilities.get("guard_fault"):
                    raise ValueError(self.capabilities["guard_fault"])
                self.engine.unlock(command.get("lock_id"), command["expected_version"])
            elif kind == "END_AND_RELEASE":
                self.engine.end()
                if self.recorder:
                    self.journal["media"].extend(self.recorder.completed(float("inf")))
            elif kind == "REVIEW":
                event_id = command["event_id"]
                revision = command["review_revision"]
                if revision > self.journal["reviews"].get(event_id, 0):
                    self.engine.review(event_id, command["decision"])
                    self.journal["reviews"][event_id] = revision
            else:
                raise ValueError("UNKNOWN_COMMAND")
        except (ValueError, OSError) as err:
            ok, error = False, str(err)
        ack = {"id": ident, "ok": ok, "error": error}
        self.journal["processed"][ident] = ack
        self.journal["acks"].append(ack)
        self.save()

    def launch_environment(self):
        env = self.environment or {}
        target = next(
            (
                t
                for t in self.targets
                if t["id"] == env.get("target_id") and t["kind"] == env.get("kind")
            ),
            None,
        )
        if not target:
            raise ValueError("TARGET_UNAVAILABLE")
        if target.get("window"):
            from .windows_guard import WindowsGuard, WindowTarget

            selected = WindowTarget(**target["window"])
            if not WindowsGuard().valid(selected):
                raise ValueError("TARGET_UNAVAILABLE")
            self.guard_target = selected.public()
            return
        if target["id"] == "qorgau-browser":
            from .exam_browser import origin

            if not origin(env.get("url", "")):
                raise ValueError("INVALID_URL")
            self.guard_target = {"builtin_url": env["url"]}
            return
        args = [target["executable"]]
        if target["kind"] == "BROWSER":
            url = env.get("url", "")
            if urlparse(url).scheme not in ("http", "https"):
                raise ValueError("INVALID_URL")
            if self.guarded:
                args.extend(
                    [
                        "--new-window",
                        "--kiosk",
                        "--no-first-run",
                        "--user-data-dir=" + str(self.folder / "exam-browser"),
                    ]
                )
            args.append(url)
        process = subprocess.Popen(
            args, shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.guard_target = {
            "launched_pid": process.pid,
            "executable": target["executable"],
        }

    def sync(self):
        with self.sync_mutex:
            self._sync()

    @property
    def guarded(self):
        return (self.environment or {}).get("guarded", False)

    def _sync(self):
        with self.mutex:
            sent_events = list(self.journal["events"])
            sent_acks = list(self.journal["acks"])
            payload = {
                "exam_id": self.journal["exam_id"],
                "state": self.engine.state.public(),
                "events": sent_events[:100],
                "acknowledgements": sent_acks[:100],
                "targets": [
                    {k: t[k] for k in ("id", "name", "kind", "guardable") if k in t}
                    for t in self.targets
                ],
                "capabilities": self.capabilities.copy(),
            }
        response = self.http.post("/api/agent/sync", json=payload)
        response.raise_for_status()
        config = response.json()
        with self.mutex:
            # Keep items captured during the HTTP request, even when an update shares an event ID.
            del self.journal["events"][: len(payload["events"])]
            del self.journal["acks"][: len(payload["acknowledgements"])]
            if config["exam_id"] != self.journal["exam_id"]:
                if self.journal["events"]:
                    raise ValueError("Нельзя сменить сеанс до синхронизации событий")
                self.engine = RuleEngine()
                self.journal.update(exam_id=config["exam_id"], reviews={}, processed={})
                self.journal["recent_events"] = []
                self.guard_target = None
                self.origin = time.monotonic()
                if self.recorder:
                    self.recorder.frames.clear()
            self.environment = config["environment"]
            for command in config["commands"]:
                self.apply(command)
            self.status = "Сервер подключён"
            self.last_synced_at = time.monotonic()
            self.session = config.get("session")
            self.save()

    def remember(self, events):
        """Keep lock evidence after the delivery queue has been acknowledged."""
        recent = self.journal.setdefault("recent_events", [])
        for event in events:
            old = next((e for e in recent if e["id"] == event["id"]), None)
            if old:
                old.update(event)
            elif not event.get("update"):
                recent.append(dict(event))
        del recent[:-50]

    def security_event(self, reason, *, lock=True):
        with self.mutex:
            if self.engine.state.lifecycle != "RUNNING":
                return
            t = time.monotonic() - self.origin
            if t - self.last_security_event.get(reason, -1e9) < 10:
                return
            self.last_security_event[reason] = t
            event = self.engine.event(
                reason, t, start=t, created_at=time.time(), category="TECHNICAL"
            )
            self.journal["events"].append(event)
            self.remember([event])
            if self.recorder:
                self.recorder.mark(event)
            if lock and self.engine.state.access != "LOCKED":
                self.engine.lock(reason)
            self.save()

    def teacher_unlock(self, password):
        # Serialize sync + unlock to prevent concurrent queue deletion/command delivery.
        with self.sync_mutex:
            self.sync()
            with self.mutex:
                state = self.engine.state
                body = {
                    "password": password,
                    "exam_id": self.journal["exam_id"],
                    "lock_id": state.lock_id,
                    "expected_version": state.version,
                }
            response = self.http.post("/api/agent/teacher-unlock", json=body)
            if response.status_code != 200:
                try:
                    detail = response.json()["detail"]
                except (ValueError, KeyError):
                    detail = "Не удалось проверить пароль преподавателя"
                raise ValueError(str(detail))
            self.sync()
            if self.engine.state.access == "LOCKED":
                raise ValueError(
                    "Причина блокировки сохраняется. Проверьте камеру, телефон и окно теста."
                )

    def upload_media(self):
        with self.mutex:
            queue = list(self.journal.get("media", []))
        for item in queue[:2]:
            file = Path(item["path"])
            with file.open("rb") as stream:
                response = self.http.post(
                    "/api/agent/media/" + item["event_id"],
                    content=stream,
                    headers={
                        "Content-Type": "video/mp4",
                        "X-Clip-Start": str(item["start"]),
                        "X-Clip-End": str(item["end"]),
                    },
                    timeout=20,
                )
            response.raise_for_status()
            with self.mutex:
                self.journal["media"].remove(item)
                self.save()
            file.unlink(missing_ok=True)

    def observe(self, direction="UNKNOWN", phone_confidence=0.0, faces=1, frame=None):
        with self.mutex:
            t = time.monotonic() - self.origin
            before = self.engine.state.version
            events = self.engine.observe(t, direction, phone_confidence, faces)
            for event in events:
                if not event.get("update"):
                    event["created_at"] = time.time()
            self.journal["events"].extend(events)
            self.remember(events)
            if frame is not None and events:
                import cv2

                for event in events:
                    if not event.get("update"):
                        ok, encoded = cv2.imencode(
                            ".jpg", cv2.resize(frame, (320, 240))
                        )
                        if ok:
                            folder = self.folder / "evidence"
                            folder.mkdir(exist_ok=True)
                            path = folder / (event["id"] + ".jpg")
                            path.write_bytes(encoded.tobytes())
                            self.remember([{**event, "thumbnail_path": str(path)}])
            if self.recorder and frame is not None:
                self.recorder.push(t, frame)
                for event in events:
                    if not event.get("update"):
                        self.recorder.mark(event)
                self.journal["media"].extend(self.recorder.completed(t))
            if events or before != self.engine.state.version:
                self.save()

    def read_browser(self):
        if (self.environment or {}).get("target_id") == "qorgau-browser":
            return
        path = self.folder / "browser.json"
        if not path.exists():
            return
        try:
            observation = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if observation.get("at") == self.browser_seen:
            return
        self.browser_seen = observation.get("at")
        if time.time() - observation.get("at", 0) > 5:
            return
        env = self.environment or {}
        if self.engine.state.lifecycle != "RUNNING" or env.get("kind") != "BROWSER":
            return
        data = observation.get("observation", {})
        parsed = urlparse(data.get("url", ""))
        allowed = urlparse(env.get("url", ""))
        wrong_origin = (parsed.scheme, parsed.netloc) != (
            allowed.scheme,
            allowed.netloc,
        )
        if not wrong_origin and data.get("focused", True):
            return
        reason = "OTHER_SITE" if wrong_origin else "BROWSER_NOT_FOCUSED"
        t = time.monotonic() - self.origin
        if t - self.last_browser_event.get(reason, -1e9) < 10:
            return
        with self.mutex:
            # Store origin only, never arbitrary browsing paths or query tokens.
            event = self.engine.event(
                "BROWSER_ATTEMPT",
                t,
                start=t,
                created_at=time.time(),
                detail=reason,
                origin=f"{parsed.scheme}://{parsed.netloc}",
            )
            self.journal["events"].append(event)
            self.remember([event])
            if self.guarded and wrong_origin and self.engine.state.access != "LOCKED":
                self.engine.lock("BROWSER_ATTEMPT")
            if self.recorder:
                self.recorder.mark(event)
            self.last_browser_event[reason] = t
            self.save()

    def snapshot(self):
        with self.mutex:
            return {
                "state": self.engine.state.public(),
                "status": self.status,
                "environment": self.environment,
                "pending": len(self.journal["events"]),
                "camera": self.capabilities["camera"],
                "camera_fault": self.camera_fault,
                "camera_preparing": self.camera_preparing,
                "connected": self.last_synced_at is not None
                and time.monotonic() - self.last_synced_at < 6,
                "session": self.session,
                "exam_id": self.journal.get("exam_id"),
                "device_name": self.config.get("name", "Компьютер аудитории"),
                "pending_media": len(self.journal.get("media", [])),
                "recent_events": list(self.journal.get("recent_events", [])),
                "guarded": self.guarded,
                "guard_active": self.capabilities.get("guard_active", False),
                "environment_name": next(
                    (
                        t["name"]
                        for t in self.targets
                        if t["id"] == (self.environment or {}).get("target_id")
                    ),
                    "Среда не выбрана",
                ),
            }

    def run(self, stop):
        last_sync = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = None
            media_future = None
            last_upload = 0
            while not stop.is_set():
                if future and future.done():
                    try:
                        future.result()
                    except (httpx.HTTPError, ValueError, OSError) as error:
                        self.status = (
                            "Нет связи с сервером · данные остаются на компьютере"
                        )
                        print(type(error).__name__ + ": " + str(error), flush=True)
                    future = None
                if media_future and media_future.done():
                    try:
                        media_future.result()
                    except (httpx.HTTPError, OSError):
                        pass  # durable queue retries after reconnect
                    media_future = None
                if (
                    media_future is None
                    and self.journal.get("media")
                    and time.monotonic() - last_upload >= 3
                ):
                    last_upload = time.monotonic()
                    media_future = pool.submit(self.upload_media)
                if future is None and time.monotonic() - last_sync >= 1:
                    last_sync = time.monotonic()
                    future = pool.submit(self.sync)
                try:
                    if self.guarded and self.engine.state.lifecycle == "RUNNING":
                        last_ok = (
                            self.last_synced_at
                            or self.guard_started_at
                            or time.monotonic()
                        )
                        if time.monotonic() - last_ok > 10:
                            self.security_event("SERVER_UNAVAILABLE")
                        if (
                            self.guard_started_at
                            and time.monotonic() - self.guard_started_at > 10
                            and not self.capabilities.get("guard_active")
                        ):
                            self.security_event("GUARD_UNAVAILABLE")
                    self.read_browser()
                    if self.camera and self.engine.state.lifecycle == "RUNNING":
                        frame, observation = self.camera.read()
                        self.camera_fault = False
                        self.observe(frame=frame, **observation)
                    else:
                        stop.wait(0.1)
                except (OSError, RuntimeError) as err:
                    with self.mutex:
                        if not self.camera_fault:
                            t = time.monotonic() - self.origin
                            self.journal["events"].append(
                                self.engine.event(
                                    "CAMERA_UNAVAILABLE",
                                    t,
                                    created_at=time.time(),
                                    detail=str(err),
                                )
                            )
                            self.engine.lock("CAMERA_UNAVAILABLE")
                            self.remember(self.journal["events"][-1:])
                            self.camera_fault = True
                            self.save()
                    stop.wait(1)
            with self.mutex:
                if self.recorder:
                    self.journal["media"].extend(self.recorder.completed(float("inf")))
                self.save()
        if self.camera:
            self.camera.close()
        if self.recorder:
            self.recorder.close()
        self.http.close()


def main():
    parser = argparse.ArgumentParser(description="Qorgau — локальный агент наблюдения")
    parser.add_argument("--data", type=Path, default=Path.home() / ".qorgau")
    parser.add_argument("--server", default=None)
    parser.add_argument("--enroll", metavar="CODE")
    parser.add_argument("--name", default="Компьютер аудитории")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument(
        "--show",
        action="store_true",
        help="Открыть окно состояния вместо запуска только в трее",
    )
    parser.add_argument(
        "--camera", type=int, default=None, help="Явно включить указанную камеру"
    )
    parser.add_argument("--phone-model", type=Path)
    parser.add_argument("--face-model", type=Path)
    parser.add_argument(
        "--self-test",
        type=Path,
        help="Write a packaged runtime diagnostic and exit without camera or input hooks",
    )
    args = parser.parse_args()
    if args.self_test:
        from .selftest import selftest

        selftest(args.self_test)
        return
    if args.enroll:
        from .provision import enroll

        try:
            enroll(
                args.data,
                args.server or "http://127.0.0.1:8000",
                args.enroll,
                args.name,
            )
        except ValueError as error:
            parser.error(str(error))
        print("Компьютер зарегистрирован. Запустите приложение ученика.")
        return
    if not args.headless and args.camera is None:
        from .desktop import launch

        launch(args.data, args.server, show_window=args.show)
        return
    if not (args.data / "config.json").exists():
        parser.error(
            "Сначала зарегистрируйте компьютер через приложение ученика или --enroll"
        )
    agent = Agent(args.data, args.server)
    if args.camera is not None:
        if not args.phone_model or not args.face_model:
            parser.error(
                "Для камеры требуются --phone-model и --face-model с локальными файлами"
            )
        from .vision import Camera, ClipRecorder

        agent.camera = Camera(args.camera, args.phone_model, args.face_model)
        agent.recorder = ClipRecorder(args.data / "clips")
        agent.capabilities.update(
            camera=True, recording=True, vision="experimental-calibrated-iris"
        )
    stop = threading.Event()
    if args.headless:
        try:
            agent.run(stop)
        except KeyboardInterrupt:
            stop.set()
            agent.save()
    else:
        from .desktop import show

        show(agent, stop)


if __name__ == "__main__":
    main()
