"""Monitor-sized calibration targets; camera sampling belongs to the worker."""

from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .screen_capture import TARGET_SETTLE_SECONDS, TARGET_VISIBLE_SECONDS


# Five fitting targets and four separate check targets, with no per-target click.
FIT_POINTS = ((.5, .5), (.05, .05), (.95, .05), (.95, .95), (.05, .95))
VALIDATION_POINTS = ((.5, .05), (.95, .5), (.5, .95), (.05, .5))
TARGET_SETTLE_MS = round(TARGET_SETTLE_SECONDS * 1000)
COUNTDOWN_INTERVAL_MS = 200


def screen_signature(screen):
    """Describe the selected screen in Qt logical coordinates, including origin."""
    rect = screen.geometry()
    return {
        "name": screen.name(),
        "serial": screen.serialNumber(),
        "geometry": [rect.x(), rect.y(), rect.width(), rect.height()],
        "device_pixel_ratio": float(screen.devicePixelRatio()),
        "logical_dpi": float(screen.logicalDotsPerInch()),
    }


def target_position(geometry, point, *, global_coordinates=False):
    """Map a normalized target without multiplying logical coordinates by DPI."""
    if (
        len(point) != 2
        or not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in point)
        or geometry.width() <= 0
        or geometry.height() <= 0
    ):
        raise ValueError("Invalid calibration target or monitor geometry")
    x = float(point[0]) * (geometry.width() - 1)
    y = float(point[1]) * (geometry.height() - 1)
    if global_coordinates:
        x += geometry.x()
        y += geometry.y()
    return QPointF(x, y)


class ScreenCalibrationDialog(QDialog):
    """A GUI-only presentation handshake for the nine-point camera worker.

    Connect ``started`` and ``target_presented`` to the worker's thread-safe
    input methods; connect worker signals to this object's GUI-thread slots.
    A target acknowledgement occurs once, only after paint and settling.
    """

    started = Signal(object)
    target_presented = Signal(int, object)
    cancelled = Signal()
    invalidated = Signal(str)

    def __init__(self, parent=None, *, screen=None):
        super().__init__(parent)
        self.setWindowTitle("Qorgau — настройка взгляда по экрану")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setModal(True)
        self.setStyleSheet(
            "QDialog { background: #101d33; }"
            "QLabel { color: #eef4ff; font: 16px 'Segoe UI'; }"
            "QWidget#calibrationPanel { background: #1b2d49; border-radius: 16px; }"
            "QPushButton, QComboBox { padding: 12px; font: 16px 'Segoe UI'; }"
            "QPushButton { background: #d5e8ff; color: #102440; border-radius: 8px; }"
            "QComboBox { background: white; color: #102440; }"
        )
        self._screens = list(QApplication.screens())
        if not self._screens:
            raise RuntimeError("Нет доступного монитора для настройки взгляда")
        if screen is not None and screen not in self._screens:
            raise RuntimeError("Выбранный монитор отключён. Выберите экран экзамена заново.")
        initial = screen if screen is not None else (
            parent.screen() if parent is not None else QApplication.primaryScreen())
        self._screen = initial if initial in self._screens else self._screens[0]
        self._signature = None
        self._active = False
        self._completed = False
        self._cancel_emitted = False
        self._target = None
        self._target_index = -1
        self._presentation_id = -1
        self._target_attempt = 1
        self._generation = 0
        self._painted_generation = -1
        self._acknowledged_generation = -1
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setTimerType(Qt.TimerType.PreciseTimer)
        self._settle.timeout.connect(self._ack_target)
        self._target_elapsed = QElapsedTimer()
        self._countdown = QTimer(self)
        self._countdown.setInterval(COUNTDOWN_INTERVAL_MS)
        self._countdown.timeout.connect(self._refresh_progress)
        self._progress_detail = "Смотрите на точку, не поворачивая голову"
        self._received_gaze_frames = 0
        self._success_timer = QTimer(self)
        self._success_timer.setSingleShot(True)
        self._success_timer.timeout.connect(self.accept)

        self.panel = QWidget(self)
        self.panel.setObjectName("calibrationPanel")
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(18)
        self.title = QLabel("Настройка взгляда по всему экрану", self.panel)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        point_count = len(FIT_POINTS) + len(VALIDATION_POINTS)
        duration_unit = "секунду" if TARGET_VISIBLE_SECONDS == 1 else (
            "секунды" if 2 <= TARGET_VISIBLE_SECONDS <= 4 else "секунд"
        )
        self.instructions = QLabel(
            "Выберите монитор, на котором будете проходить экзамен. "
            "Смотрите на появляющуюся точку, сохраняя обычное положение головы. "
            f"Каждая из {point_count} точек показывается {TARGET_VISIBLE_SECONDS:g} {duration_unit} — "
            f"первый проход займёт примерно {point_count * TARGET_VISIBLE_SECONDS:g} секунд. "
            "Неудачные измерения повторятся автоматически. При необходимости приложение "
            "заново уточнит границы экрана без перезапуска. Нажимать на точки не нужно. Esc — отмена.",
            self.panel,
        )
        self.instructions.setWordWrap(True)
        layout.addWidget(self.instructions)
        self.screen_selector = QComboBox(self.panel)
        for screen in self._screens:
            rect = screen.geometry()
            self.screen_selector.addItem(f"{screen.name()} — {rect.width()} × {rect.height()}")
        self.screen_selector.setCurrentIndex(self._screens.index(self._screen))
        self.screen_selector.setEnabled(screen is None)
        self.screen_selector.currentIndexChanged.connect(self._select_screen)
        layout.addWidget(self.screen_selector)
        self.error_label = QLabel("", self.panel)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.start_button = QPushButton("Начать настройку", self.panel)
        self.start_button.clicked.connect(self._start)
        layout.addWidget(self.start_button)
        self.cancel_button = QPushButton("Отмена (Esc)", self.panel)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        self.progress = QLabel("", self)
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress.setWordWrap(True)
        self.progress.hide()
        self._monitor_signals = []
        for screen in self._screens:
            for name in (
                "geometryChanged", "logicalDotsPerInchChanged", "physicalDotsPerInchChanged",
            ):
                signal = getattr(screen, name)

                def callback(*args, observed=screen):
                    self._screen_changed(observed)

                signal.connect(callback)
                self._monitor_signals.append((signal, callback))
        application = QApplication.instance()
        application.screenRemoved.connect(self._screen_removed)
        self._monitor_signals.append((application.screenRemoved, self._screen_removed))
        self._place_on_screen()

    @property
    def selected_signature(self):
        return screen_signature(self._screen)

    def _place_on_screen(self):
        # Create the native window before choosing its QScreen; this also works
        # for secondary monitors with negative desktop origins or another DPI.
        self.winId()
        self.windowHandle().setScreen(self._screen)
        self.setGeometry(self._screen.geometry())
        self._layout_contents()

    def showEvent(self, event):
        super().showEvent(event)
        self._place_on_screen()

    def hideEvent(self, event):
        super().hideEvent(event)
        if self._active:
            self._invalidate("Точки настройки скрыты. Откройте настройку заново.")

    def _select_screen(self, index):
        if not self._active and 0 <= index < len(self._screens):
            self._screen = self._screens[index]
            self._place_on_screen()

    def _start(self):
        if self._active or self._completed:
            return
        if self._screen not in QApplication.screens():
            self._invalidate("Выбранный монитор отключён. Откройте настройку заново.")
            return
        self._signature = self.selected_signature
        self._active = True
        self._target = None
        self.panel.hide()
        self.progress.setText("Смотрите на точку. Держите голову спокойно.\nEsc — отмена")
        self.progress.show()
        self._layout_contents()
        self.started.emit(dict(self._signature))

    def show_target(self, index, phase, point, count=0, total=3):
        """Present a unique token: target index + nine times the retry number."""
        if not self._active:
            return
        point = tuple(point)
        target_position(self.rect(), point)
        if type(index) is not int or not 0 <= index <= 2_147_483_647:
            raise ValueError("Invalid calibration target index")
        if phase not in {"fit", "validation"}:
            raise ValueError("Invalid calibration target phase")
        self._settle.stop()
        self._countdown.stop()
        self._target_elapsed.invalidate()
        self._generation += 1
        target_count = len(FIT_POINTS) + len(VALIDATION_POINTS)
        self._presentation_id = index
        self._target_index = index % target_count
        self._target_attempt = index // target_count + 1
        self._target = point
        self._phase = phase
        self._received_gaze_frames = 0
        self._progress_detail = "Смотрите на точку, не поворачивая голову"
        self._refresh_progress()
        self._layout_contents()
        self.update()

    def update_progress(self, count, total, message=""):
        if not self._active or self._acknowledged_generation != self._generation:
            return
        # Only the camera worker supplies accepted fresh gaze frames. Painting,
        # target acknowledgements and elapsed time never increase this counter.
        self._received_gaze_frames = count if type(count) is int and count >= 0 else 0
        self._progress_detail = message or "Смотрите на точку, не поворачивая голову"
        self._refresh_progress()

    def _refresh_progress(self):
        if not self._active or self._target is None:
            return
        phase = "Проверка настройки" if getattr(self, "_phase", "fit") == "validation" else "Настройка"
        elapsed = self._target_elapsed.elapsed() / 1000 if self._target_elapsed.isValid() else 0
        remaining = max(0., TARGET_VISIBLE_SECONDS - elapsed)
        timing = f"осталось {remaining:.1f} с" if remaining > 0 else "завершаем точку"
        heading = f"{phase} · точка {self._target_index + 1} из 9"
        retained = ""
        if self._target_attempt > 1:
            heading = f"Повтор точки {self._target_index + 1} · попытка {self._target_attempt}"
            retained = "Повторное измерение идёт автоматически.\n"
        value = (
            f"{heading} · {timing}\n"
            f"{retained}"
            f"Получено кадров взгляда: {self._received_gaze_frames}\n"
            f"{self._progress_detail} · Esc — отмена"
        )
        if self.progress.text() != value:
            self.progress.setText(value)
            self._layout_contents()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._target is None or not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        location = target_position(self.rect(), self._target)
        painter.setPen(QPen(QColor("#80caff"), 3))
        painter.setBrush(QColor("#244b76"))
        painter.drawEllipse(location, 23, 23)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(location, 6, 6)
        painter.end()
        if self.isVisible() and self._painted_generation != self._generation:
            self._painted_generation = self._generation
            self._target_elapsed.start()
            self._countdown.start()
            self._refresh_progress()
            self._settle.start(TARGET_SETTLE_MS)

    def _ack_target(self):
        if (
            not self._active or not self.isVisible() or self._target is None
            or self._painted_generation != self._generation
            or self._acknowledged_generation == self._generation
            or not self._target_elapsed.isValid()
        ):
            return
        elapsed = self._target_elapsed.elapsed()
        if elapsed < TARGET_SETTLE_MS:
            # A queued timeout from a replaced target (or sub-millisecond Qt
            # rounding) must wait for this presentation's own settling window.
            self._settle.start(max(1, TARGET_SETTLE_MS - elapsed))
            return
        if self.selected_signature != self._signature:
            self._invalidate("Параметры монитора изменились. Настройку нужно повторить.")
            return
        self._acknowledged_generation = self._generation
        self.target_presented.emit(self._presentation_id, dict(self._signature))

    def finish_error(self, message):
        if self._completed or self._cancel_emitted:
            return
        self._settle.stop()
        self._countdown.stop()
        self._active = False
        self._target = None
        self._signature = None
        self.progress.hide()
        self.error_label.setText(str(message))
        self.error_label.show()
        self.start_button.setText("Повторить настройку")
        self.panel.show()
        self._layout_contents()
        self.update()

    def finish_quality(self, message):
        if not self._active:
            return
        self._settle.stop()
        self._countdown.stop()
        self._active = False
        self._completed = True
        self._target = None
        self.progress.setText(str(message))
        self._layout_contents()
        self.update()
        self._success_timer.start(650)

    def _screen_changed(self, screen):
        if screen is not self._screen:
            return
        if self._active:
            self._invalidate("Размер или масштаб монитора изменился. Повторите настройку.")
        elif not self._completed:
            self._place_on_screen()

    def _screen_removed(self, screen):
        if screen is self._screen:
            self._invalidate("Выбранный монитор отключён. Откройте настройку заново.")

    def _invalidate(self, message):
        if self._cancel_emitted or self._completed:
            return
        self.invalidated.emit(message)
        self.reject()

    def reject(self):
        self._settle.stop()
        self._countdown.stop()
        self._success_timer.stop()
        self._active = False
        self._target = None
        if not self._completed and not self._cancel_emitted:
            self._cancel_emitted = True
            self.cancelled.emit()
        self._disconnect_monitors()
        super().reject()

    def accept(self):
        if not self._completed:
            return
        self._disconnect_monitors()
        super().accept()

    def closeEvent(self, event):
        if not self._completed:
            self.reject()
        else:
            self._disconnect_monitors()
        event.accept()

    def _disconnect_monitors(self):
        for signal, callback in self._monitor_signals:
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        self._monitor_signals.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_contents()

    def _layout_contents(self):
        if not hasattr(self, "panel"):
            return
        width = min(660, max(200, self.width() - 48))
        self.panel.setFixedWidth(width)
        self.panel.adjustSize()
        self.panel.move((self.width() - self.panel.width()) // 2,
                        max(0, (self.height() - self.panel.height()) // 2))
        if hasattr(self, "progress"):
            self.progress.setFixedWidth(min(760, max(200, self.width() - 48)))
            self.progress.adjustSize()
            y = round(self.height() * .36) if self._target and self._target[1] < .15 else 20
            self.progress.move((self.width() - self.progress.width()) // 2, y)
