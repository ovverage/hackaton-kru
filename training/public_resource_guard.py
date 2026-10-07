"""Bounded Qorgau resource lease: temporarily unload and reliably restore Ollama.

No daemon is stopped and no model files are removed. The only process signals
are sent to exact, root-scoped identities supplied by the queue journal or to
descendants snapshotted while those identities are verified alive.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time

import psutil
import requests

from training.datasets.common import process_lock, write_json

OLLAMA_URL = "http://127.0.0.1:11434"
MODEL = "qwen3.6:35b-a3b-q4_K_M"
TERMINAL = {"complete", "completed", "success", "succeeded", "failed", "error",
            "cancelled", "canceled", "deadline", "stopped"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("TIMESTAMP_REQUIRES_TIMEZONE")
    return parsed.timestamp()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def original_model(snapshot):
    matches = [model for model in snapshot.get("models", [])
               if str(model.get("name", model.get("model", ""))).casefold() == MODEL.casefold()]
    if len(matches) != 1:
        raise ValueError("EXPECTED_OLLAMA_MODEL_MISSING_OR_AMBIGUOUS_IN_SNAPSHOT")
    model = matches[0]
    if not model.get("digest") or not isinstance(model.get("context_length"), int) or model["context_length"] <= 0:
        raise ValueError("OLLAMA_SNAPSHOT_REQUIRES_DIGEST_AND_CONTEXT_LENGTH")
    return dict(name=model.get("name", model.get("model")), digest=model["digest"],
                context_length=model["context_length"], size_vram=model.get("size_vram"), keep_alive=-1)


class Ollama:
    def __init__(self, session=None):
        self.session = session or requests.Session()
        # This resource operation is strictly local, irrespective of proxy env.
        self.session.trust_env = False

    def models(self):
        response = self.session.get(OLLAMA_URL+"/api/ps", timeout=(5,15))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload.get("models"), list):
            raise RuntimeError("INVALID_OLLAMA_PS_RESPONSE")
        return payload["models"]

    def request(self, expected, load, timeout=120):
        body = dict(model=expected["name"], prompt="", stream=False,
                    keep_alive=-1 if load else 0)
        if load:
            body["options"] = {"num_ctx":expected["context_length"]}
        response = self.session.post(OLLAMA_URL+"/api/generate", json=body, timeout=(5,timeout))
        response.raise_for_status()
        result = response.json()
        if result.get("error") or result.get("done") is not True:
            raise RuntimeError("OLLAMA_REQUEST_NOT_COMPLETED:"+str(result.get("error",result.get("done"))))
        return result

    def restored(self, expected):
        matches = [model for model in self.models()
                   if model.get("name",model.get("model")) == expected["name"]]
        if len(matches) != 1:
            return None
        model = matches[0]
        if model.get("digest") != expected["digest"] or model.get("context_length") != expected["context_length"]:
            raise RuntimeError("OLLAMA_RESTORED_IDENTITY_OR_CONTEXT_MISMATCH")
        return model


def inside_root(path, root):
    try:
        value = Path(path)
        if not value.is_absolute():
            return False
        value.resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError, TypeError):
        return False


def scoped_process(cwd, cmdline, root):
    if inside_root(cwd, root):
        return True
    # An absolute script/data argument is checked as a path, never a substring.
    for token in cmdline:
        candidate = token.split("=",1)[1] if token.startswith("--") and "=" in token else token
        if inside_root(candidate, root):
            return True
    return False


def process_record(process):
    with process.oneshot():
        return dict(pid=process.pid, create_time=process.create_time(),
                    cmdline=process.cmdline(), cwd=process.cwd())


def identity_state(record, root, process_factory=psutil.Process):
    """Return verified/gone/unverified; reused PID is gone for the old identity."""
    try:
        pid = int(record["pid"])
        if pid <= 0 or pid == os.getpid():
            return "unverified", None, "invalid_identity_or_guard_pid"
        process = process_factory(pid)
        created = float(record["create_time"])
        command = record["cmdline"]
        if not math.isfinite(created) or not isinstance(command,list) or not command:
            return "unverified", None, "invalid_identity_or_guard_pid"
        current = process_record(process)
        if abs(current["create_time"]-created) > .000001:
            return "gone", None, "pid_reused_other_process_untouched"
        if current["cmdline"] != command:
            return "unverified", None, "cmdline_changed"
        if record.get("cwd") and Path(record["cwd"]).resolve() != Path(current["cwd"]).resolve():
            return "unverified", None, "cwd_changed"
        if not scoped_process(current["cwd"], current["cmdline"], root):
            return "unverified", None, "outside_authorized_root"
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return "gone", None, "exited"
        return "verified", process, None
    except psutil.NoSuchProcess:
        return "gone", None, "exited"
    except (KeyError, ValueError, TypeError, OSError, psutil.Error) as error:
        return "unverified", None, str(error)


def journal_records(journal):
    records = list(journal.get("processes", []))
    parent = journal.get("parent")
    if not parent and journal.get("parent_pid"):
        parent = dict(pid=journal["parent_pid"], create_time=journal.get("parent_create_time"),
                      cmdline=journal.get("parent_cmdline"), cwd=journal.get("parent_cwd"))
    if parent:
        records.insert(0,dict(parent,queue_parent=True))
    return records


def snapshot_owned(records, root, process_factory=psutil.Process):
    snapshots = {(record.get("pid"),record.get("create_time")):record for record in records}
    problems = []
    for record in list(snapshots.values()):
        state, process, reason = identity_state(record,root,process_factory)
        if state == "unverified":
            problems.append(dict(pid=record.get("pid"),reason=reason))
        if state != "verified":
            continue
        try:
            for child in process.children(recursive=True):
                if child.pid == os.getpid():
                    continue
                try:
                    child_record = process_record(child)
                except psutil.NoSuchProcess:
                    continue
                except psutil.Error as error:
                    child_record = dict(pid=child.pid,create_time=None,cmdline=[],cwd=None,
                                        snapshot_error=str(error))
                    snapshots[(child.pid,None)] = child_record
                    problems.append(dict(pid=child.pid,reason="descendant_snapshot_failed:"+str(error)))
                    continue
                child_record["verified_descendant_of"] = record["pid"]
                snapshots[(child_record["pid"],child_record["create_time"])] = child_record
                if not scoped_process(child_record["cwd"],child_record["cmdline"],root):
                    problems.append(dict(pid=child.pid,reason="descendant_outside_authorized_root"))
                    continue
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as error:
            problems.append(dict(pid=record.get("pid"),reason="descendant_snapshot_failed:"+str(error)))
    return list(snapshots.values()),problems


def stop_owned(records, root, process_factory=psutil.Process, wait_procs=psutil.wait_procs):
    """Stop only reverified identities; stop queue owner first to prevent spawn."""
    snapshots, problems = snapshot_owned(records,root,process_factory)
    ordered = sorted(snapshots,key=lambda row:not row.get("queue_parent",False))
    signaled = []
    for record in ordered:
        state, process, reason = identity_state(record,root,process_factory)
        if state != "verified":
            continue
        try:
            process.terminate()
            signaled.append(process)
        except psutil.NoSuchProcess:
            pass
        except psutil.Error as error:
            problems.append(dict(pid=record["pid"],reason="terminate_failed:"+str(error)))
    if signaled:
        wait_procs(signaled,timeout=5)
    # Revalidate before escalation; a reused PID must never receive kill().
    for record in ordered:
        state, process, reason = identity_state(record,root,process_factory)
        if state == "verified":
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as error:
                problems.append(dict(pid=record["pid"],reason="kill_failed:"+str(error)))
    if signaled:
        wait_procs(signaled,timeout=5)
    remaining = []
    for record in snapshots:
        state, process, reason = identity_state(record,root,process_factory)
        if state != "gone":
            remaining.append(dict(pid=record.get("pid"),state=state,reason=reason))
    # Unscoped descendants were deliberately not signalled; they prevent an
    # unsafe restore over potentially still-running training descendants.
    uncertain = [problem for problem in problems if problem["reason"].startswith(("descendant_","invalid_"))]
    return dict(stopped=not remaining and not uncertain, remaining=remaining,
                problems=problems, snapshots=snapshots)


def termination_reason(journal, now, deadline, armed_at, *, missing_grace=1800, stale_after=300):
    if now >= deadline:
        return "deadline"
    if journal is None:
        return "queue_start_grace_expired" if now-armed_at >= missing_grace else None
    if str(journal.get("status","")).lower() in TERMINAL:
        return "queue_"+str(journal["status"]).lower()
    try:
        heartbeat = timestamp(journal["heartbeat_utc"])
        if heartbeat > now+60:
            return "queue_heartbeat_invalid_future"
        if now-heartbeat > stale_after:
            return "queue_heartbeat_stale"
    except (KeyError,ValueError,TypeError):
        return "queue_heartbeat_missing_or_invalid"
    return None


class ResourceGuard:
    def __init__(self,args,api=None):
        self.args = args
        self.root = args.root.resolve()
        self.status_path = self.root/"resource-guard-status.json"
        self.queue_path = args.queue_status if args.queue_status.is_absolute() else self.root/args.queue_status
        self.expected = original_model(read_json(self.root/"ollama-before-budget.json"))
        self.api = api or Ollama()
        self.journal = None
        self.state = dict(status="initializing",restore_required=False,expected_model=self.expected,
                          guard=process_record(psutil.Process()),deadline_utc=args.deadline_utc)
        if self.status_path.exists():
            previous = read_json(self.status_path)
            if previous.get("restore_required"):
                if previous.get("expected_model") != self.expected:
                    raise ValueError("RESTORE_OBLIGATION_SNAPSHOT_CHANGED")
                self.state.update(previous)
                self.state["guard"] = process_record(psutil.Process())
        self.armed_at = time.time()

    def status(self,phase,**details):
        self.state.update(status=phase,updated_utc=utc_now(),**details)
        write_json(self.status_path,self.state)
        print(json.dumps(dict(status=phase,updated_utc=self.state["updated_utc"],
                              restore_required=self.state["restore_required"])),flush=True)

    def queue(self):
        if self.queue_path.exists():
            self.journal = read_json(self.queue_path)
        return self.journal

    def release(self):
        if self.api.restored(self.expected) is None:
            raise RuntimeError("OLLAMA_EXPECTED_MODEL_NOT_RESIDENT_BEFORE_RELEASE")
        # Persist the obligation before the HTTP mutation: a timeout can mean
        # the unload happened even though the client did not receive a response.
        self.status("releasing",restore_required=True,release_requested_utc=utc_now())
        self.api.request(self.expected,load=False,timeout=60)
        for attempt in range(10):
            if not any(model.get("name",model.get("model")) == self.expected["name"] for model in self.api.models()):
                self.status("released",unload_verified=True,released_utc=utc_now())
                return
            time.sleep(1)
        raise RuntimeError("OLLAMA_UNLOAD_NOT_VERIFIED")

    def cleanup(self,reason):
        end = time.monotonic()+self.args.restore_retry_minutes*60
        self.status("restoring",cleanup_reason=reason,cleanup_started_utc=utc_now())
        while time.monotonic() < end:
            try:
                try:
                    self.queue()
                except (OSError,ValueError) as error:
                    self.state["queue_read_error"] = str(error)
                records = journal_records(self.journal or {})
                records += self.state.get("owned_process_snapshots",[])
                stopped = stop_owned(records,self.root)
                self.status("restoring",owned_processes=stopped,
                            owned_process_snapshots=stopped["snapshots"])
                if not stopped["stopped"]:
                    raise RuntimeError("OWNED_PROCESSES_NOT_CONFIRMED_STOPPED")
                if not self.state["restore_required"]:
                    self.status("finished_without_release",finished_utc=utc_now())
                    return True
                # Do not call successful until /api/ps verifies digest + context.
                # Even if another request already reloaded the model, restore
                # the original indefinite keep_alive and context explicitly.
                self.api.request(self.expected,load=True,timeout=max(1,min(120,end-time.monotonic())))
                current = self.api.restored(self.expected)
                if current is None:
                    raise RuntimeError("OLLAMA_RESTORE_NOT_VISIBLE_IN_PS")
                self.status("restored",restore_required=False,restore_verified=True,
                            restored_utc=utc_now(),restored_model=current)
                return True
            except (OSError,ValueError,RuntimeError,requests.RequestException,psutil.Error) as error:
                self.status("restore_retry",restore_error=str(error),restore_verified=False)
                remaining = end-time.monotonic()
                if remaining > 0:
                    time.sleep(min(self.args.poll_seconds,remaining))
        self.status("restore_failed",restore_verified=False,failed_utc=utc_now())
        return False

    def run(self):
        obligation_on_start = self.state["restore_required"]
        self.status("armed",armed_utc=utc_now())
        reason = "guard_exception"
        try:
            if obligation_on_start:
                reason = "guard_restarted_with_restore_obligation"
                return self.cleanup(reason)
            deadline = timestamp(self.args.deadline_utc)
            if time.time() >= deadline:
                reason = "deadline"
            else:
                if self.args.release_on_start:
                    self.release()
                while True:
                    try:
                        self.queue()
                    except (OSError,ValueError) as error:
                        self.state["queue_read_error"] = str(error)
                    reason = termination_reason(self.journal,time.time(),deadline,self.armed_at)
                    if reason:
                        break
                    self.status("monitoring",queue_seen=self.journal is not None,
                                queue_status=self.journal.get("status") if self.journal else None)
                    time.sleep(min(self.args.poll_seconds,max(0,deadline-time.time())))
        except BaseException as error:
            reason = "guard_exception:"+type(error).__name__+":"+str(error)
            self.status("guard_exception",error=str(error))
        finally:
            if not obligation_on_start:
                result = self.cleanup(reason)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    parser.add_argument("--deadline-utc",required=True)
    parser.add_argument("--queue-status",type=Path,default=Path("budget-training-status.json"))
    parser.add_argument("--release-on-start",action="store_true")
    parser.add_argument("--poll-seconds",type=float,default=30)
    parser.add_argument("--restore-retry-minutes",type=float,default=15)
    args = parser.parse_args()
    if not 1 <= args.poll_seconds <= 60 or not 0 < args.restore_retry_minutes <= 15:
        parser.error("poll-seconds must be 1..60; restore-retry-minutes must be >0 and <=15")
    timestamp(args.deadline_utc)
    args.root = args.root.resolve(strict=True)
    with process_lock(args.root/".resource-guard.lock"):
        success = ResourceGuard(args).run()
    raise SystemExit(0 if success else 1)


if __name__ == "__main__":
    main()
