"""Online teacher identity verification and short-lived in-memory exclusions."""
import base64
import time
from uuid import uuid4

import httpx


def checked(response):
    if response.status_code != 200:
        try:
            detail = response.json()['detail']
        except (ValueError, KeyError):
            detail = 'Сервер не подтвердил преподавателя.'
        raise ValueError(str(detail))
    return response.json()


class TeacherAccess:
    def identity_http(self):
        return self.http

    def teacher_engine(self):
        with self._teacher_engine_lock:
            if self._teacher_engine is None:
                from shared.teacher_faces import TeacherFaceEngine
                from .resources import resource_root
                self._teacher_engine = TeacherFaceEngine(resource_root() / 'models' / 'teacher-faces')
            return self._teacher_engine

    def refresh_teacher_templates(self):
        """Only provisioned identity connections; never enroll implicitly in local mode."""
        try:
            if (self.mode == 'offline' and self.engine.state.lifecycle != 'RUNNING'
                    and not self.start_pending):
                return
            result = checked(self.identity_http().get('/api/agent/teacher-faces'))
            from shared.teacher_faces import MODEL_ID
            if result.get('model') != MODEL_ID:
                raise ValueError('TEACHER_FACE_MODEL_VERSION')
            expires = min(float(result['expires_at']), time.time() + 60)
            templates = result.get('templates', [])
            engine = self.teacher_engine() if templates else None
        except (httpx.HTTPError, ValueError, OSError, KeyError, ImportError):
            expires, templates, engine = 0, [], None
        with self.mutex:
            self._teacher_templates = (engine, templates, expires)
            if self.camera:
                self.camera.teacher_identity = self._teacher_templates

    def teacher_face_unlock(self, progress=lambda message: None):
        with self.mutex:
            state = self.engine.state
            if state.lifecycle != 'RUNNING' or state.access != 'LOCKED':
                raise ValueError('Сеанс не заблокирован.')
            if self.teacher_face_scan:
                raise ValueError('Проверка преподавателя уже идёт.')
            if not self.camera:
                raise ValueError('Камера недоступна. Используйте пароль.')
            lock_id, version, exam_id = state.lock_id, state.version, self.journal['exam_id']
            camera = self.camera
            self.teacher_face_scan = True
        try:
            http = self.identity_http()
            body = {'exam_id': None if self.mode == 'offline' else exam_id,
                    'lock_id': lock_id, 'expected_version': version}
            challenge = checked(http.post('/api/agent/teacher-face/challenge', json=body))
            capture_floor = time.monotonic()
            engine = self.teacher_engine()
            self.refresh_teacher_templates()
            sign = challenge['turn_sign']
            if sign not in (-1, 1):
                raise ValueError('Некорректное задание сервера.')
            import cv2
            prompts = ['Преподаватель: смотрите прямо в камеру. В кадре должно быть одно лицо.',
                       'Слегка поверните голову ' + ('влево' if sign > 0 else 'вправо') + ' и задержитесь.',
                       'Вернитесь лицом к камере.']
            progress(prompts[0])
            frames, samples = [], []
            deadline = min(time.monotonic() + 35,
                           time.monotonic() + max(0, challenge['expires_at'] - time.time() - 2))
            sequence, last_try = -1, 0
            began = None
            while len(frames) < 3 and time.monotonic() < deadline:
                with self.mutex:
                    if self.engine.state.lock_id != lock_id or self.engine.state.lifecycle != 'RUNNING':
                        raise ValueError('Блокировка изменилась. Повторите проверку.')
                packet = camera.latest_preview()
                now = time.monotonic()
                if (packet is None or packet.sequence == sequence or now - packet.captured_at > 1
                        or packet.captured_at < capture_floor or packet.captured_at > now + .1
                        or now - last_try < .15):
                    time.sleep(.04)
                    continue
                sequence, last_try = packet.sequence, now
                frame = packet.frame
                scale = min(1, 640 / max(frame.shape[:2]))
                if scale < 1:
                    frame = cv2.resize(frame, (round(frame.shape[1] * scale), round(frame.shape[0] * scale)))
                try:
                    sample = engine.encode(frame)
                except ValueError:
                    continue
                pose = sample['pose']
                stage = len(frames)
                elapsed = (packet.captured_at - began) * 1000 if began is not None else 0
                good = (abs(pose) <= .35 if stage == 0 else
                        sign * (pose - samples[0]['pose']) >= .18 and elapsed >= 250 if stage == 1 else
                        abs(pose - samples[0]['pose']) <= .12 and elapsed >= 1000
                        and elapsed - frames[-1]['captured_ms'] >= 250)
                if not good:
                    continue
                ok, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ok:
                    continue
                # Evaluate exactly the pixels sent to the server, not a less-compressed image.
                try:
                    encoded = engine.encode(engine.decode(jpg.tobytes()))
                except ValueError:
                    continue
                if abs(encoded['pose'] - pose) > .04:
                    continue
                from shared.teacher_faces import verify_motion
                if stage == 0 and abs(encoded['pose']) > .35:
                    continue
                if stage == 1 and sign * (encoded['pose'] - samples[0]['pose']) < .18:
                    continue
                if stage == 2 and not verify_motion(samples + [encoded],
                    [0, frames[1]['captured_ms'], round(elapsed)], sign):
                    continue
                if began is None:
                    began = packet.captured_at
                samples.append(encoded)
                frames.append({'image_b64': base64.b64encode(jpg.tobytes()).decode('ascii'),
                               'captured_ms': round((packet.captured_at - began) * 1000)})
                if len(frames) < 3:
                    progress(prompts[len(frames)])
            if len(frames) != 3:
                raise ValueError('Не удалось получить три уверенных кадра. Повторите или используйте пароль.')
            result = checked(http.post('/api/agent/teacher-face/verify',
                                      json={'challenge_id': challenge['challenge_id'], 'frames': frames}, timeout=15))
            if (result.get('matched') is not True or result.get('challenge_id') != challenge['challenge_id']
                    or result.get('expected_version') != version
                    or result.get('lock_id') != lock_id or result.get('exam_id') != body['exam_id']):
                raise ValueError('Ответ относится к другой блокировке.')
            if self.mode == 'offline':
                with self.mutex:
                    if self.engine.state.lock_id != lock_id or self.engine.state.version != version:
                        raise ValueError('Блокировка изменилась. Повторите проверку.')
                    command = dict(id=uuid4().hex, type='UNLOCK', exam_id=exam_id,
                                   lock_id=lock_id, expected_version=version,
                                   expires_at=time.time() + 30)
                    self.apply(command)
                    if not self.journal['processed'][command['id']]['ok']:
                        raise ValueError(self.journal['processed'][command['id']]['error'])
            else:
                self.sync()
                self.sync()
            if self.engine.state.access == 'LOCKED':
                raise ValueError('Причина блокировки сохраняется. Проверьте окно и камеру.')
            progress('Преподаватель подтверждён. Прокторинг продолжен.')
        except httpx.HTTPError as error:
            raise ValueError('Нет связи с базой преподавателей. Используйте резервный пароль.') from error
        finally:
            with self.mutex:
                self.teacher_face_scan = False
