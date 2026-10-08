"""Explicit, cancellable camera preparation inside the student app."""

from pathlib import Path
import threading
import time
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QSpinBox,
    QProgressBar,
    QSizePolicy,
)
from .client import atomic_json
from .behavior import POSITIONS
from .theme import COLORS, StepBubble, camera_marks

REFERENCE_SETTLE_SECONDS = 2
REFERENCE_TIMEOUT_SECONDS = 45


class CameraPreview(QLabel):
    """Live preview that keeps its frame visible through DPI and resize changes."""

    def __init__(self, placeholder: str, parent=None):
        super().__init__(placeholder, parent)
        self._camera_image = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, image: QImage):
        self._camera_image = image.copy()
        self._fit_image()

    def _fit_image(self):
        if self._camera_image is None or self._camera_image.isNull():
            return
        target = self.contentsRect().size()
        if target.width() <= 1 or target.height() <= 1:
            return
        self.setPixmap(QPixmap.fromImage(self._camera_image).scaled(
            target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_image()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._camera_image is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        camera_marks(painter, self.contentsRect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS['navy900']))
        painter.drawRoundedRect(29, 27, 165, 30, 15, 15)
        painter.setBrush(QColor(COLORS['blue_light']))
        painter.drawEllipse(40, 38, 8, 8)
        painter.setPen(QColor(COLORS['surface']))
        painter.drawText(56, 48, "Камера включена")
        pen = QPen(QColor(255, 255, 255, 190), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        oval_width, oval_height = self.width() * .34, self.height() * .68
        painter.drawEllipse(int((self.width()-oval_width)/2), int((self.height()-oval_height)/2), int(oval_width), int(oval_height))


class CameraStartWorker(QThread):
    """Preview first; collect a demo reference only after explicit confirmation."""

    frame = Signal(QImage)
    message = Signal(str)
    ready = Signal()
    reference_required = Signal()
    reference_progress = Signal(object)
    screen_reference_required = Signal()
    screen_target = Signal(int, str, object, int, int)
    screen_progress = Signal(int, int, str)
    screen_result = Signal(object)

    def __init__(self, index, phone, face, folder):
        super().__init__()
        self.index, self.phone, self.face, self.folder = index, phone, face, folder
        self.stop = threading.Event()
        self.confirm = threading.Event()
        self.result = None
        self.error = ""
        self.preview_source = None
        from .screen_capture import ScreenCaptureSession
        self.screen_session = ScreenCaptureSession()

    def run(self):
        camera = None
        try:
            from .vision import Camera

            self.message.emit("Включаем камеру и распознавание…")
            if self.stop.is_set():
                return
            camera = Camera(self.index, self.phone, self.face, calibrate=False)
            if callable(getattr(camera, 'latest_preview', None)):
                self.preview_source = camera
            public_reference = getattr(camera, "public_gaze", None) is not None
            first = True
            collecting_reference = False
            reference_started = 0
            screen_reference = public_reference and getattr(camera, 'screen_calibration_required', False)

            def read_screen_frame():
                _, observation = camera.read()
                # The fullscreen target remains the focus; no preview of the
                # face or recording distracts from the labelled gaze location.
                return observation

            while not self.stop.is_set():
                frame, _ = camera.read()
                # The live GUI pulls the newest raw slot independently of
                # these unchanged full-model passes. No queued image backlog.
                if self.preview_source is None:
                    rgb = camera.cv2.cvtColor(frame, camera.cv2.COLOR_BGR2RGB)
                    height, width = rgb.shape[:2]
                    self.frame.emit(QImage(rgb.data, width, height, int(rgb.strides[0]),
                                           QImage.Format.Format_RGB888).copy())
                if first:
                    first = False
                    if screen_reference:
                        self.screen_reference_required.emit()
                    elif public_reference:
                        self.reference_required.emit()
                    self.ready.emit()
                if screen_reference:
                    signature = self.screen_session.next_start()
                    if signature is not None:
                        result = self.screen_session.run(camera, signature, self.stop,
                            target=self.screen_target.emit, progress=self.screen_progress.emit,
                            read=read_screen_frame)
                        from .calibration_diagnostics import save_calibration_report
                        from shared.version import APP_VERSION
                        try:
                            save_calibration_report(self.folder, result, version=APP_VERSION)
                        except (OSError, ValueError):
                            # Diagnostics must not change acceptance or interrupt a retry.
                            self.message.emit('Не удалось сохранить диагностику настройки.')
                        self.screen_result.emit(result)
                        if result['ready']:
                            break
                    self.stop.wait(.01)
                    continue
                if public_reference:
                    if collecting_reference:
                        progress = dict(camera.gaze_reference_progress)
                        if (
                            not progress["ready"]
                            and time.monotonic() - reference_started >= REFERENCE_TIMEOUT_SECONDS
                        ):
                            camera.cancel_gaze_reference()
                            progress = dict(camera.gaze_reference_progress)
                            progress["error"] = "GAZE_REFERENCE_TIMEOUT"
                        self.reference_progress.emit(progress)
                        if progress["ready"]:
                            break
                        if progress.get("error"):
                            collecting_reference = False
                            self.confirm.clear()
                    if not collecting_reference and self.confirm.is_set():
                        self.confirm.clear()
                        # Allow the student to move their eyes from the button
                        # to the screen centre before any observations count.
                        if self.stop.wait(REFERENCE_SETTLE_SECONDS):
                            return
                        camera.begin_gaze_reference()
                        reference_started = time.monotonic()
                        collecting_reference = True
                    self.stop.wait(0.08)
                elif self.confirm.wait(0.08):
                    break
            if self.stop.is_set():
                return
            # The first frame after confirmation establishes the automatic
            # reference while the student is looking at the monitor.
            if not public_reference and getattr(camera, "gaze", None):
                camera.gaze.reference = None
            self.result, camera = camera, None
        except Exception as error:
            self.error = str(error)
        finally:
            self.preview_source = None
            if camera:
                camera.close()


class CalibrationWorker(QThread):
    frame = Signal(QImage)
    phase = Signal(int, int, bool)
    message = Signal(str)

    def __init__(self, index, phone, face, folder):
        super().__init__()
        self.index = index
        self.phone = phone
        self.face = face
        self.folder = folder
        self.stop = threading.Event()
        self.collect = threading.Event()
        self.result = None
        self.error = ""

    def run(self):
        camera = None
        try:
            from .vision import Camera

            self.message.emit("Загружаем локальные модели и открываем камеру…")
            camera = Camera(self.index, self.phone, self.face, calibrate=False)
            if getattr(camera, "public_gaze", None) is not None:
                raise ValueError(
                    "Для этой сборки настройте взгляд при обычном включении камеры: "
                    "закройте это окно и нажмите «Проверить камеру» рядом с выбранной камерой."
                )
            for index, (key, prompt) in enumerate(POSITIONS):
                self.collect.clear()
                samples = []
                self.phase.emit(index, 0, False)
                while len(samples) < 25 and not self.stop.is_set():
                    frame, _ = camera.read(analyze=False)
                    frame = camera.cv2.resize(frame, (640, 480))
                    faces, feature = camera.face_features(frame)
                    rgb = camera.cv2.cvtColor(frame, camera.cv2.COLOR_BGR2RGB)
                    self.frame.emit(
                        QImage(
                            rgb.data,
                            640,
                            480,
                            int(rgb.strides[0]),
                            QImage.Format.Format_RGB888,
                        ).copy()
                    )
                    valid = faces == 1 and feature is not None
                    if self.collect.is_set() and valid:
                        samples.append(feature)
                    self.phase.emit(index, len(samples), valid)
                    self.stop.wait(0.08)
                if self.stop.is_set():
                    return
                camera.centres[key] = camera.np.median(samples, axis=0)
            camera.validate_calibration()
            if self.stop.is_set():
                return
            self.result = camera
            camera = None
        except ImportError:
            self.error = "Модули распознавания не установлены в этой сборке. Сотруднику нужно установить зависимости CV и локальные модели. Камера не подготовлена."
        except Exception as error:
            self.error = str(error)
        finally:
            if camera:
                camera.close()


class CameraSetup(QDialog):
    def __init__(self, agent, parent=None, *, calibrate=False, index=None, screen=None):
        super().__init__(parent)
        self.agent = agent
        self.calibrate = calibrate
        self.worker = None
        self.cancelled = False
        self.preparing = False
        self.preview_ready = False
        self.public_reference = False
        self.screen_reference = False
        self.screen_dialog = None
        self.calibration_screen = screen
        self._handled_worker = None
        self._preview_sequence = None
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(67)
        self.preview_timer.timeout.connect(self.update_latest_preview)
        self.setWindowTitle(
            "Qorgau — настройка взгляда" if calibrate else "Qorgau — включение камеры"
        )
        # Leave enough vertical room at Windows display scaling so the preview,
        # instructions and button labels never overlap or get clipped.
        self.resize(760, 780)
        self.setMinimumSize(520, 580)
        from .desktop import STYLE, label

        self.setStyleSheet(STYLE)
        self.setObjectName("window")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(
            label("Настройка взгляда" if calibrate else "Проверка камеры", "title")
        )
        layout.addWidget(
            label(
                (
                    "Дополнительная настройка контроля взгляда: центр, края и пространство за экраном. "
                    if calibrate
                    else "Проверьте изображение с выбранной камеры. "
                )
                + "Звук не записывается.",
                "body",
            )
        )
        settings = agent.config.get("camera_settings", {})
        row = QHBoxLayout()
        row.addWidget(label("Номер камеры", "field"))
        self.index = QSpinBox()
        self.index.setRange(0, 9)
        self.index.setValue(settings.get("index", 0) if index is None else index)
        row.addWidget(self.index)
        row.addStretch()
        layout.addLayout(row)
        from .resources import verified_models

        self.model_error = ""
        try:
            phone, face = verified_models()
        except ValueError as error:
            phone = face = ""
            self.model_error = str(error)
        self.phone = QLineEdit(str(phone))
        self.face = QLineEdit(str(face))
        self.phone.hide()
        self.face.hide()
        layout.addWidget(
            label(
                self.model_error
                or "",
                "small",
            )
        )
        self.preview = CameraPreview("Предпросмотр камеры появится здесь")
        self.preview.setStyleSheet(
            f"background:{COLORS['scene']};border:1px solid {COLORS['line']};border-radius:12px;color:{COLORS['surface']};"
        )
        layout.addWidget(self.preview, 1)
        guides = QHBoxLayout()
        guides.setSpacing(18)
        for number, title, hint in (
            (1, "Лицо в овале", "Свет спереди, не сзади"),
            (2, "Посмотрите на экран", "Qorgau запомнит позу"),
            (3, "Нажмите кнопку", "Больше ничего не нужно"),
        ):
            guide = QHBoxLayout()
            guide.setSpacing(6)
            guide.addWidget(StepBubble(number, "now" if number == 1 else "empty"), 0, Qt.AlignmentFlag.AlignTop)
            wording = QVBoxLayout()
            wording.setSpacing(3)
            wording.addWidget(label(title, "field"))
            wording.addWidget(label(hint, "small"))
            guide.addLayout(wording)
            guides.addLayout(guide, 1)
        layout.addLayout(guides)
        layout.addWidget(label("Изображение остаётся на этом компьютере. Звук не записывается.", "small"))
        self.instruction = label(
            "Перед началом уберите телефон и убедитесь, что в кадре только вы."
            if calibrate
            else "После подключения дождитесь начала теста.",
            "heading",
        )
        layout.addWidget(self.instruction)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress.setVisible(calibrate)
        layout.addWidget(self.progress)
        self.feedback = label("Распознавание пока выключено", "small")
        layout.addWidget(self.feedback)
        buttons = QHBoxLayout()
        self.cancel = QPushButton("Отмена")
        self.cancel.clicked.connect(self.reject)
        buttons.addWidget(self.cancel)
        buttons.addStretch()
        self.capture = QPushButton("Смотрю в нужную сторону")
        self.capture.clicked.connect(self.capture_position)
        self.capture.hide()
        buttons.addWidget(self.capture)
        self.start_button = QPushButton("Проверить камеру")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.primary_action)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)
        if not calibrate:
            QTimer.singleShot(0, self.start)

    def path_field(self, layout, title, value, pattern):
        from .desktop import label

        layout.addWidget(label(title, "field"))
        row = QHBoxLayout()
        field = QLineEdit(value)
        field.setAccessibleName(title)
        row.addWidget(field)
        button = QPushButton("Выбрать…")
        button.clicked.connect(lambda: self.browse(field, pattern))
        row.addWidget(button)
        layout.addLayout(row)
        return field

    def browse(self, field, pattern):
        file, _ = QFileDialog.getOpenFileName(
            self, "Выберите локальную модель", str(Path.home()), pattern
        )
        if file:
            field.setText(file)

    def start(self):
        if self.cancelled:
            return
        if self.worker and self.worker.isRunning():
            return
        phone = Path(self.phone.text().strip())
        face = Path(self.face.text().strip())
        if not phone.is_file() or not face.is_file():
            self.feedback.setText(
                self.model_error
                or "Не найден комплект моделей. Повторно скачайте полную сборку."
            )
            return
        with self.agent.mutex:
            state = self.agent.engine.state
            recoverable = state.access == "LOCKED" and state.reason in (
                "AGENT_RESTARTED",
                "CAMERA_UNAVAILABLE",
                "DISPLAY_CHANGED",
            )
            if state.lifecycle == "RUNNING" and not recoverable:
                self.feedback.setText(
                    "Сеанс уже начался. Подготовку камеры нужно выполнить до старта."
                )
                return
            self.agent.camera_preparing = True
            self.preparing = True
            if self.agent.capture_pump:
                self.agent.capture_pump.close()
                self.agent.capture_pump = None
            if self.agent.camera:
                if self.agent.camera.close() is False:
                    self.agent.camera_preparing = self.preparing = False
                    self.agent.capabilities['gaze'] = False
                    self.feedback.setText('Камера завершает обработку. Повторите через несколько секунд.')
                    return
                self.agent.camera = None
            self.agent.capabilities.update(camera=False, recording=False, gaze=False)
        self.start_button.setEnabled(False)
        self.start_button.setText("Проверяем камеру…")
        self.preview_ready = False
        self.public_reference = False
        self.screen_reference = False
        self.progress.setVisible(self.calibrate)
        self.phone.setEnabled(False)
        self.face.setEnabled(False)
        self.index.setEnabled(False)
        worker_type = CalibrationWorker if self.calibrate else CameraStartWorker
        self.worker = worker_type(
            self.index.value(), phone.resolve(), face.resolve(), self.agent.folder
        )
        self.worker.frame.connect(self.show_frame)
        if not self.calibrate:
            self.worker.ready.connect(self.camera_ready)
            self.worker.reference_required.connect(self.require_reference)
            self.worker.reference_progress.connect(self.reference_progress)
            self.worker.screen_reference_required.connect(self.require_screen_reference)
            self.worker.screen_target.connect(self.show_screen_target)
            self.worker.screen_progress.connect(self.screen_progress)
            self.worker.screen_result.connect(self.screen_result)
        if self.calibrate:
            self.worker.phase.connect(self.phase)
        self.worker.message.connect(self.feedback.setText)
        self.worker.finished.connect(self.finished_calibration)
        self.worker.start()
        self._preview_sequence = None
        self.preview_timer.start()

    def primary_action(self):
        if (
            not self.calibrate
            and self.worker
            and self.worker.isRunning()
            and self.preview_ready
        ):
            self.start_button.setEnabled(False)
            if self.screen_reference:
                self.open_screen_calibration()
                return
            elif self.public_reference:
                self.start_button.setText("Настраиваем взгляд…")
                self.instruction.setText("Смотрите в центр экрана и спокойно держите голову.")
                self.feedback.setText("Сейчас начнётся настройка. В кадре должны быть только вы.")
                self.progress.setValue(0)
                self.progress.show()
            else:
                self.start_button.setText("Подключаем камеру…")
                self.feedback.setText("Сохраняем выбранную камеру…")
            self.worker.confirm.set()
            return
        self.start()

    def camera_ready(self):
        if self.cancelled or self.calibrate:
            return
        self.preview_ready = True
        if self.screen_reference:
            self.instruction.setText('Следите глазами за 9 точками: по 3 секунды на каждую, первый проход — примерно 27 секунд.')
            self.feedback.setText('Неудачные измерения повторятся автоматически; перезапуск не нужен. Esc — отмена.')
            self.start_button.setText('Настроить по точкам')
            self.start_button.setEnabled(True)
            QTimer.singleShot(0, self.open_screen_calibration)
            return
        self.instruction.setText(
            "Посмотрите в центр экрана. Нажмите кнопку и удерживайте взгляд, пока идёт настройка."
            if self.public_reference else
            "Убедитесь, что лицо хорошо видно, посмотрите на монитор и подтвердите камеру."
        )
        self.feedback.setText("Предпросмотр работает. Изображение остаётся только на этом компьютере.")
        self.start_button.setText("Смотрю в центр экрана" if self.public_reference else "Использовать эту камеру")
        self.start_button.setEnabled(True)

    def require_reference(self):
        if not self.cancelled:
            self.public_reference = True

    def require_screen_reference(self):
        if not self.cancelled:
            self.public_reference = self.screen_reference = True

    def open_screen_calibration(self):
        from .screen_calibration import ScreenCalibrationDialog
        if self.screen_dialog is not None or self.cancelled or not self.worker.isRunning():
            return
        try:
            dialog = self.screen_dialog = ScreenCalibrationDialog(self, screen=self.calibration_screen)
        except RuntimeError as error:
            self.worker.error = str(error)
            self.reject()
            return
        dialog.started.connect(self.worker.screen_session.start)
        dialog.target_presented.connect(self.worker.screen_session.presented)
        dialog.cancelled.connect(self.reject)
        dialog.invalidated.connect(lambda reason: self.reject())
        dialog.finished.connect(self.screen_dialog_finished)
        dialog.showFullScreen()

    def show_screen_target(self, index, phase, point, count, total):
        if self.screen_dialog is not None and not self.cancelled:
            self.screen_dialog.show_target(index, phase, point, count, total)

    def screen_progress(self, count, total, message):
        if self.screen_dialog is not None and not self.cancelled:
            self.screen_dialog.update_progress(count, total, message)

    def screen_result(self, result):
        if self.screen_dialog is None or self.cancelled:
            return
        if result.get('ready'):
            self.screen_dialog.finish_quality('Личная зона экрана настроена. Запас за границей: 6°.')
        else:
            from .calibration_diagnostics import format_calibration_failure
            self.screen_dialog.finish_error(format_calibration_failure(result))

    def screen_dialog_finished(self, result):
        self.screen_dialog = None
        if result != QDialog.DialogCode.Accepted and not self.cancelled:
            self.reject()
        elif result == QDialog.DialogCode.Accepted and not self.worker.isRunning():
            self.finished_calibration()

    def reference_progress(self, progress):
        if self.cancelled:
            return
        required = max(15, progress.get("required", 25))
        count = max(0, min(required, progress.get("collected", 0)))
        self.progress.setValue(round(count / required * 100))
        if progress.get("ready"):
            self.feedback.setText("Взгляд настроен. Подключаем камеру…")
            self.start_button.setEnabled(False)
        elif progress.get("error"):
            self.feedback.setText(
                "Не удалось получить устойчивое положение. Смотрите в центр экрана, "
                "держите голову спокойно и проверьте освещение. В кадре должны быть только вы."
            )
            self.start_button.setText("Повторить настройку")
            self.start_button.setEnabled(True)
        else:
            self.feedback.setText(
                f"Удерживайте взгляд в центре экрана… {count}/{required}. "
                "Нужно одно хорошо освещённое лицо и открытые глаза."
            )

    def update_latest_preview(self):
        if self.cancelled or (self.screen_dialog is not None and self.screen_dialog.isVisible()):
            return
        source = getattr(self.worker, 'preview_source', None)
        packet = source.latest_preview() if source is not None else None
        if packet is None or packet.sequence == self._preview_sequence:
            return
        self._preview_sequence = packet.sequence
        frame = packet.frame
        height, width = frame.shape[:2]
        scale = min(1., 640 / width, 360 / height)
        small = source.cv2.resize(frame, (round(width * scale), round(height * scale)))
        rgb = source.cv2.cvtColor(small, source.cv2.COLOR_BGR2RGB)
        self.show_frame(QImage(rgb.data, rgb.shape[1], rgb.shape[0], int(rgb.strides[0]),
                              QImage.Format.Format_RGB888).copy())

    def show_frame(self, image):
        self.preview.set_image(image)

    def phase(self, index, n, valid):
        self.instruction.setText(
            f"Шаг {index + 1} из {len(POSITIONS)}. {POSITIONS[index][1]}"
        )
        self.progress.setValue(round((index * 25 + n) / (len(POSITIONS) * 25) * 100))
        self.capture.show()
        self.capture.setEnabled(valid and not self.worker.collect.is_set())
        self.feedback.setText(
            f"Удерживайте взгляд… {n}/25"
            if self.worker.collect.is_set() and valid
            else "Лицо видно. Посмотрите в нужную сторону и нажмите кнопку."
            if valid
            else "Нужно одно хорошо освещённое лицо и открытые глаза. Сбор кадров приостановлен."
        )

    def capture_position(self):
        if self.worker:
            self.worker.collect.set()
            self.capture.setEnabled(False)

    def finished_calibration(self):
        self.preview_timer.stop()
        if self._handled_worker is self.worker:
            return
        if (self.screen_dialog is not None and not self.cancelled
                and self.worker.result is not None):
            # Let the fullscreen success indicator auto-close before closing
            # this parent dialog and transferring camera ownership to Agent.
            return
        self._handled_worker = self.worker
        if self.worker.error and self.screen_dialog is not None:
            dialog, self.screen_dialog = self.screen_dialog, None
            dialog.cancelled.disconnect(self.reject)
            dialog.invalidated.disconnect()
            dialog.finished.disconnect(self.screen_dialog_finished)
            dialog.reject()
            dialog.deleteLater()
        with self.agent.mutex:
            self.agent.camera_preparing = False
            self.preparing = False
            if self.worker.result:
                camera = self.worker.result
                self.worker.result = None
                public_reference_missing = (
                    getattr(camera, "public_gaze", None) is not None
                    and not camera.gaze_reference_progress["ready"]
                )
                if public_reference_missing:
                    camera.close()
                    self.worker.error = "Настройка взгляда не завершена. Повторите включение камеры."
                elif self.cancelled or (
                    self.agent.engine.state.lifecycle == "RUNNING"
                    and self.agent.engine.state.access != "LOCKED"
                ):
                    camera.close()
                else:
                    from .recording import ClipRecorder

                    try:
                        recorder = self.agent.recorder or ClipRecorder(
                            self.agent.folder / "clips"
                        )
                    except (OSError, ValueError) as error:
                        camera.close()
                        self.worker.error = str(error)
                        self.feedback.setText(str(error))
                        self.start_button.setEnabled(True)
                        return
                    self.agent.camera = camera
                    self.agent.recorder = recorder
                    self.agent.collect_media(float("inf"))
                    recorder.reset_session(self.agent.journal.get("exam_id"))
                    if recorder.last_t is not None:
                        import time

                        self.agent.origin = min(
                            self.agent.origin, time.monotonic() - recorder.last_t - 0.1
                        )
                    self.agent.camera_fault = False
                    self.agent.capabilities.update(
                        camera=True,
                        recording=True,
                        gaze=bool(camera.centres) or bool(getattr(camera, 'gaze_enabled', False)),
                        vision="yolo11n-onnx/mediapipe-personal-calibration"
                        if camera.centres
                        else "yolo11n-phone/yolov8n-face/public-gaze-v1"
                        if getattr(camera, "public_gaze", None) is not None
                        else "yolo11n-phone/yolov8n-face/mediapipe-auto-gaze",
                    )
                    self.agent.config["camera_settings"] = {
                        "index": self.index.value(),
                        "phone_model": str(self.worker.phone),
                        "face_model": str(self.worker.face),
                    }
                    try:
                        atomic_json(
                            self.agent.folder / "config.json", self.agent.config
                        )
                    except OSError:
                        self.feedback.setText(
                            "Камера подготовлена, но пути моделей не удалось сохранить."
                        )
                    self.accept()
                    return
        if self.cancelled:
            super().reject()
            return
        self.feedback.setText(
            self.worker.error or "Подготовку нужно повторить до начала сеанса."
        )
        self.preview_ready = False
        self.start_button.setText("Повторить проверку камеры")
        self.start_button.setEnabled(True)
        self.phone.setEnabled(True)
        self.face.setEnabled(True)
        self.index.setEnabled(True)
        self.capture.hide()

    def reject(self):
        self.cancelled = True
        self.preview_timer.stop()
        if self.screen_dialog is not None:
            self.screen_dialog.hide()
        if self.worker and self.worker.isRunning():
            self.cancelled = True
            self.worker.stop.set()
            self.cancel.setEnabled(False)
            self.capture.setEnabled(False)
            self.feedback.setText("Отключаем камеру…")
            return
        if self.worker is not None and self._handled_worker is not self.worker:
            self.finished_calibration()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            self.reject()
            event.accept()
