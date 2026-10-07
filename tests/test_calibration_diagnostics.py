"""Explain the failed calibration gate and retain only bounded aggregates."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from agent.calibration_diagnostics import (
    MAX_REPORT_BYTES, MAX_REPORTS, TARGET_NAMES, compact_calibration_report,
    format_calibration_failure, save_calibration_report,
)


def failed_result():
    return {
        'ready': False, 'error': 'SCREEN_VALIDATION_ERROR_TOO_HIGH', 'elapsed_seconds': 28.3,
        'quality': {
            'policy': 'personal_screen_heuristic_v1', 'statistical_accuracy_guarantee': False,
            'fit_target_count': 5, 'validation_target_count': 4, 'margin_degrees': 6,
            'yaw_span_degrees': 17.3, 'pitch_span_degrees': 10.4,
            'fit_max_error': .09, 'validation_median_error': .17, 'validation_max_error': .31,
            'validation_errors': {'validation_top': .07, 'validation_right': .31},
            'sample_counts': {'fit_center': 12, 'validation_right': 11},
            'failed_target': 'validation_right',
        },
        'capture': {
            'completed_targets': 9, 'target_count': 9, 'target_visible_seconds': 3,
            'settle_seconds': .4, 'rejections': {'blink': 4, 'duplicate_frame': 2, 'no_gaze': 3},
            'targets': {'validation_right': {
                'accepted_samples': 11, 'rejected_samples': 3,
                'elapsed_seconds': 3.01, 'spread_degrees': 2.3,
                'rejections': {'blink': 2, 'head_moved': 1},
            }},
        },
    }


def test_final_validation_failure_is_distinguished_from_lighting_or_missing_frames():
    message = format_calibration_failure(failed_result())
    assert 'четыре проверочные точки' in message
    assert 'не подтвердили' in message
    assert 'середина правого края' in message
    assert 'освещ' not in message.lower()


@pytest.mark.parametrize('code,word', [
    ('SCREEN_SAMPLES_UNSTABLE', 'колебались'),
    ('SCREEN_SAMPLES_UNSTABLE_IN_SCREEN_SPACE', 'разброс'),
    ('SCREEN_FIT_ERROR_TOO_HIGH', 'Пять основных точек'),
    ('SCREEN_TRANSFORM_DEGENERATE', 'горизонтальные и вертикальные'),
    ('SCREEN_CORNER_GEOMETRY_INVALID', 'противоречивыми'),
    ('SCREEN_TOO_FEW_FRESH_SAMPLES', 'новых пригодных кадров'),
    ('SCREEN_HEAD_MOVED', 'переводите только глаза'),
    ('SCREEN_CHANGED', 'монитор'),
    ('SCREEN_TARGET_NOT_PRESENTED', 'показ следующей точки'),
    ('SCREEN_CALIBRATION_TIMEOUT', 'отведённое время'),
    ('CAMERA_CAPTURE_FAILED', 'перестала передавать'),
])
def test_each_gate_gets_specific_russian_feedback(code, word):
    assert word in format_calibration_failure({'ready': False, 'error': code})


@pytest.mark.parametrize('target_id,name', TARGET_NAMES.items())
def test_every_target_has_a_human_readable_name(target_id, name):
    result = {'ready': False, 'error': 'SCREEN_SAMPLES_UNSTABLE',
              'quality': {'failed_target': target_id}}
    assert name in format_calibration_failure(result)


def test_small_angular_span_feedback_uses_actual_aggregate_measurement():
    result = {'ready': False, 'error': 'SCREEN_ANGULAR_SPAN_TOO_SMALL',
              'quality': {'yaw_span_degrees': 2.12, 'pitch_span_degrees': 1.08}}
    message = format_calibration_failure(result)
    assert 'горизонтали 2.1°' in message and 'вертикали 1.1°' in message
    assert 'почти не различила' in message


def test_report_preserves_original_gate_and_metrics_without_mutating_result():
    result = failed_result()
    before = deepcopy(result)
    compact = compact_calibration_report(result, version='0.4.15-hackathon')
    assert result == before
    assert not compact['ready'] and compact['error'] == result['error']
    assert compact['quality'] == result['quality']
    assert compact['capture'] == result['capture']
    assert compact['version'] == '0.4.15-hackathon'
    assert compact['elapsed_seconds'] == 28.3


def test_report_excludes_images_vectors_absolute_angles_identifiers_and_arbitrary_text():
    result = failed_result()
    result.update(image=b'private-frame', frames=['private-frame'], token='private-token',
                  screen_signature={'serial': 'private-monitor'})
    result['quality'].update(
        reference_yaw_degrees=13.21, reference_pitch_degrees=7.8,
        center_observation={'vector': [1, 2, 3], 'yaw_degrees': 17},
        angular_polygon=[[1, 2], [3, 4]], raw_samples=['private-sample'],
        arbitrary_text='private-field',
    )
    result['capture']['targets']['validation_right'].update(
        frames=['private-frame'], gaze={'vector': [1, 2, 3]},
        rejections={'blink': 2, 'private-rejection': 100},
    )
    result['capture']['targets']['private-extra-target'] = {'accepted_samples': 100}
    report = compact_calibration_report(result)
    encoded = json.dumps(report)
    assert 'private-' not in encoded
    for excluded in ('reference_yaw_degrees', 'reference_pitch_degrees', 'center_observation',
                     'angular_polygon', 'raw_samples', 'image', 'frames', 'token', 'gaze'):
        assert excluded not in report and excluded not in report['quality']
        assert excluded not in report['capture']['targets']['validation_right']
    assert report['capture']['targets']['validation_right']['rejections'] == {'blink': 2}


def test_nonfinite_and_malformed_payloads_stay_valid_bounded_json():
    result = failed_result()
    result['elapsed_seconds'] = float('inf')
    result['quality']['validation_max_error'] = float('nan')
    result['quality']['failed_target'] = ['untrusted']
    result['failed_target'] = {'not': 'a target id'}
    result['capture']['failed_target'] = ['not-an-id']
    result['capture']['rejections'] = {'blink': float('nan'), 'head_moved': -1, 'duplicate_frame': 1.5}
    result['capture']['targets']['validation_right']['accepted_samples'] = [3] * 100_000
    report = compact_calibration_report(result, version='private/path/to/file')
    encoded = json.dumps(report, allow_nan=False).encode('utf-8')
    assert len(encoded) < MAX_REPORT_BYTES
    assert report['elapsed_seconds'] is None
    assert report['quality']['validation_max_error'] is None
    assert 'failed_target' not in report and 'failed_target' not in report['quality']
    assert report['capture']['rejections'] == {}
    assert 'version' not in report


def test_exception_details_and_paths_are_not_persisted_or_echoed():
    result = {'ready': False, 'error': 'CAMERA_CAPTURE_FAILED: private/path/to/device'}
    report = compact_calibration_report(result)
    assert report['error'] == 'CAMERA_CAPTURE_FAILED'
    assert 'private' not in format_calibration_failure(result)
    malformed = compact_calibration_report({'error': 'Private user data instead of a code'})
    assert malformed['error'] == 'CALIBRATION_ERROR_UNCLASSIFIED'


def test_success_and_cancel_feedback_do_not_claim_failure_or_force_retry():
    assert format_calibration_failure({'ready': True}) == 'Личная зона экрана настроена.'
    assert format_calibration_failure({'ready': False, 'error': 'SCREEN_CALIBRATION_CANCELLED'}) == 'Настройка отменена.'


def test_save_report_creates_readable_local_json_with_no_raw_data(tmp_path):
    result = failed_result()
    result['image'] = b'not serializable and must be excluded'
    path = save_calibration_report(tmp_path, result, version='0.4.15-hackathon')
    assert path.parent == tmp_path / 'calibration-diagnostics'
    saved = json.loads(path.read_text('utf-8'))
    assert saved['error'] == 'SCREEN_VALIDATION_ERROR_TOO_HIGH'
    assert saved['quality']['validation_max_error'] == .31
    assert saved['capture']['completed_targets'] == 9
    assert path.stat().st_size < MAX_REPORT_BYTES
    assert not list(path.parent.glob('*.tmp'))


def test_retention_keeps_latest_five_own_reports_and_preserves_unrelated_files(tmp_path):
    folder = tmp_path / 'calibration-diagnostics'
    folder.mkdir()
    unrelated = folder / 'teacher-notes.json'
    unrelated.write_text('keep this', encoding='utf-8')
    created = [save_calibration_report(tmp_path, dict(failed_result(), elapsed_seconds=index))
               for index in range(8)]
    retained = sorted(folder.glob('attempt-*.json'))
    assert len(retained) == MAX_REPORTS == 5
    assert retained == sorted(created[-5:])
    assert unrelated.read_text('utf-8') == 'keep this'
    assert all(not path.exists() for path in created[:3])


def test_repeated_windows_clock_ticks_keep_true_creation_order_across_restart(tmp_path, monkeypatch):
    from agent import calibration_diagnostics

    class RepeatedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 8, 4, 5, 6, 723361, tzinfo=timezone.utc)

    monkeypatch.setattr(calibration_diagnostics, 'datetime', RepeatedDateTime)
    monkeypatch.setattr(calibration_diagnostics, '_LAST_REPORT_STAMP', None)
    created = [save_calibration_report(tmp_path, dict(failed_result(), elapsed_seconds=index))
               for index in range(8)]
    assert len({path.name.split('-')[1] for path in created}) == 8
    assert sorted((tmp_path / 'calibration-diagnostics').glob('attempt-*.json')) == created[-5:]
    # A new process has no in-memory counter but must order after saved reports
    # even if the wall clock still has exactly the same timestamp.
    monkeypatch.setattr(calibration_diagnostics, '_LAST_REPORT_STAMP', None)
    newest = save_calibration_report(tmp_path, failed_result())
    assert newest.name > created[-1].name and newest.exists()
    assert sorted((tmp_path / 'calibration-diagnostics').glob('attempt-*.json')) == created[-4:] + [newest]


def test_concurrent_attempt_writes_are_atomic_and_keep_five_latest(tmp_path, monkeypatch):
    from agent import calibration_diagnostics

    class RepeatedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 8, 4, 5, 6, 723361, tzinfo=timezone.utc)

    monkeypatch.setattr(calibration_diagnostics, 'datetime', RepeatedDateTime)
    monkeypatch.setattr(calibration_diagnostics, '_LAST_REPORT_STAMP', None)
    with ThreadPoolExecutor(max_workers=4) as executor:
        created = list(executor.map(lambda _: save_calibration_report(tmp_path, failed_result()), range(12)))
    retained = sorted((tmp_path / 'calibration-diagnostics').glob('attempt-*.json'))
    assert len(set(created)) == 12
    assert len({path.name.split('-')[1] for path in created}) == 12
    assert retained == sorted(created)[-5:]
    assert all(json.loads(path.read_text('utf-8'))['error'] == 'SCREEN_VALIDATION_ERROR_TOO_HIGH'
               for path in retained)
    assert not list((tmp_path / 'calibration-diagnostics').glob('*.tmp'))


def test_atomic_save_failure_cleans_its_temporary_and_keeps_original_result(tmp_path, monkeypatch):
    result = failed_result()
    original = deepcopy(result)

    def denied_replace(self, target):
        raise PermissionError('write denied')

    monkeypatch.setattr(Path, 'replace', denied_replace)
    with pytest.raises(PermissionError):
        save_calibration_report(tmp_path, result)
    assert result == original
    assert not list((tmp_path / 'calibration-diagnostics').iterdir())


def test_missing_result_gets_safe_diagnostic_instead_of_serialization_failure():
    compact = compact_calibration_report(None)
    assert not compact['ready'] and compact['quality'] == {} and compact['capture'] == {}
    assert 'не удалось завершить' in format_calibration_failure(None)
