"""Classroom tray agent with automatic first-run registration."""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QLockFile, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .client import Agent
from .provision import EnrollmentUnavailable, auto_enroll
from .student_state import present
from shared.bootstrap import default_bootstrap

STYLE = """
QWidget {font-family: "Segoe UI", "DejaVu Sans";font-size:13px;color:#34415a;}
QWidget#window {background:#f7f9fc;}
QFrame#sidebar {background:#244b9b;border:0;}
QLabel#brand {font-size:32px;font-weight:700;color:#edf4e7;}
QLabel#sideHeading {font-size:10px;font-weight:600;color:#94b39e;letter-spacing:1px;}
QLabel#sideStep {color:#b7cdbb;font-size:13px;padding:13px 0;}
QLabel#sideNote {color:#8daa94;font-size:11px;line-height:1.5;}
QLabel#eyebrow {font-size:10px;letter-spacing:2px;color:#7e90ad;font-weight:600;}
QLabel#title {font-size:27px;font-weight:600;color:#243651;}
QLabel#body {font-size:13px;color:#7d8799;}
QLabel#small {font-size:11px;color:#8793a6;}
QLabel#heading {font-size:16px;font-weight:600;}
QLabel#field {font-size:12px;font-weight:500;color:#687c9e;}
QFrame#card {background:white;border:1px solid #e1e6f0;border-radius:12px;}
QLabel#notice {background:#eef3fb;color:#7085a7;padding:13px;border:1px solid #dfe6f2;border-radius:8px;font-size:11px;}
QLabel#error {color:#ad5247;background:#fff0e9;border:1px solid #efcfc0;border-radius:7px;padding:12px;}
QLabel#badge {font-size:11px;color:#5778ae;background:#edf3fe;border:1px solid #dce6f7;border-radius:6px;padding:6px 10px;}
QLabel#heroTitle {font-size:23px;font-weight:600;}
QLabel#count {font-size:32px;font-weight:600;color:#426cb2;}
QLabel#countDanger {font-size:32px;font-weight:600;color:#b95f4b;}
QLineEdit {background:white;border:1px solid #dce3ef;border-radius:7px;padding:11px 12px;color:#34415a;selection-background-color:#5480d4;}
QLineEdit:focus {border:1px solid #5480d4;}
QLineEdit:disabled {background:#f2f4f8;color:#9aa8bd;}
QPushButton {background:white;border:1px solid #d9e2f1;border-radius:7px;padding:11px 15px;color:#526c94;font-weight:500;}
QPushButton:hover {background:#edf3fc;border-color:#a3b9dc;}
QPushButton:disabled {background:#f0f2ec;color:#a7b29b;border-color:#e0e6d8;}
QPushButton#primary {background:#2859bc;color:white;border-color:#2859bc;}
QPushButton#primary:hover {background:#214b9f;}
QPushButton#primary:disabled {background:#a2b6d7;border-color:#a2b6d7;color:#f2f6fd;}
QScrollArea {border:0;background:transparent;}
QScrollBar:vertical {background:#f3f6fb;width:9px;border:0;}
QScrollBar::handle:vertical {background:#d5deed;border-radius:4px;min-height:30px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
"""


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


def icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#2859bc"))
    painter.drawRoundedRect(2, 2, 60, 60, 15, 15)
    painter.setPen(QColor("#edf4e7"))
    painter.setFont(QFont("DejaVu Sans", 33, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "q")
    painter.end()
    return QIcon(pixmap)


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
        self.tray = None
        self.setObjectName("window")
        self.setWindowTitle("Qorgau — агент аудитории")
        self.setWindowIcon(icon())
        self.resize(690, 735)
        self.setMinimumSize(600, 620)
        self.setStyleSheet(STYLE)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(225)
        rail = QVBoxLayout(side)
        rail.setContentsMargins(27, 34, 24, 25)
        rail.setSpacing(12)
        rail.addWidget(label("qorgau.", "brand"))
        rail.addSpacing(34)
        rail.addWidget(label("ПРИЛОЖЕНИЕ УЧЕНИКА", "sideHeading"))
        rail.addWidget(label("01   Подключение", "sideStep"))
        rail.addWidget(label("02   Подготовка", "sideStep"))
        rail.addWidget(label("03   Внешний тест", "sideStep"))
        rail.addStretch()
        rail.addWidget(label("Тест — в привычной системе", "sideStep"))
        rail.addWidget(
            label(
                "Qorgau работает рядом с браузером или приложением. Решения по спорным событиям принимает преподаватель.",
                "sideNote",
            )
        )
        rail.addSpacing(30)
        rail.addWidget(
            label(
                "Режим наблюдения\nБез блокировки ОС\n\nQostanai Industry Hackathon\nЛокальный агент · 0.2",
                "sideNote",
            )
        )
        side.hide()
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
        layout.setContentsMargins(38, 30, 38, 28)
        layout.setSpacing(18)
        layout.addWidget(label("QORGAU / ПОДКЛЮЧЕНИЕ", "eyebrow"))
        layout.addWidget(label("Подключаем компьютер", "title"))
        layout.addWidget(
            label(f"Компьютер · {socket.gethostname()}")
        )
        layout.addWidget(
            label(
                "Подключение произойдёт автоматически. Преподаватель увидит этот компьютер в списке и сможет назначить тест."
            )
        )
        self.setup_error = label("", "error")
        self.setup_error.hide()
        layout.addWidget(self.setup_error)
        self.connect_button = QPushButton("Подключаем компьютер…")
        self.connect_button.setObjectName("primary")
        self.connect_button.setEnabled(False)
        self.connect_button.clicked.connect(self.register)
        layout.addWidget(self.connect_button)
        layout.addWidget(
            label(
                "Подключение не включает камеру и не запускает тест. Подготовка камеры доступна в состоянии компьютера.",
                "notice",
            )
        )
        layout.addStretch()
        return page

    def build_dashboard(self):
        outer = QWidget()
        root = QVBoxLayout(outer)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        page = QWidget()
        page.setObjectName("window")
        scroll.setWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 29, 32, 25)
        layout.setSpacing(15)
        top = QHBoxLayout()
        top.addWidget(label("QORGAU / СОСТОЯНИЕ АГЕНТА", "eyebrow", False))
        top.addStretch()
        self.connection = label("Подключаемся…", "badge", False)
        top.addWidget(self.connection)
        layout.addLayout(top)
        self.device_name = label("Компьютер аудитории", "title")
        layout.addWidget(self.device_name)
        self.endpoint_label = label("", "small")
        self.endpoint_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.endpoint_label)
        self.hero, self.hero_layout = card()
        layout.addWidget(self.hero)
        row = QHBoxLayout()
        self.state_badge = label("Ожидание", "badge", False)
        row.addWidget(self.state_badge)
        row.addStretch()
        self.epoch = label("Цикл 1", "small", False)
        row.addWidget(self.epoch)
        self.hero_layout.addLayout(row)
        self.hero_title = label("Компьютер подключён", "heroTitle")
        self.hero_layout.addWidget(self.hero_title)
        self.hero_message = label()
        self.hero_layout.addWidget(self.hero_message)
        self.test_button = QPushButton("Открыть среду теста  ↗")
        self.test_button.setObjectName("primary")
        self.test_button.clicked.connect(self.open_exam)
        self.hero_layout.addWidget(self.test_button)
        assignment, body = card()
        layout.addWidget(assignment)
        body.addWidget(label("Ваш сеанс", "heading"))
        self.assignment_title = label("Преподаватель ещё не назначил тест")
        body.addWidget(self.assignment_title)
        self.environment_label = label(
            "Среда появится после назначения сеанса", "small"
        )
        self.environment_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        body.addWidget(self.environment_label)
        self.counter_frame, counts = card()
        layout.addWidget(self.counter_frame)
        header = QHBoxLayout()
        header.addWidget(label("Отвлечения в текущем цикле", "heading", False))
        header.addStretch()
        header.addWidget(label("Порог — 5 секунд", "small", False))
        counts.addLayout(header)
        grid = QHBoxLayout()
        self.count_labels = {}
        for key, title in [("DOWN", "Вниз"), ("LEFT", "Влево"), ("RIGHT", "Вправо")]:
            col = QVBoxLayout()
            col.addWidget(label(title, "small"))
            number = label("0 / 3", "count")
            self.count_labels[key] = number
            col.addWidget(number)
            grid.addLayout(col, 1)
        counts.addLayout(grid)
        counts.addWidget(
            label(
                "Направления считаются отдельно. Телефон вызывает немедленное событие блокировки. Второе лицо проверяет преподаватель.",
                "small",
            )
        )
        checks, body = card()
        layout.addWidget(checks)
        self.target_button = QPushButton("Выбрать главное окно / приложение")
        self.target_button.clicked.connect(self.select_target)
        body.addWidget(self.target_button)
        self.target_status = label(
            "Выберите окно теста перед назначением сеанса.", "small"
        )
        body.addWidget(self.target_status)
        row = QHBoxLayout()
        row.addWidget(label("Готовность компьютера", "heading"))
        row.addStretch()
        self.camera_button = QPushButton("Включить камеру")
        self.camera_button.clicked.connect(self.prepare_camera)
        row.addWidget(self.camera_button)
        body.addLayout(row)
        self.camera_status = label()
        body.addWidget(self.camera_status)
        body.addWidget(label("Кнопка включает локальный анализ телефона и лиц, без записи звука. Во время экзамена преподавателю передаются события и видеофрагменты.", "small"))
        self.gaze_status = label("Контроль взгляда выключен", "small")
        body.addWidget(self.gaze_status)
        self.gaze_button = QPushButton("Настроить взгляд (необязательно)")
        self.gaze_button.clicked.connect(lambda: self.prepare_camera(calibrate=True))
        body.addWidget(self.gaze_button)
        self.delivery_status = label(
            "События будут передаваться преподавателю", "small"
        )
        body.addWidget(self.delivery_status)
        self.runtime_error = label("", "error")
        self.runtime_error.hide()
        layout.addWidget(self.runtime_error)
        layout.addWidget(
            label(
                "На Windows доступно ограничение выбранного окна и горячих клавиш. Включите камеру и выберите режим «Ограничение Windows» в кабинете преподавателя.",
                "notice",
            )
        )
        layout.addStretch()
        return outer

    def register(self):
        if self.shutting_down or self.agent:
            return
        if self.enrollment and self.enrollment.isRunning():
            return
        self.retry_timer.stop()
        self.setup_error.hide()
        self.connect_button.setEnabled(False)
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
        if self.enrollment and self.enrollment.retryable:
            self.retry_timer.start(10000)

    def registration_succeeded(self, _config):
        self.retry_timer.stop()
        try:
            self.attach(Agent(self.folder))
            if self.tray:
                self.tray.showMessage(
                    "Компьютер подключён",
                    "Qorgau работает в трее. Сеансы назначаются на сайте преподавателя.",
                )
        except Exception:  # noqa: BLE001 - GUI boundary must keep recovery controls accessible
            self.registration_failed(
                "Компьютер зарегистрирован, но агент не запустился. Закройте и снова откройте приложение."
            )

    def attach(self, agent):
        self.agent = agent
        from .exam_ui import ExamController

        self.exam_controller = ExamController(agent, self)
        self.pages.setCurrentIndex(1)
        self.device_name.setText(agent.config.get("name", "Компьютер аудитории"))
        self.endpoint_label.setText("Сервер: " + agent.server)
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
        snap = self.agent.snapshot()
        model = present(snap)
        state = snap["state"]
        if self.tray:
            status = (
                "Нет связи с сервером" if not snap.get("connected") else model["title"]
            )
            self.tray.setToolTip(f"Qorgau · {snap.get('device_name', '')}\n{status}")
            self.tray_status.setText(status)
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
            "● Сервер подключён"
            if snap.get("connected") and not self.failure
            else "○ Нет связи с сервером"
        )
        self.hero_title.setText(model["title"])
        self.hero_message.setText(model["message"])
        self.state_badge.setText(model["badge"])
        color = {
            "red": ("#fff5ef", "#edd1c2", "#a85747"),
            "green": ("#f1f7eb", "#dce9d1", "#568343"),
            "amber": ("#fcf8ea", "#ede1bd", "#9d813d"),
            "neutral": ("#ffffff", "#e1e6f0", "#718761"),
        }[model["tone"]]
        self.hero.setStyleSheet(
            f"QFrame#card {{background:{color[0]};border:1px solid {color[1]};border-radius:12px;}}"
        )
        self.hero_title.setStyleSheet(
            f"color:{color[2]};font-size:23px;font-weight:600;"
        )
        legacy_environment = (snap.get("environment") or {}).get("kind") in ("APP", "BROWSER")
        self.test_button.setVisible(state["lifecycle"] == "RUNNING" and legacy_environment)
        self.target_button.setVisible(legacy_environment)
        self.target_status.setVisible(legacy_environment)
        self.test_button.setEnabled(model["can_open"] and not self.failure)
        self.epoch.setText(f"Цикл {state['epoch']}")
        for key, value in self.count_labels.items():
            n = state["counts"][key]
            value.setText(f"{n} / 3")
            value.setStyleSheet("color:#b95f4b;" if n >= 3 else "")
        session = snap.get("session") or {}
        self.assignment_title.setText(
            " · ".join(
                str(session[k])
                for k in ("title", "group", "room", "student")
                if session.get(k)
            )
            or (
                "Сеанс назначен преподавателем"
                if snap.get("exam_id")
                else "Преподаватель ещё не назначил тест"
            )
        )
        env = snap.get("environment") or {}
        self.environment_label.setText(
            snap.get("environment_name", "Среда не выбрана")
            + (" · " + env["url"] if env.get("url") else "")
        )
        self.camera_button.setEnabled(model["can_calibrate"] and not self.failure)
        self.gaze_button.setEnabled(model["can_calibrate"] and not self.failure)
        self.target_button.setEnabled(
            state["lifecycle"] != "RUNNING"
            and not snap.get("exam_id")
            or state["lifecycle"] == "COMPLETED"
        )
        self.camera_button.setText(
            "Перезапустить камеру" if snap.get("camera") else "Включить камеру"
        )
        self.camera_status.setText(
            "Камера недоступна — сообщите преподавателю"
            if snap.get("camera_fault")
            else "Включаем камеру…"
            if snap.get("camera_preparing")
            else "● Камера включена · локальный буфер; преподавателю отправляются события экзамена"
            if snap.get("camera")
            else "○ Камера выключена · анализ взгляда и телефона не выполняется"
        )
        self.gaze_status.setText(
            "● Контроль взгляда включён: используется персональная настройка."
            if snap.get("gaze")
            else "○ Контроль взгляда выключен: отвлечения не считаются. Телефон и лица распознаются после включения камеры."
        )
        self.counter_frame.setVisible(bool(snap.get("gaze") or any(state["counts"].values())))
        pending = snap.get("pending", 0)
        media = snap.get("pending_media", 0)
        self.delivery_status.setText(
            f"Ожидают отправки: {pending} событий, {media} видеофрагментов. Данные сохраняются на компьютере."
            if pending or media
            else "Очередь событий отправлена"
            if snap.get("connected")
            else "При потере связи события сохраняются локально и отправляются после восстановления."
        )

    def open_exam(self):
        if not self.agent:
            return
        with self.agent.mutex:
            if not present(self.agent.snapshot())["can_open"]:
                return
            try:
                self.agent.launch_environment()
            except (ValueError, OSError):
                QMessageBox.warning(
                    self,
                    "Не удалось открыть тест",
                    "Проверьте, что выбранный браузер или приложение установлен. Обратитесь к преподавателю.",
                )

    def prepare_camera(self, checked=False, *, calibrate=False):
        if not self.agent or not present(self.agent.snapshot())["can_calibrate"]:
            return
        from .camera_setup import CameraSetup

        self.calibration = CameraSetup(self.agent, self, calibrate=calibrate)
        self.calibration.exec()
        self.calibration = None
        self.refresh()

    def select_target(self):
        if not self.agent or (
            self.agent.journal.get("exam_id")
            and self.agent.engine.state.lifecycle != "COMPLETED"
        ):
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
            self.target_status.setText(
                picker.selected["name"] + " · доступно преподавателю"
            )

    def create_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icon(), self)
        self.tray_menu = QMenu(self)
        self.tray_status = self.tray_menu.addAction("Qorgau · подключение")
        self.tray_status.setEnabled(False)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Состояние компьютера", self.show_status)
        self.tray_menu.addSeparator()
        self.tray_exit = self.tray_menu.addAction("Завершить агент", self.request_exit)
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

    def show_status(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def start_visibility(self, show_window=False):
        if show_window or not self.agent or not self.tray:
            self.show()
        else:
            self.hide()

    def request_exit(self):
        if self.shutting_down:
            return
        if self.enrollment and self.enrollment.isRunning():
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
        self.hide()
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
    from .install_guard import hold_installation_mutex
    hold_installation_mutex()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Qorgau Agent")
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationDisplayName("Qorgau — агент аудитории")
    app.setFont(QFont("Segoe UI" if os.name == "nt" else "DejaVu Sans", 10))
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
