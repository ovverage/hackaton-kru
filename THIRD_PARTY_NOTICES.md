# Компоненты Qorgau

Qorgau использует сторонние библиотеки и предварительно обученные модели. Они не являются разработанными командой с нуля алгоритмами. Версии Python-пакетов зафиксированы в `requirements-core.lock` и `requirements-student.lock`; происхождение и SHA-256 моделей — в `model-manifest.json`.

| Компонент | Назначение | Официальный источник / условия |
| --- | --- | --- |
| YOLO11n / Ultralytics | Дообученный детектор телефона, COCO initialization, экспорт ONNX | https://github.com/ultralytics/ultralytics ; AGPL-3.0, также существует коммерческое лицензирование разработчиком |
| YOLOv8n face / Bingsu-adetailer | Готовый отдельный детектор лиц, не собственное обучение Qorgau | https://huggingface.co/Bingsu/adetailer/blob/53cc19de382014514d9d4038601d261a7faa9b7b/README.md ; model card Apache-2.0, архитектура и экспорт Ultralytics AGPL-3.0 |
| COCO 2017 | Исходные рамки `cell phone` для дообучения | https://cocodataset.org/#termsofuse ; отдельные условия аннотаций и исходных изображений, идентификаторы лицензий сохранены в частном манифесте обучения |
| MediaPipe | Face Mesh: радужки, landmarks, blendshapes и матрица головы | https://github.com/google-ai-edge/mediapipe ; Apache-2.0 |
| scikit-learn | Обучение ExtraTrees на признаках MediaPipe; в EXE только экспортированный JSON | https://github.com/scikit-learn/scikit-learn ; BSD-3-Clause |
| ONNX Runtime | CPU inference | https://github.com/microsoft/onnxruntime ; MIT |
| OpenCV | Камера, обработка кадров | https://github.com/opencv/opencv ; Apache-2.0 и notices зависимостей |
| PySide6 / Qt | Интерфейс студента | https://doc.qt.io/qtforpython-6/licenses.html ; условия Qt for Python и используемых модулей |
| imageio-ffmpeg / FFmpeg | Поставка кодировщика / видео | https://github.com/imageio/imageio-ffmpeg ; BSD для Python-обёртки, отдельные условия FFmpeg |
| PyInstaller | Сборка EXE | https://github.com/pyinstaller/pyinstaller ; GPL с исключением для bootloader |
| Safe Exam Browser | Отдельно устанавливаемая экзаменационная среда | https://github.com/SafeExamBrowser/seb-win-refactoring ; условия официального проекта |

Тексты лицензий и доступные notices установленных пакетов собираются `scripts/prepare_licenses.py` в `dist/third-party/`; этот каталог включается в полный EXE и комплект сотрудника. В каталоге также находятся условия upstream-моделей и сведения о конкретном FFmpeg binary. Перечень выше не заменяет тексты лицензий и не является заявлением о выполнении всех условий промышленного распространения.

Исходники версий и команды воспроизведения доступны в Git-репозитории https://github.com/ovverage/hackaton-kru. Точный тег и SHA исходников указываются в соответствующем релизе. Исходные частные фотографии, видео, признаки лиц, токены и пароли не входят в Git и публичные артефакты. Отчёты об обучении и происхождении весов: `training/README.md`, `docs/MODEL_CARD_2026_10_06.md` и `model-manifest.json`.

Выбор лицензии самого проекта и обязательства при распространении AGPL-модели/FFmpeg/Qt должны быть закреплены владельцем перед промышленным тиражированием; документация не меняет лицензию проекта автоматически.
