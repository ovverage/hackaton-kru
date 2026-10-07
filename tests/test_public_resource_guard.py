"""Resource-lease tests: no real Ollama calls or process signals."""
from argparse import Namespace
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    import psutil
except ModuleNotFoundError as error:
    raise unittest.SkipTest("Resource-guard tests require the training psutil dependency") from error
import requests

from training.public_resource_guard import (
    MODEL, Ollama, ResourceGuard, identity_state, original_model, read_json,
    scoped_process, stop_owned, termination_reason, timestamp,
)


class FakeProcess:
    def __init__(self,pid,cwd,created=10.,command=None):
        self.pid,self.directory,self.created = pid,str(cwd),created
        self.command = command or ["python","-m","training.public_gaze_train"]
        self.live,self.signals,self.descendants = True,[],[]

    def oneshot(self):
        return nullcontext()

    def create_time(self):
        return self.created

    def cmdline(self):
        return self.command

    def cwd(self):
        return self.directory

    def is_running(self):
        return self.live

    def status(self):
        return psutil.STATUS_RUNNING if self.live else psutil.STATUS_ZOMBIE

    def children(self,recursive=True):
        return self.descendants

    def terminate(self):
        self.signals.append("terminate")
        self.live = False

    def kill(self):
        self.signals.append("kill")
        self.live = False

    def record(self):
        return dict(pid=self.pid,create_time=self.created,cmdline=self.command,cwd=self.directory)


class ProcessSafetyTests(unittest.TestCase):
    def test_pid_reuse_and_cmdline_change_never_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            process = FakeProcess(999101,directory)
            record = process.record()
            process.created += 1
            result = stop_owned([record],Path(directory),lambda pid:process,lambda *a,**k:None)
            self.assertTrue(result["stopped"])
            self.assertEqual(process.signals,[])
            process.created -= 1
            process.command = ["python","unrelated.py"]
            result = stop_owned([record],Path(directory),lambda pid:process,lambda *a,**k:None)
            self.assertFalse(result["stopped"])
            self.assertEqual(process.signals,[])

    def test_root_prefix_collision_is_not_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/"run"
            self.assertFalse(scoped_process(str(root)+"-other",["python","unrelated.py"],root))
            process = FakeProcess(999102,str(root)+"-other")
            self.assertEqual(identity_state(process.record(),root,lambda pid:process)[0],"unverified")

    def test_verified_parent_descendant_stopped_unrelated_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = FakeProcess(999103,directory)
            child = FakeProcess(999104,directory,command=["python","-c","multiprocessing.spawn"])
            unrelated = FakeProcess(999105,str(Path(directory)/"elsewhere"))
            parent.descendants = [child]
            processes = {p.pid:p for p in (parent,child,unrelated)}
            record = dict(parent.record(),queue_parent=True)
            result = stop_owned([record],Path(directory),processes.__getitem__,lambda *a,**k:None)
            self.assertTrue(result["stopped"])
            self.assertEqual(parent.signals,["terminate"])
            self.assertEqual(child.signals,["terminate"])
            self.assertEqual(unrelated.signals,[])

    def test_unscoped_child_is_preserved_and_blocks_restore_across_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/"run"
            parent = FakeProcess(999106,root)
            child = FakeProcess(999107,Path(directory)/"other")
            parent.descendants = [child]
            processes = {parent.pid:parent,child.pid:child}
            result = stop_owned([parent.record()],root,processes.__getitem__,lambda *a,**k:None)
            self.assertFalse(result["stopped"])
            second = stop_owned(result["snapshots"],root,processes.__getitem__,lambda *a,**k:None)
            self.assertFalse(second["stopped"])
            self.assertEqual(child.signals,[])


class OllamaTests(unittest.TestCase):
    def expected(self):
        return dict(name=MODEL,digest="original-digest",context_length=32768,size_vram=24_000_000_000,keep_alive=-1)

    def test_bom_snapshot_and_model_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"before.json"
            path.write_text(json.dumps(dict(models=[self.expected()])),encoding="utf-8-sig")
            self.assertEqual(original_model(read_json(path)),self.expected())

    def test_exact_unload_and_restore_payloads(self):
        session = Mock()
        session.post.return_value.json.return_value = {"done":True}
        api = Ollama(session)
        api.request(self.expected(),load=False)
        self.assertEqual(session.post.call_args.kwargs["json"],dict(model=MODEL,prompt="",stream=False,keep_alive=0))
        api.request(self.expected(),load=True)
        self.assertEqual(session.post.call_args.kwargs["json"],dict(model=MODEL,prompt="",stream=False,keep_alive=-1,options={"num_ctx":32768}))
        self.assertFalse(session.trust_env)

    def test_http_error_is_not_success_and_context_mismatch_fails(self):
        session = Mock()
        session.post.return_value.raise_for_status.side_effect = requests.HTTPError("500")
        api = Ollama(session)
        with self.assertRaises(requests.HTTPError):
            api.request(self.expected(),load=True)
        session.get.return_value.json.return_value = {"models":[dict(self.expected(),context_length=4096)]}
        with self.assertRaisesRegex(RuntimeError,"MISMATCH"):
            api.restored(self.expected())

    def test_release_obligation_is_persisted_before_unload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"ollama-before-budget.json").write_text(json.dumps({"models":[self.expected()]}))
            api = Mock()
            api.restored.return_value = self.expected()
            api.models.return_value = []
            args = Namespace(root=root,queue_status=Path("queue.json"),deadline_utc="2026-10-07T18:47:54Z")
            guard = ResourceGuard(args,api)
            def check_request(*a,**k):
                state = read_json(root/"resource-guard-status.json")
                self.assertTrue(state["restore_required"])
                self.assertEqual(state["status"],"releasing")
            api.request.side_effect = check_request
            guard.release()
            self.assertEqual(read_json(root/"resource-guard-status.json")["status"],"released")

    def test_cleanup_waits_for_owned_processes_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"ollama-before-budget.json").write_text(json.dumps({"models":[self.expected()]}))
            api = Mock()
            api.restored.return_value = self.expected()
            args = Namespace(root=root,queue_status=Path("queue.json"),deadline_utc="2026-10-07T18:47:54Z",restore_retry_minutes=1,poll_seconds=1)
            guard = ResourceGuard(args,api)
            guard.state["restore_required"] = True
            results = [dict(stopped=False,snapshots=[],remaining=[1]),dict(stopped=True,snapshots=[],remaining=[])]
            with patch("training.public_resource_guard.stop_owned",side_effect=results), \
                 patch("training.public_resource_guard.time.sleep") as sleep:
                self.assertTrue(guard.cleanup("queue_complete"))
                self.assertEqual(sleep.call_count,1)
            api.request.assert_called_once()
            self.assertTrue(api.request.call_args.kwargs["load"])
            self.assertTrue(read_json(root/"resource-guard-status.json")["restore_verified"])

    def test_cleanup_http_failure_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"ollama-before-budget.json").write_text(json.dumps({"models":[self.expected()]}))
            api = Mock()
            api.request.side_effect = requests.HTTPError("restore500")
            args = Namespace(root=root,queue_status=Path("queue.json"),deadline_utc="2026-10-07T18:47:54Z",restore_retry_minutes=.001,poll_seconds=1)
            guard = ResourceGuard(args,api)
            guard.state["restore_required"] = True
            with patch("training.public_resource_guard.stop_owned",return_value=dict(stopped=True,snapshots=[],remaining=[])):
                self.assertFalse(guard.cleanup("queue_failed"))
            state = read_json(root/"resource-guard-status.json")
            self.assertEqual(state["status"],"restore_failed")
            self.assertFalse(state["restore_verified"])
            self.assertTrue(state["restore_required"])

    def test_deadline_heartbeat_and_missing_queue_grace(self):
        now = timestamp("2026-10-07T10:00:00Z")
        self.assertIsNone(termination_reason(None,now,now+1000,now-1799))
        self.assertEqual(termination_reason(None,now,now+1000,now-1800),"queue_start_grace_expired")
        self.assertEqual(termination_reason(None,now,now,now),"deadline")
        heartbeat = datetime.fromtimestamp(now-301,timezone.utc).isoformat()
        self.assertEqual(termination_reason({"status":"running","heartbeat_utc":heartbeat},now,now+1000,now),"queue_heartbeat_stale")
        self.assertEqual(termination_reason({"status":"completed"},now,now+1000,now),"queue_completed")


if __name__ == "__main__":
    unittest.main()
