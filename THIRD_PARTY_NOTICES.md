# Third-party components

Qorgau uses third-party software and pretrained assets. The project does not claim to have trained COCO YOLO or MediaPipe from scratch.

| Component | Upstream / license information | Use |
|---|---|---|
| Ultralytics / YOLO11n COCO | https://www.ultralytics.com/license | Local phone detection; auxiliary training experiments. AGPL-3.0 or an applicable commercial license must be considered for distribution and deployment. |
| MediaPipe / Face Landmarker | https://github.com/google-ai-edge/mediapipe ; https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker | Local face landmarks and calibrated gaze features; consult upstream code and model notices. |
| PySide6 / Qt | https://doc.qt.io/qtforpython-6/licenses.html | Desktop UI and QtWebEngine; LGPL/GPL/commercial options and third-party Chromium notices apply. Shared libraries remain separate in the portable folder. |
| PyTorch | https://github.com/pytorch/pytorch/blob/main/LICENSE | CPU model inference and training. |
| imageio-ffmpeg / bundled FFmpeg | https://github.com/imageio/imageio-ffmpeg ; https://ffmpeg.org/legal.html | H.264 evidence clips; the wrapper and the executable have separate licenses, including the encoder build configuration. |
| Public behavior dataset | https://zenodo.org/records/14606173 | Auxiliary behavior experiment; the dataset's terms are separate from the software. Raw dataset media is not included in the release. |

The portable build includes package license files when collected by the packaging hooks. This notice is an inventory, not a replacement for upstream licenses. The team must settle the project's distribution license and provide any required corresponding source/notices before external product distribution. The hackathon repository/release retains the repository's existing access level.
