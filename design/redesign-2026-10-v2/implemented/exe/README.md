# Qt v2 visual verification

Reproduce from the repository root:

```sh
QT_QPA_PLATFORM=offscreen python scripts/capture_redesign_qt.py
```

The script creates temporary agent state and uses the supplied design SVG illustrations as camera fixtures. It disables camera startup, enrollment workers and browser navigation; it does not contact the server. It captures actual QWidget instances with `grab()`. Geologica and the Fusion style are loaded. All 17 PNGs were inspected after rendering.

| Screen | Capture |
| --- | --- |
| First connection and retry | `exe-01-connect.png`, `exe-01-connect-retry.png` |
| Preparation, online and local password setup | `exe-02-prep.png`, `exe-02-prep-local.png` |
| Camera and gaze setup | `exe-03-camera.png`, `exe-03-gaze-setup.png` |
| Program window, browser tab and extension binding | `exe-04-window.png`, `exe-04-browser-tabs.png`, `exe-04-extension.png` |
| Tray menu | `exe-05-tray.png` |
| Browser teacher button, gaze countdown and head warning | `exe-06-browser-teacher-button.png`, `exe-06-gaze.png`, `exe-06-head-warning.png` |
| Pause with face/password controls, online and local | `exe-07-pause.png`, `exe-07-pause-local.png` |
| Screen calibration panel and target | `exe-08-screen-calibration.png`, `exe-08-screen-target.png` |

Both pause captures are exactly 1366×768; their body scrollbar maximum is zero. Preparation captures are taller than the HTML reference because the existing gaze diagnostics must retain their reserved height, and the local screen includes password setup and browser-tab controls. Smaller preparation windows retain the existing scrollable layout; the 480×400 / 560×450 diagnostic visibility tests pass.

The existing public gaze model can report uncertain directions and independent head warnings. Those tested explanatory messages remain intact. A countdown appears only while a confirmed, active gaze timer is below the existing five-second rule threshold; it never promises a new mark after one has already been counted. `shared/rules.py` exposes this threshold as a literal rather than an importable constant, so the UI documents the same fixed display value without changing the rules.

Pause times are shown only when the triggering event has `created_at`. Missing times are hidden. Evidence displays existing annotated thumbnails; no detection confidence or coordinates are fabricated. Preparation omits a camera thumbnail because there is no already-available preview frame there.

The offscreen platform supplies an 800×800 virtual display for fullscreen calibration. Its target positions, radii and timing are unchanged. QWebEngine cannot reliably include its floating control in an offscreen view capture on this host, so the actual teacher button is captured separately; its drop shadow is removed only in the capture fixture to permit a direct widget grab. This does not replace a Windows end-to-end exam/guard check.

Validation: the targeted desktop/camera/calibration/lifecycle/gaze suite passed **172 tests**. After the final caption/countdown text polish, the affected student, exam UI and gaze tests passed **52 tests**. Ruff `E4,E7,E9,F` and `git diff --check` pass for the changed Qt code and capture script.
