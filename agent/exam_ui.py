"""Window selection, evidence screen and GUI-thread Windows guard lifecycle."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, QSize, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QBoxLayout,
    QFrame,
    QStyledItemDelegate,
    QStyle,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from .student_state import REASONS
from .theme import APP_QSS, COLORS, CountdownRing, CameraEvidence, brand_widget, ui_icon, app_font


def text(value, size=14):
    widget = QLabel(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setStyleSheet(f"font-size:{size}px;")
    return widget


def teacher_template_access_note(agent):
    """Show the server device identity, never its enrollment token."""
    config = getattr(agent, 'config', {})
    if getattr(agent, 'mode', 'online') == 'offline':
        from .profile import read_object
        folder = getattr(agent, 'folder', None)
        config = read_object(Path(folder) / 'face-connection' / 'config.json') if folder else {}
    prefix = 'Исключение преподавателей из подсчёта лиц: доступ разрешается в кабинете → «Преподаватели».'
    if config.get('device_id'):
        return f"{prefix}\n{config.get('name', 'Компьютер')} · ID: {config['device_id']}"
    return prefix + ' ID подключения появится после начала локального экзамена при наличии связи.'


def gaze_warning_active(snap):
    """Show fresh observed motion; this UI nudge does not decide rule strikes."""
    gaze = snap.get("gaze_diagnostics") or {}
    if gaze.get("source") == "public_gaze_model":
        away = (
            gaze.get("gaze_tracking_status") == "tracked"
            and not gaze.get("gaze_display_stale")
            and gaze.get("gaze_observed_direction") in ("DOWN", "LEFT", "RIGHT", "UP")
        )
    else:
        away = (
            gaze.get("attention_away")
            or gaze.get("direction") in ("DOWN", "LEFT", "RIGHT", "UP")
        )
    return bool(
        snap["state"]["lifecycle"] == "RUNNING"
        and snap["state"]["access"] == "OPEN"
        and snap.get("camera")
        and not snap.get("camera_fault")
        and not snap.get("recognition_paused")
        and gaze.get("reference_ready")
        and away
    )


def gaze_warning_text(snap):
    """Name the displayed camera-image direction without implying a penalty."""
    gaze = snap.get("gaze_diagnostics") or {}
    instruction = "Смотрите на экран"
    if gaze.get("source") != "public_gaze_model":
        return instruction
    direction = {
        "LEFT": "влево", "RIGHT": "вправо", "UP": "вверх", "DOWN": "вниз",
    }.get(gaze.get("gaze_observed_direction"))
    if direction is None:
        return instruction
    prefix = "Предварительная оценка: взгляд" if gaze.get("gaze_observation_uncertain", True) else "Взгляд"
    return f"{prefix} {direction} (по изображению камеры)\n{instruction}"


def head_warning_active(snap):
    gaze = snap.get("gaze_diagnostics") or {}
    return bool(
        snap["state"]["lifecycle"] == "RUNNING"
        and snap["state"]["access"] == "OPEN"
        and snap.get("camera") and not snap.get("camera_fault")
        and not snap.get("recognition_paused")
        and gaze.get("head_reference_ready")
        and gaze.get("head_tracking_status") == "tracked"
        and (gaze.get("head_warning") or gaze.get("head_extreme"))
    )


def head_warning_text(snap):
    gaze = snap.get("gaze_diagnostics") or {}
    direction = {"LEFT": "влево", "RIGHT": "вправо", "UP": "вверх", "DOWN": "вниз"}.get(
        gaze.get("head_direction"), "в сторону"
    )
    prefix = "Сильный поворот головы" if gaze.get("head_extreme") else "Поворот головы"
    return f"{prefix} {direction} (по изображению камеры)\nПовернитесь к монитору"


class WarningDisplayHold:
    """One-second UI persistence; observations and rule timers are never changed."""

    def __init__(self):
        self.parts = {}

    def update(self, snap, now):
        if (snap["state"]["lifecycle"] != "RUNNING"
                or snap["state"]["access"] != "OPEN"
                or not snap.get("camera") or snap.get("camera_fault")
                or snap.get("recognition_paused")):
            self.parts.clear()
            return ""
        if gaze_warning_active(snap):
            self.parts['gaze'] = (gaze_warning_text(snap), now + 1.0)
        if head_warning_active(snap):
            self.parts['head'] = (head_warning_text(snap), now + 1.0)
        return "\n".join(self.parts[kind][0] for kind in ('gaze', 'head')
                         if kind in self.parts and now < self.parts[kind][1])


def calibrated_exam_screen(camera, screens):
    """Resolve the actual calibrated QScreen, including its geometry and DPI."""
    from .screen_calibration import screen_signature
    signature = getattr(camera, 'screen_signature', None)
    if signature is None:
        return None
    return next((screen for screen in screens if screen_signature(screen) == signature), None)


def place_exam_browser(browser, screen):
    """Choose the calibrated display before the native browser becomes visible."""
    if screen is not None:
        browser.winId()
        browser.windowHandle().setScreen(screen)
        browser.setGeometry(screen.geometry())
    browser.showFullScreen()


def calibration_overlay_handles(surfaces):
    """Both setup and its fullscreen target dialog are permitted while locked."""
    handles = []
    for surface in surfaces:
        calibration = surface.calibration
        if calibration is not None and calibration.isVisible():
            handles.append(int(calibration.winId()))
            targets = getattr(calibration, 'screen_dialog', None)
            if targets is not None and targets.isVisible():
                handles.append(int(targets.winId()))
    return handles


def raise_calibration_overlays(surfaces):
    """Keep target dots above every lock surface without activating a window."""
    target_dialogs = []
    for surface in surfaces:
        calibration = surface.calibration
        if calibration is not None and calibration.isVisible():
            calibration.raise_()
            targets = getattr(calibration, 'screen_dialog', None)
            if targets is not None and targets.isVisible():
                target_dialogs.append(targets)
    for targets in target_dialogs:
        targets.raise_()


class GazeWarning(QWidget):
    """Click-through topmost banner shown without taking focus from the exam."""

    def __init__(self):
        super().__init__()
        self.setObjectName("gazeWarning")
        self.setWindowTitle("Qorgau — смотрите на экран")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            f"QWidget#gazeWarning {{background:{COLORS['navy900']};border-radius:18px;}}"
            f"QLabel {{background:transparent;color:{COLORS['surface']};font-family:'Geologica','Segoe UI';}}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)
        self.direction = QLabel("↓")
        self.direction.setFixedSize(54, 54)
        self.direction.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.direction.setStyleSheet(f"background:{COLORS['amber_fill']};color:{COLORS['navy900']};border-radius:14px;font-size:34px;")
        layout.addWidget(self.direction, 0, Qt.AlignmentFlag.AlignTop)
        copy = QVBoxLayout()
        copy.setSpacing(6)
        self.message = QLabel("Смотрите на экран")
        self.message.setWordWrap(True)
        self.message.setStyleSheet("font-size:24px;font-weight:600;")
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        copy.addWidget(self.message)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(f"font-size:13px;color:{COLORS['side_text']};")
        copy.addWidget(self.detail)
        layout.addLayout(copy, 1)
        self.countdown = CountdownRing()
        layout.addWidget(self.countdown, 0, Qt.AlignmentFlag.AlignVCenter)

    def update_countdown(self, snap):
        gaze = snap.get("gaze_diagnostics") or {}
        direction = snap.get("attention_direction") or gaze.get("attention_direction") or gaze.get("direction")
        observed = gaze.get("gaze_observed_direction") or gaze.get("head_direction") or direction
        self.direction.setText({"DOWN": "↓", "LEFT": "←", "RIGHT": "→", "UP": "◉"}.get(observed, "◉"))
        seconds = max(0.0, float(snap.get("gaze_seconds") or 0))
        # Only a confirmed rule timer can promise a future mark. Public model
        # uncertainty and independent head feedback retain their honest copy.
        counting = direction in ("DOWN", "LEFT", "RIGHT") and 0 < seconds < self.countdown.threshold
        remaining = max(0.0, self.countdown.threshold - seconds) if counting else None
        self.countdown.remaining = remaining
        self.countdown.update()
        if counting:
            word = {"DOWN": "вниз", "LEFT": "влево", "RIGHT": "вправо"}[direction]
            self.detail.setText(f"Взгляд {word} засчитается как отметка через {remaining:.1f} с".replace('.', ','))
        else:
            self.detail.setText("Посмотрите на экран и продолжайте тест")

    def place(self, screen):
        geometry = screen.geometry()
        width = min(660, max(360, geometry.width() - 48))
        self.ensurePolished()
        self.message.ensurePolished()
        text_width = max(100, width - 180)
        height = max(106, self.message.heightForWidth(text_width) + self.detail.heightForWidth(text_width) + 44)
        self.setGeometry(geometry.x() + (geometry.width() - width) // 2,
                         geometry.y() + 28, width, height)


class UnlockWorker(QThread):
    done = Signal(str)

    def __init__(self, agent, password, parent, action="UNLOCK"):
        super().__init__(parent)
        self.agent, self.password = agent, password
        self.action = action

    def run(self):
        try:
            self.agent.teacher_unlock(self.password, self.action)
            self.done.emit("")
        except Exception as error:  # noqa: BLE001 - never let network failures dismiss the lock screen
            # Never expose the credential or HTTP request body.
            self.done.emit(
                str(error)
                if isinstance(error, ValueError)
                else "Не удалось проверить локальный пароль. Повторите попытку."
                if getattr(self.agent, 'mode', 'online') == 'offline'
                else "Нет связи с сервером. Для онлайн-теста восстановите связь и повторите проверку пароля."
            )
        finally:
            self.password = ""


class FaceUnlockWorker(QThread):
    done = Signal(str)
    progress = Signal(str)

    def __init__(self, agent, parent):
        super().__init__(parent)
        self.agent = agent

    def run(self):
        try:
            self.agent.teacher_face_unlock(progress=self.progress.emit)
            self.done.emit("")
        except ValueError as error:
            self.done.emit(str(error))
        except Exception:
            self.done.emit("Не удалось проверить лицо преподавателя. Используйте пароль.")


class LockScreen(QWidget):
    def __init__(self, agent):
        super().__init__()
        self.agent = agent
        self.worker = None
        self.calibration = None
        self.marker = None
        self.setWindowTitle("Qorgau — Позовите преподавателя")
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setObjectName("pauseScreen")
        self.setStyleSheet(APP_QSS + f"""
            QWidget#pauseScreen {{background:{COLORS['surface']};}}
            QFrame#pauseHeader {{background:{COLORS['red_fill']};}}
            QFrame#pauseHeader QLabel {{color:{COLORS['surface']};}}
            QFrame#teacherForm {{background:{COLORS['surface']};border:1px solid {COLORS['line']};border-radius:14px;}}
            QLineEdit {{font-size:13px;}}
            QPushButton {{font-size:13px;}}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame()
        header.setObjectName('pauseHeader')
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(56, 25, 56, 28)
        header_layout.setSpacing(17)
        brand = QHBoxLayout()
        brand.addWidget(brand_widget(paused=True))
        brand.addStretch()
        self.device_time = text("", 16)
        brand.addWidget(self.device_time)
        header_layout.addLayout(brand)
        header_layout.addWidget(text("Тест на паузе", 24))
        self.heading = text("Позовите преподавателя", 72)
        self.heading.setStyleSheet("font-size:72px;font-weight:600;")
        header_layout.addWidget(self.heading)
        root.addWidget(header)
        self.body_scroll = QScrollArea()
        self.body_scroll.setWidgetResizable(True)
        self.body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("pauseBody")
        body.setStyleSheet(f"QWidget#pauseBody {{background:{COLORS['surface']};}}")
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, body)
        self.columns.setContentsMargins(56, 25, 56, 20)
        self.columns.setSpacing(48)
        main_widget = QWidget()
        main = QVBoxLayout(main_widget)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(10)
        self.reason = text("Тест на паузе", 24)
        main.addWidget(self.reason)
        main.addWidget(text("Преподаватель посмотрит запись и решит, продолжать ли тест. Окно теста остаётся открытым под этим экраном.", 15))
        main.addWidget(text("Вернуть взгляд или перезапустить Qorgau не поможет: продолжить тест может только преподаватель.", 12))
        self.online_notice = text("Преподаватель уже видит это у себя в кабинете", 12)
        self.online_notice.setObjectName('badge')
        self.online_notice.setVisible(getattr(agent, 'mode', 'online') != 'offline')
        main.addWidget(self.online_notice)
        self.form = QFrame()
        self.form.setObjectName('teacherForm')
        form = QVBoxLayout(self.form)
        form.setContentsMargins(16, 12, 16, 12)
        form.setSpacing(7)
        form.addWidget(text("Для преподавателя", 16))
        credentials = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(128)
        self.password.setPlaceholderText("Локальный пароль преподавателя" if getattr(agent, 'mode', 'online') == 'offline'
                                         else "Пароль кабинета преподавателя")
        self.password.setAccessibleName("Пароль преподавателя")
        credentials.addWidget(self.password, 1)
        self.unlock = QPushButton("Продолжить тест")
        self.unlock.setObjectName("primary")
        self.unlock.clicked.connect(self.request_unlock)
        self.password.returnPressed.connect(self.request_unlock)
        credentials.addWidget(self.unlock)
        form.addLayout(credentials)
        self.face_unlock = QPushButton("Продолжить по лицу преподавателя")
        self.face_unlock.clicked.connect(self.request_face_unlock)
        form.addWidget(self.face_unlock)
        self.face_access_note = text(teacher_template_access_note(agent), 11)
        form.addWidget(self.face_access_note)
        self.finish = QPushButton("Завершить контроль на этом компьютере")
        self.finish.setObjectName('danger')
        self.finish.clicked.connect(lambda: self.request_unlock("END_AND_RELEASE"))
        form.addWidget(self.finish)
        self.recover = QPushButton("Восстановить камеру")
        self.recover.clicked.connect(self.recover_camera)
        form.addWidget(self.recover)
        self.feedback = text("", 12)
        self.feedback.setObjectName('error')
        form.addWidget(self.feedback)
        main.addWidget(self.form)
        main.addStretch()
        self.columns.addWidget(main_widget, 3)
        self.evidence = QWidget()
        self.evidence_layout = QVBoxLayout(self.evidence)
        self.evidence_layout.setContentsMargins(0, 0, 0, 0)
        self.evidence_layout.setSpacing(12)
        self.columns.addWidget(self.evidence, 2)
        self.body_scroll.setWidget(body)
        root.addWidget(self.body_scroll, 1)
        footer = text("Событие — повод посмотреть запись. Решение о нарушении принимает преподаватель.", 12)
        footer.setContentsMargins(56, 0, 56, 18)
        root.addWidget(footer)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 1000
        self.columns.setDirection(QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight)
        self.heading.setStyleSheet(f"font-size:{36 if narrow else 72}px;font-weight:600;")

    def update_state(self, snap):
        self.face_access_note.setText(teacher_template_access_note(self.agent))
        self.face_access_note.setVisible(bool(self.face_access_note.text()))
        self.feedback.setVisible(bool(self.feedback.text()))
        locked = snap["state"]["access"] == "LOCKED"
        self.heading.setText("Позовите преподавателя" if locked else "Идёт экзамен")
        events = [e for e in snap.get("recent_events", []) if e.get("epoch") == snap["state"]["epoch"]]
        trigger = next((e for e in reversed(events) if e.get('type') == snap['state'].get('reason')), None)
        timestamp = trigger.get('created_at') if trigger else None
        happened = time.strftime('%H:%M', time.localtime(timestamp)) if isinstance(timestamp, (int, float)) else ''
        reason = REASONS.get(snap["state"].get("reason"), "Работайте в выбранном окне теста.").rstrip('.')
        self.reason.setText(reason + (f" в {happened}" if happened else ''))
        self.device_time.setText(snap.get('device_name') or getattr(self.agent, 'config', {}).get('name', 'Компьютер'))
        if happened:
            self.device_time.setText(self.device_time.text() + ', ' + happened)
        self.form.setVisible(locked)
        self.face_unlock.setEnabled(not snap.get("teacher_face_scan") and not (self.worker and self.worker.isRunning()))
        self.recover.setVisible(locked and snap["state"].get("reason") in (
            "AGENT_RESTARTED", "CAMERA_UNAVAILABLE", "CAMERA_FROZEN", "DISPLAY_CHANGED"
        ))
        marker = (snap["state"]["lock_id"], snap["state"]["epoch"],
                  tuple((e["id"], e.get("thumbnail_path"), e.get("end")) for e in events))
        if marker == self.marker:
            return
        self.marker = marker
        while self.evidence_layout.count():
            item = self.evidence_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.evidence_layout.addWidget(text("Что заметила камера", 18))
        thumbnail = next((e for e in reversed(events) if e.get('thumbnail_path')), None)
        if thumbnail:
            picture = QPixmap(thumbnail['thumbnail_path'])
            if not picture.isNull():
                preview = CameraEvidence()
                preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
                preview.setStyleSheet(f"background:{COLORS['scene']};border-radius:12px;")
                preview.setPixmap(picture.scaled(420, 236, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                preview.setMaximumHeight(236)
                self.evidence_layout.addWidget(preview)
                caption = REASONS.get(thumbnail['type'], thumbnail['type'])
                if isinstance(thumbnail.get('created_at'), (int, float)):
                    caption += ' — ' + time.strftime('%H:%M:%S', time.localtime(thumbnail['created_at']))
                self.evidence_layout.addWidget(text(caption, 12))
        other_events = [e for e in events if e is not thumbnail]
        for event in other_events[-5:][::-1]:
            happened = time.strftime("%H:%M:%S", time.localtime(event['created_at'])) if isinstance(event.get('created_at'), (int, float)) else ''
            caption = {
                "GAZE_DOWN": "↓ Взгляд вниз", "GAZE_LEFT": "← Взгляд влево",
                "GAZE_RIGHT": "→ Взгляд вправо", "HEAD_TURN_REVIEW": "Поворот головы — ждёт решения",
            }.get(event["type"], REASONS.get(event['type'], event['type']))
            if event.get('duration'):
                caption += f", {event['duration']:.1f} с"
            self.evidence_layout.addWidget(text(caption, 14))
            if happened:
                self.evidence_layout.addWidget(text(happened, 12))
        if not events:
            self.evidence_layout.addWidget(text("Причина указана слева. История и видео доступны в кабинете преподавателя."))
        self.evidence_layout.addStretch()

    def recover_camera(self):
        from .camera_setup import CameraSetup
        if self.calibration and self.calibration.isVisible():
            self.calibration.raise_()
            return
        self.calibration = CameraSetup(self.agent, self)
        self.calibration.show()

    def request_unlock(self, action="UNLOCK"):
        # QPushButton.clicked supplies a boolean when connected directly.
        if not isinstance(action, str):
            action = "UNLOCK"
        if self.worker and self.worker.isRunning():
            return
        password = self.password.text()
        self.password.clear()
        if not password:
            self.feedback.setText("Введите пароль преподавателя")
            return
        self.unlock.setEnabled(False)
        self.face_unlock.setEnabled(False)
        self.finish.setEnabled(False)
        self.feedback.setText("Проверяем пароль и актуальную блокировку…")
        self.worker = UnlockWorker(self.agent, password, self, action)
        self.worker.done.connect(self.unlock_done)
        self.worker.start()

    def request_face_unlock(self):
        if self.worker and self.worker.isRunning():
            return
        self.password.clear()
        self.unlock.setEnabled(False)
        self.face_unlock.setEnabled(False)
        self.finish.setEnabled(False)
        self.feedback.setText("Преподаватель: посмотрите в камеру и следуйте подсказкам проверки лица.")
        self.worker = FaceUnlockWorker(self.agent, self)
        self.worker.progress.connect(self.feedback.setText)
        self.worker.done.connect(self.unlock_done)
        self.worker.start()

    def unlock_done(self, error):
        self.unlock.setEnabled(True)
        self.face_unlock.setEnabled(True)
        self.finish.setEnabled(True)
        self.feedback.setText(error or "Преподаватель разрешил продолжить")

    def closeEvent(self, event):
        event.ignore()


class PickerDelegate(QStyledItemDelegate):
    """Native list selection with a status point, icon tile and two text lines."""

    def sizeHint(self, option, index):
        return QSize(300, 82)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        muted = bool(index.data(Qt.ItemDataRole.UserRole + 1))
        rect = option.rect
        painter.fillRect(rect, QColor(COLORS['blue_tint'] if selected else COLORS['surface']))
        painter.setPen(QPen(QColor(COLORS['blue'] if selected else COLORS['line']), 1.5))
        painter.setBrush(QColor(COLORS['navy800'] if selected else COLORS['surface']))
        painter.drawEllipse(QRectF(rect.x()+16, rect.center().y()-8, 16, 16))
        tile = QRectF(rect.x()+46, rect.center().y()-18, 36, 36)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS['surface2'] if muted else COLORS['blue_tint']))
        painter.drawRoundedRect(tile, 9, 9)
        ui_icon(index.data(Qt.ItemDataRole.UserRole) or 'window', COLORS['muted'] if muted else COLORS['navy800']).paint(painter, int(tile.x()+8), int(tile.y()+8), 20, 20)
        title, _, detail = str(index.data() or '').partition('\n')
        painter.setPen(QColor(COLORS['muted'] if muted else COLORS['text']))
        painter.setFont(app_font(14, QFont.Weight.Medium))
        title_width = max(40, rect.width()-110-(85 if selected else 0))
        painter.drawText(rect.x()+96, rect.y()+31, painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, title_width))
        painter.setPen(QColor(COLORS['muted']))
        painter.setFont(app_font(12))
        painter.drawText(rect.x()+96, rect.y()+54, painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, max(40, rect.width()-110)))
        if selected:
            painter.setPen(QColor(COLORS['blue']))
            painter.drawText(rect.right()-88, rect.y()+31, '✓ выбрано')
        painter.setPen(QColor(COLORS['line_soft']))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()


class TargetPicker(QDialog):
    def __init__(self, agent, parent):
        super().__init__(parent)
        self.agent = agent
        self.setWindowTitle("Выберите главное окно или приложение")
        self.resize(740, 450)
        self.setObjectName("window")
        self.setStyleSheet(APP_QSS)
        self.selected = None
        self.windows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(text("Выберите окно программы", 28))
        layout.addWidget(
            text(
                "Откройте программу тестирования и выберите её окно. Для сайта преподаватель откроет Qorgau Browser.",
                14,
            )
        )
        watched = QFrame()
        watched.setObjectName("card")
        watched_layout = QVBoxLayout(watched)
        watched_layout.setContentsMargins(22, 22, 22, 22)
        self.items = QListWidget()
        self.items.setItemDelegate(PickerDelegate(self.items))
        self.items.setStyleSheet(f"QListWidget{{border:0;background:{COLORS['sheet']};}} QListWidget::item{{padding:12px;border-bottom:1px solid {COLORS['rule_soft']};}} QListWidget::item:selected{{background:{COLORS['ink_tint']};color:{COLORS['ink_strong']};}}")
        watched_layout.addWidget(self.items)
        layout.addWidget(watched, 1)
        self.feedback = text("", 12)
        layout.addWidget(self.feedback)
        row = QHBoxLayout()
        refresh = QPushButton("Обновить список")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        row.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        use = QPushButton("Выбрать это окно")
        use.setObjectName("primary")
        use.clicked.connect(self.choose_window)
        row.addWidget(use)
        layout.addLayout(row)
        layout.addWidget(
            text(
                "Выбор закрепляется за конкретным окном. Новое окно того же приложения автоматически не разрешается.",
                12,
            )
        )
        self.refresh()

    def refresh(self):
        self.items.clear()
        if os.name != "nt":
            self.items.addItem(
                "Выбор открытого окна доступен на Windows; можно добавить приложение."
            )
            return
        from .windows_guard import WindowsGuard

        self.windows = WindowsGuard().windows()
        for window in self.windows:
            browser = Path(window.executable).name.lower() in {"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe"}
            detail = "Браузер не подходит: сайт откроет Qorgau Browser" if browser else f"Программа, {Path(window.executable).name}"
            self.items.addItem(f"{window.title}\n{detail}")
            item = self.items.item(self.items.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, 'globe' if browser else 'window')
            item.setData(Qt.ItemDataRole.UserRole + 1, browser)

    def choose_window(self):
        row = self.items.currentRow()
        if 0 <= row < len(self.windows):
            w = self.windows[row]
            browser = Path(w.executable).name.lower() in {
                "chrome.exe",
                "msedge.exe",
                "firefox.exe",
                "opera.exe",
                "brave.exe",
            }
            if browser and getattr(self.agent, "mode", "online") == "offline":
                self.feedback.setText("Для Chrome / Edge выберите отдельную вкладку кнопкой «Выбрать вкладку» в главном окне.")
                return
            self.selected = {
                "id": "primary-window",
                "name": "Главное окно: " + w.title[:100],
                "kind": "APP",
                "executable": w.executable,
                "window": w.public(),
                "guardable": not browser,
            }
            self.accept()

    def choose_executable(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Приложение тестирования",
            "",
            "Приложение (*.exe)" if os.name == "nt" else "Все файлы (*)",
        )
        if path:
            browser = Path(path).name.lower() in {
                "chrome.exe",
                "msedge.exe",
                "firefox.exe",
                "opera.exe",
                "brave.exe",
            }
            self.selected = {
                "id": "primary-app",
                "name": Path(path).stem,
                "kind": "APP",
                "executable": str(Path(path).resolve()),
                "guardable": not browser,
            }
            self.accept()


class BrowserTabPicker(QDialog):
    """Use the extension's actual tab roster; window titles never impersonate tabs."""

    def __init__(self, agent, parent):
        super().__init__(parent)
        self.agent, self.selected, self.tabs = agent, None, []
        self.setWindowTitle("Выберите вкладку Chrome / Edge")
        self.setObjectName("window")
        self.setStyleSheet(APP_QSS)
        self.resize(780, 480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(text("Выберите вкладку Chrome / Edge", 26))
        layout.addWidget(text("Выберите отдельную вкладку из подключённого браузера.", 14))
        self.items = QListWidget()
        self.items.setItemDelegate(PickerDelegate(self.items))
        layout.addWidget(self.items)
        self.status = text("", 12)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        refresh = QPushButton("Обновить вкладки")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        buttons.addStretch()
        select = QPushButton("Использовать вкладку")
        select.setObjectName("primary")
        select.clicked.connect(self.choose)
        buttons.addWidget(select)
        layout.addLayout(buttons)
        self.extension_help = text(
            "Для списка вкладок требуется расширение Qorgau и привязка браузера к приложению. "
            "Откройте chrome://extensions или edge://extensions, включите режим разработчика "
            "и загрузите папку extension из комплекта Qorgau.", 12,
        )
        self.extension_help.setObjectName("notice")
        layout.addWidget(self.extension_help)
        folder = QPushButton("Открыть папку расширения")
        folder.clicked.connect(self.open_extension_folder)
        layout.addWidget(folder)
        binding = QPushButton("Подключить расширение к Qorgau")
        binding.clicked.connect(self.connect_extension)
        layout.addWidget(binding)
        self.refresh()

    def refresh(self):
        self.items.clear()
        self.tabs = []
        try:
            self.tabs = list(self.agent.available_browser_tabs())
        except (OSError, ValueError):
            self.status.setText("Не удалось получить вкладки. Проверьте подключение расширения и обновите список.")
            return
        for tab in self.tabs:
            self.items.addItem(f"{tab.get('title') or tab.get('name', 'Вкладка')}\n{tab.get('url', '')}")
            self.items.item(self.items.count() - 1).setData(Qt.ItemDataRole.UserRole, 'globe')
        self.status.setText("" if self.tabs else "Подключённых вкладок пока нет. Окна приложений выбираются отдельно.")

    def choose(self):
        row = self.items.currentRow()
        if 0 <= row < len(self.tabs):
            self.selected = dict(self.tabs[row])
            self.accept()

    def open_extension_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        try:
            folder = Path(self.agent.extension_folder())
            if not folder.is_dir():
                raise ValueError("EXTENSION_FOLDER_MISSING")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        except (OSError, ValueError):
            self.status.setText("Папка расширения отсутствует в комплекте. Повторно скачайте полную сборку.")

    def connect_extension(self):
        dialog = BrowserBindingDialog(self.agent, self)
        if dialog.exec():
            self.refresh()


class BrowserBindingDialog(QDialog):
    def __init__(self, agent, parent):
        super().__init__(parent)
        self.agent = agent
        self.setWindowTitle("Подключение расширения Qorgau")
        self.setObjectName("window")
        self.setStyleSheet(APP_QSS)
        self.resize(620, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(text("Подключение расширения", 26))
        layout.addWidget(text("Откройте настройки расширения Qorgau и скопируйте показанные ID расширения и ID профиля.", 16))
        self.browser = QComboBox()
        self.browser.addItem("Google Chrome", "chrome")
        self.browser.addItem("Microsoft Edge", "edge")
        layout.addWidget(self.browser)
        layout.addWidget(text("ID расширения", 12))
        self.extension_id = QLineEdit()
        self.extension_id.setMaxLength(32)
        layout.addWidget(self.extension_id)
        layout.addWidget(text("ID профиля браузера", 12))
        self.browser_instance = QLineEdit()
        self.browser_instance.setMaxLength(36)
        layout.addWidget(self.browser_instance)
        self.replace = QCheckBox("Заменить существующую привязку этого браузера")
        layout.addWidget(self.replace)
        self.status = text("", 12)
        layout.addWidget(self.status)
        button = QPushButton("Подключить")
        button.setObjectName("primary")
        button.clicked.connect(self.bind)
        layout.addWidget(button)

    def bind(self):
        try:
            self.agent.bind_browser(self.extension_id.text().strip(), self.browser_instance.text().strip(),
                                    self.browser.currentData(), self.replace.isChecked())
            self.accept()
        except (OSError, ValueError) as error:
            self.status.setText(str(error))


class ExamController(QObject):
    def __init__(self, agent, parent):
        super().__init__(parent)
        self.agent = agent
        self.guard = None
        self.browser = None
        self.browser_exam = None
        self.warning_display = WarningDisplayHold()
        self.surfaces = []
        self.gaze_warnings = []
        self.active_exam = None
        self.displays = 0
        self.preparation = None
        self.environment_timer = QTimer(self)
        self.environment_timer.timeout.connect(self.poll_environment_preparation)
        self.browser_warmup = None
        agent.targets.insert(
            0,
            {
                "id": "qorgau-browser",
                "name": "Qorgau Browser · без вкладок",
                "kind": "BROWSER",
                "guardable": True,
            },
        )
        if os.name == "nt":
            from .windows_guard import WindowsGuard

            self.guard = WindowsGuard()
            agent.capabilities["window_guard"] = True
            agent.capabilities["desktop_monitor"] = True
        if agent.engine.state.lifecycle == "RUNNING" and agent.guarded:
            # Recover the selected environment after an agent restart while
            # keeping AGENT_RESTARTED locked until the teacher authorizes it.
            try:
                agent.launch_environment()
                agent.guard_started_at = time.monotonic()
            except (OSError, ValueError):
                agent.capabilities["guard_fault"] = "TARGET_CLOSED"
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        QApplication.instance().aboutToQuit.connect(self.release)

    def warmup_browser(self):
        """Initialize Chromium on a blank hidden page before exam guards exist."""
        if self.browser_warmup is not None or self.agent.engine.state.lifecycle == "RUNNING":
            return
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.browser_warmup = QWebEngineView(self.parent())
        self.browser_warmup.hide()
        self.browser_warmup.setHtml("<!doctype html><html><title>Qorgau</title></html>")

    def preparation_screen(self):
        screens = QApplication.screens()
        pending = self.agent.snapshot().get("start_pending") or {}
        hwnd = pending.get("target_hwnd")
        if hwnd and self.guard:
            monitor = self.guard.monitor_handle(hwnd)
            for screen in screens:
                try:
                    if int(screen.nativeInterface().handle()) == monitor:
                        return screen
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    continue
            raise ValueError("TARGET_SCREEN_UNKNOWN")
        return self.parent().screen() or QApplication.primaryScreen()

    def focus_environment(self):
        """Restore the exact running target; never relaunch or replace it."""
        snap = self.agent.snapshot()
        if snap['state']['lifecycle'] != 'RUNNING' or snap['state']['access'] != 'OPEN':
            return
        if self.browser is not None:
            self.browser.showFullScreen()
            self.browser.raise_()
            self.browser.activateWindow()
        else:
            selected = self.agent.guard_target or {}
            if not selected.get('desktop'):
                from .windows_guard import WindowTarget
                if self.guard is None or not selected.get('hwnd'):
                    raise ValueError('TARGET_CLOSED')
                target = WindowTarget(**selected)
                if not self.guard.valid(target):
                    raise ValueError('TARGET_CLOSED')
                self.guard.u.ShowWindow(target.hwnd, 9)
                self.guard.u.SetForegroundWindow(target.hwnd)
        self.parent().hide()

    def prepare_environment(self, token, on_ready, on_error):
        self.cancel_environment_preparation()
        self.preparation = {"token": token, "on_ready": on_ready, "on_error": on_error,
                            "started": time.monotonic(), "focused_since": None, "loaded": True}
        selected = self.agent.guard_target or {}
        if selected.get("builtin_url"):
            from .exam_browser import ExamBrowser
            self.preparation["loaded"] = None
            self.browser = ExamBrowser(selected["builtin_url"], self.browser_attempt)
            self.browser.teacher_button.setEnabled(False)
            self.browser_exam = self.agent.snapshot().get("exam_id")
            self.browser.loadFinished.connect(lambda ok: self.browser_prepared(token, ok))
            place_exam_browser(self.browser, calibrated_exam_screen(getattr(self.agent, "camera", None), QApplication.screens()))
            if self.guard:
                target = self.guard.info(int(self.browser.winId()))
                if target is None:
                    self.fail_environment_preparation("Не удалось подготовить окно браузера.")
                    return
                self.agent.guard_target = target.public()
        elif selected.get("hwnd") and self.guard:
            from .windows_guard import WindowTarget
            target = WindowTarget(**selected)
            if not self.guard.valid(target):
                self.fail_environment_preparation("Выбранное окно закрыто. Выберите его заново.")
                return
            self.guard.u.ShowWindow(target.hwnd, 9)
            self.guard.u.SetForegroundWindow(target.hwnd)
        self.parent().hide()
        self.environment_timer.start(100)

    def browser_attempt(self, reason):
        if self.preparation is not None:
            self.fail_environment_preparation("Страница пытается открыть адрес вне выбранного сайта.")
        else:
            self.agent.security_event(reason)

    def browser_prepared(self, token, ok):
        if self.preparation is not None and self.preparation["token"] == token:
            self.preparation["loaded"] = bool(ok)

    def poll_environment_preparation(self):
        preparation = self.preparation
        if preparation is None:
            return
        pending = self.agent.snapshot().get("start_pending") or {}
        if pending.get("id") != preparation["token"]:
            self.cancel_environment_preparation()
            return
        now = time.monotonic()
        if now - preparation["started"] >= 120:
            self.fail_environment_preparation("Подготовка окна не завершена. Закройте системные диалоги и повторите начало.")
            return
        if preparation["loaded"] is False:
            self.fail_environment_preparation("Страница теста не загрузилась. Проверьте подключение и повторите начало.")
            return
        selected = self.agent.guard_target or {}
        if self.guard and selected.get("hwnd"):
            from .windows_guard import WindowTarget
            if not self.guard.valid(WindowTarget(**selected)):
                self.fail_environment_preparation("Выбранное окно закрыто. Выберите его заново.")
                return
            focused = int(self.guard.u.GetAncestor(self.guard.u.GetForegroundWindow(), 2)) == selected["hwnd"]
        elif selected.get("launched_pid") and self.guard:
            target = next((w for w in self.guard.windows() if w.pid == selected["launched_pid"]
                           and w.executable.casefold() == selected.get("executable", "").casefold()), None)
            if target is None:
                return
            self.agent.guard_target = target.public()
            self.guard.u.ShowWindow(target.hwnd, 9)
            self.guard.u.SetForegroundWindow(target.hwnd)
            return
        else:
            focused = self.browser is None or self.browser.isActiveWindow()
        if not focused or preparation["loaded"] is None:
            preparation["focused_since"] = None
            return
        if preparation["focused_since"] is None:
            preparation["focused_since"] = now
        if now - preparation["focused_since"] >= 1.0:
            if not self.check_exam_monitor(QApplication.screens()):
                self.fail_environment_preparation("Окно теста находится на другом экране. Повторите начало и настройку взгляда на экране теста.")
                return
            self.preparation = None
            self.environment_timer.stop()
            preparation["on_ready"]()

    def fail_environment_preparation(self, message):
        callback = self.preparation["on_error"] if self.preparation is not None else None
        self.cancel_environment_preparation()
        if callback:
            callback(message)

    def cancel_environment_preparation(self):
        if self.preparation is None and (self.browser is None or self.agent.engine.state.lifecycle == "RUNNING"):
            return
        self.preparation = None
        self.environment_timer.stop()
        if self.browser is not None:
            self.browser.released = True
            self.browser.close()
            self.browser.deleteLater()
            self.browser = None
            self.browser_exam = None

    def release(self):
        had_exam = bool(self.active_exam or self.browser)
        if self.guard:
            self.guard.stop()
        self.cancel_environment_preparation()
        self.agent.capabilities["guard_active"] = False
        for surface in self.surfaces:
            surface.hide()
        for warning in self.gaze_warnings:
            warning.hide()
        # Keep surfaces alive while a password request finishes.
        self.active_exam = None
        self.warning_display = WarningDisplayHold()
        if self.browser:
            self.browser.released = True
            self.browser.close()
            self.browser.deleteLater()
            self.browser = None
            self.browser_exam = None
        if had_exam and not getattr(self.parent(), "shutting_down", False):
            self.parent().showFullScreen()
            self.parent().raise_()
            self.parent().activateWindow()

    def invalidate_exam_monitor(self):
        camera = getattr(self.agent, 'camera', None)
        if camera is not None:
            camera.request_screen_invalidation('SCREEN_CHANGED')
        self.agent.capabilities['gaze'] = False
        if (self.agent.engine.state.lifecycle == 'RUNNING'
                and self.agent.engine.state.access == 'OPEN'):
            self.agent.security_event('DISPLAY_CHANGED')

    def check_exam_monitor(self, screens):
        """A connected calibrated monitor alone is insufficient: the test uses it."""
        camera = getattr(self.agent, 'camera', None)
        if getattr(camera, 'screen_signature', None) is None:
            return True
        expected = calibrated_exam_screen(camera, screens)
        if expected is None:
            self.invalidate_exam_monitor()
            return False
        if self.browser is not None:
            handle = self.browser.windowHandle()
            if (handle is not None and handle.screen() is not expected
                    and self.agent.engine.state.access == 'LOCKED'
                    and not getattr(camera, 'requires_gaze_reference', True)):
                # A newly completed recovery may choose another monitor. Move
                # only our browser while lock overlays still protect the exam.
                place_exam_browser(self.browser, expected)
                handle = self.browser.windowHandle()
            if handle is None or handle.screen() is not expected:
                self.invalidate_exam_monitor()
                return False
            return True
        selected = self.agent.guard_target or {}
        if self.guard is None or selected.get('desktop') or selected.get('builtin_url'):
            return True
        from .windows_guard import WindowTarget
        target = WindowTarget(**selected) if 'hwnd' in selected else next(
            (window for window in self.guard.windows()
             if window.pid == selected.get('launched_pid')
             and window.executable.casefold() == selected.get('executable', '').casefold()), None,
        )
        # The existing target watchdog owns missing/reused handles. There is no
        # current foreign window whose display can be evaluated in that case.
        if target is None or not self.guard.valid(target):
            return True
        try:
            native = expected.nativeInterface()
            expected_monitor = int(native.handle())
            actual_monitor = self.guard.monitor_handle(target.hwnd)
            matches = expected_monitor != 0 and expected_monitor == actual_monitor
        except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
            matches = False
        if not matches:
            self.invalidate_exam_monitor()
        return matches

    def tick(self):
        snap = self.agent.snapshot()
        screens = QApplication.screens()
        if (self.active_exam or (self.browser and not snap.get("start_pending"))) and snap["state"]["lifecycle"] == "COMPLETED":
            self.release()
        if snap["state"]["lifecycle"] == "RUNNING" and not self.active_exam:
            self.active_exam = snap["exam_id"]
            self.displays = len(screens)
            self.parent().hide()
        if self.browser is not None and snap["state"]["lifecycle"] == "RUNNING":
            self.browser.teacher_button.setEnabled(True)
        selected = self.agent.guard_target
        if (
            selected
            and selected.get("builtin_url")
            and not self.browser
            and snap["state"]["lifecycle"] == "RUNNING"
        ):
            from .exam_browser import ExamBrowser

            self.browser = ExamBrowser(
                selected["builtin_url"], self.agent.security_event
            )
            self.browser_exam = snap["exam_id"]
            expected = calibrated_exam_screen(getattr(self.agent, 'camera', None), screens)
            place_exam_browser(self.browser, expected)
            if self.guard:
                self.agent.guard_target = self.guard.info(
                    int(self.browser.winId())
                ).public()
        if snap['state']['lifecycle'] == 'RUNNING':
            self.check_exam_monitor(screens)
            snap = self.agent.snapshot()
        while len(self.gaze_warnings) < len(screens):
            self.gaze_warnings.append(GazeWarning())
        warning_text = self.warning_display.update(snap, time.monotonic())
        warn_about_gaze = bool(warning_text)
        for i, screen in enumerate(screens):
            warning = self.gaze_warnings[i]
            if warn_about_gaze:
                warning.message.setText(warning_text)
                warning.update_countdown(snap)
                warning.place(screen)
                warning.show()
                warning.raise_()
            else:
                warning.hide()
        for extra in self.gaze_warnings[len(screens) :]:
            extra.hide()
        if snap["state"]["lifecycle"] != "RUNNING":
            if self.active_exam:
                self.release()
            return
        if not snap["guarded"]:
            return
        desktop = bool((self.agent.guard_target or {}).get("desktop"))
        locked = snap["state"]["access"] == "LOCKED"
        if not self.active_exam:
            self.active_exam = snap["exam_id"]
            self.displays = len(screens)
            self.parent().hide()
        last_sync = self.agent.last_synced_at or self.agent.guard_started_at
        if getattr(self.agent, "mode", "online") != "offline" and (last_sync is None or time.monotonic() - last_sync > 10):
            self.agent.security_event("SERVER_UNAVAILABLE")
            snap = self.agent.snapshot()
        if len(screens) != self.displays:
            self.agent.security_event("DISPLAY_CHANGED")
            self.displays = len(screens)
        snap = self.agent.snapshot()
        locked = snap["state"]["access"] == "LOCKED"
        while len(self.surfaces) < len(screens):
            self.surfaces.append(LockScreen(self.agent))
        for i, screen in enumerate(screens):
            surface = self.surfaces[i]
            surface.update_state(snap)
            if locked:
                if not surface.isVisible() or not surface.isFullScreen():
                    surface.setGeometry(screen.geometry())
                    surface.winId()
                    surface.windowHandle().setScreen(screen)
                    surface.showFullScreen()
            else:
                surface.hide()
        for extra in self.surfaces[len(screens) :]:
            extra.hide()
        if not self.guard:
            self.agent.security_event("GUARD_UNAVAILABLE")
            return
        try:
            from .windows_guard import WindowTarget

            missing_target = False
            if desktop and not self.guard.hooks:
                self.guard.start(desktop=True)
            if not desktop and (not self.guard.target or not self.guard.hooks):
                selected = self.agent.guard_target
                if selected and "hwnd" in selected:
                    target = WindowTarget(**selected)
                else:
                    target = next(
                        (
                            w
                            for w in self.guard.windows()
                            if selected
                            and w.pid == selected.get("launched_pid")
                            and w.executable.casefold()
                            == selected["executable"].casefold()
                        ),
                        None,
                    )
                if target is not None and not self.guard.valid(target):
                    target = None
                if target is None:
                    started = self.agent.guard_started_at
                    if started is None or time.monotonic() - started > 10:
                        self.agent.security_event("TARGET_CLOSED")
                    if not locked:
                        return
                    # A restarted/closed target cannot leave a locked desktop
                    # without hooks. Teacher may still end the session here.
                    missing_target = True
                    if not self.guard.hooks:
                        self.guard.start(desktop=True)
                else:
                    self.guard.start(target)
            locked = snap["state"]["access"] == "LOCKED"
            previous = self.guard.locked
            result = self.guard.tick(
                locked=locked,
                overlays=[int(s.winId()) for s in self.surfaces[: len(screens)]]
                + calibration_overlay_handles(self.surfaces),
            )
            if missing_target:
                result = "TARGET_CLOSED"
            self.agent.capabilities["guard_active"] = len(self.guard.hooks) == 2 and result not in ("TARGET_CLOSED", "GUARD_UNAVAILABLE")
            self.agent.capabilities["guard_fault"] = (
                result if result in ("TARGET_CLOSED", "REMOTE_SESSION", "GUARD_UNAVAILABLE") else None
            )
            if result:
                self.agent.security_event(result, lock=result != "ENVIRONMENT_ATTEMPT")
            if locked:
                for surface in self.surfaces[: len(screens)]:
                    surface.raise_()
                if not previous:
                    self.surfaces[0].activateWindow()
                    self.surfaces[0].password.setFocus()
                raise_calibration_overlays(self.surfaces)
            elif not locked and not desktop:
                self.guard.u.SetWindowPos(self.guard.target.hwnd, -1, 0, 0, 0, 0, 0x13)
                if previous:
                    self.guard.u.SetForegroundWindow(self.guard.target.hwnd)
            if warn_about_gaze:
                for warning in self.gaze_warnings[: len(screens)]:
                    warning.raise_()
        except (OSError, ValueError):
            self.agent.capabilities["guard_active"] = False
            self.agent.capabilities["guard_fault"] = "GUARD_UNAVAILABLE"
            self.agent.security_event("GUARD_UNAVAILABLE")
