"""Window selection, evidence screen and GUI-thread Windows guard lifecycle."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from .student_state import REASONS


def text(value, size=14):
    widget = QLabel(value)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setStyleSheet(f"font-size:{size}px;")
    return widget


def gaze_warning_active(snap):
    """A visual nudge is immediate; rule strikes still use the five-second timer."""
    gaze = snap.get("gaze_diagnostics") or {}
    return bool(
        snap["state"]["lifecycle"] == "RUNNING"
        and snap["state"]["access"] == "OPEN"
        and snap.get("camera")
        and not snap.get("camera_fault")
        and not snap.get("recognition_paused")
        and gaze.get("reference_ready")
        and (
            gaze.get("attention_away")
            or gaze.get("direction") in ("DOWN", "LEFT", "RIGHT", "UP")
        )
    )


class GazeWarning(QWidget):
    """Click-through topmost banner shown without taking focus from the exam."""

    def __init__(self):
        super().__init__()
        self.setObjectName("gazeWarning")
        self.setWindowTitle("Qorgau — верните взгляд на монитор")
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
            "QWidget#gazeWarning {background:#ffd34f;border:2px solid #9d6b00;border-radius:12px;}"
            "QLabel {background:transparent;color:#2b2100;font-family:'Segoe UI';font-size:24px;font-weight:700;}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 15, 26, 15)
        self.message = QLabel("Верните взгляд на монитор")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.message)

    def place(self, screen):
        geometry = screen.geometry()
        width = min(720, max(360, geometry.width() - 48))
        height = 68
        self.setGeometry(
            geometry.x() + (geometry.width() - width) // 2,
            geometry.y() + 28,
            width,
            height,
        )


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
                else "Нет связи с сервером. Преподаватель может завершить сеанс после восстановления связи."
            )
        finally:
            self.password = ""


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
        self.setStyleSheet(
            "QWidget {background:#101a2c;color:#eef3ff;font-family:'Segoe UI';}"
            "QLineEdit {background:#1e304d;border:1px solid #536987;border-radius:8px;padding:14px;}"
            "QPushButton {background:#376ad8;border:0;border-radius:8px;padding:14px;color:white;font-size:15px;font-weight:600;min-height:20px;}"
            "QPushButton:disabled {background:#33415b;color:#cbd5e1;}"
        )
        root = QHBoxLayout(self)
        root.setContentsMargins(60, 50, 60, 50)
        root.setSpacing(50)
        main = QVBoxLayout()
        main.addWidget(text("QORGAU  /  КОНТРОЛЬ ЭКЗАМЕНА", 14))
        main.addStretch()
        self.heading = text("Позовите преподавателя", 38)
        main.addWidget(self.heading)
        self.reason = text("Доступ к тесту приостановлен", 18)
        main.addWidget(self.reason)
        main.addSpacing(25)
        self.form = QWidget()
        form = QVBoxLayout(self.form)
        form.setContentsMargins(0, 0, 0, 0)
        form.addWidget(text("Преподаватель проверяет события и разрешает продолжить."))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setMaxLength(128)
        self.password.setPlaceholderText("Пароль кабинета преподавателя")
        self.password.setAccessibleName("Пароль преподавателя")
        form.addWidget(self.password)
        self.unlock = QPushButton("Разблокировать тест")
        self.unlock.clicked.connect(self.request_unlock)
        self.password.returnPressed.connect(self.request_unlock)
        form.addWidget(self.unlock)
        self.finish = QPushButton("Завершить контроль · пароль преподавателя")
        self.finish.clicked.connect(lambda: self.request_unlock("END_AND_RELEASE"))
        form.addWidget(self.finish)
        self.recover = QPushButton("Восстановить камеру")
        self.recover.clicked.connect(self.recover_camera)
        form.addWidget(self.recover)
        self.feedback = text("")
        form.addWidget(self.feedback)
        main.addWidget(self.form)
        main.addStretch()
        main.addWidget(
            text(
                "Событие — повод для проверки. Окончательное решение принимает преподаватель.",
                12,
            )
        )
        root.addLayout(main, 3)
        self.evidence = QWidget()
        self.evidence_layout = QVBoxLayout(self.evidence)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.evidence)
        scroll.setMinimumWidth(310)
        scroll.setStyleSheet("QScrollArea {border:0;}")
        root.addWidget(scroll, 2)

    def update_state(self, snap):
        locked = snap["state"]["access"] == "LOCKED"
        self.heading.setText("Позовите преподавателя" if locked else "Идёт экзамен")
        self.reason.setText(
            REASONS.get(
                snap["state"].get("reason"), "Работайте в выбранном окне теста."
            )
        )
        self.form.setVisible(locked)
        self.recover.setVisible(locked and snap["state"].get("reason") in (
            "AGENT_RESTARTED", "CAMERA_UNAVAILABLE", "CAMERA_FROZEN"
        ))
        marker = (
            snap["state"]["lock_id"],
            snap["state"]["epoch"],
            tuple((e["id"], e.get("thumbnail_path"), e.get("end")) for e in snap.get("recent_events", [])),
        )
        if marker == self.marker:
            return
        self.marker = marker
        while self.evidence_layout.count():
            item = self.evidence_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.evidence_layout.addWidget(text("Моменты срабатывания", 22))
        events = [
            e
            for e in snap.get("recent_events", [])
            if e.get("epoch") == snap["state"]["epoch"]
        ]
        for event in events[-8:][::-1]:
            happened = time.strftime("%H:%M:%S", time.localtime(event.get("created_at", time.time())))
            label = REASONS.get(event["type"], event["type"])
            label = {
                "GAZE_DOWN": "Длительный взгляд вниз",
                "GAZE_LEFT": "Длительный взгляд влево",
                "GAZE_RIGHT": "Длительный взгляд вправо",
            }.get(event["type"], label)
            self.evidence_layout.addWidget(
                text(f"{happened}  ·  {label}", 16)
            )
            if event.get("duration"):
                self.evidence_layout.addWidget(
                    text(f"Длительность: {event['duration']:.1f} с", 12)
                )
            if event.get("thumbnail_path"):
                from PySide6.QtGui import QPixmap

                picture = QPixmap(event["thumbnail_path"])
                if not picture.isNull():
                    preview = QLabel()
                    preview.setPixmap(
                        picture.scaledToWidth(
                            max(280, min(640, self.evidence.width() - 24)), Qt.TransformationMode.SmoothTransformation
                        )
                    )
                    self.evidence_layout.addWidget(preview)
        if not events:
            self.evidence_layout.addWidget(
                text(
                    "Причина указана слева. История и видео доступны в кабинете преподавателя."
                )
            )
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
        self.finish.setEnabled(False)
        self.feedback.setText("Проверяем пароль и актуальную блокировку…")
        self.worker = UnlockWorker(self.agent, password, self, action)
        self.worker.done.connect(self.unlock_done)
        self.worker.start()

    def unlock_done(self, error):
        self.unlock.setEnabled(True)
        self.finish.setEnabled(True)
        self.feedback.setText(error or "Преподаватель разрешил продолжить")

    def closeEvent(self, event):
        event.ignore()


class TargetPicker(QDialog):
    def __init__(self, agent, parent):
        super().__init__(parent)
        self.agent = agent
        self.setWindowTitle("Выберите главное окно или приложение")
        self.resize(740, 450)
        self.selected = None
        self.windows = []
        layout = QVBoxLayout(self)
        layout.addWidget(
            text(
                "Откройте нужную вкладку теста и выберите её окно. При начале контроля оно развернётся на весь экран; переход в другие окна и инструменты разработчика будут ограничены.",
                17,
            )
        )
        self.items = QListWidget()
        layout.addWidget(self.items)
        row = QHBoxLayout()
        refresh = QPushButton("Обновить окна")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        use = QPushButton("Использовать окно")
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
            self.items.addItem(f"{window.title}  —  {Path(window.executable).name}")

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


class ExamController(QObject):
    def __init__(self, agent, parent):
        super().__init__(parent)
        self.agent = agent
        self.guard = None
        self.browser = None
        self.browser_exam = None
        self.completed_browsers = []
        self.surfaces = []
        self.gaze_warnings = []
        self.active_exam = None
        self.displays = 0
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

    def release(self):
        if self.guard:
            self.guard.stop()
        self.agent.capabilities["guard_active"] = False
        for surface in self.surfaces:
            surface.hide()
        for warning in self.gaze_warnings:
            warning.hide()
        # Keep surfaces alive while a password request finishes.
        self.active_exam = None
        if self.browser:
            self.browser.released = True
            # Keep answers visible after the exam. Closing remains the user's choice.
            self.completed_browsers.append(self.browser)
            self.browser = None
            self.browser_exam = None

    def tick(self):
        snap = self.agent.snapshot()
        if self.browser and snap["state"]["lifecycle"] == "COMPLETED":
            self.release()
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
            self.browser.showFullScreen()
            if self.guard:
                self.agent.guard_target = self.guard.info(
                    int(self.browser.winId())
                ).public()
        screens = QApplication.screens()
        while len(self.gaze_warnings) < len(screens):
            self.gaze_warnings.append(GazeWarning())
        warn_about_gaze = gaze_warning_active(snap)
        for i, screen in enumerate(screens):
            warning = self.gaze_warnings[i]
            if warn_about_gaze:
                warning.place(screen)
                warning.show()
                warning.raise_()
            else:
                warning.hide()
        for extra in self.gaze_warnings[len(screens) :]:
            extra.hide()
        if snap["state"]["lifecycle"] != "RUNNING" or not snap["guarded"]:
            if self.active_exam:
                self.release()
            return
        desktop = bool((self.agent.guard_target or {}).get("desktop"))
        locked = snap["state"]["access"] == "LOCKED"
        if not self.active_exam:
            self.active_exam = snap["exam_id"]
            self.displays = len(screens)
            self.parent().hide()
        last_sync = self.agent.last_synced_at or self.agent.guard_started_at
        if last_sync is None or time.monotonic() - last_sync > 10:
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
                overlays=[int(s.winId()) for s in self.surfaces[: len(screens)]] + [
                    int(s.calibration.winId()) for s in self.surfaces
                    if s.calibration and s.calibration.isVisible()
                ],
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
