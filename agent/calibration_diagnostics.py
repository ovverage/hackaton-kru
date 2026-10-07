"""Compact local calibration diagnostics: aggregates only, never camera frames."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from numbers import Real
from pathlib import Path
import re
import threading
from uuid import uuid4


TARGET_NAMES = {
    'fit_center': 'центр экрана',
    'fit_top_left': 'верхний левый угол',
    'fit_top_right': 'верхний правый угол',
    'fit_bottom_right': 'нижний правый угол',
    'fit_bottom_left': 'нижний левый угол',
    'validation_top': 'середина верхнего края',
    'validation_right': 'середина правого края',
    'validation_bottom': 'середина нижнего края',
    'validation_left': 'середина левого края',
}
MAX_REPORTS = 5
MAX_REPORT_BYTES = 32 * 1024
_REPORT_NAME = re.compile(r'attempt-\d{8}T\d{12}Z-[a-f0-9]{12}\.json\Z')
_CODE = re.compile(r'[A-Z][A-Z0-9_]{0,95}\Z')
_VERSION = re.compile(r'[A-Za-z0-9_.-]{1,80}\Z')
_REPORT_LOCK = threading.Lock()
_LAST_REPORT_STAMP = None
_QUALITY_NUMBERS = (
    'fit_target_count', 'validation_target_count', 'center_sample_count',
    'margin_degrees', 'yaw_span_degrees', 'pitch_span_degrees',
    'fit_max_error', 'fit_condition_number', 'transform_condition_number',
    'validation_median_error', 'validation_max_error', 'maximum_sample_screen_spread',
    'adaptive_target_retries',
)
_TARGET_NUMBERS = (
    'accepted_samples', 'rejected_samples', 'elapsed_seconds', 'spread_degrees',
    'sample_count', 'valid_samples', 'total_frames', 'yaw_spread_degrees',
    'pitch_spread_degrees', 'model_error_median_degrees', 'head_max_delta_degrees',
    'attempts',
)
_REJECTION_REASONS = frozenset({
    'missing_sample', 'missing_observation', 'no_sample', 'no_gaze', 'model_uncertain',
    'high_error', 'blink', 'eye_state_missing', 'face_landmarks_missing',
    'multiple_faces', 'invalid_crop', 'invalid_observation', 'invalid_timestamp',
    'before_target', 'future_timestamp', 'duplicate_frame', 'sample_too_soon',
    'stale_frame', 'head_moved', 'head_missing', 'tracking_unavailable',
})


def _number(value):
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        numeric = float(value)
    except (OverflowError, ValueError):
        return None
    return numeric if math.isfinite(numeric) and abs(numeric) <= 1e12 else None


def _numbers(source, keys):
    if not isinstance(source, dict):
        return {}
    return {key: _number(source[key]) for key in keys if key in source}


def _target_values(source):
    return _numbers(source, TARGET_NAMES)


def _target_id(value):
    return value if isinstance(value, str) and value in TARGET_NAMES else None


def _rejections(source):
    if not isinstance(source, dict):
        return {}
    return {key: int(value) for key, value in source.items()
            if key in _REJECTION_REASONS and isinstance(value, Real)
            and not isinstance(value, bool) and 0 <= value <= 1_000_000
            and math.isfinite(value) and value == int(value)}


def _error_code(result):
    error = result.get('error') if isinstance(result, dict) else None
    if error is None:
        return None
    if isinstance(error, str):
        code = error.split(':', 1)[0]
        if _CODE.fullmatch(code):
            return code
    return 'CALIBRATION_ERROR_UNCLASSIFIED'


def compact_calibration_report(result, *, version=None):
    """Copy only bounded aggregate metadata from a potentially rich result.

    Deliberately exclude images, vectors, individual gaze/head angles, fitted
    polygons, monitor identifiers, file paths and arbitrary exception messages.
    Invalid numeric metrics become JSON null rather than NaN/Infinity.
    """
    result = result if isinstance(result, dict) else {}
    report = {
        'schema': 'qorgau-screen-calibration-attempt-v1',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'ready': result.get('ready') is True,
        'error': _error_code(result),
        'elapsed_seconds': _number(result.get('elapsed_seconds')),
    }
    if isinstance(version, str) and _VERSION.fullmatch(version):
        report['version'] = version
    if 'retry_count' in result:
        report['retry_count'] = _number(result['retry_count'])
    quality = result.get('quality')
    quality = quality if isinstance(quality, dict) else {}
    compact_quality = _numbers(quality, _QUALITY_NUMBERS)
    if quality.get('policy') == 'personal_screen_heuristic_v1':
        compact_quality['policy'] = quality['policy']
    if isinstance(quality.get('statistical_accuracy_guarantee'), bool):
        compact_quality['statistical_accuracy_guarantee'] = quality['statistical_accuracy_guarantee']
    if _target_id(quality.get('failed_target')):
        compact_quality['failed_target'] = quality['failed_target']
    for key in ('sample_counts', 'validation_errors', 'sample_spreads_degrees', 'target_spreads_degrees'):
        if isinstance(quality.get(key), dict):
            compact_quality[key] = _target_values(quality[key])
    report['quality'] = compact_quality
    if _target_id(result.get('failed_target')):
        report['failed_target'] = result['failed_target']
    capture = result.get('capture')
    capture = capture if isinstance(capture, dict) else {}
    compact_capture = _numbers(capture, (
        'completed_targets', 'target_count', 'elapsed_seconds', 'target_visible_seconds',
        'settle_seconds', 'total_frames', 'accepted_samples', 'rejected_samples',
        'retry_count',
    ))
    if _target_id(capture.get('failed_target')):
        compact_capture['failed_target'] = capture['failed_target']
    if isinstance(capture.get('rejections'), dict):
        compact_capture['rejections'] = _rejections(capture['rejections'])
    targets = capture.get('targets')
    if isinstance(targets, dict):
        compact_capture['targets'] = {}
        for key in TARGET_NAMES:
            source = targets.get(key)
            if isinstance(source, dict):
                target = _numbers(source, _TARGET_NUMBERS)
                if 'last_error' in source:
                    target['last_error'] = _error_code({'error': source['last_error']})
                if isinstance(source.get('rejections'), dict):
                    target['rejections'] = _rejections(source['rejections'])
                compact_capture['targets'][key] = target
    report['capture'] = compact_capture
    return report


def format_calibration_failure(result):
    """Explain the actual failed gate without assuming poor lighting caused it."""
    report = compact_calibration_report(result)
    code = report['error']
    if report['ready']:
        return 'Личная зона экрана настроена.'
    target_id = (report.get('failed_target') or report['quality'].get('failed_target')
                 or report['capture'].get('failed_target'))
    target = TARGET_NAMES.get(target_id)
    location = f' Точка: {target}.' if target else ''
    messages = {
        'SCREEN_ANGULAR_SPAN_TOO_SMALL': 'Модель почти не различила взгляд на противоположные края экрана.',
        'SCREEN_CORNER_GEOMETRY_INVALID': 'Направления на углы экрана получились противоречивыми: личную границу построить не удалось.',
        'SCREEN_TRANSFORM_DEGENERATE': 'Модель не смогла отдельно различить горизонтальные и вертикальные перемещения взгляда.',
        'SCREEN_FIT_ERROR_TOO_HIGH': 'Пять основных точек не согласуются с одной зоной экрана.',
        'SCREEN_VALIDATION_ERROR_TOO_HIGH': 'Основные точки собраны, но четыре проверочные точки не подтвердили рассчитанную зону экрана.',
        'SCREEN_SAMPLES_UNSTABLE': 'Оценки взгляда на одной точке слишком сильно колебались.',
        'SCREEN_SAMPLES_UNSTABLE_IN_SCREEN_SPACE': 'Даже при удержании точки разброс оценок оказался слишком большим для размера этого экрана.',
        'SCREEN_SAMPLES_INSUFFICIENT': 'На одной точке не хватило пригодных измерений взгляда.',
        'SCREEN_TOO_FEW_FRESH_SAMPLES': 'На одной точке не хватило новых пригодных кадров.',
        'SCREEN_TARGETS_MISSING': 'Получены измерения не для всех точек экрана.',
        'SCREEN_HEAD_MOVED': 'Во время настройки положение головы изменилось слишком сильно.',
        'SCREEN_CHANGED': 'Во время настройки изменился монитор, его размер или масштаб.',
        'SCREEN_TARGET_NOT_PRESENTED': 'Не удалось подтвердить показ следующей точки на экране.',
        'SCREEN_CALIBRATION_TIMEOUT': 'Настройка превысила отведённое время: получение пригодных кадров заняло слишком долго.',
        'SCREEN_CALIBRATION_CANCELLED': 'Настройка отменена.',
        'SCREEN_CALIBRATION_REQUIRED': 'Настройка по точкам ещё не завершена.',
        'GAZE_REFERENCE_INSUFFICIENT': 'Не хватило пригодных кадров для исходного взгляда в центр.',
        'GAZE_REFERENCE_UNSTABLE': 'При взгляде в центр оценки сильно менялись.',
        'GAZE_REFERENCE_UNCERTAIN': 'Модель не получила достаточно уверенную оценку взгляда в центр.',
        'HEAD_REFERENCE_INSUFFICIENT': 'Не хватило измерений исходного положения головы.',
        'HEAD_REFERENCE_UNSTABLE': 'При взгляде в центр положение головы заметно менялось.',
        'CAMERA_CAPTURE_FAILED': 'Камера перестала передавать кадры во время настройки.',
        'CAMERA_TIMEOUT': 'Камера не передала новый кадр вовремя.',
        'CAMERA_FROZEN': 'Изображение камеры перестало обновляться.',
    }
    fallback = 'Настройку не удалось завершить.'
    if code:
        fallback += f' Код причины: {code}.'
    message = messages.get(code, fallback) + location
    if code == 'SCREEN_ANGULAR_SPAN_TOO_SMALL':
        spans = report['quality']
        horizontal, vertical = spans.get('yaw_span_degrees'), spans.get('pitch_span_degrees')
        if horizontal is not None and vertical is not None:
            message += f' Разница между краями: по горизонтали {horizontal:.1f}°, по вертикали {vertical:.1f}°.'
    if code in ('SCREEN_HEAD_MOVED', 'HEAD_REFERENCE_UNSTABLE'):
        return message + ' Сохраняйте обычное положение головы и переводите только глаза.'
    if code in ('SCREEN_CHANGED', 'SCREEN_TARGET_NOT_PRESENTED'):
        return message + ' Откройте настройку заново на выбранном мониторе.'
    if code == 'SCREEN_CALIBRATION_CANCELLED':
        return message
    return message + ' Повторите настройку, дожидаясь смены каждой точки.'


def format_target_retry(result):
    """Short feedback for an automatic repeat, without asking for a restart."""
    report = compact_calibration_report(result)
    messages = {
        'SCREEN_TOO_FEW_FRESH_SAMPLES': 'Не хватило новых пригодных кадров взгляда.',
        'SCREEN_SAMPLES_INSUFFICIENT': 'Не хватило пригодных измерений взгляда.',
        'SCREEN_HEAD_MOVED': 'Голова заметно сместилась. Верните её в исходное положение.',
        'HEAD_REFERENCE_UNSTABLE': 'Голова заметно смещалась. Держите её спокойно.',
        'SCREEN_SAMPLES_UNSTABLE': 'Оценки взгляда на этой точке сильно колебались.',
        'SCREEN_SAMPLES_UNSTABLE_IN_SCREEN_SPACE': 'Разброс оценок слишком велик для личной зоны экрана.',
        'SCREEN_VALIDATION_ERROR_TOO_HIGH': 'Эта точка не подтвердила рассчитанную зону экрана.',
    }
    reason = messages.get(report['error'], 'Нужно заново измерить взгляд на этой точке.')
    return reason + ' Повторяем эту точку автоматически; остальные пройденные точки сохранены.'


def _owned_reports(destination):
    return sorted((candidate for candidate in destination.iterdir()
                   if _REPORT_NAME.fullmatch(candidate.name)
                   and candidate.is_file() and not candidate.is_symlink()
                   and candidate.resolve().parent == destination),
                  key=lambda candidate: candidate.name, reverse=True)


def save_calibration_report(folder, result, *, version=None):
    """Atomically save one aggregate attempt; retain the newest five own files.

    The caller may catch OSError/ValueError and keep showing the original
    calibration result. Saving diagnostics must never make a failed fit ready.
    """
    global _LAST_REPORT_STAMP
    root = Path(folder).resolve()
    destination = root / 'calibration-diagnostics'
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    if destination.parent != root:
        raise ValueError('CALIBRATION_REPORT_PATH_INVALID')
    report = compact_calibration_report(result, version=version)
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    if len(payload.encode('utf-8')) > MAX_REPORT_BYTES:
        raise ValueError('CALIBRATION_REPORT_TOO_LARGE')
    # Windows wall clocks can repeat at sub-millisecond resolution. Serialize
    # report writes and advance the filename stamp by at least one microsecond
    # so a random UUID can never reorder or delete a more recent attempt.
    with _REPORT_LOCK:
        stamp_at = datetime.now(timezone.utc)
        previous = _LAST_REPORT_STAMP
        owned = _owned_reports(destination)
        if owned:
            persisted_stamp = datetime.strptime(owned[0].name.split('-')[1],
                                                 '%Y%m%dT%H%M%S%fZ').replace(tzinfo=timezone.utc)
            previous = max(previous, persisted_stamp) if previous is not None else persisted_stamp
        if previous is not None and stamp_at <= previous:
            stamp_at = previous + timedelta(microseconds=1)
        _LAST_REPORT_STAMP = stamp_at
        stamp = stamp_at.strftime('%Y%m%dT%H%M%S%fZ')
        path = destination / f'attempt-{stamp}-{uuid4().hex[:12]}.json'
        temporary = path.with_suffix('.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                stream.write(payload)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        for stale in _owned_reports(destination)[MAX_REPORTS:]:
            stale.unlink(missing_ok=True)
        return path
