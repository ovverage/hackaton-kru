"""The private gaze demo cannot leave camera setup without explicit calibration."""

import os
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from agent.camera_setup import CameraSetup, CalibrationWorker
from agent.client import Agent, atomic_json


@pytest.fixture(scope="module")
def application():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


class DemoCamera:
    """Slow enough to exercise the real Qt worker and the user's buttons."""

    def __init__(self):
        import cv2

        self.cv2 = cv2
        self.centres = {}
        self.public_gaze = SimpleNamespace(reference=None)
        self.gaze_enabled = False
        self.closed = False
        self.collecting = False
        self.collected = 0
        self.error = None
        self.allow_samples = True
        self.fail_once = False
        self.begin_calls = 0
        self.read_calls = 0

    @property
    def gaze_reference_progress(self):
        return dict(collecting=self.collecting, collected=self.collected,
                    required=25, ready=self.public_gaze.reference is not None,
                    error=self.error)

    def cancel_gaze_reference(self):
        self.collecting = False
        self.collected = 0
        self.error = None
        self.public_gaze.reference = None
        self.gaze_enabled = False

    def begin_gaze_reference(self):
        self.cancel_gaze_reference()
        self.begin_calls += 1
        self.collecting = True

    def read(self):
        self.read_calls += 1
        if self.collecting and self.allow_samples:
            self.collected += 1
            if self.fail_once and self.collected == 15:
                self.error = "GAZE_REFERENCE_UNSTABLE"
                self.collecting = False
                self.fail_once = False
            elif self.collected == 25:
                self.public_gaze.reference = [0, 0, -1]
                self.gaze_enabled = True
                self.collecting = False
        return np.zeros((48, 64, 3), dtype=np.uint8), {"direction": "UNKNOWN", "faces": 1}

    def close(self):
        self.closed = True


def until(application, predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(.005)
    application.processEvents()
    assert predicate()


@pytest.fixture
def setup_demo(application, tmp_path, monkeypatch):
    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "test"})
    agent = Agent(tmp_path)
    model = tmp_path / "model"
    model.write_bytes(b"fixture")
    camera = DemoCamera()
    callbacks = []
    monkeypatch.setattr("agent.camera_setup.REFERENCE_SETTLE_SECONDS", 0)
    with (
        patch("agent.resources.verified_models", return_value=(model, model)),
        patch("agent.camera_setup.QTimer.singleShot", side_effect=lambda _, fn: callbacks.append(fn)),
        patch("agent.vision.Camera", return_value=camera),
    ):
        dialog = CameraSetup(agent)
        callbacks[0]()
        until(application, lambda: dialog.preview_ready)
        try:
            yield agent, camera, dialog
        finally:
            if dialog.worker.isRunning():
                dialog.reject()
                until(application, lambda: not dialog.preparing)
            if agent.camera:
                agent.camera.close()
            if agent.recorder:
                agent.recorder.close()
            agent.http.close()
            dialog.close()


def test_preview_never_collects_and_only_explicit_stable_reference_connects(application, setup_demo):
    agent, camera, dialog = setup_demo
    until(application, lambda: camera.read_calls >= 3)
    assert camera.begin_calls == 0 and camera.collected == 0
    assert agent.camera is None and not agent.capabilities["gaze"]
    assert "центр экрана" in dialog.instruction.text()
    assert dialog.start_button.text() == "Смотрю в центр экрана"

    camera.allow_samples = False
    dialog.start_button.click()
    until(application, lambda: camera.begin_calls == 1)
    assert not dialog.start_button.isEnabled()
    assert agent.camera is None and agent.camera_preparing
    assert not any(agent.engine.state.counts().values())
    camera.allow_samples = True
    until(application, lambda: not dialog.preparing)
    assert dialog.result() == dialog.DialogCode.Accepted
    assert agent.camera is camera and camera.collected == 25
    assert camera.public_gaze.reference == [0, 0, -1]
    assert agent.capabilities["gaze"]
    assert agent.capabilities["vision"].endswith("public-gaze-v1")


def test_unstable_reference_requires_another_explicit_click(application, setup_demo):
    agent, camera, dialog = setup_demo
    camera.fail_once = True
    dialog.start_button.click()
    until(application, lambda: dialog.start_button.text() == "Повторить настройку")
    assert agent.camera is None and dialog.start_button.isEnabled()
    assert "устойчивое положение" in dialog.feedback.text()
    reads = camera.read_calls
    until(application, lambda: camera.read_calls >= reads + 2)
    assert camera.begin_calls == 1 and not camera.gaze_enabled
    dialog.start_button.click()
    until(application, lambda: not dialog.preparing)
    assert camera.begin_calls == 2 and camera.collected == 25
    assert agent.camera is camera


@pytest.mark.parametrize("timeout", [False, True])
def test_incomplete_reference_can_be_cancelled_without_connecting_camera(
    application, setup_demo, monkeypatch, timeout,
):
    agent, camera, dialog = setup_demo
    camera.allow_samples = False
    if timeout:
        monkeypatch.setattr("agent.camera_setup.REFERENCE_TIMEOUT_SECONDS", 0)
    dialog.start_button.click()
    until(application, lambda: camera.begin_calls == 1)
    if timeout:
        until(application, lambda: dialog.start_button.text() == "Повторить настройку")
        assert not camera.collecting
    dialog.reject()
    until(application, lambda: not dialog.preparing)
    assert camera.closed and agent.camera is None
    assert not agent.capabilities["camera"] and not agent.capabilities["gaze"]
    assert not any(agent.engine.state.counts().values())


def test_legacy_multi_direction_wizard_cannot_bypass_demo_reference(application, tmp_path):
    camera = DemoCamera()
    worker = CalibrationWorker(0, tmp_path / "phone", tmp_path / "face", tmp_path)
    with patch("agent.vision.Camera", return_value=camera):
        worker.run()
    assert camera.closed and worker.result is None
    assert "обычном включении камеры" in worker.error


def test_unready_result_is_rejected_even_if_worker_returns_it(application, tmp_path):
    atomic_json(tmp_path / "config.json", {"server": "http://localhost:8000", "token": "test"})
    agent = Agent(tmp_path)
    camera = DemoCamera()
    with (
        patch("agent.resources.verified_models", return_value=(tmp_path, tmp_path)),
        patch("agent.camera_setup.QTimer.singleShot"),
    ):
        dialog = CameraSetup(agent)
    dialog.worker = SimpleNamespace(result=camera, error="")
    try:
        dialog.finished_calibration()
        assert camera.closed and agent.camera is None
        assert "не завершена" in dialog.feedback.text()
        assert dialog.start_button.isEnabled()
    finally:
        dialog.worker = None
        dialog.close()
        agent.http.close()


@pytest.mark.parametrize('status,explanation', [
    ('blink', 'моргание или закрытые глаза'),
    ('eye_state_missing', 'не удалось оценить видимость глаз'),
    ('face_landmarks_missing', 'ориентиры лица потеряны'),
])
def test_missing_gaze_status_explains_the_actual_tracking_failure(status, explanation):
    from agent.desktop import public_gaze_status_text

    gaze = dict(reference_ready=True, gaze_observed_direction='UNKNOWN',
                gaze_tracking_status=status, head_reference_ready=True,
                head_direction='SCREEN')
    text = public_gaze_status_text(gaze, active=True, gaze_seconds=4)
    eye_text, head_text = text.split('\n', 1)
    assert 'не отслеживается' in eye_text and explanation in eye_text
    assert 'не настроен' not in eye_text
    assert '4.0 / 5 с' not in eye_text
    assert 'Положение головы: прямо' in head_text


def test_blink_hold_is_explicitly_a_previous_estimate_without_current_timer():
    from agent.desktop import public_gaze_status_text

    gaze = dict(reference_ready=True, gaze_observed_direction='RIGHT',
                gaze_tracking_status='blink', gaze_feedback_reason='blink_hold',
                gaze_display_stale=True, gaze_observation_uncertain=True,
                direction='UNKNOWN', head_reference_ready=True, head_direction='SCREEN')
    before = dict(gaze)
    text = public_gaze_status_text(gaze, active=True, gaze_seconds=4.8)
    assert 'Взгляд (по изображению камеры): вправо' in text
    assert 'моргание, показана последняя оценка' in text
    assert '4.8 / 5 с' not in text
    assert gaze == before and gaze['direction'] == 'UNKNOWN'


@pytest.mark.parametrize('direction,label', [
    ('LEFT', 'влево'), ('RIGHT', 'вправо'), ('DOWN', 'вниз'), ('UP', 'вверх'),
])
def test_extreme_head_turn_has_a_strong_turn_label_separate_from_eye_gaze(direction, label):
    from agent.desktop import public_gaze_status_text

    gaze = dict(reference_ready=True, gaze_observed_direction='UNKNOWN',
                gaze_tracking_status='eye_state_missing', head_reference_ready=True,
                head_direction=direction, head_extreme=True, head_tracking_status='tracked')
    text = public_gaze_status_text(gaze)
    eye_text, head_text = text.split('\n', 1)
    assert 'не удалось оценить видимость глаз' in eye_text
    assert f'Положение головы: сильный поворот {label} (по изображению камеры)' in head_text


def test_unavailable_head_tracking_never_displays_historical_angle_as_current_pose():
    from agent.desktop import public_gaze_status_text

    gaze = dict(reference_ready=True, gaze_observed_direction='UNKNOWN',
                gaze_tracking_status='face_landmarks_missing', head_reference_ready=True,
                head_direction='UNKNOWN', head_tracking_status='unavailable',
                head_yaw=None, head_pitch=None, head_warning=False, head_extreme=False,
                last_known_head_yaw=70., last_known_head_pitch=0.)
    text = public_gaze_status_text(gaze)
    _, head_text = text.split('\n', 1)
    assert 'Положение головы: не отслеживается — текущий угол неизвестен.' in head_text
    assert '70' not in head_text and 'сильный поворот' not in head_text
    assert 'прямо' not in head_text and 'не настроено' not in head_text


def display_observation(**updates):
    values = dict(reference_ready=True, gaze_observed_direction='RIGHT', direction='RIGHT',
                  gaze_tracking_status='tracked', gaze_feedback_reason='observed',
                  gaze_observation_uncertain=False, gaze_display_yaw_degrees=-25.,
                  gaze_display_pitch_degrees=12., head_reference_ready=True,
                  head_tracking_status='tracked', head_direction='RIGHT',
                  head_yaw=70., head_pitch=20., head_extreme=True)
    values.update(updates)
    return values


@pytest.mark.parametrize('sign', [-1, 1])
def test_current_angles_use_opposite_gaze_and_head_camera_axis_conventions(sign):
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(gaze_display_yaw_degrees=sign * 25.2,
                               gaze_display_pitch_degrees=sign * 12.3,
                               head_yaw=sign * 70.4, head_pitch=sign * 20.5)
    eye_text, head_text = public_gaze_status_text(gaze).split('\n', 1)
    eye_horizontal, eye_vertical = ('влево', 'вверх') if sign > 0 else ('вправо', 'вниз')
    head_horizontal, head_vertical = ('вправо', 'вниз') if sign > 0 else ('влево', 'вверх')
    assert f'углы от центра: {eye_horizontal} 25.2°; {eye_vertical} 12.3°' in eye_text
    assert f'углы от центра: {head_horizontal} 70.4°; {head_vertical} 20.5°' in head_text
    assert 'сильный поворот' in head_text


def test_measured_centre_shows_real_zero_angles_for_both_axes():
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(gaze_observed_direction='SCREEN', direction='SCREEN',
                               gaze_display_yaw_degrees=0., gaze_display_pitch_degrees=-0.,
                               head_direction='SCREEN', head_yaw=0., head_pitch=-0., head_extreme=False)
    eye_text, head_text = public_gaze_status_text(gaze).split('\n', 1)
    assert 'на экран' in eye_text and 'Положение головы: прямо' in head_text
    for value in (eye_text, head_text):
        assert 'горизонталь 0.0°; вертикаль 0.0°' in value
        assert 'недоступна' not in value and '-0.0' not in value


@pytest.mark.parametrize('value', [None, '15', True, float('nan'), float('inf'), -float('inf'), 10**1000])
def test_invalid_or_missing_angle_is_unavailable_never_coerced_to_zero(value):
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(gaze_display_yaw_degrees=value, gaze_display_pitch_degrees=value,
                               head_yaw=value, head_pitch=value, head_direction='UNKNOWN', head_warning=True)
    text = public_gaze_status_text(gaze)
    assert text.count('горизонталь недоступна') == 2
    assert text.count('вертикаль недоступна') == 2
    assert '°' not in text and 'nan' not in text and 'inf' not in text


@pytest.mark.parametrize('ready,status', [(False, 'tracked'), (True, 'face_landmarks_missing')])
def test_unready_or_missing_tracking_never_presents_old_finite_angles_as_current(ready, status):
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(reference_ready=ready, gaze_tracking_status=status,
                               head_reference_ready=ready,
                               head_tracking_status='tracked' if ready is False else 'unavailable')
    text = public_gaze_status_text(gaze)
    assert text.count('углы недоступны') == 2
    assert '°' not in text and 'сильный поворот' not in text
    if ready:
        assert 'ориентиры лица потеряны' in text
        assert 'текущий угол неизвестен' in text


@pytest.mark.parametrize('stale_flag', [False, True])
def test_held_blink_angles_are_explicit_previous_estimates_not_current_or_timer(stale_flag):
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(direction='UNKNOWN', gaze_tracking_status='blink',
                               gaze_feedback_reason='blink_hold', gaze_display_stale=stale_flag,
                               gaze_observation_uncertain=True)
    eye_text = public_gaze_status_text(gaze, active=True, gaze_seconds=4.2).split('\n', 1)[0]
    assert 'моргание, показана последняя оценка' in eye_text
    assert 'предыдущая оценка углов от центра: вправо 25.0°; вверх 12.0°' in eye_text
    assert '4.2 / 5 с' not in eye_text


@pytest.mark.parametrize('reason,explanation', [
    ('uncertain_observation', 'модель не уверена в направлении; замечание не начисляется'),
    ('strict_unknown', 'направление оценено, но не подтверждено для замечания'),
    ('strict_mismatch', 'направление оценено, но не подтверждено для замечания'),
])
def test_uncertainty_explanation_distinguishes_model_error_from_unconfirmed_direction(reason, explanation):
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(direction='UNKNOWN', gaze_feedback_reason=reason,
                               gaze_observation_uncertain=True)
    before = dict(gaze)
    eye_text = public_gaze_status_text(gaze, active=True, gaze_seconds=4.2).split('\n', 1)[0]
    assert explanation in eye_text
    assert 'вправо 25.0°' in eye_text and '4.2 / 5 с' not in eye_text
    assert gaze == before


def test_demo_sensitivity_label_and_valid_direction_show_the_existing_five_second_timer():
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(gaze_decision_policy='demo_sensitivity',
                               gaze_decision_threshold_degrees=9.)
    text = public_gaze_status_text(gaze, active=True, gaze_seconds=3.2)
    assert 'чувствительность: 9°' in text and '3.2 / 5 с' in text
    assert 'вправо 25.0°' in text


def test_demo_sensitivity_does_not_hide_model_uncertainty_or_create_a_timer():
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(gaze_decision_policy='demo_sensitivity',
                               gaze_decision_threshold_degrees=9., direction='UNKNOWN',
                               gaze_feedback_reason='uncertain_observation',
                               gaze_observation_uncertain=True)
    text = public_gaze_status_text(gaze, active=True, gaze_seconds=4.)
    assert 'чувствительность: 9°' in text
    assert 'модель не уверена в направлении' in text
    assert '4.0 / 5 с' not in text


@pytest.mark.parametrize('direction', ['SCREEN', 'UNKNOWN', 'UP'])
def test_timer_requires_a_current_direction_that_the_rule_engine_counts(direction):
    from agent.desktop import public_gaze_status_text

    gaze = display_observation(direction=direction, gaze_observed_direction='RIGHT',
                               gaze_observation_uncertain=False)
    text = public_gaze_status_text(gaze, active=True, gaze_seconds=4.5)
    assert '4.5 / 5 с' not in text


def test_real_dashboard_scrolls_without_clipping_angles_at_default_and_narrow_sizes(application, tmp_path):
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtMultimedia import QMediaDevices
    from PySide6.QtWidgets import QScrollArea
    from agent.desktop import StudentWindow

    diagnostics = display_observation(
        source='public_gaze_model', gaze_decision_policy='demo_sensitivity',
        gaze_decision_threshold_degrees=9., gaze_feedback_reason='uncertain_observation',
        gaze_observation_uncertain=True, direction='UNKNOWN',
    )
    snapshot = dict(
        state=dict(lifecycle='RUNNING', access='OPEN', reason=None, lock_id=None),
        connected=True, camera=True, camera_fault=False, recognition_paused=False,
        gaze_diagnostics=diagnostics, gaze_seconds=3.2,
        session=dict(title='Проверка прототипа Qorgau — демонстрация судьям'),
    )
    agent = SimpleNamespace(config=dict(name='Офлайн-проверка интерфейса'), targets=[],
                            capabilities={}, snapshot=lambda: snapshot)
    with (
        patch.object(StudentWindow, 'create_tray'),
        patch('agent.exam_ui.ExamController'),
        patch.object(QMediaDevices, 'videoInputs', return_value=[]),
    ):
        window = StudentWindow(tmp_path, agent=agent, run_worker=False)
    window.timer.stop()
    window.retry_timer.stop()
    try:
        window.show()
        heights = []
        for width, height in ((560, 450), (480, 400), (560, 450)):
            window.resize(width, height)
            for _ in range(5):
                application.processEvents()
            scroll = window.pages.widget(1)
            assert isinstance(scroll, QScrollArea) and scroll.widgetResizable()
            assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            assert scroll.verticalScrollBar().maximum() > 0
            assert window.size().width() == width and window.size().height() == height
            for status in (window.gaze_status, window.target_status, window.camera_status,
                           window.assignment_title):
                assert status.height() >= status.heightForWidth(status.width())
            heights.append(window.gaze_status.heightForWidth(window.gaze_status.width()))
            # All gaze/head lines can be brought into view in the scroll viewport.
            scroll.ensureWidgetVisible(window.gaze_status)
            application.processEvents()
            gaze_rect = QRect(window.gaze_status.mapTo(scroll.viewport(), QPoint(0, 0)),
                              window.gaze_status.size())
            assert scroll.viewport().rect().contains(gaze_rect)
            assert 'вправо 25.0°' in window.gaze_status.text()
            assert 'вправо 70.0°' in window.gaze_status.text()
            assert 'чувствительность: 9°' in window.gaze_status.text()
        assert heights[1] >= heights[0] and heights[2] == heights[0]
        # A live observation with extra diagnostic text still updates wrapping.
        diagnostics['interval_ms'] = 900
        window.refresh()
        for _ in range(5):
            application.processEvents()
        assert 'Кадры поступают редко' in window.gaze_status.text()
        assert window.gaze_status.height() >= window.gaze_status.heightForWidth(window.gaze_status.width())
    finally:
        window.hide()
        window.deleteLater()
        application.processEvents()
