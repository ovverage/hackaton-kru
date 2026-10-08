"""Render deterministic UI fixtures without cameras, enrollment or network I/O.

Run from the repository root with QT_QPA_PLATFORM=offscreen. Screenshot camera
imagery is the supplied design's SVG illustration, never a student's recording.
"""
from __future__ import annotations

import os
import argparse
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--no-sandbox --disable-gpu')

from PySide6.QtCore import QByteArray, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from agent.localized_widgets import QMenu  # noqa: E402
from agent.i18n import set_language  # noqa: E402
from agent.client import Agent, atomic_json  # noqa: E402
from agent.desktop import StudentWindow  # noqa: E402
from agent.camera_setup import CameraSetup  # noqa: E402
from agent.exam_ui import BrowserBindingDialog, BrowserTabPicker, GazeWarning, LockScreen, TargetPicker  # noqa: E402
from agent.screen_calibration import ScreenCalibrationDialog  # noqa: E402
from agent.theme import APP_QSS, app_font, logo_icon  # noqa: E402

app = QApplication.instance() or QApplication([])
parser = argparse.ArgumentParser()
parser.add_argument('--language', choices=('ru', 'kk', 'en'), default='ru')
parser.add_argument('--output', type=Path)
options = parser.parse_args()
set_language(options.language, persist=False)
app.setStyle('Fusion')
app.setFont(app_font())
OUT = options.output or ROOT / 'design/redesign-2026-10-v2/implemented/exe'
OUT.mkdir(parents=True, exist_ok=True)
windows = []
agents = []


def capture(widget, name, width, height):
    windows.append(widget)
    widget.resize(width, height)
    widget.show()
    for _ in range(4):
        app.processEvents()
    assert widget.grab().save(str(OUT / f'{name}.png'))
    widget.hide()


def agent_at(folder, *, offline=False):
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / 'config.json', {'server': 'http://localhost:8000', 'token': 'fixture',
                                       'name': 'K301-PC14', 'device_id': 'preview-computer'})
    agent = Agent(folder)
    if offline:
        agent.mode = 'offline'
        agent.local_access.initialize_demo()
    agent.last_synced_at = time.monotonic()
    agents.append(agent)
    return agent


def student(folder, agent=None):
    window = StudentWindow(folder, agent=agent, run_worker=False)
    window.timer.stop()
    window.retry_timer.stop()
    if window.exam_controller:
        window.exam_controller.timer.stop()
    return window


with tempfile.TemporaryDirectory(prefix='qorgau-ui-') as temp:
    base = Path(temp)
    with patch('agent.desktop.socket.gethostname', return_value='K301-PC14'):
        connect = student(base / 'new')
    capture(connect, 'exe-01-connect', 660, 640)
    connect.registration_failed('Нет связи с сервером. Проверьте подключение к сети.')
    capture(connect, 'exe-01-connect-retry', 660, 690)
    agent = agent_at(base / 'online')
    agent.capabilities.update(camera=True, gaze=True)
    agent.gaze_diagnostics = {'reference_ready': True, 'gaze_tracking_status': 'tracked',
                              'gaze_observed_direction': 'SCREEN', 'gaze_observation_uncertain': False}
    prep = student(agent.folder, agent)
    prep.camera_choice.clear()
    prep.camera_choice.addItem('Logitech C270 HD', 0)
    capture(prep, 'exe-02-prep', 780, 900)
    local_agent = agent_at(base / 'local', offline=True)
    local = student(local_agent.folder, local_agent)
    local.camera_choice.clear()
    local.camera_choice.addItem('Logitech C270 HD', 0)
    capture(local, 'exe-02-prep-local', 780, 1200)

    html = (ROOT / 'design/redesign-2026-10-v2/html/exe-07-pause.html').read_text(encoding='utf-8')
    svg = re.search(r'<svg class="scene".*?</svg>', html, re.S).group(0)
    svg = svg.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    image = QImage(640, 360, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    image_path = base / 'illustrated-camera.png'
    image.save(str(image_path))
    camera_html = (ROOT / 'design/redesign-2026-10-v2/html/exe-03-camera.html').read_text(encoding='utf-8')
    camera_svg = re.search(r'<svg class="scene".*?</svg>', camera_html, re.S).group(0)
    camera_svg = re.sub(r'<ellipse[^>]*stroke-dasharray[^>]*></ellipse>', '', camera_svg)
    camera_svg = camera_svg.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    camera_image = QImage(640, 360, QImage.Format.Format_RGB32)
    camera_painter = QPainter(camera_image)
    QSvgRenderer(QByteArray(camera_svg.encode())).render(camera_painter)
    camera_painter.end()
    with patch.object(CameraSetup, 'start', lambda self: None), patch('agent.resources.verified_models', return_value=('phone.onnx', 'face.onnx')):
        camera = CameraSetup(agent)
        camera.preview.set_image(camera_image)
        camera.feedback.setText('Камера готова')
        camera.start_button.setText('Использовать эту камеру')
        capture(camera, 'exe-03-camera', 800, 800)
        calibration_camera = CameraSetup(agent, calibrate=True)
        calibration_camera.preview.set_image(camera_image)
        capture(calibration_camera, 'exe-03-gaze-setup', 800, 820)

    picker = TargetPicker(agent, None)
    picker.items.clear()
    for title, detail, kind, muted in (
        ('Тест по математике — MyTest', 'Программа, MyTestStudent.exe', 'window', False),
        ('Документ — Microsoft Word', 'Программа, WINWORD.EXE', 'window', False),
        ('Тест — Google Chrome', 'Браузер не подходит: сайт откроет Qorgau Browser', 'globe', True),
    ):
        picker.items.addItem(title + '\n' + detail)
        item = picker.items.item(picker.items.count()-1)
        item.setData(Qt.ItemDataRole.UserRole, kind)
        item.setData(Qt.ItemDataRole.UserRole + 1, muted)
    picker.items.setCurrentRow(0)
    capture(picker, 'exe-04-window', 800, 560)
    tabs = [dict(title='Рубежный контроль 2', url='https://test.example/exam/2'),
            dict(title='Личный кабинет', url='https://test.example/profile')]
    tabs_agent = SimpleNamespace(available_browser_tabs=lambda: tabs)
    tab_picker = BrowserTabPicker(tabs_agent, None)
    tab_picker.items.setCurrentRow(0)
    capture(tab_picker, 'exe-04-browser-tabs', 800, 680)
    binding = BrowserBindingDialog(agent, None)
    capture(binding, 'exe-04-extension', 640, 580)

    menu = QMenu()
    menu.setStyleSheet(APP_QSS)
    status = menu.addAction('K301-PC14 — Ждём преподавателя')
    status.setEnabled(False)
    menu.addSeparator()
    menu.addAction(logo_icon(), 'Открыть Qorgau')
    menu.addSeparator()
    menu.addAction('Выйти из Qorgau')
    menu.ensurePolished()
    capture(menu, 'exe-05-tray', menu.sizeHint().width(), menu.sizeHint().height())

    warning = GazeWarning()
    warning.update_countdown({'gaze_seconds': 2.1, 'gaze_diagnostics': {'direction': 'DOWN', 'gaze_observed_direction': 'DOWN'}})
    capture(warning, 'exe-06-gaze', 660, 124)
    warning.message.setText('Верните взгляд на монитор')
    warning.update_countdown({'gaze_diagnostics': {'head_direction': 'RIGHT'}})
    capture(warning, 'exe-06-head-warning', 660, 174)

    for current, filename in ((agent, 'exe-07-pause'), (local_agent, 'exe-07-pause-local')):
        current.engine.start()
        current.engine.lock('PHONE_DETECTED')
        current.remember([
            dict(id='gaze', type='GAZE_DOWN', epoch=current.engine.state.epoch, duration=6.2, created_at=1791434851),
            dict(id='phone', type='PHONE_DETECTED', epoch=current.engine.state.epoch, created_at=1791435134,
                 thumbnail_path=str(image_path)),
        ])
        pause = LockScreen(current)
        pause.update_state(current.snapshot())
        capture(pause, filename, 1366, 768)
        print(filename, 'body scroll maximum:', pause.body_scroll.verticalScrollBar().maximum())

    calibration = ScreenCalibrationDialog()
    capture(calibration, 'exe-08-screen-calibration', 1366, 768)
    calibration.start_button.click()
    calibration.show_target(0, 'fit', (.5, .5), 0, 3)
    capture(calibration, 'exe-08-screen-target', 1366, 768)
    from agent.exam_browser import ExamBrowser
    with patch.object(ExamBrowser, 'setUrl', lambda *args: None):
        browser = ExamBrowser('https://test.example', lambda reason: None)
    browser.resize(1366, 768)
    browser.show()
    app.processEvents()
    browser.teacher_button.setGraphicsEffect(None)
    capture(browser.teacher_button, 'exe-06-browser-teacher-button', browser.teacher_button.width(), browser.teacher_button.height())
    browser.released = True
    browser.close()
    browser.exam_page.deleteLater()
    app.processEvents()
    for widget in windows:
        widget.hide()
    for current in agents:
        current.http.close()
print(f'Saved desktop UI fixtures to {OUT}')
