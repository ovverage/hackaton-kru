import pytest
from scripts.evaluate_events import evaluate, wilson


def test_matching_is_one_to_one_and_isolated_by_recording():
    truth = [{"recording": "A", "type": "PHONE", "start": 1, "end": 5}]
    predictions = [truth[0], truth[0], {**truth[0], "recording": "B"}]
    metrics = evaluate(truth, predictions)["PHONE"]
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == (1, 2, 0)
    assert metrics["precision"] == pytest.approx(1/3)
    assert metrics["recall_ci95"][0] < 1


def test_empty_denominators_are_not_perfect_accuracy():
    assert wilson(0, 0) is None
    assert wilson(10, 10)[0] == pytest.approx(.7224672)
    assert evaluate([{"recording": "A", "type": "PHONE", "start": 1, "end": 5}], [])["PHONE"]["precision"] is None
