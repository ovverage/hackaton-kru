"""Bounded camera-thread sampling with a GUI-painted target acknowledgement."""
from __future__ import annotations

import copy
from collections import deque
import math
from queue import Empty, Queue
import time

from shared.screen_gaze import FIT_TARGETS, VALIDATION_TARGETS, ScreenGazeCalibration

TARGET_VISIBLE_SECONDS = 3.0
TARGET_SETTLE_SECONDS = .8
# The target has already been painted for the settling interval when its GUI
# acknowledgement arrives. The fixed settling window excludes transition frames
# without selecting samples according to their fit. A full target exposure is
# three seconds, even if the minimum sample count was reached immediately.
CAPTURE_SECONDS = TARGET_VISIBLE_SECONDS - TARGET_SETTLE_SECONDS
MAX_CAPTURE_SECONDS = CAPTURE_SECONDS
PRESENTATION_TIMEOUT_SECONDS = 4.0
# Retained for diagnostic callers; automatic target retries have no total limit.
# Presentation and camera reads remain bounded, and Esc cancels every loop.
SESSION_TIMEOUT_SECONDS = 65.0
MIN_SAMPLE_GAP_SECONDS = .045
PROGRESS_INTERVAL_SECONDS = .25


class ScreenCaptureSession:
    def __init__(self):
        self.starts = Queue()
        self.presentations = Queue()

    def start(self, signature):
        self.starts.put(copy.deepcopy(signature))

    def presented(self, index, signature):
        self.presentations.put((index, copy.deepcopy(signature), time.monotonic()))

    def next_start(self):
        try:
            return self.starts.get_nowait()
        except Empty:
            return None

    def run(self, camera, signature, stop, *, target, progress, read):
        """`read` captures/analyses a fresh frame and may publish a preview.

        Callbacks only emit queued Qt signals. The camera thread never reads
        window geometry or paints a target. `read` may return the current analysis
        dictionary for live tracking feedback. No image/sample is persisted.
        """
        camera.begin_screen_calibration(signature)
        begun = time.monotonic()
        last_sample_at = None
        fit, validation, rotations = {}, {}, []
        reference_rotation = None
        sequence = [('fit', row) for row in FIT_TARGETS] + [('validation', row) for row in VALIDATION_TARGETS]
        capture = dict(target_count=len(sequence), completed_targets=0,
                       target_visible_seconds=TARGET_VISIBLE_SECONDS,
                       settle_seconds=TARGET_SETTLE_SECONDS, retry_count=0, targets={})
        current_key = None
        pending = deque(range(len(sequence)))
        checker = ScreenGazeCalibration()
        last_quality = {}
        retry_messages = {}

        def finish(result):
            return dict(result, elapsed_seconds=time.monotonic() - begun, capture=capture)

        def fail(reason, result=None):
            camera.invalidate_screen_calibration(reason)
            result = dict(result or {}, ready=False, error=reason)
            result.setdefault('quality', last_quality)
            if current_key:
                capture['failed_target'] = current_key
            return finish(result)

        def repeat(index, reason):
            key = sequence[index][1][0]
            capture['retry_count'] += 1
            capture['targets'][key]['last_error'] = reason
            retry_messages[index] = reason
            pending.appendleft(index)

        while pending:
            if stop.is_set():
                return fail('SCREEN_CALIBRATION_CANCELLED')
            index = pending.popleft()
            phase, (key, x, y) = sequence[index]
            current_key = key
            previous = capture['targets'].get(key, {})
            stats = capture['targets'][key] = dict(
                accepted_samples=0, total_frames=previous.get('total_frames', 0),
                rejected_samples=previous.get('rejected_samples', 0),
                elapsed_seconds=previous.get('elapsed_seconds', 0.),
                rejections=dict(previous.get('rejections', {})), attempts=previous.get('attempts', 0) + 1,
                last_error=previous.get('last_error'))
            # Tokens distinguish a new exposure of the SAME point from an old
            # queued acknowledgement. GUI decodes token % target_count for display.
            presentation = index + len(sequence) * (stats['attempts'] - 1)
            seen = set()  # Bounded by one three-second exposure, even after hours of retries.
            while not self.presentations.empty():
                try:
                    self.presentations.get_nowait()
                except Empty:
                    break
            requested_at = time.monotonic()
            target(presentation, phase, (x, y), 0, 3)
            retry_hint = ''
            if index in retry_messages:
                from .calibration_diagnostics import format_target_retry
                retry_hint = format_target_retry(dict(
                    ready=False, error=retry_messages.pop(index), quality={'failed_target': key}))
            acknowledged_at = None
            while acknowledged_at is None:
                if stop.is_set():
                    return fail('SCREEN_CALIBRATION_CANCELLED')
                if time.monotonic() - requested_at > PRESENTATION_TIMEOUT_SECONDS:
                    return fail('SCREEN_TARGET_NOT_PRESENTED')
                read()
                try:
                    current, display, presented_at = self.presentations.get_nowait()
                except Empty:
                    continue
                if display != signature:
                    return fail('SCREEN_CHANGED')
                if current == presentation and presented_at >= requested_at:
                    acknowledged_at = presented_at
            samples = []
            point_rotations = []
            head_moved = False
            last_progress_at = None
            while time.monotonic() - acknowledged_at < MAX_CAPTURE_SECONDS:
                if stop.is_set():
                    return fail('SCREEN_CALIBRATION_CANCELLED')
                observation = read()
                stats['total_frames'] += 1
                reason = 'no_gaze'
                sample = camera.screen_calibration_sample()
                if sample is not None:
                    reason = 'stale_frame'
                    at, frame_id = sample.get('at'), sample.get('frame_id')
                    fresh = (isinstance(at, (int, float)) and math.isfinite(at)
                             and acknowledged_at <= at <= time.monotonic() + .05
                             and frame_id and frame_id not in seen
                             and (last_sample_at is None or at - last_sample_at >= MIN_SAMPLE_GAP_SECONDS))
                    if fresh and reference_rotation is not None:
                        from shared.head_pose import rotation_distance, rotation_from_matrix
                        current_rotation = rotation_from_matrix(sample.get('rotation'))
                        if current_rotation is not None and rotation_distance(reference_rotation, current_rotation) > 12:
                            head_moved = True
                            fresh = False
                            reason = 'head_moved'
                    if fresh:
                        seen.add(frame_id)
                        last_sample_at = at
                        samples.append(sample['gaze'])
                        reason = None
                        if key == 'fit_center':
                            point_rotations.append(sample.get('rotation'))
                if reason is not None:
                    if reason == 'no_gaze' and isinstance(observation, dict):
                        diagnostics = observation.get('gaze_diagnostics') or {}
                        tracking = diagnostics.get('gaze_tracking_status')
                        if tracking in {'blink', 'eye_state_missing', 'face_landmarks_missing',
                                        'multiple_faces', 'invalid_crop', 'model_uncertain'}:
                            reason = tracking
                        elif isinstance(diagnostics.get('error90_degrees'), (int, float)):
                            if diagnostics['error90_degrees'] > 20:
                                reason = 'model_uncertain'
                    stats['rejected_samples'] += 1
                    stats['rejections'][reason] = stats['rejections'].get(reason, 0) + 1
                now = time.monotonic()
                stats['accepted_samples'] = len(samples)
                stats['elapsed_seconds'] = previous.get('elapsed_seconds', 0.) + now - requested_at
                if last_progress_at is None or now - last_progress_at >= PROGRESS_INTERVAL_SECONDS:
                    messages = {
                        'blink': 'Камера передаёт кадры; ждём открытых глаз.',
                        'eye_state_missing': 'Камера передаёт кадры; глаза пока не отслеживаются.',
                        'face_landmarks_missing': 'Камера передаёт кадры; лицо пока не отслеживается.',
                        'multiple_faces': 'В кадре должно быть одно лицо.',
                        'model_uncertain': 'Камера передаёт кадры; оценка взгляда пока неуверенная.',
                        'head_moved': 'Положение головы изменилось; верните её в исходное положение.',
                        'stale_frame': 'Ждём нового кадра взгляда.',
                    }
                    message = ('Трекинг взгляда работает. Следите за точкой.' if reason is None else
                               messages.get(reason, 'Камера передаёт кадры; ждём пригодной оценки взгляда.'))
                    progress(len(samples), 3, (retry_hint + ' ' if retry_hint else '') + message)
                    last_progress_at = now
                if len(samples) >= 3 and now - acknowledged_at >= CAPTURE_SECONDS:
                    break
            if len(samples) < 3:
                repeat(index, 'SCREEN_HEAD_MOVED' if head_moved else 'SCREEN_TOO_FEW_FRESH_SAMPLES')
                continue
            point_quality = checker.check_target(samples)
            stats['spread_degrees'] = point_quality['quality']['sample_spread_degrees']
            if not point_quality['ready']:
                repeat(index, point_quality['error'])
                continue
            if key == 'fit_center':
                from shared.head_pose import HeadPoseObserver, rotation_from_matrix
                centre_head = HeadPoseObserver()
                available = [parsed for r in point_rotations if (parsed := rotation_from_matrix(r)) is not None]
                if centre_head.set_reference_rotations(available, required_samples=max(3, len(available))):
                    if 'fit_center' not in fit:
                        reference_rotation = centre_head.reference
                elif centre_head.reference_error == 'HEAD_REFERENCE_UNSTABLE':
                    repeat(index, 'SCREEN_HEAD_MOVED')
                    continue
                if 'fit_center' not in fit:
                    # Head orientation is independent of the retried eye ray.
                    # Keep the reference used to admit ALL saved target windows;
                    # a later centre retry must not rebase or erase that reference.
                    rotations = point_rotations
            # Replace the complete exposure, never mix retries or retain a best
            # angle. Other accepted point windows remain untouched in memory.
            (fit if phase == 'fit' else validation)[key] = samples
            capture['completed_targets'] = len(fit) + len(validation)
            if pending:
                continue
            if stop.is_set():
                return fail('SCREEN_CALIBRATION_CANCELLED')
            profile = ScreenGazeCalibration()
            result = profile.fit(fit, validation)
            # These are adaptive personal setup checks, not an independent
            # statistical evaluation once failed targets are remeasured.
            profile.quality['adaptive_target_retries'] = capture['retry_count']
            result = profile.report()
            last_quality = result['quality']
            if not result['ready']:
                failed = result['quality'].get('failed_target')
                retry_index = next((i for i, (_, row) in enumerate(sequence) if row[0] == failed), None)
                if retry_index is not None and result['error'] != 'SCREEN_FIT_ERROR_TOO_HIGH':
                    repeat(retry_index, result['error'])
                    continue
                # A globally collapsed/contradictory mapping does not identify
                # one bad target. A largest affine-fit residual also need not
                # identify the causal bad input. Do not loop on that guess.
                current_key = None
                return fail(result['error'], result)
            if not camera.install_screen_calibration(profile, fit['fit_center'], rotations, signature):
                reason = camera.screen_calibration_progress.get('error') or 'SCREEN_CALIBRATION_FAILED'
                return fail(reason, result)
            return finish(result)
        return fail('SCREEN_CALIBRATION_FAILED')
