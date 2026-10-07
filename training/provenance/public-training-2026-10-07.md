# Research training on the four acquired datasets

This run uses the complete verified source datasets acquired on 2026-10-07,
not the earlier small private collection. Source URLs, revisions, licenses,
archive hashes and extracted counts are retained in `data/datasets` locally.
Raw images, credentials and weights are excluded from source control.

| Model | Training data | Validation and final test | Budget |
| --- | --- | --- | --- |
| YOLO11n face | WIDER FACE train, valid annotation policy | Event-disjoint internal dev; original val reserved for final audit | Up to 100 epochs; patience 20 |
| YOLO11n phone + person | COCO train2017, including negative images | Fixed internal dev; all original val2017 held for final audit | Up to 80 epochs; patience 20 |
| MobileNetV3-small gaze + MediaPipe | MPIIFaceGaze and Gaze360 | MPII participant holdouts and original Gaze360 recording splits | Up to 40 epochs; patience 8 |

Both CNN architectures use declared transfer initialization. This is actual
fine-tuning, not random initialization and not simply renaming pretrained weights.
The YOLO initializer has already seen COCO train images, including internal dev;
the final official validation set remains the independent image audit. Public
image benchmarks do not establish webcam event accuracy or cheating intent.

Detailed preregistered protocols and rejection criteria:
[detection](detection-public-protocol.md), [gaze](gaze-public-v1.md).
Selection and uncertainty calibration use development/validation only. A final
test result cannot be reused to select the next checkpoint. Failed models remain
research artifacts and are not automatically promoted into the application.

## Durable execution

The isolated training directory uses a verified CUDA environment, one GPU
training process at a time, and up to two CPU preparation processes. Other
server processes are left running. On the audited host, approximately 20 GiB
of GPU memory were available; detection batch is 16 and gaze batch is 128.

```powershell
python -m training.public_pipeline --root <run-root> --weights <verified-yolo11n.pt> --landmarker <verified-face_landmarker.task>
```

Run it through a persistent process supervisor on the host. An SSH-attached
Windows child process alone may be terminated when its channel closes.
`public-training-status.json` records stage PIDs, commands, timestamps and
exit codes. Logs are in `logs/public-training`. Dataset readiness unlocks each
stage; training does not run against a partial extraction. The operator must
inspect failed runs and use the documented explicit recovery commands.

### Actual launch record

The 2026-10-07 run uses source commit
`d16a140a53a7c3e2e7a055c6cc7744a69132c20a`, root
`C:\QorgauTraining\20261007`, and the existing verified CUDA interpreter at
`C:\QorgauTraining\20261006\.venv\Scripts\python.exe`.
[Launch evidence](../reports/public-training-launch-2026-10-07.json) records
the remote journal, live development epoch, saved checkpoints, versions and
initialization hashes. It is a timestamped progress snapshot, not final scores.

COCO and WIDER use the same pinned archives as the local acquisition.
The already verified local MPIIFaceGaze/Gaze360 raw files were transferred in
a 4,903,883,864-byte ZIP; remote SHA-256 matched
`2697cc6e4e085b1c6fae218fffc8a0c84453f22971bda385c1989acda18bb7c5`.
Per-file CRC and annotation checks gate readiness on the training host.
`gaze-transfer-status.json` tracks this import and `acquisition-handoff.json`
consolidates readiness. The original Gaze360 archive-download process was
intentionally stopped after switching to this verified copy; its historical
exit code does not describe the replacement import. The temporary transfer
service was closed after the remote archive hash passed.

On the training host, `collect_training_status.py` writes a current read-only
snapshot to `operator-status.json`. Use `public-training-status.json` and
`logs/public-training` for stage status. These processes survive SSH disconnect;
an OS restart requires explicit recovery. Do not erase the journal or start a
second queue over an active run. No later deployment of trained weights is
scheduled by this queue.

## Deployment boundary

The application release 0.4.7 updates browser policy, camera preparation and
the control room. It keeps model manifest `2026.10.06.2` until new candidates
finish their separate image and application evaluations. No new accuracy
claim is inferred from synthetic unit/smoke tests.

Gaze360's retained research license restricts distribution and commercial use,
including trained models. MPIIFaceGaze and WIDER also carry research/noncommercial
conditions. This run does not authorize a public weights release. Keep the
joint gaze model in the private research prototype unless separate rights are
established; publishing a source training pipeline does not publish its weights.

The TЗ mapping and unresolved observable limits are in
[TZ-compliance.md](../../docs/TZ-compliance.md). A raised/held phone is a review
signal, not proof that its camera is aimed at the screen or that a photo was taken.
