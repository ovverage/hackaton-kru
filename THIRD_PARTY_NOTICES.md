# Компоненты Qorgau

Qorgau использует сторонние библиотеки и предварительно обученные модели. Они не являются разработанными командой с нуля алгоритмами. Версии Python-пакетов зафиксированы в `requirements-core.lock` и `requirements-student.lock`; происхождение и SHA-256 моделей — в `model-manifest.json`.

| Компонент | Назначение | Официальный источник / условия |
| --- | --- | --- |
| YOLO11n / Ultralytics | Дообученный детектор телефона, COCO initialization, экспорт ONNX | https://github.com/ultralytics/ultralytics ; AGPL-3.0, также существует коммерческое лицензирование разработчиком |
| YOLOv8n face / Bingsu-adetailer | Готовый отдельный детектор лиц, не собственное обучение Qorgau | https://huggingface.co/Bingsu/adetailer/blob/53cc19de382014514d9d4038601d261a7faa9b7b/README.md ; model card Apache-2.0, архитектура и экспорт Ultralytics AGPL-3.0 |
| COCO 2017 | Исходные рамки `cell phone` для дообучения | https://cocodataset.org/#termsofuse ; отдельные условия аннотаций и исходных изображений, идентификаторы лицензий сохранены в частном манифесте обучения |
| Gaze360 | Данные обучения исследовательской CNN взгляда, если выбран `runtime_gaze: public-gaze-v1` | https://github.com/erkil1452/gaze360/blob/546762ef1373dae13569afdfbe501a834040e8c8/LICENSE.md ; отдельная Research License для базы, моделей и связанного исходного кода; условия и цитирование сохранены в `docs/licenses/` |
| MPIIFaceGaze | Данные обучения исследовательской CNN взгляда, если выбран `runtime_gaze: public-gaze-v1` | https://doi.org/10.18419/DARUS-3240 ; CC BY-NC-SA 4.0, некоммерческие научные цели; текст и цитирование сохранены в `docs/licenses/` |
| MediaPipe | Face Mesh: радужки, landmarks, blendshapes и матрица головы | https://github.com/google-ai-edge/mediapipe ; Apache-2.0 |
| scikit-learn | Обучение ExtraTrees на признаках MediaPipe; в EXE только экспортированный JSON | https://github.com/scikit-learn/scikit-learn ; BSD-3-Clause |
| ONNX Runtime | CPU inference | https://github.com/microsoft/onnxruntime ; MIT |
| OpenCV | Камера, обработка кадров | https://github.com/opencv/opencv ; Apache-2.0 и notices зависимостей |
| PySide6 / Qt | Интерфейс студента | https://doc.qt.io/qtforpython-6/licenses.html ; условия Qt for Python и используемых модулей |
| imageio-ffmpeg / FFmpeg | Поставка кодировщика / видео | https://github.com/imageio/imageio-ffmpeg ; BSD для Python-обёртки, отдельные условия FFmpeg |
| PyInstaller | Сборка EXE | https://github.com/pyinstaller/pyinstaller ; GPL с исключением для bootloader |
| Geologica | Шрифт интерфейсов преподавателя и ученика | https://github.com/googlefonts/geologica ; SIL Open Font License 1.1, текст включён в `agent/fonts/OFL.txt` |
| Safe Exam Browser | Отдельно устанавливаемая экзаменационная среда | https://github.com/SafeExamBrowser/seb-win-refactoring ; условия официального проекта |

Тексты лицензий и доступные notices установленных пакетов собираются `scripts/prepare_licenses.py` в `dist/third-party/`; этот каталог включается в полный EXE и комплект сотрудника. В каталоге также находятся условия upstream-моделей и сведения о конкретном FFmpeg binary. Полные условия приведены в текстах лицензий.

Для профиля `public-gaze-v1` сборка дополнительно включает сохранённые тексты Gaze360 и MPIIFaceGaze, обе ссылки на научные публикации и `Research-Gaze-Notice.md`. Лицензия Gaze360 ограничивает использование исследованием, кругом прямых коллег одной исследовательской организации и копированием для резервной копии; коммерческий запрет прямо упоминает обученные на данных модели. MPIIFaceGaze опубликован на условиях CC BY-NC-SA 4.0: атрибуция, некоммерческое использование и соответствующие условия распространения адаптаций.

Модель взгляда поставляется как `gaze-public.onnx` и метаданные; их происхождение и SHA-256 указаны в `model-manifest.json`. Исходные датасеты и их архивы сборщик notices не копирует.

Цитирование исследовательской модели: Petr Kellnhofer, Adrià Recasens, Simon Stent, Wojciech Matusik, Antonio Torralba. **Gaze360: Physically Unconstrained Gaze Estimation in the Wild.** ICCV, 2019. https://doi.org/10.1109/ICCV.2019.00701. Xucong Zhang, Yusuke Sugano, Mario Fritz, Andreas Bulling. **It’s Written All Over Your Face: Full-Face Appearance-Based Gaze Estimation.** CVPR Workshops, 2017, pp. 2299–2308. https://doi.org/10.1109/CVPRW.2017.284.

Исходники версий и команды воспроизведения доступны в Git-репозитории https://github.com/ovverage/hackaton-kru. Точный тег и SHA исходников указываются в соответствующем релизе. Исходные частные фотографии, видео, признаки лиц, токены и пароли не входят в Git и публичные артефакты. Отчёты об обучении и происхождении весов: `training/README.md`, `docs/MODEL_CARD_2026_10_06.md` и `model-manifest.json`.

## Teacher identity components (0.5.0)

Teacher matching uses OpenCV Zoo YuNet (MIT) and SFace (Apache-2.0), pinned to
commit `47534e27c9851bb1128ccc0102f1145e27f23f98`. Exact sources and SHA-256 values
are in `teacher-face-manifest.json`; unmodified upstream license texts ship in
`models/teacher-faces/YuNet-LICENSE.txt` and `models/teacher-faces/SFace-LICENSE.txt`.
These identity components are separate from the trained gaze/phone/face-counting
models and do not establish a biometric accuracy or anti-spoofing guarantee.
