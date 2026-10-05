"""Explicit, cancellable camera preparation inside the student app."""

from pathlib import Path
import threading
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPixmap
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
)
from .client import atomic_json
from .behavior import POSITIONS

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
            for index, (key, prompt) in enumerate(POSITIONS):
                self.collect.clear()
                samples = []
                self.phase.emit(index, 0, False)
                while len(samples) < 25 and not self.stop.is_set():
                    ok, frame = camera.capture.read()
                    if not ok:
                        raise OSError(
                            "Камера не передаёт изображение. Проверьте подключение и разрешения."
                        )
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
    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.worker = None
        self.cancelled = False
        self.preparing = False
        self.setWindowTitle("Qorgau — подготовка камеры")
        self.resize(720, 720)
        self.setMinimumWidth(620)
        from .desktop import STYLE, label

        self.setStyleSheet(STYLE)
        self.setObjectName("window")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)
        layout.addWidget(label("Подготовка камеры", "title"))
        layout.addWidget(
            label(
                "Камера включится после нажатия кнопки. Сначала посмотрим в центр и на края экрана, затем за его пределы. Анализ выполняется на этом ПК, без записи звука. Преподаватель получает события и видеофрагменты для проверки экзамена. Видео хранится 7 дней; преподаватель может продлить разбор инцидента. Локальная копия удаляется после отправки. Резервные копии сервера хранятся ещё до 7 дней.",
                "body",
            )
        )
        settings = agent.config.get("camera_settings", {})
        row = QHBoxLayout()
        row.addWidget(label("Номер камеры", "field"))
        self.index = QSpinBox()
        self.index.setRange(0, 9)
        self.index.setValue(settings.get("index", 0))
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
                self.model_error or "Модели проверены. Анализ выполняется на этом компьютере без внешнего сервиса распознавания.",
                "small",
            )
        )
        self.preview = QLabel("Предпросмотр камеры появится здесь")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(230)
        self.preview.setStyleSheet(
            "background:#e9eee1;border:1px solid #dce5d0;border-radius:9px;color:#829773;"
        )
        layout.addWidget(self.preview, 1)
        self.instruction = label(
            "Перед началом уберите телефон и убедитесь, что в кадре только вы.",
            "heading",
        )
        layout.addWidget(self.instruction)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
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
        self.start_button = QPushButton("Включить камеру и начать")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)

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
        if self.worker and self.worker.isRunning():
            return
        phone = Path(self.phone.text().strip())
        face = Path(self.face.text().strip())
        if not phone.is_file() or not face.is_file():
            self.feedback.setText(
                self.model_error or "Не найден комплект моделей. Повторно скачайте полную сборку."
            )
            return
        with self.agent.mutex:
            state = self.agent.engine.state
            recoverable = state.access == "LOCKED" and state.reason in ("AGENT_RESTARTED", "CAMERA_UNAVAILABLE")
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
                self.agent.camera.close()
                self.agent.camera = None
            self.agent.capabilities.update(camera=False, recording=False)
        self.start_button.setEnabled(False)
        self.phone.setEnabled(False)
        self.face.setEnabled(False)
        self.index.setEnabled(False)
        self.worker = CalibrationWorker(
            self.index.value(), phone.resolve(), face.resolve(), self.agent.folder
        )
        self.worker.frame.connect(self.show_frame)
        self.worker.phase.connect(self.phase)
        self.worker.message.connect(self.feedback.setText)
        self.worker.finished.connect(self.finished_calibration)
        self.worker.start()

    def show_frame(self, image):
        self.preview.setPixmap(
            QPixmap.fromImage(image).scaled(
                self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def phase(self, index, n, valid):
        self.instruction.setText(f"Шаг {index + 1} из {len(POSITIONS)}. {POSITIONS[index][1]}")
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
        with self.agent.mutex:
            self.agent.camera_preparing = False
            self.preparing = False
            if self.worker.result:
                camera = self.worker.result
                if self.cancelled or (self.agent.engine.state.lifecycle == "RUNNING" and self.agent.engine.state.access != "LOCKED"):
                    camera.close()
                else:
                    from .recording import ClipRecorder
                    try:
                        recorder = self.agent.recorder or ClipRecorder(self.agent.folder / "clips")
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
                        self.agent.origin = min(self.agent.origin, time.monotonic() - recorder.last_t - .1)
                    self.agent.camera_fault = False
                    self.agent.capabilities.update(
                        camera=True,
                        recording=True,
                        vision="yolo11n-onnx/mediapipe-personal-calibration",
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
        self.start_button.setEnabled(True)
        self.phone.setEnabled(True)
        self.face.setEnabled(True)
        self.index.setEnabled(True)
        self.capture.hide()

    def reject(self):
        if self.worker and self.worker.isRunning():
            self.cancelled = True
            self.worker.stop.set()
            self.cancel.setEnabled(False)
            self.capture.setEnabled(False)
            self.feedback.setText("Отключаем камеру…")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.reject()
            event.ignore()
        else:
            event.accept()
