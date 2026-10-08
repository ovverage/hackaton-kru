"""Hardware-free check executed by the same frozen EXE distributed to students."""
import json
from pathlib import Path
import statistics
import tempfile
import time


def check_browser():
    """Exercise Chromium rendering and rejected redirects on loopback only."""
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication.instance() or QApplication([])
    loop = QEventLoop()
    view = QWebEngineView()
    observed = []

    def received(value):
        observed.append(value)
        loop.quit()

    def loaded(ok):
        if ok:
            view.page().runJavaScript(
                "document.getElementById('qorgau-check').textContent", received
            )
        else:
            loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    view.loadFinished.connect(loaded)
    view.setHtml('<!doctype html><p id="qorgau-check">Qorgau browser ready</p>')
    timer.start(20000)
    loop.exec()
    timer.stop()
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    if observed != ["Qorgau browser ready"]:
        raise RuntimeError("Packaged browser renderer did not load the offline test page")

    # Exercise the actual native callback and the controller's cancellation
    # behavior, not a mocked navigation method. Older releases fatally exited
    # when the attempt handler closed the view before the callback returned.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from .exam_browser import ExamBrowser

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                # Different hostname means a different origin, even on loopback.
                self.send_header('Location', f'http://localhost:{self.server.server_port}/forbidden')
                self.end_headers()
            else:
                body = (f'<!doctype html><p id="boundary-check">Qorgau origin ready</p>'
                        f'<iframe src="http://localhost:{self.server.server_port}/forbidden"></iframe>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True,
                               name='qorgau-selftest-loopback')
    serving.start()
    try:
        for _ in range(3):
            attempt_loop = QEventLoop()
            attempts, destroyed = [], []
            url = f'http://127.0.0.1:{server.server_port}'

            def rejected(reason):
                attempts.append(reason)
                browser.released = True
                browser.close()
                browser.deleteLater()

            browser = ExamBrowser(url + '/ready', rejected)
            browser.exam_page.destroyed.connect(lambda: destroyed.append('page'))
            browser.profile.destroyed.connect(lambda: destroyed.append('profile'))
            browser.destroyed.connect(attempt_loop.quit)
            navigated = False

            def inspected(value):
                if value == 'Qorgau origin ready':
                    browser.setUrl(QUrl(url + '/redirect'))
                else:
                    attempt_loop.quit()

            def ready(ok):
                nonlocal navigated
                if ok and not navigated:
                    navigated = True
                    browser.page().runJavaScript(
                        "document.getElementById('boundary-check').textContent", inspected)

            browser.loadFinished.connect(ready)
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(attempt_loop.quit)
            deadline.start(20000)
            attempt_loop.exec()
            deadline.stop()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            if attempts != ['BROWSER_ATTEMPT'] or destroyed != ['page', 'profile']:
                if not destroyed:
                    browser.released = True
                    browser.close()
                    browser.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                raise RuntimeError('Packaged browser did not safely reject and recover from an external redirect')
        if '/forbidden' in requests or requests.count('/redirect') != 3:
            raise RuntimeError('Packaged browser navigation origin boundary was not preserved')
        cancelled_attempts = []
        browser = ExamBrowser(url + '/ready', cancelled_attempts.append)
        browser.exam_page.report_attempt('BROWSER_ATTEMPT')
        browser.released = True
        browser.close()
        browser.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        if cancelled_attempts:
            raise RuntimeError('A cancelled browser delivered a stale navigation attempt')
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


def check_screen_calibration():
    """Exercise packaged profile geometry and rule wiring with known observations."""
    from shared.screen_gaze import FIT_TARGETS, VALIDATION_TARGETS, ScreenGazeCalibration
    from shared.rules import RuleEngine
    from .exam_ui import gaze_warning_active, gaze_warning_text
    from .screen_capture import TARGET_VISIBLE_SECONDS, TARGET_SETTLE_SECONDS, CAPTURE_SECONDS

    def observation(x, y, *, error=16.):
        return {
            'yaw_degrees': 7. + (x - .5) * -32.,
            'pitch_degrees': -3. + (y - .5) * -24.,
            'error90_degrees': error, 'gaze_tracking_status': 'tracked',
        }

    fit = {key: [observation(x, y) for _ in range(3)] for key, x, y in FIT_TARGETS}
    validation = {key: [observation(x, y) for _ in range(3)] for key, x, y in VALIDATION_TARGETS}
    assert len(fit) == 5 and len(validation) == 4 and not set(fit).intersection(validation)
    profile = ScreenGazeCalibration()
    result = profile.fit(fit, validation)
    assert result['ready'] and result['quality']['validation_max_error'] < 1e-10
    assert not result['quality']['statistical_accuracy_guarantee']
    assert profile.observe(observation(.5, .5))['screen_direction'] == 'SCREEN'
    assert profile.observe(observation(.95, .95))['screen_direction'] == 'SCREEN'
    boundary = profile.observe(observation(.05 - 6. / 32., .5))
    assert boundary['screen_direction'] == 'UNKNOWN' and boundary['screen_reason'] == 'boundary_margin'
    assert abs(boundary['screen_distance_degrees'] - 6.) < 1e-8

    def events_for(direction):
        engine = RuleEngine()
        engine.start()
        events = []
        for index in range(27):
            new_events = engine.observe(index * .2, direction=direction, faces=1)
            if index * .2 < 5:
                assert not any(event['type'].startswith('GAZE_') for event in new_events)
            events.extend(new_events)
        return engine, events

    for x, y, direction in ((-.3, .5, 'LEFT'), (1.3, .5, 'RIGHT'), (.5, 1.3, 'DOWN')):
        outside = profile.observe(observation(x, y))
        assert outside['screen_direction'] == direction and outside['screen_distance_degrees'] > 6
        engine, events = events_for(outside['screen_direction'])
        assert sum(event['type'] == 'GAZE_' + direction for event in events) == 1
        assert engine.state.counts()[direction] == 1

    upward = profile.observe(observation(.5, -.3))
    assert upward['screen_observed_direction'] == 'UP'
    engine, _ = events_for(upward['screen_direction'])
    assert not any(engine.state.counts().values())
    uncertain = profile.observe(observation(-.3, .5, error=30.))
    assert uncertain['screen_observed_direction'] == 'LEFT'
    assert uncertain['screen_direction'] == 'UNKNOWN' and uncertain['screen_reason'] == 'model_uncertain'
    engine, _ = events_for(uncertain['screen_direction'])
    assert not any(engine.state.counts().values())
    snapshot = dict(state=dict(lifecycle='RUNNING', access='OPEN'), camera=True,
        gaze_diagnostics=dict(source='public_gaze_model', reference_ready=True,
            gaze_tracking_status='tracked', direction=uncertain['screen_direction'],
            gaze_observed_direction=uncertain['screen_observed_direction'],
            gaze_observation_uncertain=True))
    assert gaze_warning_active(snapshot) and gaze_warning_text(snapshot) == 'Верните взгляд на монитор'
    assert TARGET_VISIBLE_SECONDS == 3 and TARGET_SETTLE_SECONDS + CAPTURE_SECONDS == 3
    from .calibration_diagnostics import compact_calibration_report, format_calibration_failure
    failure = {'ready': False, 'error': 'SCREEN_ANGULAR_SPAN_TOO_SMALL',
               'quality': {'yaw_span_degrees': 0., 'pitch_span_degrees': 0.},
               'frame': 'must not be retained'}
    assert 'frame' not in compact_calibration_report(failure)
    assert 'противоположные края' in format_calibration_failure(failure)
    assert check_target_retries()
    return {'adaptive_screen_calibration': True, 'screen_margin_degrees': 6,
            'screen_target_seconds': TARGET_VISIBLE_SECONDS,
            'screen_nominal_seconds': TARGET_VISIBLE_SECONDS * 9,
            'screen_settle_seconds': TARGET_SETTLE_SECONDS, 'screen_failure_diagnostics': True,
            'screen_automatic_target_retry': True,
            'screen_targets': 9, 'screen_fit_targets': 5, 'screen_validation_targets': 4,
            'screen_rule_timer_seconds': 5, 'screen_uncertain_display_without_strike': True,
            'screen_up_banner_only': True}


def check_target_retries():
    """Exercise packaged target replacement with a synthetic clock, without camera I/O."""
    from types import SimpleNamespace
    from threading import Event
    from . import screen_capture

    session = screen_capture.ScreenCaptureSession()
    clock = SimpleNamespace(at=100.)
    clock.monotonic = lambda: clock.at
    signature = ('synthetic-screen',)
    tokens = []

    class Camera:
        token = None
        point = (.5, .5)
        pending = None
        sequence = 0

        def begin_screen_calibration(self, display):
            assert display == signature

        def invalidate_screen_calibration(self, reason):
            raise AssertionError(reason)

        def target(self, token, phase, point, count, required):
            self.token, self.point = token, point
            tokens.append(token)
            self.pending = clock.at + screen_capture.TARGET_SETTLE_SECONDS

        def read(self):
            clock.at += .1
            self.sequence += 1
            if self.pending is not None and clock.at >= self.pending:
                self.pending = None
                session.presented(self.token, signature)

        def screen_calibration_sample(self):
            x, y = self.point
            noise = (10 if self.sequence % 2 else -10) if self.token == 1 else 0
            return dict(at=clock.at, frame_id=str(self.sequence), rotation=None,
                        gaze=dict(yaw_degrees=7 + (x - .5) * -32 + noise,
                                  pitch_degrees=-3 + (y - .5) * -24, error90_degrees=16))

        def install_screen_calibration(self, profile, center, rotations, display):
            return profile.ready and display == signature

    previous_clock = screen_capture.time
    screen_capture.time = clock
    try:
        camera = Camera()
        result = session.run(camera, signature, Event(), target=camera.target,
                             progress=lambda *args: None, read=camera.read)
        assert result['ready'] and result['capture']['retry_count'] == 1
        assert tokens == [0, 1, 10, 2, 3, 4, 5, 6, 7, 8]
        return True
    finally:
        screen_capture.time = previous_clock


def check_frame_transport():
    """Exercise the bundled latest-frame owner without opening a camera."""
    from queue import Queue
    import numpy as np
    from .latest_frame import LatestFrameSource

    class SyntheticCapture:
        def __init__(self):
            self.frames = Queue()
            self.released = 0

        def read(self):
            return self.frames.get(timeout=2)

        def release(self):
            self.released += 1

    capture = SyntheticCapture()
    source = LatestFrameSource(capture)
    try:
        capture.frames.put((True, np.zeros((12, 16, 3), np.uint8)))
        first = source.read(timeout=1)
        capture.frames.put((True, np.ones((12, 16, 3), np.uint8)))
        second = source.read(after_sequence=first.sequence, timeout=1)
        assert second.sequence > first.sequence and second.captured_at >= first.captured_at
        assert source.latest() is second and int(first.frame.sum()) == 0
        assert int(second.frame.sum()) == 12 * 16 * 3
    finally:
        source.close(timeout=0)
        capture.frames.put((False, None))
        assert source.close(timeout=1) and capture.released == 1
    return True



def run(output):
    from .install_guard import hold_installation_mutex
    import os

    installer_mutex = bool(hold_installation_mutex())
    if os.name == "nt" and not installer_mutex:
        raise RuntimeError("Installation mutex is unavailable")
    check_browser()
    import cv2
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from .resources import verified_assets
    from .detector import PhoneDetector, FaceDetector
    from shared.gaze_v2 import GazeClassifier
    from .recording import ClipRecorder
    assets = verified_assets()
    phone_path, face_path = assets[:2]
    detector = PhoneDetector(phone_path)
    face_detector = FaceDetector(phone_path.parent / 'face_yolov8n.onnx')
    gaze = GazeClassifier(phone_path.parent / 'gaze-direction.json')
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    times = []
    for _ in range(12):
        start = time.perf_counter()
        assert detector.detect(frame) == []
        times.append(time.perf_counter() - start)
    assert face_detector.detect(frame) == []
    assert gaze.observe(None)['direction'] == 'UNKNOWN'
    probabilities = gaze.probabilities([0.] * 33)
    assert len(probabilities) == 5 and abs(sum(probabilities)-1) < 1e-6
    public_gaze_check = None
    public_path = phone_path.parent / 'gaze-public.onnx'
    if public_path in assets:
        from shared.public_gaze import PublicGazeEstimator
        estimator = PublicGazeEstimator(public_path)
        tensor = np.zeros((1, 3, 224, 224), dtype=np.float32)
        output_values = estimator.session.run(['gaze'], {'face': tensor})[0]
        assert output_values.shape == (1, 4) and np.isfinite(output_values).all()
        assert abs(float(np.linalg.norm(output_values[0, :3])) - 1) < 1e-4
        assert estimator.reference is None
        assert estimator.observe(frame, [], None)['direction'] == 'UNKNOWN'
        from shared.head_pose import HeadPoseObserver
        from shared.gaze_feedback import public_gaze_feedback
        head = HeadPoseObserver()
        neutral = [0.] * 33
        neutral[12:21] = np.eye(3).ravel().tolist()
        assert head.set_reference([neutral] * 25)
        turned = list(neutral)
        angle = np.deg2rad(30)
        turned[12:21] = [np.cos(angle), 0., np.sin(angle), 0., 1., 0., -np.sin(angle), 0., np.cos(angle)]
        assert head.observe(turned)['head_direction'] == 'RIGHT'
        feedback = public_gaze_feedback(
            {'yaw_degrees': 25., 'pitch_degrees': 0., 'error90_degrees': 18., 'direction': 'UNKNOWN'},
            [0., 0., -1.],
        )
        assert feedback['gaze_observed_direction'] == 'LEFT' and feedback['gaze_observation_uncertain']
        for axis, expected in (('yaw_degrees', 'LEFT'), ('pitch_degrees', 'UP')):
            sample = {'yaw_degrees': 0., 'pitch_degrees': 0., 'error90_degrees': 18., 'direction': 'UNKNOWN'}
            sample[axis] = 9.
            feedback = public_gaze_feedback(sample, [0., 0., -1.])
            assert feedback['gaze_observed_direction'] == expected
            assert feedback['gaze_display_threshold_degrees'] == 9
            assert sample['direction'] == 'UNKNOWN'
        # Direct matrices work when the irises/legacy feature vector are absent.
        head.clear()
        assert head.set_reference_rotations([np.eye(4)] * 25)
        angle = np.deg2rad(75)
        profile = np.eye(4)
        profile[:3, :3] = [[np.cos(angle), 0., np.sin(angle)], [0., 1., 0.], [-np.sin(angle), 0., np.cos(angle)]]
        observation = head.observe_rotation(profile)
        assert observation['head_direction'] == 'RIGHT' and observation['head_extreme']
        lost = head.observe_rotation(None)
        assert lost['head_tracking_status'] == 'unavailable' and lost['head_yaw'] is None
        from shared.gaze_display_memory import GazeDisplayMemory
        display = GazeDisplayMemory()
        display.update(feedback, at=0., blink=False, single_face=True, reference_ready=True)
        held = display.update({'gaze_observed_direction': 'UNKNOWN'}, at=.1, blink=True,
                              single_face=True, reference_ready=True)
        assert held['gaze_display_stale'] and held['gaze_feedback_reason'] == 'blink_hold'
        # Exercise the live demo policy with a controlled sensor output. This
        # validates the decision wiring, not real-image angular accuracy.
        from unittest.mock import patch
        from shared.rules import RuleEngine
        from .desktop import public_gaze_status_text
        estimator.reference = [0., 0., -1.]
        estimator.reference_error = 16.
        sample = {'vector': [0., 0., -1.], 'yaw_degrees': 25., 'pitch_degrees': 0.,
                  'error90_degrees': 16., 'source': 'public_gaze_model'}
        with patch.object(estimator, 'estimate', return_value=sample):
            assert estimator.observe(frame, [], eye_blink=[0., 0.])['direction'] == 'UNKNOWN'
            policy = estimator.observe(frame, [], eye_blink=[0., 0.], demo_sensitivity=True)
        assert policy['direction'] == 'LEFT' and policy['gaze_decision_threshold_degrees'] == 9
        engine = RuleEngine()
        engine.start()
        events = []
        for index in range(27):
            events.extend(engine.observe(index * .2, direction=policy['direction'], faces=1))
        assert sum(event.get('type') == 'GAZE_LEFT' for event in events) == 1
        policy.update(public_gaze_feedback(policy, estimator.reference))
        ui = public_gaze_status_text(policy, active=True, gaze_seconds=2.)
        assert '25.0°' in ui and '2.0 / 5' in ui
        from .exam_ui import gaze_warning_active, gaze_warning_text
        for direction in ('LEFT', 'RIGHT', 'UP', 'DOWN'):
            warning_snapshot = dict(state=dict(lifecycle='RUNNING', access='OPEN'), camera=True,
                gaze_diagnostics=dict(source='public_gaze_model', reference_ready=True,
                    gaze_tracking_status='tracked', direction='UNKNOWN',
                    gaze_observed_direction=direction, gaze_observation_uncertain=True))
            assert gaze_warning_active(warning_snapshot)
            banner_text = gaze_warning_text(warning_snapshot)
            assert banner_text == 'Верните взгляд на монитор'
        public_gaze_check = {'onnx_inference': True, 'explicit_reference_required': True,
                             'separate_head_pose': True, 'uncertain_gaze_feedback': True,
                             'display_threshold_degrees': 9, 'independent_head_matrix': True,
                             'blink_display_continuity': True,
                             'demo_sensitivity_timer': True, 'numeric_angles_ui': True,
                             'uncertain_direction_banners': True,
                             'kind': 'synthetic packaged CPU smoke; no physical calibration or accuracy claim'}
        public_gaze_check.update(check_screen_calibration())
    with vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=str(face_path)), num_faces=2,
    )) as face:
        result = face.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=frame))
        assert not result.face_landmarks
    with tempfile.TemporaryDirectory(prefix="qorgau-selftest-") as folder:
        recorder = ClipRecorder(Path(folder))
        try:
            for i in range(21):
                recorder.push(i / 10, frame)
                if i == 10:
                    recorder.mark({"id": "selftest", "at": 1})
            clip = recorder.completed(float("inf"))[0]
            capture = cv2.VideoCapture(clip["path"])
            ok, decoded = capture.read()
            capture.release()
            assert ok and decoded.shape == frame.shape
        finally:
            recorder.close()
    from shared.version import APP_VERSION, MODEL_VERSION
    import hashlib
    import platform
    import sys
    report = {"result": "PASS", "status": "passed", "version": APP_VERSION, "platform": platform.platform(), "frozen": bool(getattr(sys, "frozen", False)), "browser_renderer": True, "browser_navigation_recovery": True, "kind": "synthetic packaging test; no webcam or accuracy claim",
              "installer_mutex": installer_mutex, "models_verified": True, "onnx_inference": True, "face_landmarker": True,
              "yolo_face_inference": True, "gaze_forest": True, "model_version": MODEL_VERSION,
              "model_files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in assets},
              "h264_encode_decode": True, "latest_frame_capture": check_frame_transport(),
              "phone_cpu_median_ms": round(statistics.median(times[2:]) * 1000, 2)}
    from .resources import resource_root
    from .profile import read_object
    from shared.teacher_faces import TeacherFaceEngine
    identity = TeacherFaceEngine(resource_root() / 'models/teacher-faces')
    assert identity.detect(frame) == []
    report['teacher_face_model_files'] = {
        entry['file']: entry['sha256'] for entry in
        json.loads((resource_root() / 'teacher-face-manifest.json').read_text(encoding='utf-8'))['files']}
    report['mode'] = read_object(resource_root() / 'build-profile.json').get('mode', 'online')
    report['teacher_face_runtime'] = True
    report.update(check_presentation())
    if public_gaze_check:
        report['public_gaze'] = public_gaze_check
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def check_presentation():
    """Verify packaged language text and UI-only warning timing without devices."""
    from PySide6.QtWidgets import QApplication
    from .i18n import language, set_language, tr
    from .localized_widgets import QLabel
    from .exam_ui import WarningDisplayHold
    app = QApplication.instance() or QApplication([])
    previous = language()
    label = QLabel('Верните взгляд на монитор')
    expected = {'ru': 'Верните взгляд на монитор', 'en': 'Return your gaze to the monitor',
                'kk': 'Көзіңізді мониторға қайтарыңыз'}
    try:
        for code, message in expected.items():
            set_language(code, persist=False)
            assert label.text() == message
            assert tr('Ctrl+Alt+Q · 12.5° · 2026-10-08') == 'Ctrl+Alt+Q · 12.5° · 2026-10-08'
        snap = dict(state=dict(lifecycle='RUNNING', access='OPEN'), camera=True,
                    gaze_diagnostics=dict(source='legacy', reference_ready=True, attention_away=True))
        hold = WarningDisplayHold()
        assert hold.update(snap, 10)
        snap['gaze_diagnostics']['attention_away'] = False
        assert hold.update(snap, 10.99)
        assert not hold.update(snap, 11.01)
    finally:
        set_language(previous, persist=False)
        label.deleteLater()
        app.processEvents()
    return {'ui_languages': ['ru', 'kk', 'en'], 'attention_warning_hold_seconds': 1.0,
            **check_warning_animation()}


def check_warning_animation():
    """Check real banner animation and, on Windows, its native stacking order.

    The synthetic exam is our own small, non-activating window. This never
    starts the camera, installs hooks, or changes another application's window.
    Animation time is advanced explicitly, without waiting for hardware or time.
    """
    import os
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication, QWidget
    from .exam_ui import GazeWarning

    app = QApplication.instance() or QApplication([])
    native = os.name == 'nt' and app.platformName().casefold() == 'windows'
    warning = GazeWarning()
    exam, guard = None, None

    def stack_and_check():
        if not native:
            return
        handles = [int(warning.winId())] if warning.isVisible() else []
        guard.stack_exam_below(handles)
        assert guard.u.GetWindowLongW(guard.target.hwnd, -20) & 0x8
        if handles:
            # Walk upwards from our exam window. A visible/fading warning must
            # occur above it even after repeated protection timer operations.
            cursor = guard.target.hwnd
            seen = set()
            while cursor and cursor not in seen:
                seen.add(cursor)
                cursor = guard.u.GetWindow(cursor, 3)  # GW_HWNDPREV
                if cursor == handles[0]:
                    break
            else:
                raise AssertionError('The exam covers its visible attention warning')

    try:
        if native:
            from ctypes import wintypes as W
            from types import SimpleNamespace
            from .windows_guard import WindowsGuard
            exam = QWidget()
            exam.setWindowTitle('Qorgau packaged warning check')
            exam.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowDoesNotAcceptFocus
                                | Qt.WindowType.WindowTransparentForInput)
            exam.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            exam.setGeometry(8, 8, 240, 90)
            exam.show()
            guard = WindowsGuard()
            guard.target = SimpleNamespace(hwnd=int(exam.winId()))
            guard.u.GetWindow.argtypes = [W.HWND, W.UINT]
            guard.u.GetWindow.restype = W.HWND
            stack_and_check()

        duration = warning.fade.duration()
        assert duration == 300
        warning.setGeometry(16, 16, 660, 130)
        warning.set_warning_visible(True)
        warning.fade.pause()
        warning.fade.setCurrentTime(duration // 2)
        assert warning.isVisible() and .35 < warning.windowOpacity() < .65
        stack_and_check()
        # Identical detector updates must not restart the fade from zero.
        halfway = warning.windowOpacity()
        warning.set_warning_visible(True)
        assert abs(warning.windowOpacity() - halfway) < .01
        warning.fade.resume()
        warning.fade.setCurrentTime(duration)
        assert warning.isVisible() and warning.windowOpacity() > .99

        warning.set_warning_visible(False)
        warning.fade.pause()
        for elapsed in (100, 150, 200):
            warning.fade.setCurrentTime(elapsed)
            assert warning.isVisible() and 0 < warning.windowOpacity() < 1
            stack_and_check()
        # A new observation during fade-out resumes at the current opacity.
        partial = warning.windowOpacity()
        warning.set_warning_visible(True)
        warning.fade.pause()
        assert warning.isVisible() and abs(warning.windowOpacity() - partial) < .01
        stack_and_check()
        warning.fade.resume()
        warning.fade.setCurrentTime(duration)
        warning.set_warning_visible(False)
        warning.fade.setCurrentTime(duration)
        assert not warning.isVisible()
        stack_and_check()

        warning.set_warning_visible(True)
        warning.set_warning_visible(False, immediate=True)
        assert not warning.isVisible() and warning.windowOpacity() == 0
        return {'attention_warning_fade_ms': duration,
                'attention_warning_native_stacking': native}
    finally:
        warning.hide()
        warning.close()
        warning.deleteLater()
        if exam is not None:
            exam.close()
            exam.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
