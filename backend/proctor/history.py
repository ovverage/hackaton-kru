"""Owner-scoped removal of explicitly selected, completed exam history."""
import time
from pathlib import Path

from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from shared.rules import State
from .db import decode, encode


class HistoryCleanup(BaseModel):
    exam_ids: list[str] = Field(min_length=1, max_length=500)
    password: str = Field(min_length=1, max_length=128)
    apply: bool = False


def register_history_routes(app, db, base, user, hasher):
    @app.post('/api/history/cleanup')
    def cleanup(body: HistoryCleanup, request: Request):
        owner = user(request)['id']
        ids = set(body.exam_ids)
        if len(ids) != len(body.exam_ids):
            raise HTTPException(422, 'Выберите каждый сеанс только один раз')
        with db.connect(True) as c:
            key, now = 'history-cleanup:' + owner, time.time()
            previous = c.execute('SELECT * FROM unlock_attempts WHERE owner=?', (key,)).fetchone()
            if previous and previous['count'] >= 5 and previous['until'] > now:
                raise HTTPException(429, 'Слишком много попыток. Подождите минуту.')
            row = c.execute('SELECT password FROM users WHERE id=?', (owner,)).fetchone()
            try:
                valid = bool(row and hasher.verify(row['password'], body.password))
            except VerificationError:
                valid = False
            if not valid:
                count = previous['count'] + 1 if previous and previous['until'] > now else 1
                c.execute('INSERT OR REPLACE INTO unlock_attempts VALUES(?,?,?)', (key, count, now + 60))
                # Return after commit so the rate limit is durable.
                denied = True
            else:
                denied = False
                result = remove_completed_history(c, base, owner, ids, apply=body.apply, now=now)
                c.execute('DELETE FROM unlock_attempts WHERE owner=?', (key,))
        if denied:
            raise HTTPException(403, 'Неверный пароль преподавателя')
        return result


def remove_completed_history(c, base, owner, ids, *, apply=False, now=None):
    """Validate the whole selection before deleting any file or row."""
    now = time.time() if now is None else now
    placeholders = ','.join('?' for _ in ids)
    values = sorted(ids)
    exams = c.execute(f'SELECT * FROM exams WHERE id IN ({placeholders})', values).fetchall()
    if len(exams) != len(ids) or any(row['owner'] != owner for row in exams):
        raise HTTPException(404, 'Сеанс не найден')
    for row in exams:
        exam = decode(row)
        if exam.get('status') != 'COMPLETED' or any(
            participant.get('state', {}).get('lifecycle') != 'COMPLETED'
            for participant in exam.get('participants', {}).values()
        ):
            raise HTTPException(409, 'Сначала завершите выбранные сеансы')
    devices = [decode(row) for row in c.execute('SELECT body FROM devices WHERE owner=?', (owner,))]
    affected = [device for device in devices if device.get('exam_id') in ids]
    if any(device['state']['lifecycle'] != 'COMPLETED'
           or device.get('capabilities', {}).get('recording_tail') for device in affected):
        raise HTTPException(409, 'Дождитесь завершения сеанса и записи на компьютере')
    media = c.execute(f'SELECT media.* FROM media JOIN events ON events.id=media.event_id '
                      f'WHERE events.exam_id IN ({placeholders})', values).fetchall()
    root = (Path(base) / 'media').resolve()
    paths = [Path(row['path']) for row in media]
    if any(path.is_symlink() or path.resolve().parent != root for path in paths):
        raise HTTPException(409, 'Некорректный путь видео; требуется проверка хранилища')
    commands = [row['id'] for row in c.execute(
        'SELECT commands.id,commands.body FROM commands JOIN devices ON devices.id=commands.device_id '
        'WHERE devices.owner=?', (owner,)) if decode(row).get('exam_id') in ids]
    events = c.execute(f'SELECT count(*) FROM events WHERE exam_id IN ({placeholders})', values).fetchone()[0]
    result = {'applied': apply, 'exams': len(exams), 'events': events, 'videos': len(media),
              'commands': len(commands), 'devices_reset': len(affected)}
    if not apply:
        return result
    for path in paths:
        path.unlink(missing_ok=True)
    for row in media:
        c.execute('DELETE FROM media_lifetime WHERE id=?', (row['id'],))
        c.execute('DELETE FROM media WHERE id=?', (row['id'],))
    for command in commands:
        c.execute('DELETE FROM commands WHERE id=?', (command,))
    c.execute(f'DELETE FROM events WHERE exam_id IN ({placeholders})', values)
    c.execute(f'DELETE FROM exams WHERE id IN ({placeholders})', values)
    for device in affected:
        device.update(exam_id=None, environment=None, state=State().public())
        c.execute('UPDATE devices SET body=? WHERE id=?', (encode(device), device['id']))
        c.execute('DELETE FROM face_challenges WHERE device_id=?', (device['id'],))
    c.execute('INSERT INTO audit(at,actor,action,body) VALUES(?,?,?,?)',
              (now, owner, 'COMPLETED_HISTORY_REMOVED', encode(result)))
    return result
