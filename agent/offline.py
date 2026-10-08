"""Local exams: no command polling or upload; network is used only for identity."""
from pathlib import Path
import secrets
import shutil
import time
import threading
from uuid import uuid4

from shared.rules import RuleEngine
from shared.storage import atomic_json
from .client import Agent


class OfflineAgent(Agent):
    mode = 'offline'

    def __init__(self, folder, server=None, transport=None):
        from shared.bootstrap import default_bootstrap
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / 'config.json').exists():
            atomic_json(folder / 'config.json', {
                'server': server or default_bootstrap()['server'],
                'token': secrets.token_urlsafe(32), 'device_id': 'local-' + uuid4().hex,
                'account_id': 'offline', 'name': 'Локальный экзамен', 'targets': []})
        super().__init__(folder, server, transport)
        self.local_access.initialize_demo()
        self.status = 'Локальный режим: выберите окно или вкладку.'
        self._face_http = None
        self._identity_mutex = threading.Lock()

    def sync(self):
        # Deliberately never submits local evidence or polls website exam commands.
        return

    def upload_media(self):
        return

    def teacher_unlock(self, password, action='UNLOCK'):
        self.local_authorize(password, action)

    def identity_http(self):
        with self._identity_mutex:
            if self._face_http is None:
                import httpx
                from .provision import auto_enroll
                from shared.bootstrap import default_bootstrap
                config = auto_enroll(self.folder / 'face-connection', default_bootstrap(self.server))
                self._face_http = httpx.Client(base_url=self.server, timeout=8,
                    headers={'Authorization': 'Bearer ' + config['token']})
            return self._face_http

    def offline_start(self, target):
        if not self.local_access.ready:
            raise ValueError('Сначала задайте резервный пароль преподавателя.')
        with self.mutex:
            if self.engine.state.lifecycle == 'RUNNING' or self.start_pending:
                raise ValueError('Сначала завершите текущий сеанс.')
        if target.get('kind') == 'BROWSER_TAB':
            from .browser_tabs import activate
            chosen, window = activate(self.folder, target)
            selected = dict(chosen, window=window.public(), guardable=True)
            environment = dict(chosen, target_id=chosen['id'], guarded=True)
        else:
            from .windows_guard import WindowsGuard, WindowTarget
            public = target.get('window', target)
            try:
                window = WindowTarget(**{key: public[key] for key in WindowTarget.__dataclass_fields__})
            except (KeyError, TypeError) as error:
                raise ValueError('Выберите открытое окно программы.') from error
            if not WindowsGuard().valid(window):
                raise ValueError('Выбранное окно уже закрыто.')
            if Path(window.executable).name.lower() in ('chrome.exe', 'msedge.exe'):
                raise ValueError('Для браузера выберите конкретную вкладку из отдельного списка.')
            selected = dict(id='offline-window', kind='APP', name=window.title,
                            window=window.public(), guardable=True)
            environment = dict(kind='APP', target_id=selected['id'], guarded=True)
        with self.mutex:
            self.engine = RuleEngine()
            self.reset_session_tracking()
            self.journal.update(exam_id='local-' + uuid4().hex, events=[], acks=[], processed={},
                                reviews={}, media=[], recent_events=[])
            self.origin = time.monotonic()
            self._recognition_after = self.origin
            self.environment = environment
            self.targets = [selected]
            self.guard_target = window.public()
            self.session = {'title': selected['name'], 'name': selected['name']}
            self.queue_start({'id': uuid4().hex, 'type': 'START', 'exam_id': self.journal['exam_id'],
                              'expected_version': 0, 'expires_at': time.time() + 60,
                              'require_camera': True})
            self.save()

    def launch_environment(self):
        from .windows_guard import WindowsGuard, WindowTarget
        selected = next((t for t in self.targets if t['id'] == self.environment['target_id']), None)
        if not selected or not selected.get('window'):
            raise ValueError('TARGET_UNAVAILABLE')
        if selected['kind'] == 'BROWSER_TAB':
            from .browser_tabs import activate
            _, activated = activate(self.folder, selected)
            if activated.public() != selected['window']:
                # Title may change after a navigation; process/window ownership may not.
                original = selected['window']
                if any(activated.public()[key] != original[key] for key in ('hwnd', 'pid', 'started', 'executable')):
                    raise ValueError('Окно вкладки изменилось. Выберите его заново.')
        window = WindowTarget(**selected['window'])
        if not WindowsGuard().valid(window):
            raise ValueError('TARGET_UNAVAILABLE')
        self.guard_target = window.public()

    def cleanup_local_exam(self):
        """Delete only this local exam's owned evidence, after all writers stop."""
        if self.capture_pump:
            self.capture_pump.close()
            self.capture_pump = None
        if self.camera:
            self.camera.close()
            self.camera = None
        if self.recorder:
            self.recorder.close()
            self.recorder = None
        root = self.folder.resolve()
        for name in ('evidence', 'clips'):
            path = self.folder / name
            if path.exists():
                # Refuse reparse points anywhere under these owned directories.
                paths = [path, *path.rglob('*')]
                if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
                       or not p.resolve().is_relative_to(root) for p in paths):
                    raise OSError('Не удалось безопасно очистить локальные материалы.')
                shutil.rmtree(path)
        self.journal.update(events=[], recent_events=[], media=[])
        self.engine.state.strikes.clear()
        self.environment = None
        self.session = None
        self.guard_target = None
        self.journal['exam_id'] = None
        self.capabilities.update(camera=False, recording=False, gaze=False)
        self.save()
        for name in ('browser.json', 'browser-tab-command.json', 'browser-tab-ack.json'):
            (self.folder / name).unlink(missing_ok=True)
        for path in self.folder.glob('browser-tabs-*.json'):
            path.unlink(missing_ok=True)
