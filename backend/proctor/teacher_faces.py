"""Account-scoped face enrolment and single-use online identity challenges."""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from uuid import uuid4

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from shared.teacher_faces import MODEL_ID, MATCH_THRESHOLD, MAX_IMAGE_BYTES, TeacherFaceEngine, verify_motion
from .db import decode, encode


class ChallengeInput(BaseModel):
    exam_id: str | None = Field(default=None, max_length=100)
    lock_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=0)


class FaceFrame(BaseModel):
    image_b64: str = Field(min_length=16, max_length=700000)
    captured_ms: float = Field(ge=0, le=35000, allow_inf_nan=False)


class FaceVerification(BaseModel):
    challenge_id: str = Field(min_length=16, max_length=100)
    frames: list[FaceFrame] = Field(min_length=3, max_length=3)


class FaceAccessInput(BaseModel):
    enabled: bool = Field(strict=True)


def device_template_access(c, row, device):
    """Biometric export is a server-owned permission, never a capability claim.

    Manual pairing and private installer provisioning were authorized by the
    teacher; public auto-registration only proves control of an installation.
    An explicit owner decision overrides either default, including revocation.
    """
    public = c.execute('SELECT 1 FROM public_devices WHERE device_id=? AND owner=?',
                       (row['id'], row['owner'])).fetchone() is not None
    override = device.get('teacher_face_access')
    enabled = override if type(override) is bool else not public
    return bool(enabled and row['token'] and not device.get('revoked_at')
                and not device.get('simulated')), public


def register_teacher_faces(app, db, user, device_auth, audit, root):
    creation_lock = threading.Lock()

    def engine():
        with creation_lock:
            current = getattr(app.state, 'teacher_face_engine', None)
            if current is None:
                try:
                    default = root / 'models/teacher-faces'
                    if not default.is_dir():
                        default = root / 'backend/proctor/assets/teacher-faces'
                    current = TeacherFaceEngine(Path(os.getenv('PROCTOR_TEACHER_FACE_MODELS', default)))
                except (OSError, ValueError, RuntimeError, ImportError) as error:
                    raise HTTPException(503, 'Распознавание преподавателя пока недоступно. Используйте пароль.') from error
                app.state.teacher_face_engine = current
            return current

    def templates(c, owner):
        return [{'teacher_id': row['id'], 'name': row['name'], 'embedding': json.loads(row['embedding'])}
                for row in c.execute('SELECT id,name,embedding FROM teacher_faces WHERE owner=? AND model=? ORDER BY created_at,id', (owner, MODEL_ID))]

    def authorize_device(c, request):
        row, device = device_auth(request, c)
        if device.get('revoked_at'):
            raise HTTPException(403, 'Доступ устройства отозван')
        return row, device

    def same_online_lock(device, body):
        return (body['exam_id'] is None or (device.get('exam_id') == body['exam_id']
                and device['state']['lifecycle'] == 'RUNNING' and device['state']['access'] == 'LOCKED'
                and device['state']['lock_id'] == body['lock_id']
                and device['state']['version'] == body['expected_version']))

    @app.get('/api/teacher-face-devices')
    def list_face_devices(request: Request):
        owner = user(request)['id']
        items = []
        with db.connect() as c:
            for row in c.execute('SELECT * FROM devices WHERE owner=? ORDER BY rowid DESC', (owner,)):
                device = decode(row)
                if not row['token'] or device.get('revoked_at') or device.get('simulated'):
                    continue
                enabled, public = device_template_access(c, row, device)
                items.append({'id': row['id'], 'name': device.get('name', 'Компьютер'),
                              'enabled': enabled, 'public_enrollment': public})
        return {'devices': items}

    @app.post('/api/devices/{device_id}/teacher-face-access')
    def set_face_access(device_id: str, body: FaceAccessInput, request: Request):
        owner = user(request)['id']
        with db.connect(True) as c:
            row = c.execute('SELECT * FROM devices WHERE id=? AND owner=?', (device_id, owner)).fetchone()
            if row is None:
                raise HTTPException(404, 'Компьютер не найден')
            device = decode(row)
            if not row['token'] or device.get('revoked_at') or device.get('simulated'):
                raise HTTPException(403, 'Доступ устройства отозван или недоступен')
            device['teacher_face_access'] = body.enabled
            c.execute('UPDATE devices SET body=? WHERE id=?', (encode(device), device_id))
            audit(c, owner, 'TEACHER_FACE_ACCESS_GRANTED' if body.enabled else 'TEACHER_FACE_ACCESS_REVOKED',
                  {'device_id': device_id})
        return {'device_id': device_id, 'enabled': body.enabled}

    @app.get('/api/teacher-faces')
    def list_faces(request: Request):
        owner = user(request)['id']
        with db.connect() as c:
            items = [dict(row) for row in c.execute(
                'SELECT id,name,created_at FROM teacher_faces WHERE owner=? ORDER BY created_at DESC', (owner,))]
        return {'faces': items, 'limit': 20}

    @app.post('/api/teacher-faces')
    async def add_face(request: Request):
        owner = user(request)['id']
        try:
            length = int(request.headers.get('content-length', '0'))
        except ValueError:
            raise HTTPException(413, 'Фото слишком большое')
        if not 0 < length <= MAX_IMAGE_BYTES + 65536:
            raise HTTPException(413, 'Фотография должна быть не больше 3 МБ')
        async with request.form(max_files=1, max_fields=1, max_part_size=MAX_IMAGE_BYTES) as form:
            name, image = form.get('name'), form.get('image')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not hasattr(image, 'read'):
                raise HTTPException(422, 'Укажите имя и фотографию преподавателя')
            content = await image.read(MAX_IMAGE_BYTES + 1)
        current = engine()
        try:
            observation = await run_in_threadpool(lambda: current.encode(current.decode(content)))
        except (ValueError, RuntimeError) as error:
            raise HTTPException(422, 'Нужна чёткая фотография с одним видимым лицом. ' + str(error)) from error
        now, teacher_id = time.time(), str(uuid4())
        with db.connect(True) as c:
            existing = current.match(observation['embedding'], templates(c, owner))
            if existing is not None:
                raise HTTPException(409, f"Этот преподаватель уже добавлен: {existing['name']}. Используйте существующую запись.")
            if c.execute('SELECT count(*) FROM teacher_faces WHERE owner=?', (owner,)).fetchone()[0] >= 20:
                raise HTTPException(409, 'Сохранено 20 преподавателей. Удалите ненужный образец.')
            c.execute('INSERT INTO teacher_faces VALUES(?,?,?,?,?,?)',
                      (teacher_id, owner, name.strip(), encode(observation['embedding']), MODEL_ID, now))
            audit(c, owner, 'TEACHER_FACE_ADDED', {'teacher_id': teacher_id, 'name': name.strip()})
        # Only the descriptor is persisted, never the uploaded photo.
        return {'id': teacher_id, 'name': name.strip(), 'created_at': now}

    @app.delete('/api/teacher-faces/{teacher_id}')
    def delete_face(teacher_id: str, request: Request):
        owner = user(request)['id']
        with db.connect(True) as c:
            if c.execute('DELETE FROM teacher_faces WHERE id=? AND owner=?', (teacher_id, owner)).rowcount != 1:
                raise HTTPException(404, 'Преподаватель не найден')
            audit(c, owner, 'TEACHER_FACE_REMOVED', {'teacher_id': teacher_id})
        return {'deleted': True}

    @app.get('/api/agent/teacher-faces')
    def device_faces(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        with db.connect() as c:
            row, device = authorize_device(c, request)
            enabled, _ = device_template_access(c, row, device)
            if not enabled:
                raise HTTPException(403, 'Ожидает разрешения преподавателя. В кабинете откройте «Преподаватели» и разрешите этому компьютеру доступ к образцам лиц.')
            items = templates(c, row['owner'])
        return {'templates': items, 'model': MODEL_ID, 'threshold': MATCH_THRESHOLD,
                'expires_at': time.time() + 60}

    @app.post('/api/agent/teacher-face/challenge')
    def challenge(body: ChallengeInput, request: Request):
        now = time.time()
        error = None
        with db.connect(True) as c:
            row, device = authorize_device(c, request)
            if not same_online_lock(device, body.model_dump()):
                raise HTTPException(409, 'Блокировка изменилась. Повторите проверку.')
            if not templates(c, row['owner']):
                raise HTTPException(409, 'Сначала добавьте лицо преподавателя в кабинете. Можно использовать пароль.')
            c.execute('DELETE FROM face_challenges WHERE expires<?', (now - 120,))
            previous = c.execute('SELECT count,until FROM face_attempts WHERE device_id=?', (device['id'],)).fetchone()
            count = previous['count'] if previous and previous['until'] > now else 0
            until = previous['until'] if count else now + 60
            if count >= 5:
                error = HTTPException(429, 'Слишком много попыток. Подождите одну минуту.')
            else:
                c.execute('INSERT OR REPLACE INTO face_attempts VALUES(?,?,?)', (device['id'], count + 1, until))
                c.execute('DELETE FROM face_challenges WHERE device_id=?', (device['id'],))
                token, sign = secrets.token_urlsafe(24), secrets.choice((-1, 1))
                data = dict(body.model_dump(), turn_sign=sign, created_at=now)
                c.execute('INSERT INTO face_challenges VALUES(?,?,?,?,?,?,?)',
                          (token, row['owner'], device['id'], encode(data), now + 45, 0, MODEL_ID))
        if error:
            raise error
        return {'challenge_id': token, 'expires_at': now + 45, 'turn_sign': sign,
                'instructions': 'Посмотрите в камеру, поверните голову в указанную сторону и вернитесь в центр.',
                'model': MODEL_ID, 'lock_id': body.lock_id, 'exam_id': body.exam_id,
                'expected_version': body.expected_version}

    @app.post('/api/agent/teacher-face/verify')
    def verify(body: FaceVerification, request: Request):
        now = time.time()
        with db.connect(True) as c:
            row, device = authorize_device(c, request)
            challenge_row = c.execute('SELECT * FROM face_challenges WHERE id=? AND owner=? AND device_id=?',
                                     (body.challenge_id, row['owner'], device['id'])).fetchone()
            if not challenge_row or challenge_row['used'] or challenge_row['expires'] <= now or challenge_row['model'] != MODEL_ID:
                raise HTTPException(409, 'Проверка истекла или уже использована. Начните заново.')
            c.execute('UPDATE face_challenges SET used=1 WHERE id=?', (body.challenge_id,))
            data = decode(challenge_row)
        # Consume before decoding/inference; even an unsuccessful valid submission is single-use.
        current = engine()
        images = []
        try:
            for item in body.frames:
                images.append(base64.b64decode(item.image_b64, validate=True))
            if len({hashlib.sha256(image).digest() for image in images}) != 3:
                raise ValueError('DUPLICATE_FRAMES')
            if any(len(image) > 512 * 1024 for image in images):
                raise ValueError('FRAME_TOO_LARGE')
            observations = [current.encode(current.decode(image)) for image in images]
        except (binascii.Error, ValueError, RuntimeError) as error:
            raise HTTPException(422, 'Не удалось проверить три кадра лица. Повторите попытку.') from error
        stamps = [frame.captured_ms for frame in body.frames]
        elapsed_ms = (time.time() - data['created_at']) * 1000
        if not verify_motion(observations, stamps, data['turn_sign']) or stamps[-1] > elapsed_ms + 750:
            raise HTTPException(403, 'Не подтверждён поворот и возврат головы. Повторите попытку.')
        with db.connect(True) as c:
            _, fresh = authorize_device(c, request)
            if time.time() >= challenge_row['expires'] or not same_online_lock(fresh, data):
                raise HTTPException(409, 'Блокировка или срок проверки изменились.')
            known = templates(c, row['owner'])
            matches = [current.match(observation['embedding'], known) for observation in observations]
            if not all(matches) or len({match['teacher_id'] for match in matches}) != 1:
                audit(c, row['owner'], 'TEACHER_FACE_DENIED', {'device_id': device['id']})
                result = None
            else:
                match = matches[0]
                result = {'matched': True, 'teacher_id': match['teacher_id'], 'name': match['name'],
                          'challenge_id': body.challenge_id, 'lock_id': data['lock_id'],
                          'exam_id': data['exam_id'], 'expected_version': data['expected_version']}
                if data['exam_id'] is not None:
                    exam = c.execute('SELECT body FROM exams WHERE id=? AND owner=?', (data['exam_id'], row['owner'])).fetchone()
                    if not exam:
                        raise HTTPException(409, 'Сеанс изменился')
                    command = dict(id=str(uuid4()), device_id=device['id'], exam_id=data['exam_id'],
                                   type='UNLOCK', lock_id=data['lock_id'], expected_version=data['expected_version'],
                                   actor=match['name'], reason='Подтверждение преподавателя по лицу на рабочем месте',
                                   require_camera=decode(exam).get('require_camera', True), created_at=time.time(),
                                   expires_at=time.time() + 30, status='PENDING')
                    c.execute('INSERT INTO commands VALUES(?,?,?)', (command['id'], device['id'], encode(command)))
                    result['command'] = command
                audit(c, row['owner'], 'TEACHER_FACE_VERIFIED', {'device_id': device['id'],
                      'teacher_id': match['teacher_id'], 'lock_id': data['lock_id'], 'exam_id': data['exam_id']})
        if result is None:
            raise HTTPException(403, 'Лицо преподавателя не совпало. Используйте пароль или повторите проверку.')
        return result
