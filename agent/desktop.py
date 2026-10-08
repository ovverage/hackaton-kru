"""Classroom tray agent with automatic first-run registration."""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLayout, QScrollArea, QStackedWidget, QVBoxLayout)
from .localized_widgets import (QLabel, QMenu, QLineEdit, QMessageBox, QPushButton, QComboBox, QSystemTrayIcon, QWidget)

from .client import Agent
from .provision import EnrollmentUnavailable, auto_enroll
from .student_state import present
from .theme import APP_QSS as STYLE, COLORS, StepBubble, StepsPanel, ProgressPoint, brand_widget, ui_icon, load_fonts, logo_icon
from shared.bootstrap import default_bootstrap
from .i18n import initialize, language_selector, tr


def label(text="", name="body", wrap=True):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def card():
    widget = QFrame()
    widget.setObjectName("card")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(13)
    return widget, layout


def public_gaze_status_text(gaze, *, active=False, gaze_seconds=0):
    """Keep camera motion feedback separate from a strict gaze decision."""
    import math

    def finite_angle(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        try:
            return float(value) if math.isfinite(value) else None
        except (OverflowError, ValueError):
            return None

    def axis_text(value, positive, negative, axis):
        value = finite_angle(value)
        if value is None:
            return f'{axis} недоступна'
        if abs(value) < .05:
            return f'{axis} 0.0°'
        return f'{positive if value > 0 else negative} {abs(value):.1f}°'

    names = {"SCREEN": "на экран", "CENTER": "на экран", "LEFT": "влево", "RIGHT": "вправо",
             "DOWN": "вниз", "UP": "вверх"}
    observed = gaze.get("gaze_observed_direction", "UNKNOWN")
    feedback_reason = gaze.get('gaze_feedback_reason')
    held = bool(gaze.get('gaze_display_stale') or feedback_reason == 'blink_hold')
    if not held and gaze.get('gaze_tracking_status') not in (None, 'tracked'):
        observed = 'UNKNOWN'
    text = "Взгляд (по изображению камеры): "
    if not gaze.get("reference_ready"):
        text += ("не настроен. Повторите настройку по точкам на своём мониторе."
                 if gaze.get('gaze_decision_policy') == 'adaptive_screen' else
                 "не настроен. Повторно включите камеру и посмотрите в центр экрана.")
    elif observed not in names:
        reason = {
            'blink': 'моргание или закрытые глаза.',
            'eye_state_missing': 'не удалось оценить видимость глаз.',
            'face_landmarks_missing': 'ориентиры лица потеряны.',
            'multiple_faces': 'в кадре несколько лиц.',
            'invalid_crop': 'лицо слишком маленькое или плохо видно.',
        }.get(gaze.get('gaze_tracking_status'), 'направление сейчас не определено.')
        if gaze.get('screen_reason') == 'boundary_margin':
            text += 'у края личной зоны экрана; замечание не начисляется.'
        else:
            text += 'не отслеживается — ' + reason
    else:
        text += names[observed]
        if held:
            text += ' · моргание, показана последняя оценка.'
        elif gaze.get("gaze_observation_uncertain", True):
            if feedback_reason in ('uncertain_observation', 'model_uncertain'):
                text += ' · модель не уверена в направлении; замечание не начисляется.'
            elif feedback_reason in ('strict_unknown', 'strict_mismatch'):
                text += ' · направление оценено, но не подтверждено для замечания.'
            else:
                text += " · оценка неуверенная, замечание не начисляется."
        elif active and gaze.get('direction') in ('DOWN', 'LEFT', 'RIGHT'):
            text += f" · {gaze_seconds:.1f} / 5 с"

    current_gaze = (gaze.get('reference_ready')
                    and gaze.get('gaze_tracking_status') in (None, 'tracked')
                    and feedback_reason not in ('invalid_observation', 'observation_missing', 'reference_missing'))
    if gaze.get('reference_ready') and (held or current_gaze):
        yaw = gaze.get('gaze_display_yaw_degrees', gaze.get('relative_yaw_degrees'))
        pitch = gaze.get('gaze_display_pitch_degrees', gaze.get('relative_pitch_degrees'))
        angle_label = 'предыдущая оценка углов от центра' if held else 'углы от центра'
        text += (f" · {angle_label}: "
                 + axis_text(yaw, 'влево', 'вправо', 'горизонталь') + '; '
                 + axis_text(pitch, 'вверх', 'вниз', 'вертикаль') + '.')
    else:
        text += ' · углы недоступны.'
    threshold = finite_angle(gaze.get('gaze_decision_threshold_degrees'))
    if (gaze.get('gaze_decision_policy') == 'demo_sensitivity'
            and threshold is not None and threshold > 0):
        text += f' · чувствительность: {threshold:g}°.'
    elif gaze.get('gaze_decision_policy') == 'adaptive_screen':
        margin = finite_angle(gaze.get('screen_margin_degrees'))
        if margin is not None:
            text += f' · личная зона экрана + запас {margin:g}°.'

    head_names = dict(names, SCREEN="прямо", CENTER="прямо")
    head_direction = gaze.get("head_direction", "UNKNOWN")
    current_head = (gaze.get('head_reference_ready')
                    and gaze.get('head_tracking_status') in (None, 'tracked'))
    yaw = finite_angle(gaze.get('head_yaw')) if current_head else None
    pitch = finite_angle(gaze.get('head_pitch')) if current_head else None
    if not gaze.get("head_reference_ready"):
        head_text = "не настроено."
    elif not current_head:
        head_text = "не отслеживается — текущий угол неизвестен."
    elif head_direction == 'UNKNOWN' and gaze.get('head_warning'):
        if yaw is not None and pitch is not None:
            if abs(yaw) > abs(pitch):
                turn, magnitude = ('вправо' if yaw > 0 else 'влево'), abs(yaw)
            else:
                turn, magnitude = ('вниз' if pitch > 0 else 'вверх'), abs(pitch)
            head_text = f"небольшой поворот {turn}, {magnitude:.0f}° (по изображению камеры)."
        else:
            head_text = "направление сейчас не определено."
    elif head_direction not in head_names:
        head_text = "не отслеживается — текущий угол неизвестен."
    else:
        prefix = 'сильный поворот ' if gaze.get('head_extreme') else ''
        head_text = prefix + head_names[head_direction] + " (по изображению камеры)."
    if current_head:
        head_text += (' · углы от центра: '
                      + axis_text(yaw, 'вправо', 'влево', 'горизонталь') + '; '
                      + axis_text(pitch, 'вниз', 'вверх', 'вертикаль') + '.')
    else:
        head_text += ' · углы недоступны.'
    text += "\nПоложение головы: " + head_text
    if (gaze.get("interval_ms") or 0) > 750:
        text += "\nКадры поступают редко — таймер отвлечения сброшен."
    return text


def icon():
    return logo_icon()


class EnrollmentWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, folder, *, bootstrap):
        super().__init__()
        self.folder = folder
        self.bootstrap = bootstrap
        self.retryable = False

    def run(self):
        try:
            self.succeeded.emit(auto_enroll(self.folder, self.bootstrap))
        except Exception as error:  # noqa: BLE001 - report worker failures through Qt signals
            self.retryable = isinstance(error, EnrollmentUnavailable)
            self.failed.emit(str(error))


class AgentWorker(QThread):
    failed = Signal(str)

    def __init__(self, agent, stop):
        super().__init__()
        self.agent = agent
        self.stop = stop

    def run(self):
        try:
            self.agent.run(self.stop)
        except Exception:  # noqa: BLE001 - GUI boundary must keep recovery controls accessible
            self.failed.emit(
                "Работа агента прервана. Перезапустите приложение и сообщите преподавателю. История и состояние блокировки сохранены на компьютере."
            )


class OfflineStartWorker(QThread):
    done = Signal(str)

    def __init__(self, agent, target, parent):
        super().__init__(parent)
        self.agent, self.target = agent, dict(target)

    def run(self):
        try:
            self.agent.offline_start(self.target)
            self.done.emit("")
        except (OSError, ValueError) as error:
            self.done.emit(str(error))
        except Exception:
            self.done.emit("Не удалось подготовить выбранную вкладку. Обновите список и повторите.")


class PasswordSetupWorker(QThread):
    done = Signal(str)

    def __init__(self, agent, password, parent, current=""):
        super().__init__(parent)
        self.agent, self.password = agent, password
        self.current = current

    def run(self):
        try:
            if self.current:
                self.agent.change_local_password(self.current, self.password)
            else:
                self.agent.setup_password(self.password)
            self.done.emit("")
        except ValueError as error:
            self.done.emit(str(error))
        except Exception:
            self.done.emit("Не удалось сохранить пароль на этом компьютере. Проверьте доступ к папке настроек и повторите.")
        finally:
            self.password = ""
            self.current = ""


class PrepareStartWorker(QThread):
    done = Signal(str, str)

    def __init__(self, agent, token, parent):
        super().__init__(parent)
        self.agent, self.token = agent, token

    def run(self):
        try:
            self.agent.prepare_start(self.token)
            self.done.emit(self.token, "")
        except (OSError, ValueError) as error:
            self.done.emit(self.token, str(error))
        except Exception:
            self.done.emit(self.token, "Не удалось подготовить выбранную среду. Повторите начало экзамена.")


class StudentWindow(QWidget):
    def __init__(
        self,
        folder: Path,
        server=None,
        agent=None,
        stop=None,
        *,
        run_worker=True,
        bootstrap=None,
    ):
        super().__init__()
        self.folder = folder
        self.bootstrap = bootstrap or default_bootstrap(server)
        self.server_override = server
        self.agent = None
        self.stop = stop or threading.Event()
        self.worker = None
        self.enrollment = None
        self.calibration = None
        self.exam_controller = None
        self.run_worker = run_worker
        self.shutting_down = False
        self.failure = ""
        self.last_notification = None
        self.preparing_start_id = None
        self.offline_target = None
        self.offline_start_worker = None
        self.password_worker = None
        self.environment_worker = None
        self.prepared_camera_token = None
        self.environment_error = None
        self.tray = None
        self.setObjectName("window")
        self.setWindowTitle("Qorgau — агент аудитории")
        self.setWindowIcon(icon())
        screen = QApplication.primaryScreen()
        available_height = screen.availableGeometry().height() - 60 if screen else 640
        self.resize(740, min(900, max(400, available_height)))
        self.setMinimumSize(480, 400)
        self.setStyleSheet(STYLE)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.pages = QStackedWidget()
        root.addWidget(self.pages, 1)
        self.pages.addWidget(self.build_auto_setup())
        self.pages.addWidget(self.build_dashboard())
        self.create_tray()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self.register)
        if agent is not None:
            self.attach(agent)
        elif (folder / "config.json").exists():
            try:
                self.attach(Agent(folder, server))
            except Exception:  # noqa: BLE001 - GUI boundary must keep recovery controls accessible
                self.setup_error.setText(
                    "Не удалось прочитать настройки этого компьютера. Обратитесь к сотруднику аудитории: файл конфигурации требует проверки."
                )
                self.setup_error.show()
                self.connect_button.setEnabled(False)
        else:
            self.retry_timer.start(0)

    def build_auto_setup(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 30, 36, 28)
        layout.setSpacing(22)
        header = QHBoxLayout()
        header.addWidget(brand_widget())
        header.addStretch()
        header.addWidget(language_selector())
        header.addWidget(label("Подключаемся", "badge"))
        layout.addLayout(header)
        layout.addSpacing(12)
        layout.addWidget(label("Подключаем компьютер к аудитории", "title"))
        layout.addWidget(label("Вводить ничего не нужно. Qorgau подключится сам, и преподаватель увидит этот компьютер в кабинете."))
        progress, steps = card()
        for number, state, title, detail in (
            (1, "done", "Сервер найден", "Адрес встроен в приложение"),
            (2, "now", "Регистрируем компьютер", socket.gethostname()),
            (3, "empty", "Откроется подготовка к тесту", "Камера и окно программы"),
        ):
            row = QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(ProgressPoint(state), 0, Qt.AlignmentFlag.AlignTop)
            content = QVBoxLayout()
            content.setSpacing(5)
            content.addWidget(label(title, "heading"))
            content.addWidget(label(detail, "small"))
            row.addLayout(content, 1)
            steps.addLayout(row)
        layout.addWidget(progress)
        layout.addWidget(label("Если связи нет, Qorgau сам повторит попытку через 10 секунд.", "small"))
        self.setup_error = label("", "warning")
        self.setup_error.hide()
        layout.addWidget(self.setup_error)
        self.connect_button = QPushButton("Повторить подключение")
        self.connect_button.setObjectName("primary")
        self.connect_button.setEnabled(False)
        self.connect_button.hide()
        self.connect_button.clicked.connect(self.register)
        layout.addWidget(self.connect_button)
        layout.addStretch()
        return page

    def build_dashboard(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(18)
        row = QHBoxLayout()
        row.addWidget(brand_widget())
        row.addStretch()
        row.addWidget(language_selector())
        self.device_name = label("Компьютер аудитории", "badge")
        self.device_name.setToolTip("Так этот компьютер называется у преподавателя")
        row.addWidget(self.device_name)
        self.connection = label("Подключаемся…", "badge")
        row.addWidget(self.connection)
        layout.addLayout(row)
        self.band = QFrame()
        self.band.setObjectName("band")
        band_layout = QHBoxLayout(self.band)
        band_layout.setContentsMargins(20, 20, 20, 20)
        band_layout.setSpacing(16)
        self.band_icon = QLabel()
        self.band_icon.setFixedSize(40, 40)
        self.band_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.band_icon.setStyleSheet(f"background:{COLORS['navy800']};border-radius:10px;")
        band_layout.addWidget(self.band_icon, 0, Qt.AlignmentFlag.AlignTop)
        band_text = QVBoxLayout()
        self.band_title = label("Подготовка к тесту", "heroTitle")
        self.band_message = label("Выберите камеру и среду теста.", "body")
        band_text.addWidget(self.band_title)
        band_text.addWidget(self.band_message)
        band_layout.addLayout(band_text, 1)
        layout.addWidget(self.band)
        sheet = StepsPanel()
        sheet_layout = QVBoxLayout(sheet)
        sheet_layout.setContentsMargins(0, 0, 0, 0)
        sheet_layout.setSpacing(22)

        camera_row = QHBoxLayout()
        camera_row.setSpacing(16)
        self.camera_step = StepBubble(1, "now")
        camera_row.addWidget(self.camera_step, 0, Qt.AlignmentFlag.AlignTop)
        camera_content = QVBoxLayout()
        camera_content.setSpacing(9)
        camera_heading = QHBoxLayout()
        camera_heading.addWidget(label("Камера", "heading"))
        camera_heading.addStretch()
        self.camera_step_status = label("не включена", "small")
        camera_heading.addWidget(self.camera_step_status)
        camera_content.addLayout(camera_heading)
        camera_content.addWidget(label("Лицо должно быть хорошо видно. Звук не записывается.", "small"))
        self.camera_choice = QComboBox()
        self.camera_choice.setAccessibleName("Камера")
        try:
            from PySide6.QtMultimedia import QMediaDevices
            devices = QMediaDevices.videoInputs()
            for index, device in enumerate(devices):
                self.camera_choice.addItem(device.description(), index)
        except ImportError:
            pass
        if not self.camera_choice.count():
            for index in range(4):
                self.camera_choice.addItem(f"Камера {index + 1}", index)
        camera_controls = QHBoxLayout()
        camera_controls.setSpacing(10)
        self.camera_choice.setMinimumContentsLength(8)
        self.camera_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        camera_controls.addWidget(self.camera_choice, 1)
        self.camera_button = QPushButton("Проверить камеру")
        self.camera_button.setObjectName("primary")
        self.camera_button.clicked.connect(self.prepare_camera)
        camera_controls.addWidget(self.camera_button)
        camera_content.addLayout(camera_controls)
        self.camera_status = label("Выберите камеру. Настройка по точкам начнётся после команды «Начать».", "small")
        self.camera_status.setMinimumHeight(40)
        camera_content.addWidget(self.camera_status)
        self.gaze_status = label("", "small")
        self.gaze_status.setWordWrap(True)
        # Reserve the diagnostic block's full three-line height. Qt can
        # otherwise shave one text-leading unit from a wrapped QLabel after
        # other windows change the application font metrics, clipping the
        # final gaze/head angle line in packaged Linux and Windows builds.
        self.gaze_status.setMinimumHeight(120)
        camera_content.addWidget(self.gaze_status)
        camera_row.addLayout(camera_content, 1)
        sheet_layout.addLayout(camera_row)

        target_row = QHBoxLayout()
        target_row.setSpacing(16)
        self.target_step = StepBubble(2, "skip")
        target_row.addWidget(self.target_step, 0, Qt.AlignmentFlag.AlignTop)
        target_content = QVBoxLayout()
        target_content.setSpacing(9)
        target_heading = QHBoxLayout()
        target_heading.addWidget(label("Окно программы", "heading"))
        target_heading.addStretch()
        self.target_step_status = label("не нужно для сайта", "small")
        target_heading.addWidget(self.target_step_status)
        target_content.addLayout(target_heading)
        self.target_button = QPushButton("Выбрать окно программы")
        self.target_button.clicked.connect(self.select_target)
        target_content.addWidget(self.target_button)
        self.tabs_button = QPushButton("Выбрать вкладку Chrome / Edge")
        self.tabs_button.clicked.connect(self.select_browser_tab)
        target_content.addWidget(self.tabs_button)
        self.target_status = label("Для Qorgau Browser тест откроет преподаватель. Окно выбирайте только для отдельной программы.", "small")
        self.target_status.setMinimumHeight(60)
        target_content.addWidget(self.target_status)
        target_row.addLayout(target_content, 1)
        sheet_layout.addLayout(target_row)

        wait_row = QHBoxLayout()
        wait_row.setSpacing(16)
        self.wait_step = StepBubble(3)
        wait_row.addWidget(self.wait_step, 0, Qt.AlignmentFlag.AlignTop)
        wait_content = QVBoxLayout()
        wait_content.setSpacing(9)
        self.wait_heading = label("Начало теста", "heading")
        wait_content.addWidget(self.wait_heading)
        self.assignment_title = label("", "small")
        self.assignment_title.setMinimumHeight(40)
        wait_content.addWidget(self.assignment_title)
        self.wait_hint = label("Преподаватель назначит тест и запустит его со своего компьютера.", "small")
        wait_content.addWidget(self.wait_hint)
        self.offline_controls = QWidget()
        offline = QVBoxLayout(self.offline_controls)
        offline.setContentsMargins(0, 0, 0, 0)
        self.password_setup = QWidget()
        passwords = QVBoxLayout(self.password_setup)
        passwords.setContentsMargins(0, 0, 0, 0)
        self.password_heading = label("Пароль преподавателя для этого компьютера", "field")
        passwords.addWidget(self.password_heading)
        self.password_demo_hint = label("Демо-пароль преподавателя: admin. Его можно изменить ниже.", "small")
        passwords.addWidget(self.password_demo_hint)
        self.local_password_current = QLineEdit()
        self.local_password_current.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_password_current.setMaxLength(128)
        self.local_password_current.setPlaceholderText("Текущий локальный пароль")
        passwords.addWidget(self.local_password_current)
        self.local_password = QLineEdit()
        self.local_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_password.setMaxLength(128)
        self.local_password.setPlaceholderText("Новый локальный пароль")
        passwords.addWidget(self.local_password)
        self.local_password_repeat = QLineEdit()
        self.local_password_repeat.setEchoMode(QLineEdit.EchoMode.Password)
        self.local_password_repeat.setMaxLength(128)
        self.local_password_repeat.setPlaceholderText("Повторите пароль")
        passwords.addWidget(self.local_password_repeat)
        self.save_password = QPushButton("Сохранить пароль")
        self.save_password.clicked.connect(self.setup_local_password)
        passwords.addWidget(self.save_password)
        self.password_feedback = label("", "small")
        passwords.addWidget(self.password_feedback)
        wait_content.addWidget(label("Смена языка ввода во время теста: Win+Пробел или Alt+Shift.", "small"))
        wait_content.addWidget(self.password_setup)
        self.local_start = QPushButton("Начать экзамен")
        self.local_start.setObjectName("primary")
        self.local_start.clicked.connect(self.start_offline)
        offline.addWidget(self.local_start)
        self.local_end = QPushButton("Завершить экзамен")
        self.local_end.clicked.connect(self.finish_offline)
        offline.addWidget(self.local_end)
        self.offline_feedback = label("", "small")
        offline.addWidget(self.offline_feedback)
        self.offline_controls.hide()
        wait_content.addWidget(self.offline_controls)
        wait_row.addLayout(wait_content, 1)
        sheet_layout.addLayout(wait_row)
        layout.addWidget(sheet)

        self.runtime_error = label("", "error")
        self.runtime_error.hide()
        layout.addWidget(self.runtime_error)
        self.environment_retry = QPushButton("Повторить открытие экзамена")
        self.environment_retry.clicked.connect(self.retry_pending_environment)
        self.environment_retry.hide()
        layout.addWidget(self.environment_retry)
        self.environment_cancel = QPushButton("Отменить начало экзамена")
        self.environment_cancel.clicked.connect(self.cancel_pending_environment)
        self.environment_cancel.hide()
        layout.addWidget(self.environment_cancel)
        self.face_access_note = label("", "small")
        layout.addWidget(self.face_access_note)
        layout.addStretch()
        footer = QHBoxLayout()
        mic = QLabel()
        mic.setPixmap(ui_icon('mic-off', COLORS['muted'], 18).pixmap(18, 18))
        footer.addWidget(mic)
        footer.addWidget(label("Во время теста Qorgau отмечает события и сохраняет короткие видео. Звук не записывается.", "small"), 1)
        layout.addLayout(footer)
        self.dashboard_scroll = QScrollArea()
        self.dashboard_scroll.setObjectName('dashboard-scroll')
        self.dashboard_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.dashboard_scroll.setWidgetResizable(True)
        self.dashboard_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.dashboard_scroll.setWidget(page)
        page.setAutoFillBackground(False)
        return self.dashboard_scroll

    def register(self):
        if self.shutting_down or self.agent:
            return
        if self.enrollment and self.enrollment.isRunning():
            return
        self.retry_timer.stop()
        self.setup_error.hide()
        self.connect_button.setEnabled(False)
        self.connect_button.hide()
        self.connect_button.setText("Подключаем компьютер…")
        self.enrollment = EnrollmentWorker(self.folder, bootstrap=self.bootstrap)
        self.enrollment.failed.connect(self.registration_failed)
        self.enrollment.succeeded.connect(self.registration_succeeded)
        self.enrollment.start()

    def registration_failed(self, message):
        self.setup_error.setText(message)
        self.setup_error.show()
        self.connect_button.setEnabled(True)
        self.connect_button.setText("Повторить подключение")
        self.connect_button.show()
        if self.enrollment and self.enrollment.retryable:
            self.retry_timer.start(10000)

    def registration_succeeded(self, _config):
        self.retry_timer.stop()
        try:
            self.attach(Agent(self.folder))
            if self.tray:
                self.tray.showMessage(
                    "Компьютер подключён",
                    "Главное окно готово. Сеансы назначаются на сайте преподавателя.",
                )
        except Exception:  # noqa: BLE001 - GUI boundary must keep recovery controls accessible
            self.registration_failed(
                "Компьютер зарегистрирован, но агент не запустился. Закройте и снова откройте приложение."
            )

    def attach(self, agent):
        self.agent = agent
        agent.capabilities["interactive_start"] = True
        from .exam_ui import ExamController

        self.exam_controller = ExamController(agent, self)
        if self.run_worker and getattr(agent, "mode", "online") != "offline":
            QTimer.singleShot(0, self.exam_controller.warmup_browser)
        offline = getattr(agent, "mode", "online") == "offline"
        self.offline_controls.setVisible(offline)
        self.tabs_button.setVisible(offline)
        if offline:
            self.wait_heading.setText("Начните экзамен")
            self.wait_hint.setText("Сохраните пароль преподавателя и нажмите «Начать экзамен». Затем пройдите настройку взгляда.")
        self.pages.setCurrentIndex(1)
        self.device_name.setText(agent.config.get("name", "Компьютер аудитории"))
        self.camera_choice.setCurrentIndex(max(0, self.camera_choice.findData(agent.config.get("camera_settings", {}).get("index", 0))))
        if self.run_worker:
            self.worker = AgentWorker(agent, self.stop)
            self.worker.failed.connect(self.agent_failed)
            self.worker.finished.connect(self.worker_finished)
            self.worker.start()
        self.refresh()

    def agent_failed(self, message):
        self.failure = message
        if self.agent:
            self.agent.camera_fault = True
            self.agent.security_event("AGENT_FAILURE")
        self.runtime_error.setText(message)
        self.runtime_error.show()
        self.show_status()
        if self.tray:
            self.tray.showMessage(
                "Агент остановлен", message, QSystemTrayIcon.MessageIcon.Critical
            )

    def worker_finished(self):
        if self.shutting_down:
            self.finish_exit()

    def refresh(self):
        if not self.agent:
            return
        self.check_calibrated_monitor()
        snap = self.agent.snapshot()
        model = present(snap)
        state = snap["state"]
        offline = getattr(self.agent, "mode", "online") == "offline"
        from .exam_ui import teacher_template_access_note
        self.face_access_note.setText(teacher_template_access_note(self.agent))
        self.face_access_note.setVisible(bool(self.face_access_note.text()))
        self.band_title.setText(model["title"])
        self.band_message.setText(model["message"])
        self.band.setProperty("pause", model["locked"])
        self.band.style().unpolish(self.band)
        self.band.style().polish(self.band)
        self.band_icon.setPixmap(ui_icon("pause" if model["locked"] else "check" if state["lifecycle"] == "RUNNING" else "clock", COLORS["surface"]).pixmap(24, 24))
        pending = snap.get("start_pending") or {}
        pending_id = pending.get("id")
        if pending_id and self.preparing_start_id != pending_id and self.calibration is None:
            self.preparing_start_id = pending_id
            QTimer.singleShot(0, lambda token=pending_id: self.begin_pending_start(token))
        elif not pending_id and self.preparing_start_id is not None:
            self.preparing_start_id = None
            if self.calibration is not None:
                self.calibration.reject()
            self.exam_controller.cancel_environment_preparation()
        if not pending_id:
            self.prepared_camera_token = None
            self.environment_error = None
        self.environment_retry.setVisible(bool(pending_id and self.environment_error))
        self.environment_cancel.setVisible(bool(pending_id and self.prepared_camera_token == pending_id))
        self.environment_retry.setEnabled(not (self.environment_worker and self.environment_worker.isRunning()))
        if self.tray:
            status = (
                "Нет связи с сервером" if not snap.get("connected") else model["title"]
            )
            self.tray.setToolTip(f"Qorgau · {snap.get('device_name', '')}\n{status}")
            self.tray_status.setText(tr(f"{snap.get('device_name', self.device_name.text())} — {status}"))
            self.tray_exit.setEnabled(
                state["lifecycle"] != "RUNNING" and not snap.get("camera_preparing")
            )
            marker = (
                snap.get("exam_id"),
                state["lifecycle"],
                state["access"],
                state["lock_id"],
            )
            if marker != self.last_notification:
                if state["access"] == "LOCKED":
                    self.tray.showMessage(
                        "Требуется преподаватель",
                        model["message"],
                        QSystemTrayIcon.MessageIcon.Warning,
                    )
                elif state["lifecycle"] == "RUNNING":
                    self.tray.showMessage(
                        "Контроль начат",
                        "Тест открыт в выбранной преподавателем среде. Qorgau продолжает работу в трее.",
                    )
                self.last_notification = marker
        self.connection.setText(
            "Автономный режим" if offline else "На связи"
            if snap.get("connected") and not self.failure
            else "Нет связи, переподключаемся"
        )
        self.connection.setProperty("tone", "ok" if offline or snap.get("connected") else "warn")
        self.connection.style().unpolish(self.connection)
        self.connection.style().polish(self.connection)
        active = state["lifecycle"] == "RUNNING"
        selected = next((t for t in self.agent.targets if t.get("id") == "primary-window"), None)
        self.camera_step.set_state("done" if snap.get("camera") and not snap.get("camera_fault") else "now")
        self.camera_step_status.setText("недоступна" if snap.get("camera_fault") else "включаем…" if snap.get("camera_preparing") else "работает" if snap.get("camera") else "не включена")
        self.target_step.set_state("done" if selected or self.offline_target else "empty" if offline else "skip")
        self.target_step_status.setText("выбрано" if selected or self.offline_target else "выберите среду" if offline else "не нужно для сайта")
        self.wait_step.set_state("done" if active else "now" if snap.get("exam_id") else "empty")
        self.agent.capabilities["selected_window"] = bool(selected)
        self.target_button.setEnabled(not active and not pending_id)
        self.target_button.setText("Изменить окно" if selected else "Выбрать окно программы")
        target_text = self.offline_target.get("title", self.offline_target.get("name", "Выбрана вкладка")) if self.offline_target else None
        self.target_status.setText(target_text if offline and target_text else selected["name"] if selected else
                                  "Выберите вкладку или открытое приложение." if offline else
                                  "Для Qorgau Browser тест откроет преподаватель. Окно выбирайте только для отдельного приложения.")
        self.camera_choice.setEnabled(not active and not pending_id and not snap.get("camera_preparing"))
        self.camera_button.setEnabled(not active and not pending_id and not self.failure)
        self.camera_button.setText("Сохранить выбор камеры")
        self.camera_status.setText(
            "Камера недоступна — выберите её повторно" if snap.get("camera_fault")
            else "Включаем камеру…" if snap.get("camera_preparing")
            else "Подготовка к началу экзамена…" if pending_id
            else "Камера включена. Ждём преподавателя." if snap.get("camera") and not active
            else model["title"] if active
            else "Настройка по точкам начнётся после команды «Начать»."
        )
        password_ready = bool(snap.get("local_password_ready"))
        self.password_setup.setVisible(offline and not active and not pending_id)
        demo = bool(getattr(getattr(self.agent, 'local_access', None), 'demo_default', False))
        self.password_demo_hint.setVisible(demo)
        self.local_password_current.setVisible(password_ready and not demo)
        saving_password = self.password_worker is not None and self.password_worker.isRunning()
        self.save_password.setEnabled(not saving_password)
        self.local_password.setEnabled(not saving_password)
        self.local_password_repeat.setEnabled(not saving_password)
        if offline:
            self.tabs_button.setEnabled(not active and not pending_id)
            activating = self.offline_start_worker is not None and self.offline_start_worker.isRunning()
            self.local_start.setEnabled(password_ready and bool(self.offline_target or selected) and not active and not pending_id and not activating)
            self.local_end.setEnabled(active)
        session = snap.get("session") or {}
        self.assignment_title.setText(session.get("title", ""))
        self.assignment_title.setVisible(bool(self.assignment_title.text()))
        gaze = snap.get("gaze_diagnostics") or {}
        names = {"SCREEN": "на экран", "LEFT": "влево", "RIGHT": "вправо",
                 "DOWN": "вниз", "UP": "вверх", "UNKNOWN": "не определён"}
        if snap["state"]["lifecycle"] == "COMPLETED":
            gaze_text = "Сеанс завершён. Распознавание выключено."
        elif snap.get("recognition_paused"):
            gaze_text = "Распознавание приостановлено. Продолжение разрешает преподаватель."
        elif not snap.get("camera") or snap.get("camera_fault"):
            gaze_text = ""
        elif gaze.get("source") == "public_gaze_model":
            gaze_text = public_gaze_status_text(
                gaze, active=active, gaze_seconds=snap.get("gaze_seconds", 0),
            )
        elif not gaze.get("reference_ready"):
            gaze_text = "Определяем исходное положение. Посмотрите на экран."
        else:
            gaze_text = "Взгляд: " + names.get(gaze.get("direction"), "не определён")
            if active:
                gaze_text += f" · {snap.get('gaze_seconds', 0):.1f} / 5 с"
            else:
                gaze_text += ". Можно проверить поворот головы до начала сеанса."
            if (gaze.get("interval_ms") or 0) > 750:
                gaze_text += " Кадры поступают редко — таймер отвлечения сброшен."
        self.gaze_status.setText(gaze_text)
        self.gaze_status.setVisible(bool(gaze_text))

    def check_calibrated_monitor(self):
        """Screen geometry/DPI is read only on the GUI thread."""
        camera = getattr(self.agent, 'camera', None)
        signature = getattr(camera, 'screen_signature', None)
        if signature is None or not getattr(camera, 'gaze_enabled', False):
            return
        from .screen_calibration import screen_signature
        if any(screen_signature(screen) == signature for screen in QApplication.screens()):
            return
        camera.request_screen_invalidation('SCREEN_CHANGED')
        with self.agent.mutex:
            self.agent.capabilities['gaze'] = False
            if (self.agent.engine.state.lifecycle == 'RUNNING'
                    and self.agent.engine.state.access == 'OPEN'):
                self.agent.security_event('DISPLAY_CHANGED')

    def open_exam(self):
        if not self.agent or not present(self.agent.snapshot())["can_open"]:
            return
        try:
            self.exam_controller.focus_environment()
        except (ValueError, OSError):
            self.agent.security_event("TARGET_CLOSED")
            self.exam_controller.tick()

    def prepare_camera(self, checked=False, *, calibrate=False):
        if not self.agent or self.agent.snapshot().get("start_pending"):
            return
        with self.agent.mutex:
            if self.agent.engine.state.lifecycle == "RUNNING":
                return
            self.agent.config.setdefault("camera_settings", {})["index"] = self.camera_choice.currentData()
            from .client import atomic_json
            atomic_json(self.folder / "config.json", self.agent.config)
        self.refresh()

    def begin_pending_start(self, token):
        if (self.agent.snapshot().get("start_pending") or {}).get("id") != token:
            return
        from .camera_setup import CameraSetup
        try:
            self.prepared_camera_token = None
            self.environment_error = None
            self.runtime_error.hide()
            self.show_status()
            dialog = CameraSetup(self.agent, self, index=self.camera_choice.currentData(),
                                 screen=self.exam_controller.preparation_screen())
            self.calibration = dialog
            dialog.finished.connect(lambda result: self.pending_camera_finished(token, dialog, result))
            dialog.open()
        except (OSError, ValueError, RuntimeError):
            self.start_preparation_failed(token, "Не удалось начать настройку камеры. Повторите начало экзамена.")

    def pending_camera_finished(self, token, dialog, result):
        from .localized_widgets import (QDialog)
        if self.calibration is dialog:
            self.calibration = None
        dialog.deleteLater()
        if (self.agent.snapshot().get("start_pending") or {}).get("id") != token:
            return
        if result != QDialog.DialogCode.Accepted:
            self.start_preparation_failed(token, "Настройка отменена. Экзамен ещё не начат.")
            return
        self.prepared_camera_token = token
        self.wait_for_start_camera(token, lambda: self.prepare_pending_environment(token))

    def wait_for_start_camera(self, token, on_ready, deadline=None):
        snap = self.agent.snapshot()
        if (snap.get("start_pending") or {}).get("id") != token:
            return
        deadline = time.monotonic() + 8 if deadline is None else deadline
        if snap.get("start_camera_ready"):
            on_ready()
        elif time.monotonic() >= deadline:
            self.start_preparation_failed(token, "Свежие кадры камеры не поступают. Повторите начало экзамена.")
        else:
            QTimer.singleShot(100, lambda: self.wait_for_start_camera(token, on_ready, deadline))

    def prepare_pending_environment(self, token):
        if (self.agent.snapshot().get("start_pending") or {}).get("id") != token:
            return
        if self.environment_worker is not None and self.environment_worker.isRunning():
            QTimer.singleShot(100, lambda: self.prepare_pending_environment(token))
            return
        self.environment_worker = PrepareStartWorker(self.agent, token, self)
        self.environment_worker.done.connect(self.environment_prepared)
        self.environment_worker.start()

    def environment_prepared(self, token, error):
        if (self.agent.snapshot().get("start_pending") or {}).get("id") != token:
            return
        if error:
            self.start_preparation_failed(token, error)
            return
        try:
            self.exam_controller.prepare_environment(
                token, lambda: self.wait_for_start_camera(token, lambda: self.complete_pending_start(token)),
                lambda message: self.start_preparation_failed(token, message),
            )
        except (OSError, ValueError):
            self.start_preparation_failed(token, "Не удалось подготовить выбранную среду. Повторите начало экзамена.")

    def complete_pending_start(self, token):
        try:
            self.agent.complete_start(token)
            self.preparing_start_id = None
            self.refresh()
        except (OSError, ValueError) as error:
            message = {
                'NEED_EXACTLY_ONE_FACE': 'Перед началом в кадре должен остаться один человек. Затем повторите открытие экзамена.',
                'REMOVE_PHONE_BEFORE_START': 'Уберите телефон из кадра и повторите открытие экзамена.',
                'CAMERA_FRAME_STALE': 'Свежие кадры камеры не поступают. Проверьте камеру и повторите открытие.',
                'NEED_1GB_RECORDING_SPACE': 'Для записи нужен минимум 1 ГБ свободного места. Освободите место и повторите открытие.',
                'SCREEN_CALIBRATION_REQUIRED': 'Настройка взгляда больше не соответствует экрану. Повторите начало экзамена и калибровку.',
            }.get(str(error), 'Камера или окно ещё не готовы. Проверьте их и повторите открытие экзамена.')
            self.start_preparation_failed(token, message)

    def start_preparation_failed(self, token, message):
        if (self.agent.snapshot().get("start_pending") or {}).get("id") != token:
            return
        camera = getattr(self.agent, 'camera', None)
        retryable = (self.prepared_camera_token == token and camera is not None
                     and not getattr(camera, 'requires_gaze_reference', False))
        if retryable:
            self.environment_error = message
        else:
            self.agent.cancel_start(token, message)
            self.preparing_start_id = None
        self.exam_controller.cancel_environment_preparation()
        self.runtime_error.setText(message)
        self.runtime_error.show()
        self.show_status()
        self.refresh()
        self.dashboard_scroll.ensureWidgetVisible(self.runtime_error, 0, 40)

    def retry_pending_environment(self):
        token = (self.agent.snapshot().get('start_pending') or {}).get('id')
        if not token or token != self.prepared_camera_token:
            return
        self.environment_error = None
        self.runtime_error.hide()
        self.refresh()
        self.wait_for_start_camera(token, lambda: self.prepare_pending_environment(token))

    def cancel_pending_environment(self):
        token = (self.agent.snapshot().get('start_pending') or {}).get('id')
        if token:
            self.agent.cancel_start(token, "Начало экзамена отменено.")
        self.prepared_camera_token = None
        self.environment_error = None
        self.exam_controller.cancel_environment_preparation()
        self.runtime_error.hide()
        self.show_status()
        self.refresh()

    def setup_local_password(self):
        if not self.agent or getattr(self.agent, 'mode', 'online') != 'offline':
            return
        if self.password_worker is not None and self.password_worker.isRunning():
            return
        password, repeat = self.local_password.text(), self.local_password_repeat.text()
        current = self.local_password_current.text()
        self.local_password_current.clear()
        self.local_password.clear()
        self.local_password_repeat.clear()
        if password != repeat:
            self.password_feedback.setText("Пароли не совпали. Введите их ещё раз.")
            return
        self.password_feedback.setText("Сохраняем локальный пароль преподавателя…")
        self.password_worker = PasswordSetupWorker(self.agent, password, self, current=current)
        self.password_worker.done.connect(self.password_setup_done)
        self.password_worker.finished.connect(self.refresh)
        self.password_worker.start()
        password = repeat = ""
        self.refresh()

    def password_setup_done(self, message):
        self.password_feedback.setText(message or "Пароль преподавателя сохранён на этом компьютере.")
        self.refresh()

    def select_browser_tab(self):
        from .exam_ui import BrowserTabPicker
        picker = BrowserTabPicker(self.agent, self)
        if picker.exec() and picker.selected:
            self.offline_target = picker.selected
            self.refresh()

    def start_offline(self):
        selected = self.offline_target or next((t for t in self.agent.targets if t.get("id") == "primary-window"), None)
        if selected is None:
            return
        if self.offline_start_worker is not None and self.offline_start_worker.isRunning():
            return
        self.offline_feedback.setText("Активируем выбранное окно…")
        self.offline_start_worker = OfflineStartWorker(self.agent, selected, self)
        self.offline_start_worker.done.connect(self.offline_start_done)
        self.offline_start_worker.finished.connect(self.refresh)
        self.offline_start_worker.start()
        self.refresh()

    def offline_start_done(self, message):
        self.offline_feedback.setText(message)
        self.refresh()

    def finish_offline(self):
        self.agent.security_event("TEACHER_REQUEST")
        self.exam_controller.tick()

    def select_target(self):
        if not self.agent or self.agent.engine.state.lifecycle == "RUNNING":
            return
        from .exam_ui import TargetPicker

        picker = TargetPicker(self.agent, self)
        if picker.exec() and picker.selected:
            with self.agent.mutex:
                self.agent.targets = [
                    t
                    for t in self.agent.targets
                    if t["id"] not in ("primary-window", "primary-app")
                ]
                self.agent.targets.append(picker.selected)
                self.offline_target = picker.selected
                self.agent.capabilities["selected_window"] = True
                self.agent.config["selected_target"] = picker.selected
                from .client import atomic_json
                atomic_json(self.folder / "config.json", self.agent.config)
            self.refresh()

    def create_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icon(), self)
        self.tray_menu = QMenu(self)
        self.tray_menu.setStyleSheet(STYLE)
        self.tray_status = self.tray_menu.addAction("Qorgau · подключение")
        self.tray_status.setEnabled(False)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Открыть Qorgau", self.show_status)
        self.tray_menu.addSeparator()
        self.tray_exit = self.tray_menu.addAction("Выйти из Qorgau", self.request_exit)
        self.tray_exit.setToolTip("Можно после окончания теста")
        self.tray_menu.setToolTipsVisible(True)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.messageClicked.connect(self.show_status)
        self.tray.show()

    def tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_status()

    @Slot()
    def show_status(self):
        if self.agent and self.agent.engine.state.access == "LOCKED" and self.exam_controller:
            self.exam_controller.tick()
            return
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    def start_visibility(self, show_window=False):
        self.showFullScreen()
        self.raise_()
        self.activateWindow()

    @Slot()
    def request_exit(self):
        if self.shutting_down:
            return
        if self.enrollment and self.enrollment.isRunning():
            return
        if any(worker and worker.isRunning() for worker in (self.password_worker, self.offline_start_worker, self.environment_worker)):
            return
        if self.agent:
            with self.agent.mutex:
                if (
                    self.agent.engine.state.lifecycle == "RUNNING"
                    or self.agent.camera_preparing
                ):
                    QMessageBox.information(
                        self,
                        "Контроль продолжается",
                        "Сначала завершите сеанс на сайте преподавателя. Окно можно свернуть, агент продолжит работу.",
                    )
                    return
                self.shutting_down = True
                self.stop.set()
        else:
            self.shutting_down = True
            self.stop.set()
        self.setEnabled(False)
        if self.tray:
            self.tray_menu.setEnabled(False)
        if not self.worker or not self.worker.isRunning():
            self.finish_exit()

    def finish_exit(self):
        self.timer.stop()
        self.retry_timer.stop()
        if self.tray:
            self.tray.hide()
            # The tray does not own its context menu. Detach it while both
            # QObjects are valid, before parent/GC destruction can reorder them.
            self.tray.setContextMenu(None)
            self.tray_menu.close()
            if not getattr(self, '_tray_disconnected', False):
                self.tray.activated.disconnect(self.tray_activated)
                self.tray.messageClicked.disconnect(self.show_status)
                self._tray_disconnected = True
        self.hide()
        self.deleteLater()
        if self.run_worker:
            QApplication.instance().quit()

    def closeEvent(self, event):
        if self.enrollment and self.enrollment.isRunning():
            event.ignore()
            return
        if self.shutting_down:
            event.ignore()
            return
        if self.agent:
            event.ignore()
            if self.tray:
                self.hide()
            else:
                # Without a tray, keep an accessible window instead of an invisible process.
                self.showMinimized()
            return
        event.accept()
        self.request_exit()


def launch(
    folder: Path,
    server=None,
    agent=None,
    stop=None,
    *,
    show_window=False,
    bootstrap=None,
):
    from .student_entry import configure_webengine
    configure_webengine()
    from .install_guard import hold_installation_mutex
    hold_installation_mutex()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    initialize()
    app.setApplicationName("Qorgau Agent")
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationDisplayName(tr("Qorgau — агент аудитории"))
    app.setFont(QFont(load_fonts(), 10))
    app.setStyle("Fusion")
    folder.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(folder / "desktop.lock"))
    if not lock.tryLock(0):
        QMessageBox.information(
            None,
            "Qorgau уже запущен",
            "Агент уже работает. Нажмите значок Qorgau в трее, чтобы открыть состояние компьютера.",
        )
        return
    window = StudentWindow(folder, server, agent, stop, bootstrap=bootstrap)
    window.start_visibility(show_window)
    try:
        app.exec()
    finally:
        lock.unlock()


def show(agent, stop):
    launch(agent.folder, agent.server, agent, stop)
