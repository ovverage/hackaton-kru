"""Training-run lifecycle and reproducibility tests (training dependencies)."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

HAS_TORCH = importlib.util.find_spec("torch") is not None
if HAS_TORCH:
    import torch
    from training.public_gaze_train import (
        capture_rng, export_only, guard_existing_run, make_loader, restore_rng,
        validate_resume, TrainingBudget,
    )
    from training.datasets.common import process_lock
    from training.public_gaze_prepare import digest
    from shared.public_gaze import SCHEMA


@unittest.skipUnless(HAS_TORCH, "Training torch environment required")
class LifecycleTests(unittest.TestCase):
    def test_budget_resume_keeps_original_start_and_absolute_deadline(self):
        args = argparse.Namespace(max_hours=2,deadline_utc="2026-10-07T10:30:00Z",finalize_reserve_minutes=15)
        start = TrainingBudget.parse_utc("2026-10-07T08:47:54Z").timestamp()
        clock = [50.]
        budget = TrainingBudget(args,"2026-10-07T08:47:54Z",wall_clock=lambda:start+3600,
                                monotonic_clock=lambda:clock[0])
        self.assertEqual(budget.reason,"absolute_deadline")
        self.assertAlmostEqual(budget.remaining(),2526.)
        clock[0] += 1650
        self.assertTrue(budget.stop_training())
        self.assertGreater(budget.remaining(),0)
        clock[0] += 900
        with self.assertRaisesRegex(TimeoutError,"BUDGET_EXHAUSTED"):
            budget.check("final_test")

    def test_budget_expands_reserve_from_observed_validation_cost(self):
        args = argparse.Namespace(max_hours=2,deadline_utc=None,finalize_reserve_minutes=15)
        start = TrainingBudget.parse_utc("2026-10-07T08:47:54Z").timestamp()
        budget = TrainingBudget(args,"2026-10-07T08:47:54Z",wall_clock=lambda:start,
                                monotonic_clock=lambda:0.)
        self.assertEqual(budget.effective_reserve(0,100,200),900.)
        self.assertEqual(budget.effective_reserve(300,100,200),1860.)
        self.assertFalse(budget.stop_training(300,100,200))

    def test_budget_rejects_naive_deadline_or_impossible_reserve(self):
        with self.assertRaisesRegex(ValueError,"TIMEZONE"):
            TrainingBudget.parse_utc("2026-10-07T18:47:54")
        args = argparse.Namespace(max_hours=.1,deadline_utc=None,finalize_reserve_minutes=15)
        with self.assertRaisesRegex(ValueError,"EXCEED_FINALIZATION_RESERVE"):
            TrainingBudget(args,"2026-10-07T08:47:54Z")

    def test_existing_artifacts_require_resume_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            sentinel = root/"run.json"
            sentinel.write_text('{"keep":"original"}')
            args = argparse.Namespace(export_only=False,resume=None)
            with self.assertRaisesRegex(ValueError,"EXISTING_RUN_REQUIRES_RESUME"):
                guard_existing_run(args,root)
            self.assertEqual(sentinel.read_text(),'{"keep":"original"}')

    def test_os_lock_rejects_second_owner_then_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/".run.lock"
            with process_lock(path):
                with self.assertRaises(RuntimeError):
                    with process_lock(path):
                        self.fail("A concurrent owner acquired the run lock")
            with process_lock(path):
                self.assertTrue(path.exists())

    def test_resume_rejects_changed_schedule_seed_and_code(self):
        saved = dict(samples_sha256="dataset",pipeline_sha256={"trainer":"sha"},
                     args=dict(epochs=40,seed=7,resume=None,export_only=False))
        for key,value in (("epochs",41),("seed",8)):
            changed = copy.deepcopy(saved)
            changed["args"][key] = value
            with self.assertRaisesRegex(ValueError,"RESUME_ARGUMENTS_CHANGED"):
                validate_resume(saved,changed)
        changed = copy.deepcopy(saved)
        changed["pipeline_sha256"]["trainer"] = "different"
        with self.assertRaisesRegex(ValueError,"RESUME_CONTRACT_CHANGED"):
            validate_resume(saved,changed)

    def test_rng_and_sampler_replay_at_epoch_boundary(self):
        args = argparse.Namespace(seed=13,batch=2,workers=0,device="cpu")
        rows = [dict(dataset="d",group=f"g{i//2}") for i in range(8)]
        train = make_loader(".",rows,args,train=True)
        validation = make_loader(".",rows,args)
        state = capture_rng(train,validation)
        expected = (random.random(),np.random.rand(),torch.rand(4),list(train.sampler))
        restore_rng(state,train,validation)
        actual = (random.random(),np.random.rand(),torch.rand(4),list(train.sampler))
        self.assertEqual(expected[:2],actual[:2])
        self.assertTrue(torch.equal(expected[2],actual[2]))
        self.assertEqual(expected[3],actual[3])
        self.assertFalse(train.persistent_workers)

    def test_export_recovery_does_not_read_dataset_or_evaluate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = dict(epoch=2,model={},run=dict(args=dict(cpu_threads=1,allow_smoke=True)))
            torch.save(checkpoint,root/"best.pt")
            sha = digest(root/"best.pt")
            report = dict(schema=SCHEMA,best_sha256=sha)
            (root/"evaluation.json").write_text(json.dumps(report))
            (root/"checkpoint-lock.json").write_text(json.dumps(dict(best_sha256=sha,epoch=3)))
            with patch("training.public_gaze_train.GazeNet") as model, \
                 patch("training.public_gaze_train.export") as writer, \
                 patch("training.public_gaze_train.evaluate",side_effect=AssertionError("Test was replayed")):
                export_only(root)
                writer.assert_called_once()
                model.assert_called_once_with(pretrained=False)
            self.assertEqual(json.loads((root/"export-recovery.json").read_text())["test_reevaluated"],False)
            (root/"checkpoint-lock.json").write_text(json.dumps(dict(best_sha256="changed",epoch=3)))
            with self.assertRaisesRegex(ValueError,"CHECKPOINT_MISMATCH"):
                export_only(root)


if __name__ == "__main__":
    unittest.main()
