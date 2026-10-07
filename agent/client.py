"""Local proctoring agent; optional Windows guard is owned by the GUI thread."""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import httpx

from shared.rules import PHONE_CONFIDENCE_THRESHOLD, RuleEngine, State
from shared.storage import atomic_json
from shared.version import APP_VERSION, MODEL_VERSION


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
        if self.config.get("selected_target"):
            self.targets.append(self.config["selected_target"])
        self.environment = self.journal.get("environment")
        self.status = "Подключение к серверу"
        self.last_synced_at = None
        self.camera_preparing = False
        self.session = self.journal.get("session")
        self.capabilities = {
            "agent_version": APP_VERSION,
            "model_version": MODEL_VERSION,
            "strict": False,
            "camera": False,
            "recording": False,
            "gaze": False,
            "platform": os.name,
        }
        self.camera = None
        self.capture_pump = None
        self.record_until = None
        self.recorder = None
        self.camera_fault = False
        self.last_observation = None
        self.last_observation_at = None
        self.gaze_diagnostics = None
        self.recognition_paused = False
        self._paused_camera = None
        self._recognition_after = self.origin
        self.browser_seen = None
        self.bridge_binding = secrets.token_urlsafe(32)
        self.last_browser_event = {}
        self.mutex = threading.RLock()
        self.sync_mutex = threading.RLock()
        self.guard_target = None
        self.guard_started_at = None
        self.last_security_event = {}

        # Recover encoded clips and unfinished segments even before reopening the
        # camera. Acknowledgements must update this single recorder instance.
        if (folder / "clips/recording.json").is_file():
            try:
                from .recording import ClipRecorder
                self.recorder = ClipRecorder(folder / "clips")
                if self.engine.state.lifecycle == "COMPLETED":
                    expired = self.recorder.expire_completed()
                    expired_paths = {item.get("path") for item in expired}
                    self.journal["media"] = [item for item in self.journal.get("media", []) if item["path"] not in expired_paths]
                    self.journal.setdefault("media_expiry", []).extend(expired)
                self.collect_media(float("inf"))
                self.save()
            except (OSError, ValueError, ImportError) as error:
                self.status = "Не удалось восстановить очередь видео: " + str(error)
                self.camera_fault = True
        # A crashed/restarted active agent must not silently resume an unobserved exam.
        if self.engine.state.lifecycle == "RUNNING":
            self.engine.lock("AGENT_RESTARTED")
            self.save()

    def save(self):
        with self.mutex:
            self.update_recognition_mode()
            self.journal["state"] = self.engine.state.public()
            self.journal["clock"] = max(0, time.monotonic() - self.origin)
            self.journal["environment"] = self.environment
            self.journal["session"] = self.session
            atomic_json(self.journal_path, self.journal)

    def update_recognition_mode(self):
        """Caller holds mutex; pausing inference must not stop evidence capture."""
        paused = self.engine.state.access == "LOCKED" or self.engine.state.lifecycle == "COMPLETED"
        changed = paused != self.recognition_paused
        if self.capture_pump:
            self.capture_pump.set_recognition(not paused)
        if paused and self.camera is not None and self._paused_camera is not self.camera:
            changed = True
            # We no longer measure incident duration after the final analyzed
            # frame. Otherwise stale phone/absence flags would prevent unlock.
            t = self.engine.last_t
            if t is None:
                t = max(0, time.monotonic() - self.origin)
            for event_id in filter(None, [self.engine.active_event, *self.engine.duration_events.values()]):
                update = {"id": event_id, "update": True, "end": t}
                self.journal["events"].append(update)
                self.remember([update])
                if self.recorder:
                    self.recorder.mark(update)
            self.engine.reset_observation()
            self.last_observation = None
            self.last_observation_at = None
            self._paused_camera = self.camera
        elif not paused and self.recognition_paused:
            self._recognition_after = time.monotonic()
            self.last_observation = None
            self.last_observation_at = None
            self._paused_camera = None
        self.recognition_paused = paused
        return changed

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
                kind not in ("REVIEW", "UNLOCK", "END_AND_RELEASE")
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

                if command.get("require_camera", True) and (not self.camera or not self.recorder or self.camera_fault):
                    raise ValueError("CAMERA_NOT_READY")
                if command.get("require_camera", True):
                    if self.last_observation_at is None or time.monotonic() - self.last_observation_at > 2:
                        raise ValueError("CAMERA_FRAME_STALE")
                    if self.last_observation["faces"] != 1:
                        raise ValueError("NEED_EXACTLY_ONE_FACE")
                    if self.last_observation["phone_confidence"] >= PHONE_CONFIDENCE_THRESHOLD:
                        raise ValueError("REMOVE_PHONE_BEFORE_START")
                    if shutil.disk_usage(self.folder).free < 1024**3:
                        raise ValueError("NEED_1GB_RECORDING_SPACE")
                self.launch_environment()
                self.engine.start()
                self.guard_started_at = time.monotonic()
            elif kind == "LOCK":
                self.engine.lock("TEACHER_LOCK")
            elif kind == "UNLOCK":
                if self.camera_fault or (command.get("require_camera", True) and not self.camera):
                    raise ValueError("CAMERA_UNAVAILABLE")
                if self.guarded and not self.capabilities.get("guard_active"):
                    raise ValueError("WINDOW_GUARD_UNAVAILABLE")
                if self.guarded and self.capabilities.get("guard_fault"):
                    raise ValueError(self.capabilities["guard_fault"])
                # Review acknowledgements may bump the version without changing
                # this lock. The lock identity, not an unrelated review, owns consent.
                if command.get("expected_version", -1) > self.engine.state.version:
                    raise ValueError("STATE_CONFLICT")
                self.update_recognition_mode()
                intervals = [self.engine.active_event, *self.engine.duration_events.values()]
                self.engine.unlock(command.get("lock_id"), self.engine.state.version)
                for event_id in filter(None, intervals):
                    update = {"id": event_id, "update": True, "end": time.monotonic() - self.origin}
                    self.journal["events"].append(update)
                    self.remember([update])
                    if self.recorder:
                        self.recorder.mark(update)
            elif kind == "END_AND_RELEASE":
                # The teacher ends this exam even if a new incident or review
                # changed its version while the command was in flight.
                if not 0 <= command.get("expected_version", -1) <= self.engine.state.version:
                    raise ValueError("STATE_CONFLICT")
                t = time.monotonic() - self.origin
                for event_id in [self.engine.active_event, *self.engine.duration_events.values()]:
                    if event_id:
                        update = {"id": event_id, "update": True, "end": t}
                        self.journal["events"].append(update)
                        if self.recorder:
                            self.recorder.mark(update)
                self.engine.end()
                self.engine.reset_observation()
                self.record_until = time.monotonic() + 5 if self.camera else None
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
        if env.get("kind") == "DESKTOP":
            selected = next((t for t in self.targets if t.get("id") == "primary-window"), None)
            if selected:
                if self.guarded and selected.get("guardable") is False:
                    raise ValueError("BROWSER_REQUIRES_QORGAU_BROWSER")
                from .windows_guard import WindowsGuard, WindowTarget
                target = WindowTarget(**selected["window"])
                if not WindowsGuard().valid(target):
                    raise ValueError("TARGET_UNAVAILABLE")
                self.guard_target = target.public()
            else:
                self.guard_target = {"desktop": True}
            return
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
        if self.guarded and (target.get("guardable") is False or (
            env.get("kind") == "BROWSER" and target["id"] != "qorgau-browser"
        )):
            raise ValueError("BROWSER_REQUIRES_QORGAU_BROWSER")
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
                "capabilities": {**self.capabilities, "camera_fault": self.camera_fault, "recording_tail": self.record_until is not None},
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
                # Preserve a calibration prepared before assignment, including
                # a second exam. END already closes the previous capture tail.
                self.last_observation = None
                self.last_observation_at = None
                self.gaze_diagnostics = None
                self.engine = RuleEngine()
                self.journal.update(exam_id=config["exam_id"], reviews={}, processed={})
                self.journal["recent_events"] = []
                self.guard_target = None
                self.origin = time.monotonic()
                self._recognition_after = self.origin
                if self.capture_pump:
                    # A queued frame belongs to the old exam's clock.
                    self.capture_pump.set_recognition(False)
                    self.capture_pump.set_recognition(True)
                if self.recorder:
                    self.collect_media(float("inf"))
                    self.recorder.reset_session(config["exam_id"])
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

    def teacher_unlock(self, password, action="UNLOCK"):
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
                    "action": action,
                }
            response = self.http.post("/api/agent/teacher-unlock", json=body)
            if response.status_code != 200:
                try:
                    detail = response.json()["detail"]
                except (ValueError, KeyError):
                    detail = "Не удалось проверить пароль преподавателя"
                raise ValueError(str(detail))
            self.sync()
            # Confirm application immediately, even if the background worker
            # stopped because of a camera/runtime failure.
            self.sync()
            if action == "END_AND_RELEASE" and self.engine.state.lifecycle != "COMPLETED":
                raise ValueError("Не удалось завершить сеанс. Обновите состояние и повторите.")
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
                        "X-Clip-Quality": json.dumps({"complete": item.get("complete"), "gaps": item.get("gaps", [])[:200]}),
                    },
                    timeout=20,
                )
            response.raise_for_status()
            with self.mutex:
                self.journal["media"].remove(item)
                self.save()
                if self.recorder:
                    self.recorder.acknowledge(item["event_id"], item["path"])
            file.unlink(missing_ok=True)

    def collect_media(self, t):
        existing = {x["path"] for x in self.journal["media"]}
        fresh = [x for x in self.recorder.completed(t) if x["path"] not in existing]
        if fresh:
            self.journal["media"].extend(fresh)
            self.save()

    def expire_local_media(self):
        with self.mutex:
            if self.engine.state.lifecycle != "COMPLETED" or self.record_until is not None:
                return
            for thumbnail in (self.folder / "evidence").glob("*.jpg"):
                if thumbnail.stat().st_mtime + 7 * 86400 <= time.time():
                    thumbnail.unlink()
            if not self.recorder:
                return
            expired = self.recorder.expire_completed()
            if expired:
                paths = {item.get("path") for item in expired}
                self.journal["media"] = [item for item in self.journal["media"] if item["path"] not in paths]
                self.journal.setdefault("media_expiry", []).extend(expired)
                self.save()

    def record_frame(self, frame, *, captured_at=None):
        """Record raw frames while recognition is paused, without invented observations."""
        with self.mutex:
            changed = self.update_recognition_mode()
            at = time.monotonic() if captured_at is None else captured_at
            if self.recorder and frame is not None and math.isfinite(at) and at <= time.monotonic() + .1:
                t = at - self.origin
                last_t = getattr(self.recorder, "last_t", None)
                if t >= 0 and (last_t is None or t > last_t):
                    self.recorder.push(t, frame)
                    self.collect_media(t)
            if changed:
                self.save()

    def observe(self, direction="UNKNOWN", phone_confidence=0.0, faces=1, frame=None, phone_aiming=False, detections=(), captured_at=None, gaze_diagnostics=None):
        with self.mutex:
            mode_changed = self.update_recognition_mode()
            now = time.monotonic()
            at = now if captured_at is None else captured_at
            if (self.recognition_paused or not math.isfinite(at)
                    or at < max(self.origin, self._recognition_after)
                    or at > now + .1 or now - at > 2
                    or (self.last_observation_at is not None
                        and (at < self.last_observation_at
                             or (captured_at is not None and at == self.last_observation_at)))):
                self.record_frame(frame, captured_at=at)
                if mode_changed:
                    self.save()
                return
            t = at - self.origin
            interval_ms = None if self.last_observation_at is None else (at - self.last_observation_at) * 1000
            self.last_observation = {"faces": faces, "phone_confidence": phone_confidence}
            self.last_observation_at = at
            diagnostics = gaze_diagnostics or {}
            self.gaze_diagnostics = {
                "direction": direction,
                **{key: diagnostics.get(key) for key in (
                    "offscreen_probability", "head_yaw", "head_pitch", "source", "reference_ready",
                    "attention_away", "attention_direction",
                )},
                "interval_ms": interval_ms,
            }
            detections = [d for d in detections if d.get("label") != "phone"
                          or d.get("confidence", 0) >= PHONE_CONFIDENCE_THRESHOLD]
            before = self.engine.state.version
            events = self.engine.observe(t, direction, phone_confidence, faces, phone_aiming)
            if self.capture_pump and self.engine.state.access == "LOCKED":
                self.capture_pump.set_recognition(False)
            if frame is not None:
                from .evidence import annotate
                frame = annotate(frame, detections)
            for event in events:
                if not event.get("update"):
                    event["created_at"] = time.time()
                    event["model_version"] = MODEL_VERSION
                    event["detections"] = list(detections)
            self.journal["events"].extend(events)
            self.remember(events)
            if frame is not None and events:
                import cv2

                for event in events:
                    if not event.get("update"):
                        ok, encoded = cv2.imencode(
                            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85]
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
                    self.recorder.mark(event)
                self.collect_media(t)
            if events or before != self.engine.state.version or mode_changed:
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
        if (observation.get("exam_id") != self.journal.get("exam_id")
                or observation.get("binding") != self.bridge_binding):
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
                "gaze": self.capabilities.get("gaze", False) and self.capabilities["camera"] and not self.camera_fault,
                "camera_fault": self.camera_fault,
                "camera_preparing": self.camera_preparing,
                "recognition_paused": self.recognition_paused,
                "gaze_diagnostics": dict(self.gaze_diagnostics) if self.gaze_diagnostics else None,
                "gaze_seconds": self.engine.seconds,
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
                    "Контроль рабочего стола" if (self.environment or {}).get("kind") == "DESKTOP" else "Ожидание сеанса",
                ),
            }

    def run(self, stop):
        last_sync = 0
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = None
            media_future = None
            last_upload = 0
            last_bridge = 0
            last_expiry = 0
            while not stop.is_set():
                if time.monotonic() - last_bridge >= 1:
                    atomic_json(self.folder / "bridge.json", {
                        "at": time.time(), "exam_id": self.journal.get("exam_id"),
                        "binding": self.bridge_binding,
                    })
                    last_bridge = time.monotonic()
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
                    and time.monotonic() - last_expiry >= 60
                ):
                    self.expire_local_media()
                    last_expiry = time.monotonic()
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
                    should_capture = self.camera and (self.engine.state.lifecycle != "COMPLETED" or self.record_until is not None)
                    if should_capture:
                        with self.mutex:
                            if self.update_recognition_mode():
                                self.save()
                            if self.capture_pump is None:
                                from .capture import CapturePump
                                self.capture_pump = CapturePump(self.camera, recognize=not self.recognition_paused)
                            captured = self.capture_pump.poll()
                            if captured:
                                frame, observation = captured
                                self.camera_fault = False
                                if observation is None:
                                    self.record_frame(frame, captured_at=self.capture_pump.last_captured_at)
                                else:
                                    self.observe(frame=frame, **observation)
                        if not captured:
                            stop.wait(.02)
                    else:
                        stop.wait(0.1)
                except (OSError, RuntimeError) as err:
                    with self.mutex:
                        if not self.camera_fault and self.engine.state.lifecycle == "RUNNING":
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
                        elif self.engine.state.lifecycle != "RUNNING":
                            self.camera_fault = True
                    stop.wait(1)
                if self.record_until is not None and time.monotonic() >= self.record_until:
                    with self.mutex:
                        if self.recorder:
                            self.collect_media(float("inf"))
                            self.recorder.discard_buffer()
                        self.record_until = None
                        if self.capture_pump:
                            self.capture_pump.close()
                            self.capture_pump = None
                        if self.camera:
                            self.camera.close()
                            self.camera = None
                        self.capabilities.update(camera=False, recording=False, gaze=False)
            with self.mutex:
                if self.recorder:
                    self.collect_media(float("inf"))
                    self.recorder.discard_buffer()
                self.save()
        if self.capture_pump:
            self.capture_pump.close()
        if self.camera:
            self.camera.close()
        if self.recorder:
            self.recorder.close()
        self.http.close()
        (self.folder / "bridge.json").unlink(missing_ok=True)


def main():
    from .install_guard import hold_installation_mutex

    hold_installation_mutex()
    parser = argparse.ArgumentParser(description="Qorgau — локальный агент наблюдения")
    parser.add_argument("--data", type=Path, default=None)
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
    parser.add_argument("--calibrate-gaze", action="store_true", help="Дополнительно настроить контроль взгляда при --camera")
    parser.add_argument("--phone-model", type=Path)
    parser.add_argument("--face-model", type=Path)
    parser.add_argument("--self-test", type=Path, help="Write a hardware-free model/video diagnostic report")
    parser.add_argument("--install-extension", metavar="EXTENSION_ID")
    parser.add_argument("--browser-instance")
    parser.add_argument("--browser", choices=["edge", "chrome"], default="edge")
    parser.add_argument("--replace-binding", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        from .selftest import run
        run(args.self_test)
        return
    from .provision import auto_enroll, bootstrap_data_dir
    from shared.bootstrap import default_bootstrap, read_bootstrap

    try:
        bootstrap = (
            read_bootstrap(Path(sys.executable))
            if getattr(sys, "frozen", False)
            else None
        )
        if bootstrap is None and not args.enroll:
            bootstrap = default_bootstrap(args.server)
    except ValueError as error:
        if args.headless:
            parser.error(str(error))
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841 - retain Qt lifetime
        QMessageBox.critical(None, "Не удалось открыть Qorgau", str(error))
        return
    args.data = args.data or (
        bootstrap_data_dir(bootstrap) if bootstrap else Path.home() / ".qorgau"
    )
    if bootstrap and args.server and args.server != bootstrap["server"]:
        parser.error("Адрес сервера задан в EXE. Скачайте пакет для нужного сервера.")
    if args.install_extension:
        from .native_install import install
        if not args.browser_instance:
            parser.error("Нужен --browser-instance из страницы настройки расширения")
        install(args.data, args.install_extension, args.browser_instance, args.browser, args.replace_binding)
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

        launch(args.data, args.server, show_window=args.show, bootstrap=bootstrap)
        return
    if bootstrap and not (args.data / "config.json").exists():
        try:
            auto_enroll(args.data, bootstrap)
        except ValueError as error:
            parser.error(str(error))
    if not (args.data / "config.json").exists():
        parser.error(
            "Сначала зарегистрируйте компьютер через приложение ученика или --enroll"
        )
    agent = Agent(args.data, args.server)
    if args.camera is not None:
        if not args.phone_model or not args.face_model:
            from .resources import verified_models
            args.phone_model, args.face_model = verified_models()
        from .vision import Camera, ClipRecorder

        agent.camera = Camera(args.camera, args.phone_model, args.face_model, calibrate=args.calibrate_gaze)
        agent.recorder = ClipRecorder(args.data / "clips")
        agent.capabilities.update(
            camera=True, recording=True, gaze=bool(agent.camera.centres) or bool(getattr(agent.camera, 'gaze_enabled', False)),
            vision="experimental-calibrated-iris" if agent.camera.centres else "yolo11n-phone/yolov8n-face/mediapipe-auto-gaze"
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
