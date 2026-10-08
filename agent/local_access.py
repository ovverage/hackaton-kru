"""Explicit local fallback credential; never stores the entered password."""
import time
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from shared.storage import atomic_json
from .profile import read_object


class LocalAccess:
    def __init__(self, folder):
        self.path = folder / 'local-access.json'
        self.hasher = PasswordHasher()
        self.failures = 0
        self.retry_at = 0.

    @property
    def ready(self):
        return bool(read_object(self.path).get('password_hash'))

    def set_password(self, password):
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise ValueError('Пароль должен содержать от 8 до 128 символов.')
        atomic_json(self.path, {'password_hash': self.hasher.hash(password)})

    def verify(self, password):
        if time.monotonic() < self.retry_at:
            raise ValueError('Слишком много попыток. Подождите 30 секунд.')
        value = read_object(self.path).get('password_hash')
        try:
            valid = bool(value and self.hasher.verify(value, password))
        except (VerificationError, InvalidHashError, TypeError):
            valid = False
        if not valid:
            self.failures += 1
            if self.failures >= 5:
                self.retry_at = time.monotonic() + 30
                self.failures = 0
            raise ValueError('Неверный резервный пароль преподавателя.' if value else
                             'Резервный пароль ещё не настроен на этом компьютере.')
        self.failures = 0
        return True
