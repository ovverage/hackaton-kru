> Historical experiment protocol, 7 October 2026. This records the frozen design and decisions at that time, not a currently running job or the latest application workflow. The completed results, retained/rejected models, current nine-point calibration and artifact manifests are indexed in [training/README.md](../README.md). Do not restart its expired absolute deadline or transfer legacy runtime assumptions to 0.5.2. Source/license texts remain authoritative for their respective components.

---

# Public-dataset detector retraining protocol, 2026-10-07

This protocol implements the case's local YOLO11n phone, person and face detectors.
It does not turn a phone bounding box into evidence of camera orientation or intent
to photograph the monitor. Those actions require separately validated temporal
logic and human review.

## Prepared data and separation

`python -m training.public_detection prepare --datasets data/datasets --output data/prepared-public-detection`

The inputs are the previously verified original files under `coco2017/raw` and
`widerface/raw`. Preparation never modifies them. The output contains hardlinked
image views on the same volume, generated labels, image lists, and a `manifest.json`
with annotation and split hashes, source/category mappings, counts and exclusions.
Use `--only wider` or `--only coco` to prepare independently. `--link-mode copy` is
available for an explicitly chosen different volume. Hardlinks preserve source
bytes without duplicating tens of gigabytes.

COCO has two classes: **0 cell phone, 1 person**. Source IDs are resolved from
category names, never from a presumed YOLO index. Every original train2017 image
is retained, including images containing neither class. Approximately 5% of each
target-presence stratum is chosen for development by a fixed SHA-256 ordering of
image IDs; all other train2017 images train the model. All 5,000 official val2017
images form the final test. Crowd boxes are retained as weak group boxes in
training because the YOLO loss cannot encode ignore regions. Crowd-aware final
metrics use the original COCO JSON and pycocotools, rather than those converted
group-box labels. The image split does not guarantee subject or scene separation.

WIDER has one class: **0 face**. Entire event categories from original WIDER train
are held out for development (approximately 10% of event categories, not 10% of
images). This separates event categories, but cannot guarantee person separation
because the dataset has no exhaustive identity grouping. Train/development images
containing invalid-flagged faces are excluded as whole images: invalid faces must
not silently become background examples. Counts of excluded images and all boxes
in those images are recorded. Every valid, positive-area clipped face box is kept,
including faces smaller than four pixels after resizing; their number is reported.
All 3,226 official validation images form the final test. Official test images
have no public bounding boxes and never contribute a fabricated score.

WIDER's YOLO AP on valid boxes is a **diagnostic**, not the official WIDER
easy/medium/hard result: YOLO's AP does not apply that benchmark's ignore masks.
Final evaluation also writes the per-event prediction text files expected by an
official WIDER evaluator. The fixed-threshold audit treats invalid regions as
ignored using prediction-area overlap, reports this policy explicitly, and is
also distinct from the official benchmark.

## Training

Use the pinned [official YOLO11n initialization](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt)
with SHA-256 `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`.
This is **transfer learning**. The initializer saw COCO train2017, so internal
COCO development examples are held out from this fine-tune only. Final COCO
val2017 is the independent final set with respect to standard COCO pretraining.
No previously face-trained checkpoint with uncertain validation exposure is used.

```powershell
python -m training.public_detection train --data C:/QorgauTraining/20261007/prepared/wider/data.yaml --weights C:/QorgauTraining/20261007/yolo11n.pt --output C:/QorgauTraining/20261007/runs/face --epochs 100 --patience 20 --batch 16 --workers 8 --device 0
python -m training.public_detection train --data C:/QorgauTraining/20261007/prepared/coco/data.yaml --weights C:/QorgauTraining/20261007/yolo11n.pt --output C:/QorgauTraining/20261007/runs/person-phone --epochs 80 --patience 20 --batch 16 --workers 8 --device 0
```

Jobs must be scheduled within the available GPU budget; the commands do not
authorize termination of other GPU users. Batch size and worker count can be
lowered without weakening dataset coverage. `--train-only` separates training
from the later export audit. `--resume <run>/fit/weights/last.pt` resumes an
interrupted run while verifying that its prepared manifest is unchanged.
Resume also verifies the initializer, software versions, checkpoint path and saved
statistical settings. Supply the same epoch/batch/seed/patience arguments as the
original run; changing device or worker count is allowed. Checkpoint training
arguments must agree with the run receipt. A run which began final evaluation
cannot resume training against that same receipt. Non-blocking OS locks reject
concurrent training/evaluation/export into the same run directory and release on
process exit, including a crash.

The pipeline uses seed 20261007, deterministic algorithms, AdamW, cosine learning
rate decay, moderate geometric/colour augmentation, no vertical flips, and a
ten-epoch close-mosaic phase. The early-stopping patience is 20 development epochs.
Training records requested settings, actual completed epochs, software versions,
initializer/checkpoint hashes and dataset manifest hash. The official final test
does not select the checkpoint or operating thresholds. The best development
checkpoint is evaluated once by default; an existing final audit is preserved
and cannot be silently repeated in the same run directory.

## Final evaluation and export

```powershell
python -m training.public_detection evaluate --data C:/QorgauTraining/20261007/prepared/coco/data.yaml --checkpoint C:/QorgauTraining/20261007/runs/person-phone/fit/weights/best.pt --output C:/QorgauTraining/20261007/runs/person-phone --batch 16 --device 0
```

Before exporting, evaluation saves immutable `heldout-metrics.json`, including
checkpoint, data-manifest and prediction hashes, all measured metrics, declared
acceptance policy, and the exact predetermined parity-image paths and byte hashes.
It checks that validation processed every final-test image; missing predictions
cannot produce a successful receipt. `final-evaluation.json` separately records
the current export/parity result. If an export dependency or parity operation
fails after metrics were saved, recover with:

```powershell
python -m training.public_detection export --data C:/QorgauTraining/20261007/prepared/coco/data.yaml --checkpoint C:/QorgauTraining/20261007/runs/person-phone/fit/weights/best.pt --output C:/QorgauTraining/20261007/runs/person-phone
```

Recovery verifies the exact original checkpoint, prepared data and annotation
hashes, saved prediction hash, unchanged metrics receipt and acceptance policy,
and the same parity-image subset and bytes. It repeats only export and graph
parity; it never calls held-out prediction/validation or recalculates AP. The
parity subset cannot be changed in the recovery command. Missing/non-finite
metrics, incomplete image coverage, or failed image-quality gates still fail
the recovered run; repairing export does not convert a weak model into a pass.

Dependencies: the validated Ultralytics 8.3.221 environment with PyTorch, Pillow,
ONNX, ONNX Runtime, NumPy, OpenCV and pycocotools for COCO final scoring. The
current upstream [training arguments](https://docs.ultralytics.com/modes/train/)
and [export arguments](https://docs.ultralytics.com/modes/export/) are references;
this implementation was checked against the locally installed 8.3.221 source.

The report includes aggregate/per-class AP, fixed-confidence precision and recall,
negative-image false-positive rate, ignored detections, and COCO's official
crowd-aware AP/AR for person and cell phone (maxDets 1/10/100). Its default fixed
confidence thresholds are phone **0.80**, person **0.55**, face **0.55**; they are
not chosen on final-test performance. Predictions use NMS IoU 0.45 to match local
runtime. Operating-point matching uses IoU 0.5 and descending confidence; duplicate
detections are false positives, and detections within crowd/invalid regions can
be ignored after valid truths have been matched.

The predeclared prototype image-model acceptance floors are AP50 >= 0.40 for
phone, >= 0.60 for person, and >= 0.50 for face, plus passing CPU graph parity.
At the fixed operating thresholds, precision must be >= 0.80 for every class;
recall must be >= 0.25 for phone and >= 0.50 for person and face. AP alone cannot
pass a candidate which produces insufficient detections at the deployed threshold.
These are modest rejection floors, not a claim of maximum accuracy or sufficient
exam-event performance. A failed gate produces a failed report and nonzero exit;
it must not silently publish a candidate. A passing image gate still requires
webcam, temporal event, CPU performance, and application integration validation.

Export is FP32 ONNX opset 17, fixed `[1,3,640,640]`, raw `[1,4+classes,8400]`
output, no embedded NMS, and class names in metadata. Its SHA-256 is recorded.
On at least 32 deterministically selected held-out images by default, the audit
compares PyTorch and ONNX on exactly the same runtime-style letterboxed tensor.
Raw box-coordinate absolute tolerance is 0.05 px; class-score tolerance is 0.0005.
Two-thread CPU median/p95 inference timings exclude capture, preprocessing and
NMS, and must not be presented as full application frame rate.

Image-model metrics cannot prove behavior over time, photographing intent, secure
browser enforcement, or performance on this hackathon's particular webcam.
Dataset source/license notices remain authoritative; retraining does not create
an unrestricted right to redistribute images or weights.

## User-imposed ten-hour limit, 2026-10-07

The user imposed a total deadline of **2026-10-07 18:47:54 UTC**, starting at
08:47:54 UTC. This changes resource planning before the final test is opened;
it does not relax any image-quality, provenance or ONNX acceptance gate.

First measure training throughput without touching the final test:

```powershell
python -m training.public_detection benchmark --data C:/QorgauTraining/20261007/prepared/coco/data.yaml --weights C:/QorgauTraining/20261007/yolo11n.pt --output C:/QorgauTraining/20261007/pilots/coco-b64-w16 --batch 64 --workers 16 --steps 64 --warmup-steps 16 --cache none
```

Use a new pilot directory for each batch/worker combination. The pilot uses the
same train split and augmentations, discards the first 16 batch timings, and
raises/catches a dedicated completion exception at batch 64. It exits before
epoch-end validation, saving a checkpoint, or exporting a candidate. It records
images per second, synchronized batch time, time between batches (loader/logging),
peak allocated/reserved GPU memory, and total startup/cache wall time in
`benchmark.json`. A benchmark-only DetectionTrainer subclass skips constructing
the development loader, avoiding Windows spawning and label-cache work for an
unused split. The native DetectionValidator accepts a missing loader at
construction; pilot validation, final evaluation and checkpoint saving are
explicitly forbidden and fail if reached. A real CPU smoke test trains two
batches with an unreadable dev image, proving that the unused split is not
scanned. This contract is pinned to Ultralytics 8.3.221; ordinary training retains
its full development loader and validation. Neither development scores nor
final-test data select the hardware configuration.

Short Windows pilots choose their worker cap automatically when the CLI option
is omitted: batches below 128 (including 32/64) cap at four workers, and batches
128 or larger cap at eight. These are joint batch/worker hardware candidates:
Windows spawn serializes the full label dataset separately for every worker,
while the larger batch candidate tests whether eight workers reduce loader idle
time enough to justify startup cost. The scheduling cap changes no dataset split,
labels or augmentation recipe; exact random augmentation sequences may differ.
Linux keeps the requested worker count. Explicit `--windows-worker-cap 4` or `8`
overrides automatic selection, and `0` disables the cap. `worker_setup` records
requested/effective counts, automatic or explicit policy and the reason, while
`settings.workers` records the actual loader count used for throughput selection.
Training has no implicit cap; it uses the worker setting chosen from the report.

The restarted budget queue may explicitly use `--reuse-completed-pilots`. It
accepts only completed reports with the exact current dataset manifest, pinned
initializer, runtime versions, settings, 48 total/12 warmup batches, full measured
image coverage, finite consistent throughput and allocator memory at most 39 GiB.
Older reports bind the already-enforced pinned initializer through the native
`fit/args.yaml` model path; new reports record its SHA256 directly. Reuse records
the report SHA256 and evidence in the new journal. Partial, failed or incompatible
outputs require explicit operator inspection/archival; no incomplete run is
silently resumed or overwritten. Existing v3 pilots explicitly retain cap four.
A separate `coco128-w8` output measures batch 128 with eight workers. Both batch
128 candidates require a completed batch64 pilot with finite peak allocation
reservation no greater than 20 GiB; hardware selection compares both candidates.

Batch 32/64 preserve nominal batch 64 through Ultralytics gradient accumulation;
batch 128 changes effective optimizer batch size. Batch changes can change batch
normalization and sample ordering even with the same seed. Select hardware from
throughput and memory measurements, record a fresh statistical run or stage, and
retain the final quality gates. Do not describe a faster pilot as higher accuracy.

For this dataset, 112,372 train images at 50 images/s need roughly 37.5 minutes
per epoch before development validation. A five-hour allowance cannot credibly
promise 80 complete epochs at that speed. Twelve full epochs require at least
approximately 80–90 images/s including development overhead; twenty require
approximately 135–150 images/s. These are planning estimates, not measurements.

The optional `--cache disk` creates original-resolution decoded `.npy` images.
It is useful only when decode cost exceeds the extra disk I/O and sufficient SSD
space is available. Existing `.npy` files are read even under `--cache none` in
Ultralytics 8.3.221. RAM cache is deliberately not offered: Windows worker spawn
can duplicate it, and upstream warns about determinism. Cache creation time is
part of the training wall budget below. All images, labels and split membership
remain unchanged.

```powershell
python -m training.public_detection train --data C:/QorgauTraining/20261007/prepared/coco/data.yaml --weights C:/QorgauTraining/20261007/yolo11n.pt --output C:/QorgauTraining/20261007/runs/person-phone-timed --epochs 80 --batch 64 --workers 16 --cache disk --time-hours 4.5 --deadline-utc 2026-10-07T18:47:54Z --evaluation-reserve-minutes 75 --device 0
```

`--time-hours` limits training wall time **including** setup/cache generation.
The training cutoff is the earlier of that limit and the overall deadline minus
the evaluation reserve. At `on_train_start`, after loader setup, the remaining
hours arm the native Ultralytics `time` mechanism. If setup exhausts the budget,
the run fails before the first training batch. `timing.json` and `training.json`
record the cutoff, setup cost, epoch batch coverage, and whether the final epoch
was partial; a partial epoch is not counted as a complete pass through the data.
Early stopping still uses only the development set.

The exact 8.3.221 trainer source checks its timer after optimizer updates and
development validation, then dynamically estimates remaining epochs and adjusts
the LR schedule. It also performs an internal final development validation.
Consequently this is a timed schedule, not an unchanged 80-epoch schedule. The
reserve covers that work and the separate final-test/export/parity stages.
The native Mosaic-close equality can be skipped when that estimated horizon
moves. A timed-only guard closes Mosaic once when the current epoch reaches or
passes `estimated_epochs - 10`, records the closure in `timing.json`, and suppresses
a duplicate native loader reset. Resume restores the closed state on its fresh
loader. Untimed training keeps native augmentation behavior unchanged.
Cooperative library timers cannot interrupt blocked setup/I/O or an ongoing
export; the queue supervisor must enforce the user's absolute deadline with a
process-tree watchdog. The pipeline refuses to start final evaluation if the
overall deadline has already elapsed. Deadline expiry never creates a passing
metric report.

Untimed CLI calls and their old resume contracts retain the same default settings.
`train` and `benchmark` accept `--scan-threads 32` (default 8, bounded 1–48).
Ultralytics 8.3.221 otherwise caps the initial image/label verifier at eight
threads independently of `--workers`. This scheduling option changes only
`ultralytics.data.dataset.NUM_THREADS` for the run and restores it afterward.
The original ordered verifier, corrupt-image/label checks, duplicate handling,
cache version 1.0.3 and file hash validation remain intact. The selected thread
count is recorded under `loader_setup`; training and internal-development caches
are both covered. No final-test image scan or metric evaluation is introduced by
this option, and completed native caches are reused by later pilots/stages.
It does not create a cache from unchecked annotations or bypass image checks.
A resumed timed run cannot extend its recorded cutoff/deadline. An explicitly
added shorter budget is recorded; workers are applied before loader setup because
8.3.221 otherwise ignores a workers override during resume. Native timed resume
can stop earlier than expected: its estimated epoch horizon is relative to
remaining runtime but compared to the absolute resumed epoch number. Prefer a
declared fresh stage when changing both hardware settings and the time schedule.

To keep learned weights while changing batch/cache, first stop the old run at a
safe checkpoint, then use a new output and an explicit parent declaration:

```powershell
python -m training.public_detection train --data C:/QorgauTraining/20261007/prepared/wider/data.yaml --weights C:/QorgauTraining/20261007/runs/face/fit/weights/best.pt --stage-from-run C:/QorgauTraining/20261007/runs/face --output C:/QorgauTraining/20261007/runs/face-timed-stage2 --epochs 30 --batch 64 --workers 16 --time-hours 0.75 --deadline-utc 2026-10-07T18:47:54Z --evaluation-reserve-minutes 75 --device 0
```

A continuation stage starts a new optimizer; it is not presented as resume.
The parent must be stopped, must use the same prepared data manifest and pinned
initializer lineage, and must not have opened its final test. Only its development-
selected `best.pt` is accepted. Parent checkpoint and receipt hashes, original
pretraining hash and both stages' settings are recorded. The old run is preserved.

Continuation stages retain the parent unless the child **strictly improves** the
same internal-development fitness. For the pinned Ultralytics 8.3.221 detector,
fitness is mAP50–95(B), with weights `[0, 0, 0, 1]` on P/R/AP50/AP50–95. The raw
parent checkpoint must preserve finite matching `best_fitness`, development
`train_metrics.fitness`, and `metrics/mAP50-95(B)`; parent receipt, checkpoint and
runtime versions must all be 8.3.221. Missing or inconsistent evidence fails
before training. No final-test metrics enter this comparison.

After training, `fit/weights/child-best.pt` preserves the child candidate bytes.
For a tie or regression, the unchanged parent's bytes are copied atomically to
the queue's expected `fit/weights/best.pt`; otherwise that path retains the child.
`stage-selection.json` and `training.json` record both paths, SHA256 hashes,
development fitness values and the selected source before final evaluation.
Child completed/partial epoch counts still describe the actual new training,
even when the older parent checkpoint wins. Final evaluation runs once on the
selected checkpoint only.

Normal continuation-stage completion also freezes the **original parent** with
an append-only `final-selection-marker.json` pointing to the descendant's
`stage-selection.json` SHA256 and selected checkpoint SHA256. This prevents a
later resume or new stage from the parent when its selected copy is evaluated
under the descendant's output directory. The normal completion path writes this
marker before returning control to the queue.

For an already completed stage from an older code version, the explicit command
below adds the same protection without repeating selection or reading any
held-out metrics. It checks both checkpoints, unchanged parent provenance, shared
dataset/versions and the recorded strict dev winner, then takes only the parent
run lock. The child may legitimately be holding its own final-evaluation lock.
Repeated calls with identical evidence preserve the original marker bytes;
conflicting selection evidence is refused.

```powershell
python -m training.public_detection freeze-parent --parent-run C:/QorgauTraining/20261007/training/runs/public-wider-v1 --stage-run C:/QorgauTraining/20261007/training/runs/budget-wider-v1
```

### Explicit fallback after a failed or timed-out continuation stage

`training.public_detection_fallback` is a separately invoked recovery tool, not
an automatic queue action. Preparation requires a queue receipt marking the exact
stage `failed` or `timed_out`, a recorded stage process identity confirmed gone,
and nonblocking OS locks on both historical runs. Every recorded queue identity
must be gone or verified as part of another currently running job's live process
tree. Unverified identities and surviving unattributed workers reject fallback;
this audit does not stop other jobs and explicitly covers recorded processes.
It verifies matching manifests, unchanged parent receipt
and weights, pinned initialization lineage, versions and checkpoint dev metrics.
The child's preserved `child-best.pt` is preferred as its candidate when present;
otherwise its `best.pt` is considered. Native stripped checkpoints require their
recognizable stripped state and matching retained fitness/mAP50–95 metrics.
Missing child weights retain the parent; inconsistent metrics fail preparation.

Strict dev improvement selects the child; ties retain the parent. The selected
bytes go to a **new** output's `selected.pt`, with `selection.json` binding both
source hashes, historical receipt hashes and the failure reason. Each original
run receives an exclusively created `final-selection-marker.json` linking that
selection receipt and checkpoint hashes. These markers prevent resume and new
continuation stages even though the final-test receipt lives elsewhere. Existing
training reports and checkpoints are not overwritten. A partially written set
of markers fails closed and requires inspection rather than a fresh selection.

```powershell
python -m training.public_detection_fallback prepare --data C:/QorgauTraining/20261007/data/public-detection/wider/data.yaml --stage C:/QorgauTraining/20261007/training/runs/budget-wider-v1 --journal C:/QorgauTraining/20261007/budget-training-status.json --output C:/QorgauTraining/20261007/training/runs/wider-fallback-final
python -m training.public_detection_fallback evaluate --output C:/QorgauTraining/20261007/training/runs/wider-fallback-final
```

Evaluation verifies both markers and the frozen checkpoint/data/history before
the existing one-shot final evaluator runs on CPU. Preparation alone reads no
final-test images or metrics. An existing final-evaluation receipt is never
discarded; export-only recovery uses the existing `public_detection export`
command with the frozen checkpoint. Run fallback evaluation only under an
external deadline/process supervisor and within the available CPU capacity.
The helper's presence does not mean fallback or final evaluation has occurred.
