"""Train, evaluate once on held-out test, and export a compact CPU gaze model.

Training uses downloaded MPIIFaceGaze AND Gaze360. The ImageNet initialized
MobileNetV3-small is fully fine-tuned; it is not described as trained from scratch.
Selection/calibration use validation only. Test is opened after checkpoint lock.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
from torchvision import models, transforms

from shared.public_gaze import SCHEMA, INPUT_SIZE
from training.public_gaze_prepare import digest, json_write, validate_splits
from training.datasets.common import process_lock


class GazeNet(nn.Module):
    def __init__(self, pretrained=True):
        super().__init__()
        weights = models.MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
        backbone = models.mobilenet_v3_small(weights=weights)
        self.features = backbone.features
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.head = nn.Sequential(nn.Linear(576, 256), nn.Hardswish(), nn.Dropout(.2), nn.Linear(256, 4))
        nn.init.normal_(self.head[-1].weight, std=.01)
        with torch.no_grad():
            self.head[-1].bias.copy_(torch.tensor([0., 0., -1., 3.]))

    def forward(self, face):
        raw = self.head(self.pool(self.features(face)).flatten(1))
        direction = nn.functional.normalize(raw[:, :3], dim=1, eps=1e-6)
        concentration = (nn.functional.softplus(raw[:, 3:4])+1).clamp(1, 200)
        return torch.cat([direction, concentration], dim=1)


def vmf_loss(output, target):
    """Proper likelihood of a 3D unit vector under von Mises-Fisher."""
    dot = (output[:, :3].float()*target.float()).sum(1).clamp(-1, 1)
    kappa = output[:, 3].float()
    log_sinh = kappa + torch.log1p(-torch.exp(-2*kappa))-math.log(2)
    return (-kappa*dot+log_sinh-torch.log(kappa)).mean()


class Faces(Dataset):
    def __init__(self, root, rows, augment=False):
        self.root, self.rows, self.augment = Path(root), rows, augment
        self.color = transforms.Compose([
            transforms.ColorJitter(.25, .2, .15, .03),
            transforms.RandomGrayscale(.05),
            transforms.RandomApply([transforms.GaussianBlur(3, (.1, .8))], p=.1)])
        self.to_tensor = transforms.Compose([transforms.ToTensor(),
            transforms.Normalize([.485, .456, .406], [.229, .224, .225])])

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        from PIL import Image, ImageOps
        row = self.rows[index]
        with Image.open(self.root/row["image"]) as source:
            image = source.convert("RGB")
        target = np.asarray(row["gaze"], np.float32).copy()
        if image.size != (INPUT_SIZE, INPUT_SIZE):
            raise ValueError("CROP_SIZE_MISMATCH")
        if self.augment:
            if random.random() < .5:
                image = ImageOps.mirror(image)
                target[0] *= -1
            image = self.color(image)
        return self.to_tensor(image), torch.from_numpy(target), index


def seed_worker(worker_id):
    seed = torch.initial_seed() % (2**32)
    np.random.seed(seed)
    random.seed(seed)
    torch.set_num_threads(1)


def make_loader(root, rows, args, train=False):
    generator = torch.Generator().manual_seed(args.seed)
    sampler = None
    if train:
        # Give the two domains equal weight, then their independent acquisition
        # groups equal weight. Adjacent Gaze360 frames cannot dominate MPII.
        counts = Counter((r["dataset"], r["group"]) for r in rows)
        groups = Counter(dataset for dataset, group in counts)
        weights = [1/(groups[r["dataset"]]*counts[(r["dataset"],r["group"])]) for r in rows]
        sampler = WeightedRandomSampler(weights, len(rows), replacement=True, generator=generator)
    return DataLoader(Faces(root, rows, augment=train), batch_size=args.batch, sampler=sampler,
                      shuffle=False, num_workers=args.workers, pin_memory=args.device.startswith("cuda"),
                      # Fresh worker seeds at epoch boundaries make resumed
                      # augmentation reproducible without inaccessible worker RNG.
                      persistent_workers=False, worker_init_fn=seed_worker,
                      generator=generator, drop_last=train)


def angular_errors(prediction, targets):
    prediction = prediction / np.maximum(np.linalg.norm(prediction, axis=1, keepdims=True), 1e-9)
    targets = targets / np.maximum(np.linalg.norm(targets, axis=1, keepdims=True), 1e-9)
    return np.degrees(np.arccos(np.clip((prediction*targets).sum(1), -1, 1)))


def summarize(errors, rows):
    result = {}
    for dataset in sorted({r["dataset"] for r in rows}):
        indices = np.asarray([i for i,r in enumerate(rows) if r["dataset"] == dataset])
        values = errors[indices]
        by_group = defaultdict(list)
        for i in indices:
            by_group[rows[i]["group"]].append(float(errors[i]))
        means = np.asarray([np.mean(v) for v in by_group.values()])
        rng = np.random.default_rng(701)
        boot = np.asarray([rng.choice(means, len(means), replace=True).mean() for _ in range(1000)])
        result[dataset] = dict(n=len(values), mean_degrees=float(values.mean()),
            median_degrees=float(np.median(values)), p90_degrees=float(np.percentile(values, 90)),
            fraction_under_10=float((values < 10).mean()), fraction_under_15=float((values < 15).mean()),
            groups=len(means), group_macro_mean_degrees=float(means.mean()),
            group_macro_bootstrap95_degrees=np.percentile(boot, [2.5,97.5]).tolist(),
            group_mean_degrees={k:float(np.mean(v)) for k,v in by_group.items()})
    result["domain_macro_mean_degrees"] = float(np.mean([v["mean_degrees"] for v in result.values()]))
    return result


def evaluate(model, loader, device):
    model.eval()
    outputs, targets = np.empty((len(loader.dataset),4), np.float32), np.empty((len(loader.dataset),3), np.float32)
    with torch.inference_mode():
        for images, gaze, indices in loader:
            result = model(images.to(device, non_blocking=True)).cpu().numpy()
            outputs[indices.numpy()] = result
            targets[indices.numpy()] = gaze.numpy()
    if not np.isfinite(outputs).all():
        raise ValueError("NONFINITE_EVALUATION")
    errors = angular_errors(outputs[:,:3], targets)
    return summarize(errors, loader.dataset.rows), outputs, targets, errors


def save_checkpoint(path, payload):
    temporary = path.with_suffix(".tmp")
    torch.save(payload, temporary)
    temporary.replace(path)


def capture_rng(train_loader, val_loader):
    return dict(python=random.getstate(), numpy=np.random.get_state(),
                torch=torch.get_rng_state(),
                cuda=torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
                train_generator=train_loader.generator.get_state(),
                val_generator=val_loader.generator.get_state())


def restore_rng(state, train_loader, val_loader):
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"].cpu())
    if state["cuda"]:
        if not torch.cuda.is_available() or len(state["cuda"]) != torch.cuda.device_count():
            raise ValueError("RESUME_CUDA_TOPOLOGY_CHANGED")
        torch.cuda.set_rng_state_all([value.cpu() for value in state["cuda"]])
    train_loader.generator.set_state(state["train_generator"].cpu())
    val_loader.generator.set_state(state["val_generator"].cpu())


def validate_resume(saved, current):
    if saved["samples_sha256"] != current["samples_sha256"]:
        raise ValueError("RESUME_DATA_CHANGED")
    for key in ("pipeline_sha256", "torch_version", "torchvision_version", "cuda_version", "gpu"):
        if saved.get(key) != current.get(key):
            raise ValueError(f"RESUME_CONTRACT_CHANGED:{key}")
    mutable = {"resume", "export_only"}
    prior = {k:v for k,v in saved["args"].items() if k not in mutable}
    proposed = {k:v for k,v in current["args"].items() if k not in mutable}
    if prior != proposed:
        changed = sorted(k for k in prior.keys() | proposed.keys() if prior.get(k) != proposed.get(k))
        raise ValueError("RESUME_ARGUMENTS_CHANGED:"+",".join(changed))


def guard_existing_run(args, output):
    if args.export_only:
        if args.resume:
            raise ValueError("EXPORT_ONLY_AND_RESUME_ARE_EXCLUSIVE")
        return
    if (output/"evaluation.json").exists():
        raise ValueError("FINAL_TEST_ALREADY_EVALUATED: use --export-only to recover export")
    existing = [p for p in output.iterdir() if p.name != ".run.lock"]
    if existing and not args.resume:
        raise ValueError("EXISTING_RUN_REQUIRES_RESUME: preserve artifacts and use --resume last.pt")
    if args.resume and (args.resume.resolve() != output/"last.pt" or not (output/"run.json").is_file()):
        raise ValueError("RESUME_REQUIRES_SAME_RUN_LAST_CHECKPOINT")


def train(args):
    args.data, args.output = args.data.resolve(), args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    with process_lock(args.output/".run.lock"):
        guard_existing_run(args, args.output)
        if args.export_only:
            export_only(args.output)
        else:
            _train_locked(args)


def _train_locked(args):
    torch.set_num_threads(args.cpu_threads)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("CUDA_REQUESTED_BUT_UNAVAILABLE")
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    # CUDA kernels can be nondeterministic; seeds and software/hardware are
    # recorded, but bitwise determinism across GPU types is not claimed.
    root, output = args.data.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    audit = json.loads((root/"extraction-audit.json").read_text("utf-8"))
    if audit.get("smoke_only") and not args.allow_smoke:
        raise ValueError("SMOKE_DATASET_CANNOT_TRAIN_RELEASE")
    rows = [json.loads(line) for line in (root/"samples.jsonl").read_text("utf-8").splitlines()]
    if digest(root/"samples.jsonl") != audit["samples_sha256"]:
        raise ValueError("PREPARED_MANIFEST_HASH_CHANGED")
    validate_splits(rows)
    subsets = {split:[r for r in rows if r["split"] == split] for split in ("train", "val", "test")}
    for split, subset in subsets.items():
        if {r["dataset"] for r in subset} != {"mpiifacegaze", "gaze360"}:
            raise ValueError(f"BOTH_DATASETS_REQUIRED:{split}")
    train_loader = make_loader(root, subsets["train"], args, train=True)
    val_loader = make_loader(root, subsets["val"], args)
    model = GazeNet(pretrained=not args.from_scratch and not args.resume).to(args.device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=.01)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs, eta_min=args.learning_rate*.03)
    scaler = torch.amp.GradScaler("cuda", enabled=args.device.startswith("cuda"))
    start_epoch, best, stale = 0, math.inf, 0
    run = dict(schema=SCHEMA, started_utc=datetime.now(timezone.utc).isoformat(),
               args={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
               initialization="random" if args.from_scratch else "torchvision MobileNet_V3_Small_Weights.IMAGENET1K_V1",
               torch_version=torch.__version__, cuda_version=torch.version.cuda,
               gpu=torch.cuda.get_device_name() if torch.cuda.is_available() else None,
               samples_sha256=audit["samples_sha256"], split_counts={k:len(v) for k,v in subsets.items()},
               objective="3D von Mises-Fisher NLL; no screen/non-screen labels",
               selection="unweighted mean of the two validation-domain mean angular errors",
               final_test="Evaluated only after training and checkpoint selection are complete")
    import torchvision
    run["torchvision_version"] = torchvision.__version__
    run["resume_semantics"] = "Epoch-boundary RNG/sampler recovery; fresh seeded workers; no cross-hardware bitwise guarantee"
    run["pipeline_sha256"] = {str(path):digest(path) for path in
        (Path(__file__), Path(__file__).with_name("public_gaze_prepare.py"),
         Path(__file__).parent.parent/"shared/public_gaze.py")}
    if args.resume:
        checkpoint = torch.load(args.resume, map_location=args.device, weights_only=False)
        validate_resume(checkpoint["run"], run)
        if "rng" not in checkpoint:
            raise ValueError("RESUME_RNG_STATE_MISSING")
        best_path = output/"best.pt"
        best_checkpoint = torch.load(best_path, map_location="cpu", weights_only=False) if best_path.exists() else None
        if best_checkpoint is None or abs(best_checkpoint["best"]-checkpoint["best"]) > 1e-8:
            # last.pt is committed before best.pt. Recover that narrow crash
            # interval using the selected state already stored in last.pt.
            if abs(checkpoint["validation"]["domain_macro_mean_degrees"]-checkpoint["best"]) > 1e-8:
                raise ValueError("RESUME_BEST_CHECKPOINT_MISSING_OR_INCONSISTENT")
            if (output/"checkpoint-lock.json").exists():
                raise ValueError("LOCKED_BEST_CHECKPOINT_CHANGED")
            save_checkpoint(best_path, checkpoint)
        model.load_state_dict(checkpoint["model"])
        optimizer.load_state_dict(checkpoint["optimizer"])
        scheduler.load_state_dict(checkpoint["scheduler"])
        scaler.load_state_dict(checkpoint["scaler"])
        start_epoch, best, stale = checkpoint["epoch"]+1, checkpoint["best"], checkpoint["stale"]
        restore_rng(checkpoint["rng"], train_loader, val_loader)
        run = checkpoint["run"]
        # Retain the original immutable run manifest; append recovery evidence.
        with (output/"resume-events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(dict(resumed_utc=datetime.now(timezone.utc).isoformat(),
                                   next_epoch=start_epoch+1, last_sha256=digest(args.resume)))+"\n")
    else:
        json_write(output/"run.json", run)
    # A crash after checkpoint lock may re-evaluate only that selected model;
    # it cannot train a further epoch or select a different checkpoint.
    if (output/"checkpoint-lock.json").exists():
        locked = json.loads((output/"checkpoint-lock.json").read_text("utf-8"))
        if digest(output/"best.pt") != locked["best_sha256"]:
            raise ValueError("LOCKED_BEST_CHECKPOINT_CHANGED")
        start_epoch = args.epochs
    if stale >= args.patience:
        start_epoch = args.epochs
    start = time.monotonic()
    for epoch in range(start_epoch, args.epochs):
        model.train()
        accumulated, count = 0., 0
        for step, (images, gaze, _) in enumerate(train_loader):
            images, gaze = images.to(args.device, non_blocking=True), gaze.to(args.device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast(device_type="cuda", enabled=args.device.startswith("cuda")):
                prediction = model(images)
                loss = vmf_loss(prediction, gaze)
            if not torch.isfinite(loss):
                raise ValueError("NONFINITE_TRAINING_LOSS")
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 5.)
            scaler.step(optimizer)
            scaler.update()
            accumulated += loss.item()*len(images)
            count += len(images)
            if (step+1) % 100 == 0:
                print(json.dumps(dict(event="batch", epoch=epoch+1, step=step+1,
                                      steps=len(train_loader), nll=accumulated/count)), flush=True)
        metrics, _, _, _ = evaluate(model, val_loader, args.device)
        score = metrics["domain_macro_mean_degrees"]
        improved = score < best-.02
        if improved:
            best, stale = score, 0
        else:
            stale += 1
        scheduler.step()
        checkpoint = dict(model=model.state_dict(), optimizer=optimizer.state_dict(),
                          scheduler=scheduler.state_dict(), scaler=scaler.state_dict(), epoch=epoch,
                          best=best, stale=stale, run=run, validation=metrics,
                          rng=capture_rng(train_loader, val_loader))
        save_checkpoint(output/"last.pt", checkpoint)
        if improved:
            save_checkpoint(output/"best.pt", checkpoint)
        entry = dict(event="epoch", epoch=epoch+1, nll=accumulated/max(count,1), validation=metrics,
                     best=best, stale=stale, seconds=time.monotonic()-start)
        with (output/"history.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry)+"\n")
        print(json.dumps(entry), flush=True)
        if stale >= args.patience:
            break
    checkpoint = torch.load(output/"best.pt", map_location=args.device, weights_only=False)
    model.load_state_dict(checkpoint["model"])
    best_hash = digest(output/"best.pt")
    if not (output/"checkpoint-lock.json").exists():
        json_write(output/"checkpoint-lock.json", dict(best_sha256=best_hash, epoch=checkpoint["epoch"]+1,
                   locked_utc=datetime.now(timezone.utc).isoformat(), purpose="Final test begins after this immutable selection"))
    validation, val_prediction, _, val_errors = evaluate(model, val_loader, args.device)
    scales = np.degrees(np.sqrt(2/np.clip(val_prediction[:,3], 1, 200)))
    # Conservative domain-wise 90th quantile calibration. This is an empirical
    # validation interval; domain shift and adjacent frames break iid coverage.
    quantiles = {dataset:float(np.quantile((val_errors/scales)[[r["dataset"] == dataset for r in subsets["val"]]], .9, method="higher"))
                 for dataset in ("mpiifacegaze", "gaze360")}
    calibration = dict(q90_scale_multiplier=max(quantiles.values()), by_domain=quantiles,
                       source="validation only", caveat="Empirical interval, not an individual guarantee under domain shift")
    # The test loader is deliberately constructed only after checkpoint lock.
    test_loader = make_loader(root, subsets["test"], args)
    test, prediction, targets, errors = evaluate(model, test_loader, args.device)
    acceptance = {}
    calibrated_error90 = np.degrees(np.sqrt(2/np.clip(prediction[:,3],1,200)))*calibration["q90_scale_multiplier"]
    for dataset in ("mpiifacegaze", "gaze360"):
        domain = np.asarray([r["dataset"] == dataset for r in subsets["test"]])
        accepted = domain & (calibrated_error90 <= 20)
        acceptance[dataset] = dict(eligible_visible_frames=int(domain.sum()),
            accepted_frames=int(accepted.sum()), accepted_fraction=float(accepted.sum()/domain.sum()),
            accepted_mean_angular_degrees=float(errors[accepted].mean()) if accepted.any() else None,
            empirical_error90_coverage=float((errors[domain] <= calibrated_error90[domain]).mean()),
            note="Confidence gate only; explicit neutral reference and temporal rules additionally required")
    np.savez_compressed(output/"test-predictions.npz", prediction=prediction, target=targets, errors_degrees=errors)
    baselines = {}
    for dataset in ("mpiifacegaze", "gaze360"):
        baseline = np.mean([r["gaze"] for r in subsets["train"] if r["dataset"] == dataset], axis=0)
        baselines[dataset] = baseline/np.linalg.norm(baseline)
    baseline_predictions = np.asarray([baselines[r["dataset"]] for r in subsets["test"]])
    test["training_mean_vector_baseline"] = summarize(angular_errors(baseline_predictions,targets), subsets["test"])
    retained_coverage = {}
    for dataset in ("mpiifacegaze", "gaze360"):
        all_frames = sum(count for key,count in audit["coverage_counts"].items() if key.startswith(dataset+"/test/"))
        retained_coverage[dataset] = audit["coverage_counts"].get(dataset+"/test/ok",0)/max(all_frames,1)
    report = dict(schema=SCHEMA, best_sha256=best_hash, validation=validation, test=test,
                  validation_calibration=calibration, extraction_coverage=audit["coverage_counts"],
                  runtime_confidence_coverage=acceptance,
                  test_retained_coverage=retained_coverage,
                  interpretation="Conditional on exactly one visible Face Mesh face and open eyes; not screen-point accuracy",
                  mpii_protocol="Two held-out subjects, not the published 15-fold leave-one-person-out benchmark",
                  gaze360_protocol="Official grouped splits, Face Mesh visible-face subset, single-frame model; not full Gaze360 benchmark")
    json_write(output/"evaluation.json", report)
    export(model, output, report, args, checkpoint["run"])
    print(json.dumps(dict(event="complete", test=test, output=str(output))), flush=True)


def export(model, output, report, args, run):
    import onnxruntime as ort
    model = model.cpu().eval()
    dummy = torch.randn(1,3,INPUT_SIZE,INPUT_SIZE)
    target = output/"gaze-public.onnx"
    torch.onnx.export(model, dummy, str(target), input_names=["face"], output_names=["gaze"],
                      opset_version=17, dynamic_axes={"face":{0:"batch"}, "gaze":{0:"batch"}}, dynamo=False)
    options = ort.SessionOptions()
    options.intra_op_num_threads = args.cpu_threads
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(target), sess_options=options, providers=["CPUExecutionProvider"])
    with torch.inference_mode():
        expected = model(dummy).numpy()
    actual = session.run(None, {"face":dummy.numpy()})[0]
    difference = float(np.max(np.abs(expected-actual)))
    if not np.allclose(expected,actual,rtol=1e-3,atol=1e-4):
        raise RuntimeError(f"ONNX_PARITY_FAILED:{difference}")
    samples = []
    for iteration in range(110):
        start = time.perf_counter()
        session.run(None, {"face":dummy.numpy()})
        if iteration >= 10:
            samples.append((time.perf_counter()-start)*1000)
    test = report["test"]
    checks = dict(mpii_mean_le_12_degrees=test["mpiifacegaze"]["mean_degrees"] <= 12,
                  gaze360_mean_le_20_degrees=test["gaze360"]["mean_degrees"] <= 20,
                  mpii_p90_le_25_degrees=test["mpiifacegaze"]["p90_degrees"] <= 25,
                  gaze360_p90_le_40_degrees=test["gaze360"]["p90_degrees"] <= 40,
                  cpu_model_p95_le_80ms=float(np.percentile(samples,95)) <= 80,
                  full_dataset=not args.allow_smoke)
    for dataset in ("mpiifacegaze", "gaze360"):
        baseline_mean = test["training_mean_vector_baseline"][dataset]["mean_degrees"]
        checks[dataset+"_improves_domain_baseline_5pct"] = test[dataset]["mean_degrees"] <= .95*baseline_mean
        checks[dataset+"_empirical_error90_coverage_ge_85pct"] = report["runtime_confidence_coverage"][dataset]["empirical_error90_coverage"] >= .85
        checks[dataset+"_runtime_confidence_acceptance_ge_10pct"] = report["runtime_confidence_coverage"][dataset]["accepted_fraction"] >= .1
        checks[dataset+"_retained_coverage_minimum"] = report["test_retained_coverage"][dataset] >= (.8 if dataset == "mpiifacegaze" else .3)
    metadata = dict(schema=SCHEMA, input_size=INPUT_SIZE, onnx_sha256=digest(target),
                    input="float32 NCHW RGB, /255 then ImageNet mean/std", output="unit x,y,z and von-Mises-Fisher concentration",
                    axes="x camera-left, y up, z away; gaze360 eye-camera ray basis",
                    training_initialization=run["initialization"], validation_calibration=report["validation_calibration"],
                    onnx_parity_max_abs=difference,
                    cpu_latency_ms=dict(median=float(np.median(samples)),p95=float(np.percentile(samples,95)),threads=args.cpu_threads),
                    test_metrics={k:v for k,v in test.items() if k != "training_mean_vector_baseline"},
                    baseline_metrics=test["training_mean_vector_baseline"],
                    runtime_confidence_coverage=report["runtime_confidence_coverage"],
                    test_retained_coverage=report["test_retained_coverage"],
                    release_checks=checks, deployment_eligible=all(checks.values()),
                    usage="Private noncommercial research prototype; retain both dataset licenses. No public model distribution authorized.",
                    limitations=["Not screen geometry or cheating intent", "Explicit neutral calibration required",
                                 "CPU timing excludes Face Mesh/camera/crop", "Far-off-axis crop perspective approximation",
                                 "Conditional visible-face evaluation; inspect coverage report", "Camera and user domain shift require local validation"])
    json_write(output/"gaze-public.json", metadata)


def export_only(output):
    """Recover a failed export without dataset access, training, or test replay."""
    report = json.loads((output/"evaluation.json").read_text("utf-8"))
    locked = json.loads((output/"checkpoint-lock.json").read_text("utf-8"))
    checkpoint_hash = digest(output/"best.pt")
    if (report.get("schema") != SCHEMA or report.get("best_sha256") != checkpoint_hash
            or locked.get("best_sha256") != checkpoint_hash):
        raise ValueError("EXPORT_RECOVERY_CHECKPOINT_MISMATCH")
    checkpoint = torch.load(output/"best.pt", map_location="cpu", weights_only=False)
    if checkpoint["epoch"]+1 != locked["epoch"]:
        raise ValueError("EXPORT_RECOVERY_EPOCH_MISMATCH")
    args = argparse.Namespace(**checkpoint["run"]["args"])
    torch.set_num_threads(args.cpu_threads)
    model = GazeNet(pretrained=False).cpu()
    model.load_state_dict(checkpoint["model"])
    export(model, output, report, args, checkpoint["run"])
    json_write(output/"export-recovery.json", dict(recovered_utc=datetime.now(timezone.utc).isoformat(),
               best_sha256=checkpoint_hash, evaluation_sha256=digest(output/"evaluation.json"),
               export_source_sha256=digest(Path(__file__)), test_reevaluated=False))
    print(json.dumps(dict(event="export_recovered", output=str(output), test_reevaluated=False)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/public-gaze"))
    parser.add_argument("--output", type=Path, default=Path("training/runs/public-gaze-v1"))
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--patience", type=int, default=8)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=.0003)
    parser.add_argument("--seed", type=int, default=20261007)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--export-only", action="store_true", help="Recover export from locked best.pt and saved evaluation; never rerun test")
    parser.add_argument("--from-scratch", action="store_true", help="Random initialization; default uses documented ImageNet transfer")
    parser.add_argument("--allow-smoke", action="store_true", help="Functional test only; resulting artifact is never release eligible")
    train(parser.parse_args())


if __name__ == "__main__":
    main()
