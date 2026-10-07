"""Unlimited target retries retain bounded counters, not histories or frames."""

from copy import deepcopy
import json

import pytest

from agent.calibration_diagnostics import (
    MAX_REPORT_BYTES, TARGET_NAMES, compact_calibration_report, save_calibration_report,
    format_target_retry,
)


def retry_result():
    return {
        'ready': False, 'error': 'SCREEN_CALIBRATION_CANCELLED', 'elapsed_seconds': 83.7,
        'retry_count': 18,
        'quality': {'failed_target': 'fit_bottom_right', 'adaptive_target_retries': 18},
        'capture': {
            'retry_count': 18, 'completed_targets': 3, 'target_count': 9,
            'target_visible_seconds': 3, 'settle_seconds': .8,
            'failed_target': 'fit_bottom_right',
            'targets': {
                'fit_center': {'attempts': 2, 'accepted_samples': 14, 'last_error': 'SCREEN_SAMPLES_UNSTABLE'},
                'fit_bottom_right': {'attempts': 18, 'accepted_samples': 2, 'last_error': 'SCREEN_TOO_FEW_FRESH_SAMPLES',
                                     'rejections': {'no_gaze': 5, 'head_moved': 2}},
            },
        },
    }


def test_retry_counts_and_last_gate_survive_compaction_without_overwriting_original():
    result = retry_result()
    original = deepcopy(result)
    report = compact_calibration_report(result)
    assert report['retry_count'] == report['capture']['retry_count'] == 18
    assert report['quality']['adaptive_target_retries'] == 18
    assert report['capture']['targets']['fit_center']['attempts'] == 2
    current = report['capture']['targets']['fit_bottom_right']
    assert current['attempts'] == 18
    assert current['last_error'] == 'SCREEN_TOO_FEW_FRESH_SAMPLES'
    assert current['rejections'] == {'no_gaze': 5, 'head_moved': 2}
    assert report['error'] == 'SCREEN_CALIBRATION_CANCELLED'
    assert result == original


def test_successful_target_can_clear_last_error_without_resetting_retry_count():
    result = retry_result()
    result['ready'], result['error'] = True, None
    result['capture']['targets']['fit_bottom_right'].update(last_error=None, accepted_samples=15)
    report = compact_calibration_report(result)
    assert report['ready'] and report['error'] is None
    assert report['capture']['targets']['fit_bottom_right']['last_error'] is None
    assert report['capture']['targets']['fit_bottom_right']['attempts'] == 18
    assert report['capture']['retry_count'] == 18


def test_many_retries_cannot_persist_ever_growing_histories_or_unknown_targets():
    result = retry_result()
    history = [{'frame': b'private frame', 'gaze': [1, 2, 3], 'error': 'private error'}] * 10_000
    result['history'] = history
    result['capture']['attempt_history'] = history
    result['capture']['targets'] = {
        target: {'attempts': 10_000, 'accepted_samples': 12,
                 'last_error': 'SCREEN_SAMPLES_UNSTABLE', 'history': history,
                 'raw_vectors': history, 'frames': history}
        for target in TARGET_NAMES
    }
    result['capture']['targets'].update({f'unknown-{index}': {'history': history} for index in range(100)})
    report = compact_calibration_report(result)
    encoded = json.dumps(report, allow_nan=False).encode('utf-8')
    assert len(encoded) < MAX_REPORT_BYTES
    assert set(report['capture']['targets']) == set(TARGET_NAMES)
    assert b'history' not in encoded and b'private' not in encoded and b'raw_vectors' not in encoded
    assert len(report['capture']['targets']) == 9


@pytest.mark.parametrize('malformed', [float('inf'), float('nan'), True, [1] * 100, 10**500])
def test_malformed_or_unbounded_retry_metrics_become_null(malformed):
    result = retry_result()
    result['retry_count'] = malformed
    result['capture']['retry_count'] = malformed
    result['capture']['targets']['fit_center']['attempts'] = malformed
    report = compact_calibration_report(result)
    assert report['retry_count'] is None and report['capture']['retry_count'] is None
    assert report['capture']['targets']['fit_center']['attempts'] is None
    json.dumps(report, allow_nan=False)


def test_last_target_error_retains_only_safe_code_not_exception_path():
    result = retry_result()
    result['capture']['targets']['fit_center']['last_error'] = 'CAMERA_CAPTURE_FAILED: private/device/path'
    result['capture']['targets']['fit_bottom_right']['last_error'] = ['private exception content']
    report = compact_calibration_report(result)
    assert report['capture']['targets']['fit_center']['last_error'] == 'CAMERA_CAPTURE_FAILED'
    assert report['capture']['targets']['fit_bottom_right']['last_error'] == 'CALIBRATION_ERROR_UNCLASSIFIED'
    assert 'private' not in json.dumps(report)


def test_saved_retry_report_is_compact_and_retains_original_failed_gate(tmp_path):
    path = save_calibration_report(tmp_path, retry_result(), version='0.4.16-hackathon')
    report = json.loads(path.read_text('utf-8'))
    assert report['error'] == 'SCREEN_CALIBRATION_CANCELLED'
    assert report['quality']['failed_target'] == 'fit_bottom_right'
    assert report['capture']['targets']['fit_bottom_right']['attempts'] == 18
    assert report['capture']['targets']['fit_bottom_right']['last_error'] == 'SCREEN_TOO_FEW_FRESH_SAMPLES'
    assert path.stat().st_size < MAX_REPORT_BYTES


@pytest.mark.parametrize(('code', 'fragment'), [
    ('SCREEN_TOO_FEW_FRESH_SAMPLES', 'новых пригодных кадров'),
    ('SCREEN_HEAD_MOVED', 'Голова'),
    ('SCREEN_SAMPLES_UNSTABLE', 'колебались'),
    ('SCREEN_VALIDATION_ERROR_TOO_HIGH', 'не подтвердила'),
    ('UNKNOWN_GATE: private/path', 'заново измерить'),
])
def test_automatic_retry_hint_explains_gate_without_requesting_manual_restart(code, fragment):
    message = format_target_retry({'ready': False, 'error': code})
    assert fragment in message
    assert 'автоматически' in message and 'точки сохранены' in message
    assert 'Повторите настройку' not in message and 'private' not in message
