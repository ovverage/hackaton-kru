"""Tab inventory and activation via an explicitly bound native extension."""
from pathlib import Path
import shutil
import time
from uuid import uuid4, UUID
from urllib.parse import urlparse

from shared.storage import atomic_json
from .profile import read_object


def safe_tabs(items, instance):
    try:
        instance = str(UUID(instance))
    except (ValueError, TypeError, AttributeError):
        return []
    result = []
    for item in items[:100] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        tab, window = item.get('tab_id'), item.get('window_id')
        url = str(item.get('url', ''))[:2048]
        if (type(tab) is not int or type(window) is not int or min(tab, window) < 0
                or urlparse(url).scheme not in ('http', 'https')):
            continue
        title = str(item.get('title', 'Вкладка'))[:256]
        result.append(dict(kind='BROWSER_TAB', id=f'{instance}:{tab}',
                           tab_id=tab, window_id=window, browser_instance=instance,
                           title=title, name=title, url=url))
    return result


def available(folder, now=None):
    now = time.time() if now is None else now
    atomic_json(folder / 'browser-tabs-request.json', {'until': now + 60})
    result = []
    for path in folder.glob('browser-tabs-*.json'):
        if path.name == 'browser-tabs-request.json':
            continue
        roster = read_object(path)
        if 0 <= now - roster.get('at', 0) < 6:
            result.extend(safe_tabs(roster.get('tabs'), roster.get('browser_instance')))
    return result


def activate(folder, target, timeout=5):
    current = next((x for x in available(folder) if x['id'] == target.get('id')), None)
    if current is None or current['url'] != target.get('url'):
        raise ValueError('Вкладка закрыта или изменилась. Обновите список.')
    nonce = uuid4().hex
    atomic_json(folder / 'browser-tab-command.json',
                dict(current, nonce=nonce, expires_at=time.time() + timeout))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reply = read_object(folder / 'browser-tab-ack.json')
        if reply.get('nonce') == nonce and reply.get('browser_instance') == current['browser_instance']:
            if not reply.get('ok'):
                raise ValueError('Не удалось открыть вкладку: ' + str(reply.get('error', ''))[:180])
            from .windows_guard import WindowsGuard
            guard = WindowsGuard()
            window = guard.info(guard.u.GetForegroundWindow())
            owner = reply.get('owner') or {}
            if (window is None or Path(window.executable).name.lower() not in ('chrome.exe', 'msedge.exe')
                    or any(window.public().get(key) != owner.get(key)
                           for key in ('hwnd', 'pid', 'started', 'executable'))
                    or any(reply.get(key) != current[key] for key in ('tab_id', 'window_id', 'url'))):
                raise ValueError('Браузер не перешёл на передний план. Выберите вкладку ещё раз.')
            return current, window
        time.sleep(.05)
    raise ValueError('Нет ответа расширения. Проверьте его установку и привязку.')


def native_browser_owner():
    """Native-host caller's foreground browser must be its verified process ancestor."""
    import os
    import ctypes as c
    from ctypes import wintypes as w
    from .windows_guard import WindowsGuard
    from .program_check import process_inventory
    if os.name != 'nt':
        return None
    guard = WindowsGuard()
    window = guard.info(guard.u.GetForegroundWindow())
    if window is None or Path(window.executable).name.lower() not in ('chrome.exe', 'msedge.exe'):
        return None
    processes = process_inventory()
    pid = os.getpid()
    younger_than = 2**64
    for _ in range(8):
        if pid not in processes:
            return None
        handle = guard.k.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [w.FILETIME() for _ in range(4)]
            if not guard.k.GetProcessTimes(handle, *(c.byref(value) for value in times)):
                return None
            created = times[0].dwLowDateTime | (times[0].dwHighDateTime << 32)
            if created > younger_than:
                return None
            if pid == window.pid:
                return window.public() if created == window.started else None
            younger_than = created
            pid = processes[pid][0]
        finally:
            guard.k.CloseHandle(handle)
    return None


def extension_folder():
    from .resources import resource_root
    source = resource_root() / 'extension'
    if not (source / 'manifest.json').is_file():
        raise ValueError('В сборке отсутствует расширение браузера.')
    destination = Path.home() / '.qorgau' / 'extension'
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('manifest.json', 'background.js', 'setup.js', 'setup.html'):
        shutil.copyfile(source / name, destination / name)
    return destination
