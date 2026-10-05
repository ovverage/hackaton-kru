"""Durable 10-second JPEG segments and incident clips with bounded frame memory."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time
from uuid import uuid4
import cv2
from shared.storage import atomic_json
from .resources import ffmpeg_executable

HEADER = struct.Struct("<dI")


def frames(path):
    """A torn final record cannot corrupt earlier, closed records."""
    with path.open("rb") as stream:
        while header := stream.read(HEADER.size):
            if len(header) != HEADER.size:
                return
            at, size = HEADER.unpack(header)
            if not 0 < size < 8 * 1024**2:
                return
            jpeg = stream.read(size)
            if len(jpeg) != size:
                return
            yield at, jpeg


class ClipRecorder:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.segments = folder / "segments"
        self.segments.mkdir(exist_ok=True)
        self.manifest = folder / "recording.json"
        self.ffmpeg = ffmpeg_executable()
        data = json.loads(self.manifest.read_text()) if self.manifest.exists() else {}
        self.pending = data.get("pending", {})
        self.ready = {k: v if isinstance(v, list) else [v] for k, v in data.get("ready", {}).items()}
        self.index = []
        for path in self.segments.glob("*.seg"):
            start = end = None
            for at, _jpeg in frames(path):
                if start is None:
                    start = at
                end = at
            if start is not None:
                self.index.append({"path": path, "start": start, "end": end})
        self.index.sort(key=lambda x: x["start"])
        self.last_t = self.index[-1]["end"] if self.index else None
        self.current = None
        self.stream = None
        self.encoding = {}
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.session = data.get("session")

    def save(self):
        atomic_json(self.manifest, {"pending": self.pending, "ready": self.ready, "session": self.session})

    def reset_session(self, session):
        if session == self.session:
            return
        self.completed(float("inf"))
        self.close_segment()
        # Encoded old incidents remain in ready until acknowledged by the server.
        for item in self.index:
            item["path"].unlink(missing_ok=True)
        self.index.clear()
        self.last_t = None
        self.session = session
        self.save()

    def expire_completed(self, now=None, days=7):
        """Caller must establish that no exam is active; never silently prune live evidence."""
        now = time.time() if now is None else now
        expired = []
        for ident, parts in list(self.ready.items()):
            for part in list(parts):
                path = Path(part["path"]).resolve()
                if path.parent != self.folder.resolve() or path.suffix != ".mp4":
                    raise ValueError("Unsafe clip path in recording manifest")
                created = part.get("created_at", path.stat().st_mtime if path.exists() else now)
                if created + days*86400 <= now:
                    self.acknowledge(ident, part["path"])
                    expired.append({"event_id": ident, "path": part["path"], "expired_at": now})
        for ident, incident in list(self.pending.items()):
            if incident.get("created_at", now) + days*86400 <= now:
                del self.pending[ident]
                expired.append({"event_id": ident, "expired_at": now})
        if expired:
            self.save()
            if not self.pending and not self.encoding:
                self.discard_buffer()
        return expired

    def close_segment(self):
        if self.stream:
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.stream.close()
        self.stream = self.current = None

    def discard_buffer(self):
        if self.pending or self.encoding:
            raise ValueError("Нельзя удалить буфер незавершённого события")
        self.close_segment()
        for item in self.index:
            item["path"].unlink(missing_ok=True)
        self.index.clear()
        self.last_t = None

    def push(self, t, frame):
        if self.last_t is not None and t < self.last_t:
            raise ValueError("RECORDING_CLOCK_REVERSED")
        if not self.current or t - self.current["start"] >= 10:
            self.close_segment()
            if shutil.disk_usage(self.folder).free < 512 * 1024**2:
                raise OSError("RECORDING_DISK_LOW: нужно 512 МБ свободного места")
            size = sum(p.stat().st_size for p in self.folder.rglob("*") if p.is_file())
            if size > 2 * 1024**3:
                raise OSError("RECORDING_QUEUE_FULL: локальная очередь превысила 2 ГБ")
            path = self.segments / (uuid4().hex + ".seg")
            self.current = {"path": path, "start": t, "end": t}
            self.index.append(self.current)
            self.stream = path.open("wb")
            self.prune(t)
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if not ok:
            raise OSError("Не удалось сохранить кадр камеры")
        raw = jpg.tobytes()
        self.stream.write(HEADER.pack(t, len(raw)))
        self.stream.write(raw)
        self.stream.flush()
        self.current["end"] = t
        self.last_t = t

    def prune(self, t):
        # Frequent-glance reviews can refer to the previous 60 seconds.
        earliest = min([t - 70] + [x["start"] - 10 for x in self.pending.values()])
        for item in list(self.index):
            if item is not self.current and item["end"] < earliest:
                item["path"].unlink(missing_ok=True)
                self.index.remove(item)

    def mark(self, event):
        ident = event["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", ident):
            raise ValueError("INVALID_RECORDING_ID")
        if event.get("update"):
            if ident in self.pending:
                self.pending[ident]["end"] = event["end"]
                self.save()
            return
        if ident not in self.pending and ident not in self.ready:
            self.pending[ident] = {"id": ident, "start": event.get("start", event["at"]),
                                   "created_at": event.get("created_at", time.time()),
                                   "at": event["at"], "end": None if event.get("ongoing") else event.get("end", event["at"])}
            self.save()

    def completed(self, t):
        force = t == float("inf")
        if force:
            self.close_segment()
        while True:
            for ident, incident in list(self.pending.items()):
                if ident in self.encoding or self.last_t is None:
                    continue
                end = incident["end"]
                if not force and (end is None or t < end + 5):
                    continue
                if len(self.encoding) >= 8:
                    # Harvest existing futures before scheduling another batch.
                    # Raising here would prevent even finished work from draining.
                    break
                snapshot = dict(incident)
                snapshot["end"] = self.last_t if end is None else end
                selected = [dict(x) for x in self.index
                            if x["end"] >= snapshot["start"] - 10 and x["start"] <= snapshot["end"] + 5]
                self.encoding[ident] = self.pool.submit(self.encode, snapshot, selected)
            for ident, future in list(self.encoding.items()):
                if future.done() or force:
                    try:
                        result = future.result(timeout=180)
                    except Exception as error:
                        del self.encoding[ident]
                        raise OSError("RECORDING_ENCODING_FAILED: запись сохранена в сегментах для повтора") from error
                    self.ready[ident] = result
                    del self.pending[ident]
                    del self.encoding[ident]
                    self.save()
            if not force or not self.pending or self.last_t is None:
                break
        return [part for parts in self.ready.values() for part in parts]

    def acknowledge(self, event_id, path=None):
        parts = self.ready.get(event_id, [])
        removed = [item for item in parts if path is None or item["path"] == str(path)]
        remaining = [item for item in parts if item not in removed]
        if remaining:
            self.ready[event_id] = remaining
        else:
            self.ready.pop(event_id, None)
        self.save()
        for item in removed:
            Path(item["path"]).unlink(missing_ok=True)

    def encode(self, incident, selected):
        # Bound each upload as well as each capture segment. Long incidents are
        # represented by several adjacent clips in the same event.
        start = max(0, incident["start"] - 10)
        end = incident["end"] + 5
        parts = []
        cursor = start
        while cursor < end:
            part_end = min(end, cursor + 30)
            part = {**incident, "id": incident["id"] + f"-{len(parts):04d}",
                    "start": cursor + 10, "end": part_end - 5}
            candidates = [x for x in selected if x["end"] >= cursor and x["start"] <= part_end]
            if candidates:
                result = self.encode_part(part, candidates)
                result["event_id"] = incident["id"]
                parts.append(result)
            cursor = part_end
        return parts

    def encode_part(self, incident, selected):
        output = self.folder / (incident["id"] + ".mp4")
        first = last = None
        gaps = []
        count = 0
        with tempfile.TemporaryDirectory(prefix="encode-", dir=self.folder) as tmp:
            directory = Path(tmp)
            with (directory / "frames.txt").open("w") as concat:
                for item in selected:
                    for at, jpg in frames(item["path"]):
                        if at < incident["start"] - 10 or at > incident["end"] + 5:
                            continue
                        if last is not None and at <= last:
                            continue
                        if first is None:
                            first = at
                        if last is not None:
                            duration = at - last
                            if duration > .5:
                                gaps.append([last, at])
                            concat.write(f"duration {duration:.6f}\n")
                        (directory / f"{count}.jpg").write_bytes(jpg)
                        concat.write(f"file '{count}.jpg'\n")
                        last = at
                        count += 1
                if count < 1:
                    raise OSError("RECORDING_INSUFFICIENT_FRAMES")
                concat.write(f"duration 0.100000\nfile '{count - 1}.jpg'\n")
            result = subprocess.run([
                self.ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "1", "-i",
                str(directory / "frames.txt"), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "25", "-pix_fmt", "yuv420p", "-fps_mode", "vfr", "-movflags", "+faststart", str(output),
            ], capture_output=True, timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if result.returncode:
                raise OSError("RECORDING_ENCODING_FAILED")
        return {"event_id": incident["id"], "path": str(output), "start": first, "end": last,
                "created_at": incident.get("created_at", time.time()),
                "gaps": gaps, "requested_start": max(0, incident["start"] - 10),
                "requested_end": incident["end"] + 5,
                "complete": not gaps and first <= max(0, incident["start"] - 10) + .2 and last >= incident["end"] + 4.8}

    def close(self):
        self.close_segment()
        self.pool.shutdown(wait=True)
