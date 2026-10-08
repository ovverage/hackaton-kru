"""Interactive START waits for calibration and environment preparation."""
import copy
import time
from uuid import uuid4


class SessionControl:
    mode = 'online'

    @property
    def start_camera_ready(self):
        return bool(self.camera and self.recorder and not self.camera_fault
                    and self.last_observation_at is not None
                    and time.monotonic() - self.last_observation_at < 2
                    and not getattr(self.camera, 'requires_gaze_reference', False))

    def queue_start(self, command):
        if self.engine.state.lifecycle == 'RUNNING' or self.start_pending or getattr(self, '_environment_preparing', None):
            raise ValueError('Сеанс уже запущен или готовится.')
        target_hwnd = (self.guard_target or {}).get('hwnd')
        if not target_hwnd:
            env = self.environment or {}
            target_id = 'primary-window' if env.get('kind') == 'DESKTOP' else env.get('target_id')
            target = next((item for item in self.targets if item.get('id') == target_id), {})
            target_hwnd = (target.get('window') or {}).get('hwnd')
        self.start_pending = {'id': uuid4().hex, 'command': copy.deepcopy(command),
                              'target_hwnd': target_hwnd}
        self.status = 'Пройдите настройку взгляда перед началом теста.'

    def prepare_start(self, token):
        with self.mutex:
            if not self.start_pending or token != self.start_pending['id']:
                raise ValueError('Подготовка сеанса отменена.')
            if not self.start_camera_ready:
                raise ValueError('Ждём свежий кадр после настройки камеры.')
            if getattr(self, '_environment_preparing', None):
                raise ValueError('Открытие экзамена уже выполняется.')
            self._environment_preparing = token
            exam_id = self.journal['exam_id']
        try:
            from .program_check import running_tools
            found = running_tools()
            self.capabilities['remote_programs'] = found
            if found:
                raise ValueError('Перед экзаменом закройте: ' + ', '.join(found))
            self.launch_environment()
            with self.mutex:
                if (not self.start_pending or self.start_pending['id'] != token
                        or self.journal['exam_id'] != exam_id):
                    self.guard_target = None
                    raise ValueError('Подготовка сеанса отменена.')
                self.start_pending['prepared'] = True
        finally:
            with self.mutex:
                self._environment_preparing = None

    def reset_session_tracking(self):
        self.last_security_event = {}
        self.last_browser_event = {}
        self.browser_seen = None
        self.gaze_diagnostics = None
        self.last_observation = None
        self.last_observation_at = None
        self._phone_review = None
        self._last_phone_aim = None
        self.start_pending = None
        self.guard_started_at = None
        self.head_review.reset()
        if self.capture_pump:
            self.capture_pump.set_recognition(False)
            self.capture_pump.set_recognition(True)

    def complete_start(self, token):
        with self.mutex:
            pending = self.start_pending
            if not pending or pending['id'] != token or not pending.get('prepared'):
                raise ValueError('Подготовка сеанса не завершена.')
            command = dict(pending['command'], id='prepared-' + token + '-' + uuid4().hex,
                           expected_version=self.engine.state.version,
                           expires_at=time.time() + 30, _prepared_start=True)
            self.apply(command)
            ack = self.journal['processed'][command['id']]
            if not ack['ok']:
                raise ValueError(ack['error'])
            self.start_pending = None
            self.save()

    def cancel_start(self, token, error='Подготовка отменена.'):
        with self.mutex:
            if self.start_pending and self.start_pending['id'] == token:
                self.start_pending = None
                self.guard_target = None
                self.status = error
                self.save()

    def available_browser_tabs(self):
        from .browser_tabs import available
        return available(self.folder)

    def extension_folder(self):
        from .browser_tabs import extension_folder
        return extension_folder()

    def bind_browser(self, extension_id, browser_instance, browser='edge', replace=False):
        if self.engine.state.lifecycle == 'RUNNING' or self.start_pending:
            raise ValueError('Привязка браузера выполняется до экзамена.')
        from .native_install import install
        return install(self.folder, extension_id, browser_instance, browser, replace)

    def setup_password(self, password):
        if self.engine.state.lifecycle == 'RUNNING' or self.start_pending:
            raise ValueError('Пароль задаётся до начала экзамена.')
        if self.local_access.ready:
            raise ValueError('Резервный пароль уже установлен на этом компьютере.')
        if self.mode != 'offline':
            from .teacher_access import checked
            checked(self.http.post('/api/agent/teacher-password', json={'password': password}))
        self.local_access.set_password(password)

    def local_authorize(self, password, action):
        self.local_access.verify(password)
        with self.mutex:
            self.apply({'id': uuid4().hex, 'exam_id': self.journal['exam_id'],
                        'type': action, 'lock_id': self.engine.state.lock_id,
                        'expected_version': self.engine.state.version,
                        'expires_at': time.time() + 30, 'require_camera': True})
            ack = self.journal['acks'][-1]
            if not ack['ok']:
                raise ValueError(ack['error'])

    def local_finish(self, password):
        self.teacher_unlock(password, 'END_AND_RELEASE')
