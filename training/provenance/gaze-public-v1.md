# Public-dataset continuous gaze experiment

This pipeline fine-tunes an ImageNet-initialized MobileNetV3-small on **both**
downloaded MPIIFaceGaze and Gaze360. It predicts a continuous 3D direction and
directional uncertainty. It does not learn a SCREEN label and does not infer
cheating intent. MediaPipe Face Mesh remains required for face selection,
consistent crops, blink gating, iris features, and head pose.

## Sources and coordinates

- MPIIFaceGaze archive `Readme.txt`, dimensions 22–24: face centre; 25–27: gaze
  target. Subtract the centre from the target, then rotate from the camera basis
  (x right, y down, z away) to an eye-camera ray basis. Merely changing signs
  would leave an off-axis systematic error. See the
  [official MPIIFaceGaze description](https://www.mpi-inf.mpg.de/departments/computer-vision-and-machine-learning/research/gaze-based-human-computer-interaction/its-written-all-over-your-face-full-face-appearance-based-gaze-estimation).
- [Gaze360 dataset coordinates and splits](https://github.com/erkil1452/gaze360/blob/546762ef1373dae13569afdfbe501a834040e8c8/dataset/README.md):
  x points camera-left, y up, z away; (0,0,-1) looks toward the camera.
  Its original [loader](https://github.com/erkil1452/gaze360/blob/546762ef1373dae13569afdfbe501a834040e8c8/code/data_loader.py)
  uses yaw=atan2(x,-z), pitch=asin(y).
- [Torchvision MobileNetV3-small](https://docs.pytorch.org/vision/stable/models/generated/torchvision.models.mobilenet_v3_small.html):
  the versioned IMAGENET1K_V1 initialization is recorded, not presented as
  random initialization or a pretrained gaze model. All backbone parameters
  are fine-tuned using the downloaded datasets.

Train and runtime use the same 224×224 square crop from 468 Face Mesh landmarks,
with reflected padding and RGB/ImageNet normalization. Crops are not warped
using assumed camera intrinsics. An image crop approximates a camera pointed at
the face; perspective differences for far off-axis faces remain a known domain
limitation. No exact camera/monitor geometry or screen coordinates are claimed.

## Frozen split protocol

MPII p00–p10 train (30,806 unique full images), p11–p12 validation (3,863), and
p13–p14 final test (2,998). Original annotations can repeat one image for eye
evaluation; each full face is retained once. This is a fixed two-person holdout,
**not** the published 15-fold leave-one-person-out benchmark.

Gaze360 retains original train 126,928 / validation 17,038 / test 25,969 central
frames. The 55 / 8 / 15 recording groups do not overlap. Unused frames remain in
the source dataset but are not labelled training examples. Recording-level
separation prevents adjacent-frame and recaptured-person leakage. Both source
paths and acquisition groups are asserted disjoint before training.

Face Mesh absence, multiple detected faces, tiny faces, closed eyes, and image
decode failures are counted separately for every dataset and split. Angular
metrics apply to the retained visible-face subset. They must be shown alongside
the extraction coverage; rear-facing frames are not counted as successes.

## Objective and validation

The model emits a unit vector and von Mises-Fisher concentration, trained with
the proper directional negative log-likelihood. Sampling gives equal weight
to each dataset, then equal weight to its acquisition groups. Horizontal flips
negate the x target; augmentation otherwise changes colour/blur only.

Best checkpoint selection and early stopping use the mean of the two domains'
validation angular errors. The empirical uncertainty multiplier uses only
validation, conservatively taking the worse domain's 90th percentile. This is
not an individual statistical guarantee under domain shift or dependent frames.
The best checkpoint hash is locked before constructing the final test loader.
Final-test results must not be used to tune a subsequent checkpoint.

Reports include mean/median/p90 angular error, fraction below 10°/15°, each
acquisition group's mean, bootstrap confidence intervals over group means,
training-mean-vector baseline, and runtime confidence coverage. Only two MPII
test subjects give a weak estimate of population generalization, even if the
frame count is large. Gaze360 visible-face, single-frame scores are not directly
comparable with the official all-directions temporal benchmark.

ONNX export checks PyTorch parity and reports CPU batch-one latency separately
from camera, crop, and Face Mesh cost. Initial release gates were fixed before
training: MPII mean≤12° and p90≤25°, Gaze360 mean≤20° and p90≤40°, model CPU p95≤80ms.
Each domain must improve its own training-mean-vector baseline by ≥5%, empirical
90% interval coverage must be ≥85%, and ≥10% of its retained frames must pass
the runtime uncertainty gate. Retained test coverage must be ≥80% for MPII and
≥30% for Gaze360, whose original scope includes backward-facing heads.
These are prototype acceptance criteria, not promises of achieved quality.

## Commands

From the project root, in an environment with numpy/opencv/mediapipe:

```powershell
python -m training.public_gaze_prepare index --data data/datasets --output data/public-gaze
python -m training.public_gaze_prepare extract --output data/public-gaze --model models/face_landmarker.task --workers 8 --disable-audio
```

`--disable-audio` suppresses only the extractor worker's optional sounddevice
import, because Windows SSH sessions may have no PortAudio device. It does not
change the installed environment, audio drivers, or other processes.

With torch/torchvision/onnx/onnxruntime installed:

```powershell
python -m training.public_gaze_train --data data/public-gaze --output training/runs/public-gaze-v1 --device cuda:0 --batch 128 --workers 6 --epochs 40 --patience 8
```

Resume an interrupted run before final evaluation with `--resume` pointing to
its `last.pt` and the same output directory. Keep epochs/data unchanged.
An OS file lock rejects concurrent writers, and a fresh launch refuses existing
run artifacts. Resume verifies all immutable CLI arguments, data/source hashes,
and library/hardware contracts, then restores Python, NumPy, Torch, CUDA, and
sampler/DataLoader generators at the last completed epoch. Workers are recreated
with deterministic seeds each epoch, so their private augmentation RNG does not
escape the recovery contract. CUDA kernels may still be nondeterministic;
bitwise equivalence across hardware is not promised.

If export fails after successful evaluation, recover without touching the test:

```powershell
python -m training.public_gaze_train --output training/runs/public-gaze-v1 --export-only
```

This verifies that `best.pt`, the checkpoint lock, and the saved evaluation refer
to the same model. It reuses the original run settings and never opens the
dataset or calls evaluation. Recovery metadata records the export source hash.
`--limit` extraction and `--allow-smoke` training are functional tests only;
their weights fail the deployment gate. Use a separate directory for smoke.

## Runtime integration

`shared.public_gaze.PublicGazeEstimator(Path('gaze-public.onnx'))` loads the
adjacent JSON, verifies the model hash, release gate, schema, and CPU provider.
`estimate(frame_bgr, landmarks)` returns continuous vector/yaw/pitch/error90.
`set_reference(observations)` requires ≥15 stable, sufficiently certain samples
while the student explicitly looks at screen centre. A first arbitrary frame
is never silently treated as a screen reference.

`observe(frame, landmarks, mesh_features)` returns UNKNOWN without a reference,
on blinks, uncertain observations, or absent valid Face Mesh features. LEFT,
RIGHT, DOWN, UP are deviations relative to the fixed reference; CENTER only
means near the neutral direction. `offscreen_probability` is always null.
The caller must require exactly one face and use temporal event rules. The
separate MediaPipe head/iris path remains usable when appearance inference is
uncertain. Do not map CENTER to a proven SCREEN event or silently substitute
uncalibrated CNN predictions for head pose.

## Rights and delivery

Retain MPIIFaceGaze CC-BY-NC-SA-4.0 and Gaze360 LICENSE alongside the prepared
dataset. Gaze360 is restricted to noncommercial research; no public redistribution
of this trained model is authorized by this experiment. Keep research weights
private on the authorized training machine and research prototype. Public code
can describe the workflow without bundling dataset-derived model binaries.

Actual production metrics are in the completed run's `evaluation.json`, never
in this document ahead of real training. Smoke metrics are not accuracy claims.
