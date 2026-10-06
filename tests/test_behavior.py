import numpy as np
import pytest
from agent.behavior import PhoneRaising, classify_gaze, validate_centres, POSITIONS
from scripts.evaluate_events import evaluate


def test_screen_edges_are_screen_and_ambiguous_is_unknown():
    centres = {key: np.array([i * .3, 0, 0, 0]) for i, (key, _) in enumerate(POSITIONS)}
    validate_centres(centres)
    assert classify_gaze(centres['SCREEN_LEFT'], centres) == 'SCREEN'
    assert classify_gaze(centres['LEFT'], centres) == 'LEFT'
    assert classify_gaze((centres['LEFT'] + centres['RIGHT']) / 2, centres) == 'UNKNOWN'
    centres['LEFT'] = centres['SCREEN_LEFT']
    with pytest.raises(ValueError):
        validate_centres(centres)


def test_phone_raise_is_one_review_and_stationary_phone_is_not_a_photo():
    detector = PhoneRaising()
    signals = []
    for i in range(50):
        y = 400 if i < 5 else max(190, 400 - (i - 5) * 30)
        signals.append(detector.update(i / 10, [{'confidence': .9, 'box': [200, y - 80, 280, y + 80]}], 640, 480))
    assert sum(signals) == 1
    still = PhoneRaising()
    assert not any(still.update(i/10, [{'confidence': .9, 'box': [200, 100, 280, 260]}], 640, 480) for i in range(40))


def test_metrics_count_duplicates_and_never_match_other_recordings():
    truth = [{'recording': 'a', 'type': 'PHONE', 'start': 0, 'end': 10}]
    predicted = truth * 2 + [{**truth[0], 'recording': 'b'}]
    metrics = evaluate(truth, predicted)['PHONE']
    assert metrics['tp'] == 1 and metrics['fp'] == 2 and metrics['fn'] == 0
    assert evaluate(truth, [])['PHONE']['precision'] is None
