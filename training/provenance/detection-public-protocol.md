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
