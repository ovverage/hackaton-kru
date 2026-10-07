"""Local detectors with legacy gaze or explicitly referenced private CNN gaze."""

from __future__ import annotations
from pathlib import Path
from contextlib import nullcontext
import threading
import time
from shared.rules import PHONE_CONFIDENCE_THRESHOLD


class Camera:
    def __init__(self, index, phone_model: Path, face_model: Path, *, calibrate=False):
        import cv2
        import numpy as np
        import mediapipe as mp
        from .detector import PhoneDetector, FaceDetector
        from shared.gaze_v2 import GazeClassifier
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        for path in (phone_model, face_model):
            if not path.is_file():
                raise ValueError(f"Локальная модель не найдена: {path}")
        self.cv2, self.np, self.mp = cv2, np, mp
        self.phone = PhoneDetector(phone_model)
        self.face_detector = FaceDetector(phone_model.parent / 'face_yolov8n.onnx')
        self.gaze = GazeClassifier(phone_model.parent / 'gaze-direction.json')
        self.gaze_enabled = True
        self.gaze_vector = None
        self.gaze_landmarks = None
        self.gaze_blinks = None
        self.head_rotation = None
        self.public_gaze = None
        public_model = phone_model.parent / 'gaze-public.onnx'
        if public_model.is_file():
            from shared.public_gaze import PublicGazeEstimator
            self.public_gaze = PublicGazeEstimator(public_model)
            self.gaze_enabled = False
        # A live CNN camera never falls back to a fixed centre-only threshold
        # after an adaptive screen calibration fails or becomes invalid.
        self.screen_calibration_required = self.public_gaze is not None
        self.screen_calibration = None
        self.screen_calibration_signature = None
        self._screen_calibration_collecting = False
        self._screen_calibration_error = None
        self._screen_calibration_sample = None
        self._screen_calibration_last_digest = None
        self._screen_invalidation_requested = None
        self._gaze_reference_samples = []
        self._gaze_reference_collecting = False
        self._gaze_reference_error = None
        self._gaze_reference_last_at = None
        self._gaze_reference_last_digest = None
        from shared.head_pose import HeadPoseObserver
        self.head_pose = HeadPoseObserver()
        self._head_reference_samples = []
        self.face = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(face_model)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=2,
                min_face_detection_confidence=0.5,
                min_face_presence_confidence=0.5,
                min_tracking_confidence=0.5,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=True,
            )
        )
        self.capture = cv2.VideoCapture(index)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        self.capture.set(cv2.CAP_PROP_FPS, 15)
        # Best effort only: backend support varies. The producer below is the
        # actual protection against queued driver frames during slow inference.
        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.capture.isOpened():
            self.capture.release()
            self.face.close()
            raise OSError("Камера не открылась")
        self.timestamp = 0
        self.centres = {}
        self.frame_digest = None
        self.frame_changed_at = time.monotonic()
        self.last_frame = 0
        from .behavior import PhoneRaising
        self.raising = PhoneRaising()
        self.recognition_was_paused = False
        self._read_lock = threading.RLock()
        self._closing = False
        self._face_closed = False
        self._capture_sequence = 0
        self.last_captured_at = None
        from .latest_frame import LatestFrameSource
        self.frame_source = LatestFrameSource(self.capture)
        if calibrate:
            self.calibrate()

    def face_features(self, frame, timestamp_ms=None):
        self.timestamp = max(self.timestamp + 1, int(time.monotonic() * 1000) if timestamp_ms is None else timestamp_ms)
        rgb = self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2RGB)
        result = self.face.detect_for_video(
            self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb),
            self.timestamp,
        )
        faces = result.face_landmarks
        self.gaze_vector = None
        self.gaze_landmarks = None
        self.gaze_blinks = None
        self.head_rotation = None
        if len(faces) != 1:
            return len(faces), None
        if len(faces[0]) >= 468:
            self.gaze_landmarks = faces[0]
        from shared.head_pose import rotation_from_matrix
        if result.facial_transformation_matrixes:
            self.head_rotation = rotation_from_matrix(result.facial_transformation_matrixes[0])
        blends = {b.category_name: float(b.score) for b in result.face_blendshapes[0]} if result.face_blendshapes else {}
        import math
        blinks = [blends.get('eyeBlinkLeft'), blends.get('eyeBlinkRight')]
        if all(isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1 for value in blinks):
            self.gaze_blinks = blinks
        if len(faces[0]) < 478 or not result.facial_transformation_matrixes or not result.face_blendshapes:
            return len(faces), None
        from shared.gaze_v2 import extract_features
        self.gaze_vector = extract_features(
            faces[0], result.facial_transformation_matrixes[0],
            blends,
            frame.shape[1], frame.shape[0],
        )
        from shared.gaze import features
        vector = features(faces[0])
        return len(faces), self.np.asarray(vector) if vector is not None else None

    def calibrate(self):
        if getattr(self, 'public_gaze', None) is not None:
            return self._calibrate_public_gaze()
        cv2 = self.cv2
        from .behavior import POSITIONS
        prompts = [(key, "Look at: " + key.replace("_", " ")) for key, _ in POSITIONS]
        try:
            for label, prompt in prompts:
                samples = []
                collect = False
                while len(samples) < 25:
                    frame, _ = self.read(analyze=False)
                    faces, feature = self.face_features(frame)
                    if collect and feature is not None and faces == 1:
                        samples.append(feature)
                    shown = frame.copy()
                    cv2.rectangle(shown, (0, 0), (640, 85), (26, 54, 38), -1)
                    cv2.putText(
                        shown,
                        prompt,
                        (15, 28),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (230, 245, 224),
                        2,
                    )
                    cv2.putText(
                        shown,
                        f"SPACE: capture | ESC: cancel | {len(samples)}/25",
                        (15, 60),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (205, 225, 200),
                        1,
                    )
                    cv2.imshow("Qorgau calibration - camera is active", shown)
                    key = cv2.waitKey(30) & 0xFF
                    if key == 27:
                        raise ValueError("Калибровка отменена")
                    if key == 32:
                        collect = True
                self.centres[label] = self.np.median(samples, axis=0)
            self.validate_calibration()
        finally:
            cv2.destroyAllWindows()

    def validate_calibration(self):
        if getattr(self, 'public_gaze', None) is not None:
            if not self.gaze_reference_progress['ready']:
                raise ValueError('GAZE_REFERENCE_INSUFFICIENT')
            return
        from .behavior import validate_centres
        validate_centres(self.centres)

    @property
    def requires_gaze_reference(self):
        model = getattr(self, 'public_gaze', None)
        if model is not None and getattr(self, '_closing', False):
            return True
        if model is not None and getattr(self, 'screen_calibration_required', False):
            return not self.screen_calibration_progress['ready']
        return model is not None and model.reference is None

    @property
    def gaze_reference_progress(self):
        model = getattr(self, 'public_gaze', None)
        if model is not None and getattr(self, 'screen_calibration_required', False):
            return self.screen_calibration_progress
        return {
            'collecting': getattr(self, '_gaze_reference_collecting', False),
            'collected': len(getattr(self, '_gaze_reference_samples', ())),
            'required': 25,
            'ready': model is not None and model.reference is not None,
            'error': getattr(self, '_gaze_reference_error', None),
        }

    def cancel_gaze_reference(self):
        """Clear a private-model reference; no live frame can silently replace it."""
        self._gaze_reference_samples = []
        self._gaze_reference_collecting = False
        self._gaze_reference_error = None
        self._gaze_reference_last_at = None
        self._gaze_reference_last_digest = None
        from shared.head_pose import HeadPoseObserver
        self.head_pose = HeadPoseObserver()
        self._head_reference_samples = []
        from shared.gaze_display_memory import GazeDisplayMemory
        self.gaze_display_memory = GazeDisplayMemory()
        self.screen_calibration = None
        self._screen_calibration_collecting = False
        self._screen_calibration_error = None
        self._screen_calibration_sample = None
        self._screen_calibration_last_digest = None
        if getattr(self, 'public_gaze', None) is not None:
            self.public_gaze.reference = None
            self.public_gaze.reference_error = None
            self.gaze_enabled = False

    @property
    def screen_calibration_progress(self):
        profile = getattr(self, 'screen_calibration', None)
        return {
            'collecting': getattr(self, '_screen_calibration_collecting', False),
            'collected': 0,
            'required': 9,
            'ready': bool(profile is not None and profile.ready
                          and self.public_gaze.reference is not None
                          and getattr(self, '_screen_invalidation_requested', None) is None),
            'error': getattr(self, '_screen_calibration_error', None),
        }

    @property
    def screen_signature(self):
        return getattr(self, 'screen_calibration_signature', None)

    def request_screen_invalidation(self, reason):
        """Queue GUI invalidation; the camera worker changes its own reference."""
        self._screen_invalidation_requested = str(reason)

    def begin_screen_calibration(self, screen_signature):
        """Start an explicit, automatic target sequence on one fixed display."""
        if getattr(self, 'public_gaze', None) is None:
            raise ValueError('PUBLIC_GAZE_NOT_AVAILABLE')
        if screen_signature is None:
            raise ValueError('SCREEN_SIGNATURE_REQUIRED')
        self.cancel_gaze_reference()
        self.screen_calibration_required = True
        self.screen_calibration_signature = screen_signature
        self._screen_invalidation_requested = None
        self._screen_calibration_collecting = True
        return self.screen_calibration_progress

    def screen_calibration_sample(self):
        """Consume one fresh, visible raw CNN result from the most recent frame."""
        sample = getattr(self, '_screen_calibration_sample', None)
        self._screen_calibration_sample = None
        if (not getattr(self, '_screen_calibration_collecting', False) or sample is None
                or getattr(self, '_screen_invalidation_requested', None) is not None):
            return None
        return sample

    def invalidate_screen_calibration(self, reason):
        """Disable screen decisions after camera/display/reference changes."""
        self.cancel_gaze_reference()
        self.screen_calibration_required = True
        self._screen_calibration_error = str(reason)
        return self.screen_calibration_progress

    def install_screen_calibration(self, profile, centre_samples, centre_rotations,
                                   screen_signature):
        """Commit an independently validated profile and actual centre frames."""
        import math
        import numpy as np
        from shared.public_gaze import angular_error, normalize

        pending = getattr(self, '_screen_invalidation_requested', None)
        if pending is not None:
            self._screen_invalidation_requested = None
            self.invalidate_screen_calibration(pending)
            return False
        if screen_signature != getattr(self, 'screen_calibration_signature', None):
            self.invalidate_screen_calibration('SCREEN_CHANGED')
            return False
        if not getattr(self, '_screen_calibration_collecting', False):
            return False
        if profile is None or not getattr(profile, 'ready', False):
            self.invalidate_screen_calibration(getattr(profile, 'error', None)
                                               or 'SCREEN_CALIBRATION_FAILED')
            return False
        try:
            if len(centre_samples) < 3:
                raise ValueError('GAZE_REFERENCE_INSUFFICIENT')
            for observation in centre_samples:
                error = float(observation['error90_degrees'])
                if not math.isfinite(error) or not 0 <= error <= 20:
                    raise ValueError('GAZE_REFERENCE_UNCERTAIN')
                normalize(observation['vector'])
            reference = normalize(np.mean([o['vector'] for o in centre_samples], axis=0))
            if np.percentile([angular_error(o['vector'], reference)
                              for o in centre_samples], 90) > 8:
                raise ValueError('GAZE_REFERENCE_UNSTABLE')
            error = float(np.median([o['error90_degrees'] for o in centre_samples]))
        except (KeyError, TypeError, ValueError, OverflowError) as failure:
            self.invalidate_screen_calibration(str(failure))
            return False
        from shared.head_pose import rotation_from_matrix
        rotations = [parsed for value in centre_rotations
                     if (parsed := rotation_from_matrix(value)) is not None]
        self.head_pose.set_reference_rotations(rotations, required_samples=max(3, len(rotations)))
        if self.head_pose.reference_error == 'HEAD_REFERENCE_UNSTABLE':
            self.invalidate_screen_calibration('SCREEN_HEAD_MOVED')
            return False
        self.public_gaze.reference = reference
        self.public_gaze.reference_error = error
        self.screen_calibration = profile
        self._screen_calibration_collecting = False
        self._screen_calibration_error = None
        self._screen_calibration_sample = None
        self._gaze_reference_samples = list(centre_samples)
        self._head_reference_samples = list(centre_rotations)
        self.gaze_enabled = True
        # An unavailable head matrix need not reject a valid full-face CNN
        # profile, and it must not acquire an invented head reference either.
        self.gaze_display_memory.reset()
        return True

    def begin_gaze_reference(self):
        """Arm capture only after the UI instructs centre gaze and the user confirms."""
        if getattr(self, 'public_gaze', None) is None:
            raise ValueError('PUBLIC_GAZE_NOT_AVAILABLE')
        if getattr(self, 'screen_calibration_required', False):
            raise ValueError('SCREEN_CALIBRATION_REQUIRED')
        self.cancel_gaze_reference()
        self._gaze_reference_collecting = True
        return self.gaze_reference_progress

    def _observe_public_gaze(self, frame, faces, mesh_faces, at):
        import hashlib
        import math

        pending_invalidation = getattr(self, '_screen_invalidation_requested', None)
        if pending_invalidation is not None:
            self._screen_invalidation_requested = None
            self.invalidate_screen_calibration(pending_invalidation)
        self._screen_calibration_sample = None
        vector = self.gaze_vector
        if hasattr(self, 'gaze_blinks'):
            blinks = self.gaze_blinks
        else:
            # Compatibility for offline legacy feature fixtures, never used by
            # live Camera after a failed current-frame blink measurement.
            blinks = (vector[29:31] if vector is not None and len(vector) == 33
                      and all(math.isfinite(v) for v in vector) else None)
        valid = (
            faces == mesh_faces == 1
            and getattr(self, 'gaze_landmarks', None) is not None
            and blinks is not None and len(blinks) == 2
            and all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= .65 for v in blinks)
        )
        single_face = faces == mesh_faces == 1
        if hasattr(self, 'gaze_blinks'):
            gaze = self.public_gaze.observe(
                frame, self.gaze_landmarks if single_face else None,
                eye_blink=blinks if single_face else None,
                demo_sensitivity=True,
            )
        else:
            gaze = self.public_gaze.observe(
                frame, self.gaze_landmarks if valid else None, vector if valid else None,
            )
        if not single_face:
            gaze['gaze_tracking_status'] = 'multiple_faces' if faces > 1 or mesh_faces > 1 else 'face_landmarks_missing'
        from shared.head_pose import rotation_from_features
        rotation = self.head_rotation if hasattr(self, 'head_rotation') else rotation_from_features(vector)
        if getattr(self, '_screen_calibration_collecting', False) and valid:
            if ('vector' in gaze and math.isfinite(at)
                    and 0 <= gaze.get('error90_degrees', math.inf) <= 20):
                digest = hashlib.blake2s(frame.tobytes()).hexdigest()
                if digest != self._screen_calibration_last_digest:
                    raw_gaze = {key: gaze[key] for key in (
                        'yaw_degrees', 'pitch_degrees', 'error90_degrees', 'source',
                    )}
                    raw_gaze['vector'] = list(gaze['vector'])
                    self._screen_calibration_sample = {
                        'gaze': raw_gaze, 'rotation': None if rotation is None else rotation.copy(),
                        'at': at, 'frame_id': digest,
                    }
                    self._screen_calibration_last_digest = digest
        if self._gaze_reference_collecting:
            last = self._gaze_reference_last_at
            if faces != 1 or mesh_faces != 1 or (last is not None and at - last > 2):
                self._gaze_reference_samples.clear()
                self._head_reference_samples.clear()
                self._gaze_reference_last_at = None
                self._gaze_reference_last_digest = None
            if valid and 'vector' in gaze and gaze['error90_degrees'] <= 20:
                digest = hashlib.blake2s(frame.tobytes()).digest()
                if (digest != self._gaze_reference_last_digest
                        and (self._gaze_reference_last_at is None
                             or at - self._gaze_reference_last_at >= .05)):
                    self._gaze_reference_samples.append(gaze)
                    self._head_reference_samples.append(None if rotation is None else rotation.copy())
                    self._gaze_reference_last_at = at
                    self._gaze_reference_last_digest = digest
                if len(self._gaze_reference_samples) >= 25:
                    self._gaze_reference_collecting = False
                    try:
                        self.public_gaze.set_reference(self._gaze_reference_samples)
                    except ValueError as error:
                        self._gaze_reference_error = str(error)
                    self.gaze_enabled = self.public_gaze.reference is not None
                    if self.gaze_enabled:
                        self.head_pose.set_reference_rotations(self._head_reference_samples)
                    else:
                        self.head_pose.clear()
                    gaze['reference_ready'] = self.gaze_enabled
        screen_required = getattr(self, 'screen_calibration_required', False)
        if screen_required:
            from shared.screen_gaze import ScreenGazeCalibration
            profile = (getattr(self, 'screen_calibration', None)
                       if getattr(self, '_screen_invalidation_requested', None) is None else None)
            screen = (profile or ScreenGazeCalibration()).observe(gaze if valid else None)
            gaze.update(screen)
            gaze['direction'] = screen['screen_direction']
            gaze['reference_ready'] = self.screen_calibration_progress['ready']
            gaze['gaze_decision_policy'] = 'adaptive_screen'
            gaze['gaze_decision_reason'] = screen['screen_reason']
            gaze['gaze_decision_threshold_degrees'] = screen['screen_margin_degrees']
        # Preserve the existing rule vocabulary and never borrow the legacy
        # classifier's automatic reference when the CNN is unready or uncertain.
        if gaze['direction'] == 'CENTER':
            gaze['direction'] = 'SCREEN'
        away = gaze['direction'] in ('LEFT', 'RIGHT', 'UP', 'DOWN')
        gaze.update(attention_away=away,
                    attention_direction=gaze['direction'] if away else None,
                    reference_progress=self.gaze_reference_progress)
        # Head orientation is independent of eye confidence and blink state.
        # It must never replace gaze direction or trigger a gaze strike alone.
        gaze.update(self.head_pose.observe_rotation(
            rotation, faces=faces,
        ))
        from shared.gaze_feedback import public_gaze_feedback
        from shared.gaze_display_memory import GazeDisplayMemory
        if not hasattr(self, 'gaze_display_memory'):
            self.gaze_display_memory = GazeDisplayMemory()
        feedback = public_gaze_feedback(gaze, self.public_gaze.reference)
        if screen_required:
            feedback.update(
                gaze_observed_direction=gaze.get('screen_observed_direction', gaze['direction']),
                gaze_observation_uncertain=gaze['direction'] == 'UNKNOWN',
                gaze_feedback_reason=gaze['screen_reason'],
                gaze_display_threshold_degrees=gaze['screen_margin_degrees'],
            )
        gaze.update(self.gaze_display_memory.update(
            feedback, at=at,
            blink=gaze.get('gaze_tracking_status') == 'blink', single_face=single_face,
            reference_ready=self.public_gaze.reference is not None,
            head_away=gaze.get('head_away', False),
        ))
        return gaze

    def _calibrate_public_gaze(self):
        """CLI equivalent of the explicitly confirmed centre-reference UI."""
        if getattr(self, 'screen_calibration_required', False):
            raise ValueError('SCREEN_CALIBRATION_REQUIRED: запустите калибровку точками в окне Qorgau')
        cv2 = self.cv2
        self.cancel_gaze_reference()
        try:
            while not self.gaze_reference_progress['ready']:
                frame, _ = self.read()
                progress = self.gaze_reference_progress
                if progress['error']:
                    raise ValueError(progress['error'])
                shown = frame.copy()
                cv2.rectangle(shown, (0, 0), (640, 85), (26, 54, 38), -1)
                cv2.putText(shown, 'Look at the centre of your screen', (15, 28),
                            cv2.FONT_HERSHEY_SIMPLEX, .65, (230, 245, 224), 2)
                prompt = (f"Keep looking at centre: {progress['collected']}/25"
                          if progress['collecting'] else 'SPACE: capture | ESC: cancel')
                cv2.putText(shown, prompt, (15, 60), cv2.FONT_HERSHEY_SIMPLEX,
                            .5, (205, 225, 200), 1)
                cv2.imshow('Qorgau calibration - camera is active', shown)
                key = cv2.waitKey(30) & 0xFF
                if key == 27:
                    self.cancel_gaze_reference()
                    raise ValueError('Калибровка отменена')
                if key == 32 and not progress['collecting']:
                    self.begin_gaze_reference()
        finally:
            cv2.destroyAllWindows()

    def latest_preview(self):
        """Read the newest raw frame without waiting for any model or inference lock."""
        source = getattr(self, 'frame_source', None)
        return source.latest() if source is not None and not self._closing else None

    def read(self, *, analyze=True, phone_review=False, phone_review_until=None):
        lock = getattr(self, '_read_lock', None)
        with lock if lock is not None else nullcontext():
            if getattr(self, '_closing', False):
                raise OSError('Камера закрыта')
            try:
                result = self._read(analyze=analyze, phone_review=phone_review,
                                    phone_review_until=phone_review_until)
                if getattr(self, '_closing', False):
                    raise OSError('Камера закрыта')
                return result
            finally:
                if getattr(self, '_closing', False):
                    self._close_face()

    def _read(self, *, analyze=True, phone_review=False, phone_review_until=None):
        # Limit processing/recording cadence, while the producer keeps draining
        # the driver independently. Consume only the newest distinct frame.
        delay = 1/15 - (time.monotonic() - self.last_frame)
        if delay > 0:
            time.sleep(delay)
        self.last_frame = time.monotonic()
        source = getattr(self, 'frame_source', None)
        if source is not None:
            packet = source.read(after_sequence=self._capture_sequence, timeout=2.)
            self._capture_sequence = packet.sequence
            frame, captured_at = packet.frame, packet.captured_at
        else:  # Compatibility for offline detector fixtures.
            ok, frame = self.capture.read()
            captured_at = time.monotonic()
            if not ok:
                raise OSError("Не получен кадр камеры")
        self.last_captured_at = captured_at
        import hashlib
        digest = hashlib.blake2s(frame.tobytes()).digest()
        if digest != self.frame_digest:
            self.frame_digest = digest
            self.frame_changed_at = time.monotonic()
        elif time.monotonic() - self.frame_changed_at > 5:
            raise OSError("CAMERA_FROZEN: изображение не меняется более 5 секунд")
        if not analyze:
            self.recognition_was_paused = True
            if phone_review and (phone_review_until is None or
                                 (captured_at <= phone_review_until and time.monotonic() <= phone_review_until)):
                # A finite evidence-only continuation keeps the pre-lock phone
                # trajectory, without evaluating faces, gaze, or penalty rules.
                observation = self.phone_observation(frame, captured_at)
                # The pump keeps only the newest frame; retain a detected rise
                # until the agent consumes it and cancels the finite window.
                observation["phone_aiming"] = observation["phone_aiming"] or self.raising.fired
                observation.update(phone_review_only=True, captured_at=captured_at,
                                   phone_episode_id=self.raising.episode_id)
                return frame, observation
            return frame, None
        if self.recognition_was_paused:
            from .behavior import PhoneRaising
            self.raising = PhoneRaising()
            self.recognition_was_paused = False
        observation = self.analyze(frame, at=captured_at)
        observation['captured_at'] = captured_at
        observation['phone_episode_id'] = self.raising.episode_id
        observation['gaze_diagnostics'] = self.gaze_diagnostics
        return frame, observation

    def analyze(self, frame, at=None):
        """The same inference for live capture and timestamped offline evaluation."""
        height, width = frame.shape[:2]
        scale = min(1., 960 / max(width, height))
        mesh_frame = self.cv2.resize(frame, (round(width * scale), round(height * scale)))
        mesh_faces, feature = self.face_features(mesh_frame, None if at is None else int(at * 1000))
        face_detections = self.face_detector.detect(frame)
        faces = max(mesh_faces, len(face_detections))
        from .behavior import classify_gaze
        if getattr(self, 'public_gaze', None) is not None:
            gaze = self._observe_public_gaze(
                mesh_frame, faces, mesh_faces, time.monotonic() if at is None else at,
            )
            direction = gaze['direction']
        else:
            gaze = self.gaze.observe(self.gaze_vector if faces == 1 else None)
            direction = classify_gaze(feature, self.centres) if self.centres else gaze['direction']
        self.gaze_diagnostics = gaze
        return {"direction": direction, "faces": faces, **self.phone_observation(frame, at)}

    def phone_observation(self, frame, at=None):
        """Phone boxes/raising only; no face or gaze model and no rule state."""
        height, width = frame.shape[:2]
        detections = [d for d in self.phone.detect(frame)
                      if d['confidence'] >= PHONE_CONFIDENCE_THRESHOLD]
        confidence = max((x["confidence"] for x in detections), default=0.0)
        return {
            "phone_confidence": confidence,
            "phone_aiming": self.raising.update(time.monotonic() if at is None else at, detections, width, height),
            "detections": [{"label": "phone", "confidence": float(d["confidence"]),
                            "box": [float(v) / (width if i % 2 == 0 else height)
                                    for i, v in enumerate(d["box"])]}
                           for d in detections],
        }

    def _close_face(self):
        if not getattr(self, '_face_closed', False):
            self._face_closed = True
            self.face.close()

    def close(self):
        self._closing = True
        source = getattr(self, 'frame_source', None)
        source_finished = True
        if source is not None:
            source_finished = source.close()
        else:
            self.capture.release()
        lock = getattr(self, '_read_lock', None)
        if lock is None:
            self._close_face()
        elif lock.acquire(timeout=.5):
            try:
                self._close_face()
            finally:
                lock.release()
        # In-flight inference owns MediaPipe. Its read() finally closes it;
        # never close a native model concurrently from the GUI thread.
        return source_finished and self._face_closed


from .recording import ClipRecorder as ClipRecorder  # noqa: E402 - public compatibility export
