# Компоненты Qorgau 0.3.0

Qorgau использует сторонние библиотеки и предварительно обученные модели. Они не являются разработанными командой с нуля алгоритмами. Версии Python-пакетов зафиксированы в `requirements-core.lock` и `requirements-student.lock`; происхождение и SHA-256 моделей — в `model-manifest.json`.

| Компонент | Назначение | Официальный источник / условия |
| --- | --- | --- |
| YOLO11n / Ultralytics | Модель телефона, экспорт ONNX | https://github.com/ultralytics/ultralytics ; AGPL-3.0, также существует коммерческое лицензирование разработчиком |
| MediaPipe | Лица и landmarks | https://github.com/google-ai-edge/mediapipe ; Apache-2.0 |
| ONNX Runtime | CPU inference | https://github.com/microsoft/onnxruntime ; MIT |
| OpenCV | Камера, обработка кадров | https://github.com/opencv/opencv ; Apache-2.0 и notices зависимостей |
| PySide6 / Qt | Интерфейс студента | https://doc.qt.io/qtforpython-6/licenses.html ; условия Qt for Python и используемых модулей |
| imageio-ffmpeg / FFmpeg | Поставка кодировщика / видео | https://github.com/imageio/imageio-ffmpeg ; BSD для Python-обёртки, отдельные условия FFmpeg |
| PyInstaller | Сборка EXE | https://github.com/pyinstaller/pyinstaller ; GPL с исключением для bootloader |
| Safe Exam Browser | Отдельно устанавливаемая экзаменационная среда | https://github.com/SafeExamBrowser/seb-win-refactoring ; условия официального проекта |

Тексты лицензий и доступные notices установленных пакетов собираются `scripts/prepare_licenses.py` в `dist/third-party/`; этот каталог включается в полный EXE и комплект сотрудника. В каталоге также находятся условия upstream-моделей и сведения о конкретном FFmpeg binary. Перечень выше не заменяет тексты лицензий и не является заявлением о выполнении всех условий промышленного распространения.

Исходники данной сборки и команды воспроизведения сохраняются в локальном `dist/Qorgau-Source-0.3.0.zip` без секретов, пользовательских данных и видеозаписей. Git-репозиторий: https://github.com/ovverage/hackaton-kru. Рабочие изменения этой сборки ещё не опубликованы в Git. Выбор лицензии самого проекта, обязательства при распространении AGPL-модели/FFmpeg/Qt и при необходимости коммерческие лицензии должны быть закреплены владельцем перед промышленным тиражированием; документация не меняет лицензию проекта автоматически.
