Ты работаешь в репозитории Qorgau (hackaton-kru). В нём:
- agent/ — приложение ученика на PySide6;
- web/ — кабинет преподавателя на React + Vite;
- backend/ — сервер на FastAPI.

Задача: перенести в код новый дизайн из папки design/redesign-2026-10/. Это смена оформления и текстов, логика остаётся прежней.

## Источник истины
- design/redesign-2026-10/DESIGN.md — правила, словарь, соответствие экранов файлам кода и откуда брать данные. Прочитай его целиком перед началом.
- design/redesign-2026-10/html/*.html — макеты экранов, открываются в браузере без интернета; стили в html/qorgau.css; оглавление html/index.html.
- design/redesign-2026-10/png/ — снимки: exe/ (окна ученика), web/ (1440 px), web-phone/ (390 px).
- design/redesign-2026-10/tokens.json — цвета, шрифт, размеры.
- design/redesign-2026-10/fonts/ — Geologica (OFL): static/*.ttf для Qt, Geologica-Variable.woff для веба.

Если макет и DESIGN.md расходятся, прав DESIGN.md. Если макет противоречит реальным данным, делай по данным и запиши расхождение в отчёт.

## Жёсткие ограничения
1. Не менять:
   - shared/rules.py (пороги и логику);
   - API сервера и модели данных; никаких новых полей на сервере;
   - протокол агента;
   - agent/windows_guard.py и ограничения agent/exam_browser.py;
   - хранение записей и проверки перед стартом.
2. Сохранить публичные атрибуты Qt-виджетов, на которые опираются тесты и код. Новые виджеты добавлять можно, эти не переименовывать и не удалять:
   - StudentWindow: pages, connect_button, setup_error, retry_timer, target_button, target_status, camera_choice, camera_button, camera_status, gaze_status, assignment_title, runtime_error, connection, device_name, tray, tray_menu, tray_status, tray_exit;
   - LockScreen: heading, reason, form, password, unlock, finish, recover, feedback, evidence, evidence_layout;
   - GazeWarning: message;
   - CameraSetup: index, preview, instruction, feedback, start_button, cancel, capture, progress, preview_ready, preparing;
   - TargetPicker: items, selected.
3. Тексты в тестах:
   - «Позовите преподавателя» (LockScreen.heading и статус при паузе) и «Использовать эту камеру» оставить как есть;
   - «Верните взгляд на монитор» становится «Смотрите на экран»: обнови ожидание в tests/test_exam_ui.py;
   - другие тесты не ослабляй; ожидание текста меняй только там, где текст изменён по словарю из DESIGN.md, раздел 4.
4. Веб: без новых npm-зависимостей. Шрифт подключить локально (скопировать woff в web/src/assets/fonts или web/public/fonts, @font-face), без Google Fonts. lucide-react уже есть.
5. Qt:
   - шрифты загружать через QFontDatabase.addApplicationFont из resource_root() (agent/resources.py);
   - добавить их в сборку PyInstaller (scripts/build_student.py, --add-data … fonts);
   - лицензию OFL добавить в THIRD_PARTY_NOTICES.md;
   - если шрифт не загрузился, без ошибки откатиться на Segoe UI.
6. Не пушить, не трогать релизы и deploy/. Работать в новой ветке redesign/ui-2026-10 и коммитить по фазам.

## План

### Фаза 0. Разведка
1. Прочитай DESIGN.md, просмотри html/index.html или png/.
2. Открой текущий код по таблицам из раздела 5 DESIGN.md.
3. Запусти тесты до изменений, чтобы знать исходное состояние:
   - `python -m pytest -q` (окружение .venv; для Qt-тестов `QT_QPA_PLATFORM=offscreen`);
   - `npm --prefix web ci && npm --prefix web run build` (внутри web-тесты, tsc и vite build).

### Фаза 1. Основа
**Веб:**
- палитру и типографику в web/src/styles.css, classroom.css и session.css заменить на CSS-переменные из `:root` в html/qorgau.css;
- подключить Geologica;
- общие куски вынести в маленькие компоненты: Bubble, StepBubble, Sheet с метками регистрации, Pill, Clock.

**Приложение ученика**, новый модуль agent/theme.py:
- палитра, загрузка шрифтов, общий QSS вместо STYLE в desktop.py;
- виджет Bubble на QPainter: состояния on/pause/warn/ink/off, флажок, размеры;
- StepBubble: номер, галочка, пунктир, текущий шаг;
- RegMarks: рамка с четырьмя квадратиками в paintEvent;
- новая иконка-логотип: кружок с ножкой, геометрия в DESIGN.md.

### Фаза 2. Кабинет преподавателя
По таблице из DESIGN.md, экран за экраном:
1. Оболочка: меню из кружков, часы вместо хлебных крошек.
2. Вход.
3. Аудитория до сеанса.
4. Аудитория во время теста: схема рассадки, итоги справа, таблица без колонки значков, очередь «Ждут решения».
5. Новый сеанс.
6. Карточка компьютера.
7. Разбор события со шкалой записи.
8. События.
9. Сеансы и отчёты.
10. Компьютеры с сеткой готовности.
11. Правила.

Затем адаптив до 720 px по png/web-phone и web-04-room-phone. Словарь, eventNames и decisionNames — из раздела 4 DESIGN.md.

### Фаза 3. Приложение ученика
1. Первое подключение.
2. Подготовка: три шага, состояния — по DESIGN.md.
3. Проверка камеры: метки регистрации и овал поверх предпросмотра.
4. Выбор окна.
5. Меню в трее.
6. Кнопка «Позвать преподавателя» в Qorgau Browser.
7. Напоминание о взгляде: секунды и полоса прогресса из snapshot["gaze_seconds"].
8. Экран паузы: красная полоса, две колонки, всё помещается в 1366×768.

Тексты present() и REASONS — по DESIGN.md.

### Фаза 4. Проверка
- Все тесты зелёные (pytest и npm run build), ruff без новых ошибок.
- **Сверка веба.** Демо-устройства сервера помечены simulated, и кабинет их скрывает. Поэтому сделай фикстуры только для разработки:
  - файл web/src/dev/fixtures.ts, включается только при import.meta.env.DEV и параметре ?fixture=room|before|computers|…;
  - подменяет api() статическими данными, как в макетах;
  - в production-сборку не попадает: проверь, что в dist нет слова fixture.

  Сними скриншоты Playwright на 1440 и 390 px и сравни с png/web и png/web-phone.
- **Сверка приложения ученика.** Собери окна в offscreen-режиме с тестовыми снимками состояния (как в tests/test_student.py и tests/test_exam_ui.py), сохрани QWidget.grab() в PNG и сравни с png/exe.
- Свои скриншоты положи в design/redesign-2026-10/implemented/ (web/ и exe/).

## Критерии приёмки
- Каждый экран из html/ узнаётся в реализации: та же структура, цвета, шрифт, тексты, состояния кружков.
- Красный и жёлтый появляются только у пауз, событий «ждёт решения» и нужных действий. В обычном состоянии интерфейс нейтральный.
- В кабинете нет горизонтальной прокрутки на 390 px; широкие таблицы прокручиваются внутри своей рамки.
- Экран паузы целиком виден на 1366×768. Напоминание о взгляде не забирает фокус и пропускает клики.
- Клавиатура: виден фокус (2 px, цвет ink); у кнопок-иконок есть aria-label или accessibleName.
- Логика и тесты не сломаны, API не изменился.

## Отчёт в конце
- Список коммитов.
- Что сделано по каждому экрану.
- Что отличается от макета и почему. Особенно где не хватило данных: время начала теста, абсолютное время событий, единицы gaps.
- Команды проверки и их результат.
- Пути к скриншотам.
