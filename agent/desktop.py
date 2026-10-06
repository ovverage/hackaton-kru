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
    QComboBox,
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
        self.resize(560, 450)
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
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(16)
        row = QHBoxLayout()
        row.addWidget(label("qorgau.", "title"))
        row.addStretch()
        self.connection = label("Подключаемся…", "small")
        row.addWidget(self.connection)
        layout.addLayout(row)
        self.device_name = label("Компьютер аудитории", "small")
        layout.addWidget(self.device_name)
        layout.addWidget(label("Окно теста", "field"))
        self.target_button = QPushButton("Выбрать открытое окно")
        self.target_button.clicked.connect(self.select_target)
        layout.addWidget(self.target_button)
        self.target_status = label("Откройте тест в браузере или приложении.", "small")
        layout.addWidget(self.target_status)
        layout.addWidget(label("Камера", "field"))
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
        layout.addWidget(self.camera_choice)
        self.camera_button = QPushButton("Готово")
        self.camera_button.setObjectName("primary")
        self.camera_button.clicked.connect(self.prepare_camera)
        layout.addWidget(self.camera_button)
        self.camera_status = label("Выберите окно и камеру, затем нажмите «Готово», глядя на экран.", "small")
        layout.addWidget(self.camera_status)
        self.gaze_status = label("", "small")
        self.gaze_status.setWordWrap(True)
        layout.addWidget(self.gaze_status)
        self.assignment_title = label("", "small")
        layout.addWidget(self.assignment_title)
        self.runtime_error = label("", "error")
        self.runtime_error.hide()
        layout.addWidget(self.runtime_error)
        layout.addStretch()
        layout.addWidget(label("Во время теста фиксируются события и видео с камеры. Звук не записывается.", "small"))
        return page

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
        active = state["lifecycle"] == "RUNNING"
        selected = next((t for t in self.agent.targets if t.get("id") == "primary-window"), None)
        self.agent.capabilities["selected_window"] = bool(selected)
        self.target_button.setEnabled(not active)
        self.target_button.setText("Изменить окно" if selected else "Выбрать открытое окно")
        self.target_status.setText(selected["name"] if selected else "Откройте тест в браузере или приложении.")
        self.camera_choice.setEnabled(not active and not snap.get("camera_preparing"))
        self.camera_button.setEnabled(bool(selected) and model["can_calibrate"] and not self.failure)
        self.camera_button.setText("Изменить камеру" if snap.get("camera") else "Готово")
        self.camera_status.setText(
            "Камера недоступна — выберите её повторно" if snap.get("camera_fault")
            else "Включаем камеру…" if snap.get("camera_preparing")
            else "Камера включена. Ждём преподавателя." if snap.get("camera") and not active
            else model["title"] if active
            else "Выберите окно и камеру, затем нажмите «Готово», глядя на экран."
        )
        session = snap.get("session") or {}
        self.assignment_title.setText(session.get("title", ""))
        gaze = snap.get("gaze_diagnostics") or {}
        names = {"SCREEN": "на экран", "LEFT": "влево", "RIGHT": "вправо",
                 "DOWN": "вниз", "UP": "вверх", "UNKNOWN": "не определён"}
        if snap["state"]["lifecycle"] == "COMPLETED":
            gaze_text = "Сеанс завершён. Распознавание выключено."
        elif snap.get("recognition_paused"):
            gaze_text = "Распознавание приостановлено. Продолжение разрешает преподаватель."
        elif not snap.get("camera") or snap.get("camera_fault"):
            gaze_text = ""
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

        self.calibration = CameraSetup(self.agent, self, calibrate=calibrate, index=self.camera_choice.currentData())
        self.calibration.exec()
        self.calibration = None
        self.refresh()

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
        if self.agent and self.agent.engine.state.access == "LOCKED" and self.exam_controller:
            self.exam_controller.tick()
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def start_visibility(self, show_window=False):
        if show_window or not self.agent or not self.tray or not self.agent.capabilities.get("selected_window"):
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
