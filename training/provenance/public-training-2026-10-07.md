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
