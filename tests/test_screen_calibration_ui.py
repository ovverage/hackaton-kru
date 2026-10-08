"""Real Qt presentation, coordinate mapping and cancellation; no camera access."""

import os
import time
from types import SimpleNamespace

import pytest


@pytest.fixture(scope="module")
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def until(app, condition, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not condition():
        app.processEvents()
        time.sleep(.002)
    app.processEvents()
    assert condition()


@pytest.fixture
def dialog(app, monkeypatch):
    from agent import screen_calibration

    monkeypatch.setattr(screen_calibration, "TARGET_SETTLE_MS", 20)
    window = screen_calibration.ScreenCalibrationDialog()
    window.showFullScreen()
    app.processEvents()
    yield window
    window.close()
    app.processEvents()


def test_monitor_signature_and_targets_preserve_negative_logical_origin_and_dpi(app):
    from PySide6.QtCore import QRect
    from agent.screen_calibration import screen_signature, target_position

    geometry = QRect(-2560, -200, 1707, 960)
    monitor = SimpleNamespace(
        name=lambda: "Secondary", serialNumber=lambda: "serial-42", geometry=lambda: geometry,
        devicePixelRatio=lambda: 1.5, logicalDotsPerInch=lambda: 96.0,
    )
    assert screen_signature(monitor) == {
        "name": "Secondary", "serial": "serial-42", "geometry": [-2560, -200, 1707, 960],
        "device_pixel_ratio": 1.5, "logical_dpi": 96.0,
    }
    local = target_position(geometry, (.95, .05))
    desktop = target_position(geometry, (.95, .05), global_coordinates=True)
    assert local.x() == pytest.approx(1706 * .95)
    assert local.y() == pytest.approx(959 * .05)
    assert desktop.x() == pytest.approx(-2560 + local.x())
    assert desktop.y() == pytest.approx(-200 + local.y())


def test_full_monitor_geometry_and_explicit_start(app, dialog):
    from PySide6.QtWidgets import QApplication
    from agent.screen_calibration import screen_signature

    started = []
    acknowledgements = []
    dialog.started.connect(started.append)
    dialog.target_presented.connect(lambda *value: acknowledgements.append(value))
    assert dialog.geometry() == dialog._screen.geometry()
    assert dialog.screen_selector.count() == len(QApplication.screens())
    assert dialog.isFullScreen()
    assert "9 точек" in dialog.instructions.text()
    assert "3 секунды" in dialog.instructions.text() and "27 секунд" in dialog.instructions.text()
    assert "повторятся автоматически" in dialog.instructions.text()
    assert "без перезапуска" in dialog.instructions.text()
    dialog.show_target(0, "fit", (.5, .5))
    app.processEvents()
    assert not started and not acknowledgements
    assert dialog.panel.isVisible()
    dialog.start_button.click()
    assert started == [screen_signature(dialog._screen)]
    assert not dialog.panel.isVisible()
    assert not acknowledgements


def test_all_nine_targets_acknowledge_once_only_after_paint_and_settle(app, dialog):
    from PySide6.QtCore import QRect
    from agent.screen_calibration import FIT_POINTS, VALIDATION_POINTS, target_position

    assert len(FIT_POINTS) == 5 and len(VALIDATION_POINTS) == 4
    assert FIT_POINTS[0] == (.5, .5)
    assert not set(FIT_POINTS).intersection(VALIDATION_POINTS)
    assert set(FIT_POINTS + VALIDATION_POINTS) == {
        (x, y) for x in (.05, .5, .95) for y in (.05, .5, .95)
    }
    acknowledgements = []
    dialog.target_presented.connect(lambda *value: acknowledgements.append(value))
    dialog.start_button.click()
    for index, point in enumerate(FIT_POINTS + VALIDATION_POINTS):
        phase = "fit" if index < len(FIT_POINTS) else "validation"
        dialog.show_target(index, phase, point, 0, 3)
        assert len(acknowledgements) == index  # Not at request time.
        app.processEvents()
        assert len(acknowledgements) == index  # Paint alone is not settled.
        until(app, lambda: len(acknowledgements) == index + 1)
        assert acknowledgements[-1] == (index, dialog.selected_signature)
        location = target_position(dialog.rect(), point).toPoint()
        assert dialog.rect().contains(location)
        assert not dialog.progress.geometry().intersects(QRect(location.x() - 26, location.y() - 26, 52, 52))
        dialog.update_progress(2, 3)
        dialog.repaint()
        app.processEvents()
        assert len(acknowledgements) == index + 1
        assert f"точка {index + 1} из 9" in dialog.progress.text()
    assert "Проверка настройки" in dialog.progress.text()


def test_pending_target_is_replaced_and_hidden_target_is_never_acknowledged(app, dialog):
    acknowledgements = []
    cancellations = []
    dialog.target_presented.connect(lambda *value: acknowledgements.append(value))
    dialog.cancelled.connect(lambda: cancellations.append(True))
    dialog.start_button.click()
    dialog.show_target(0, "fit", (.5, .5))
    app.processEvents()
    dialog.show_target(1, "fit", (.05, .05))
    until(app, lambda: len(acknowledgements) == 1)
    assert acknowledgements[0][0] == 1
    dialog.show_target(2, "fit", (.95, .05))
    dialog.hide()
    app.processEvents()
    time.sleep(.04)
    app.processEvents()
    assert len(acknowledgements) == 1
    assert cancellations == [True]


def test_same_point_retry_has_unique_ack_token_and_fresh_settle(app, dialog, monkeypatch):
    from agent import screen_calibration

    monkeypatch.setattr(screen_calibration, "TARGET_SETTLE_MS", 100)
    acknowledgements = []
    dialog.target_presented.connect(lambda *args: acknowledgements.append(args))
    dialog.start_button.click()
    point = (.95, .05)
    dialog.show_target(2, "fit", point)
    until(app, lambda: len(acknowledgements) == 1)
    assert acknowledgements[0] == (2, dialog.selected_signature)
    dialog.update_progress(8, 3)
    assert "Получено кадров взгляда: 8" in dialog.progress.text()

    dialog.show_target(11, "fit", point)
    assert dialog._presentation_id == 11 and dialog._target_index == 2
    assert dialog._target == point
    assert "Повтор точки 3" in dialog.progress.text() and "попытка 2" in dialog.progress.text()
    assert "Повторное измерение идёт автоматически" in dialog.progress.text()
    assert "Получено кадров взгляда: 0" in dialog.progress.text()
    dialog.update_progress(8, 3, "Поздний результат предыдущего показа")
    assert "Получено кадров взгляда: 0" in dialog.progress.text()
    assert "Поздний результат" not in dialog.progress.text()
    dialog._settle.timeout.emit()  # A prior timeout cannot acknowledge an unpainted retry.
    assert len(acknowledgements) == 1
    app.processEvents()
    dialog._settle.timeout.emit()  # Nor can it acknowledge freshly painted but unsettled pixels.
    assert len(acknowledgements) == 1
    until(app, lambda: len(acknowledgements) == 2)
    assert acknowledgements[-1] == (11, dialog.selected_signature)
    dialog.update_progress(3, 3)
    assert "Получено кадров взгляда: 3" in dialog.progress.text()

    dialog.show_target(20, "fit", point)
    assert "попытка 3" in dialog.progress.text()
    until(app, lambda: len(acknowledgements) == 3)
    assert [row[0] for row in acknowledgements] == [2, 11, 20]


def test_validation_retry_keeps_original_point_number_and_coordinates(app, dialog):
    from agent.screen_calibration import VALIDATION_POINTS

    acknowledgements = []
    dialog.target_presented.connect(lambda *args: acknowledgements.append(args))
    dialog.start_button.click()
    dialog.show_target(15, "validation", VALIDATION_POINTS[1])
    assert dialog._target_index == 6 and dialog._target == (.95, .5)
    assert "Повтор точки 7" in dialog.progress.text()
    until(app, lambda: bool(acknowledgements))
    assert acknowledgements[0][0] == 15


def test_escape_cancels_pending_sample_once(app, dialog):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    cancellations = []
    acknowledgements = []
    dialog.cancelled.connect(lambda: cancellations.append(True))
    dialog.target_presented.connect(lambda *value: acknowledgements.append(value))
    dialog.start_button.click()
    dialog.show_target(0, "fit", (.5, .5))
    app.processEvents()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    dialog.reject()
    time.sleep(.04)
    app.processEvents()
    assert cancellations == [True]
    assert not acknowledgements and not dialog.isVisible()
    assert not dialog._countdown.isActive()


def test_countdown_uses_elapsed_painted_time_without_advancing_target(app, dialog):
    from agent.screen_calibration import COUNTDOWN_INTERVAL_MS

    state = dict(valid=False, elapsed=0)
    dialog._target_elapsed = SimpleNamespace(
        isValid=lambda: state["valid"], elapsed=lambda: state["elapsed"],
        start=lambda: state.update(valid=True, elapsed=0),
        invalidate=lambda: state.update(valid=False),
    )
    dialog.start_button.click()
    dialog.show_target(0, "fit", (.5, .5))
    app.processEvents()
    assert "осталось 3.0 с" in dialog.progress.text()
    assert dialog._countdown.interval() == COUNTDOWN_INTERVAL_MS == 200
    assert dialog._countdown.isActive()
    state["elapsed"] = 2100
    dialog._countdown.timeout.emit()
    assert "осталось 0.9 с" in dialog.progress.text()
    state["elapsed"] = 3200
    dialog._countdown.timeout.emit()
    assert "завершаем точку" in dialog.progress.text()
    assert dialog._target_index == 0  # Only the worker may advance a target.
    dialog.show_target(1, "fit", (.05, .05))
    app.processEvents()
    assert "осталось 3.0 с" in dialog.progress.text()
    dialog.finish_error("Повторите настройку")
    assert not dialog._countdown.isActive()


def test_received_gaze_frames_are_worker_counts_not_paint_or_countdown_ticks(app, dialog):
    acknowledgements = []
    dialog.target_presented.connect(lambda *args: acknowledgements.append(args))
    dialog.start_button.click()
    dialog.show_target(0, "fit", (.5, .5))
    assert "Получено кадров взгляда: 0" in dialog.progress.text()
    until(app, lambda: bool(acknowledgements))
    dialog._countdown.timeout.emit()
    dialog.repaint()
    app.processEvents()
    assert "Получено кадров взгляда: 0" in dialog.progress.text()
    assert "отслеживание активно" not in dialog.progress.text().lower()
    dialog.update_progress(7, 3, "Продолжайте смотреть на точку")
    assert "Получено кадров взгляда: 7" in dialog.progress.text()
    assert "Продолжайте смотреть на точку" in dialog.progress.text()
    dialog._countdown.timeout.emit()
    assert "Получено кадров взгляда: 7" in dialog.progress.text()
    dialog.show_target(1, "fit", (.05, .05), 0, 3)
    assert "Получено кадров взгляда: 0" in dialog.progress.text()
    dialog.update_progress(4, 3)
    dialog.finish_error("Не удалось различить точки")
    dialog.update_progress(9, 3, "Старое сообщение")
    dialog.start_button.click()
    dialog.show_target(0, "fit", (.5, .5))
    assert "Получено кадров взгляда: 0" in dialog.progress.text()
    assert "Старое сообщение" not in dialog.progress.text()


@pytest.mark.parametrize("signal_name,value", [
    ("geometryChanged", None), ("logicalDotsPerInchChanged", 144.0),
    ("physicalDotsPerInchChanged", 110.0),
])
def test_screen_change_invalidates_active_attempt_before_ack(app, dialog, signal_name, value):
    invalidated = []
    cancelled = []
    acknowledgements = []
    dialog.invalidated.connect(invalidated.append)
    dialog.cancelled.connect(lambda: cancelled.append(True))
    dialog.target_presented.connect(lambda *args: acknowledgements.append(args))
    dialog.start_button.click()
    dialog.show_target(0, "fit", (.5, .5))
    app.processEvents()
    getattr(dialog._screen, signal_name).emit(value if value is not None else dialog._screen.geometry())
    app.processEvents()
    assert len(invalidated) == 1 and cancelled == [True]
    assert not acknowledgements and not dialog.isVisible()


def test_screen_removal_invalidates_even_before_start(app, dialog):
    from PySide6.QtWidgets import QApplication

    cancelled = []
    dialog.cancelled.connect(lambda: cancelled.append(True))
    QApplication.instance().screenRemoved.emit(dialog._screen)
    assert cancelled == [True] and not dialog.isVisible()


def test_failure_needs_one_retry_click_and_success_closes_automatically(app, dialog):
    started = []
    acknowledged = []
    accepted = []
    cancelled = []
    dialog.started.connect(started.append)
    dialog.target_presented.connect(lambda *value: acknowledged.append(value))
    dialog.accepted.connect(lambda: accepted.append(True))
    dialog.cancelled.connect(lambda: cancelled.append(True))
    dialog.start_button.click()
    dialog.show_target(0, "fit", (.5, .5))
    app.processEvents()
    dialog.finish_error("Не хватило стабильных кадров. Повторите настройку.")
    time.sleep(.04)
    app.processEvents()
    assert not acknowledged and len(started) == 1
    assert dialog.start_button.text() == "Повторить настройку"
    assert "стабильных" in dialog.error_label.text()
    assert dialog.panel.isVisible()
    dialog.start_button.click()
    dialog.show_target(0, "fit", (.5, .5))
    until(app, lambda: len(acknowledged) == 1)
    assert len(started) == 2
    dialog.finish_quality("Настройка прошла проверку. Камера готова.")
    until(app, lambda: bool(accepted))
    assert dialog.result() == dialog.DialogCode.Accepted
    assert not cancelled and not dialog.isVisible()


@pytest.mark.parametrize("point", [(float("nan"), .5), (1.2, .5), (-.1, .5), (.5,)])
def test_invalid_target_does_not_start_a_timer(app, dialog, point):
    dialog.start_button.click()
    with pytest.raises(ValueError):
        dialog.show_target(0, "fit", point)
    assert not dialog._settle.isActive()


def test_selected_exam_screen_is_fixed_for_the_target_dialog(app):
    from PySide6.QtWidgets import QApplication
    from agent.screen_calibration import ScreenCalibrationDialog, screen_signature

    selected = QApplication.screens()[0]
    window = ScreenCalibrationDialog(screen=selected)
    try:
        assert window._screen is selected
        assert window.selected_signature == screen_signature(selected)
        assert not window.screen_selector.isEnabled()
    finally:
        window.close()


def test_disconnected_explicit_exam_screen_is_not_replaced_with_primary(app):
    from agent.screen_calibration import ScreenCalibrationDialog

    with pytest.raises(RuntimeError, match='монитор отключён'):
        ScreenCalibrationDialog(screen=object())
