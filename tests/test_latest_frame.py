"""No camera access: exercise capture ownership and slow-consumer boundaries."""

from dataclasses import FrozenInstanceError
import queue
import threading
import time

import numpy as np
import pytest

from agent.latest_frame import LatestFrameSource


class Capture:
    def __init__(self):
        self.frames = queue.Queue()
        self.calls = queue.Queue()
        self.releases = []
        self.read_active = False

    def read(self):
        self.read_active = True
        self.calls.put(threading.current_thread().name)
        try:
            result = self.frames.get(timeout=5)
            if isinstance(result, Exception):
                raise result
            return result
        finally:
            self.read_active = False

    def release(self):
        assert not self.read_active, 'Native capture must never be released during read'
        self.releases.append(threading.current_thread().name)


@pytest.fixture
def source():
    capture = Capture()
    timestamps = iter([100 + index / 10 for index in range(100)])
    source = LatestFrameSource(capture, clock=lambda: next(timestamps))
    assert capture.calls.get(timeout=1) == 'qorgau-frame-source'
    try:
        yield capture, source
    finally:
        source.close(timeout=0)
        capture.frames.put((False, None))
        assert source.close(timeout=1)


def publish(capture, value):
    image = np.full((3, 4, 3), value, dtype=np.uint8)
    capture.frames.put((True, image))
    assert capture.calls.get(timeout=1) == 'qorgau-frame-source'
    return image


def test_slow_consumer_gets_newest_slot_instead_of_old_frames(source):
    capture, source = source
    publish(capture, 1)
    first = source.read()
    publish(capture, 2)
    publish(capture, 3)
    publish(capture, 4)
    newest = source.read(after_sequence=first.sequence)
    assert first.sequence == 1 and newest.sequence == 4
    assert newest.captured_at == pytest.approx(100.3)
    assert np.all(newest.frame == 4)
    assert source.latest() is newest


def test_metadata_is_immutable_and_pixels_do_not_alias_recycled_backend_array(source):
    capture, source = source
    original = publish(capture, 3)
    packet = source.read()
    with pytest.raises(FrozenInstanceError):
        packet.captured_at = 999
    original[:] = 99
    assert np.all(packet.frame == 3)


def test_timestamp_belongs_to_capture_and_is_not_changed_by_repeated_preview(source):
    capture, source = source
    publish(capture, 7)
    first = source.latest()
    assert first.sequence == 1 and first.captured_at == 100
    assert source.read().captured_at == source.latest().captured_at == 100


def test_same_sequence_cannot_be_reprocessed_as_a_fresh_frame(source):
    capture, source = source
    publish(capture, 1)
    packet = source.read()
    with pytest.raises(TimeoutError, match='CAMERA_TIMEOUT'):
        source.read(after_sequence=packet.sequence, timeout=.01)
    publish(capture, 2)
    assert source.read(after_sequence=packet.sequence).sequence == 2


def test_first_frame_timeout_does_not_invent_a_preview_or_sample(source):
    _, source = source
    assert source.latest() is None
    with pytest.raises(TimeoutError, match='CAMERA_TIMEOUT'):
        source.read(timeout=.01)


@pytest.mark.parametrize('failure', [(False, None), OSError('native camera disconnected'), RuntimeError('decoder failed')])
def test_capture_failure_surfaces_and_never_reuses_old_healthy_frame(source, failure):
    capture, source = source
    publish(capture, 1)
    first = source.read()
    capture.frames.put(failure)
    with pytest.raises(OSError):
        source.read(after_sequence=first.sequence, timeout=1)
    assert source.latest() is None
    assert source.close(timeout=1)
    assert capture.releases == ['qorgau-frame-source']


def test_close_returns_while_native_read_blocks_and_release_waits_for_read_return(source):
    capture, source = source
    assert capture.read_active
    start = time.monotonic()
    assert not source.close(timeout=.02)
    assert time.monotonic() - start < .5
    assert not capture.releases
    assert source.latest() is None
    with pytest.raises(OSError, match='CAMERA_CLOSED'):
        source.read(timeout=0)
    capture.frames.put((True, np.zeros((3, 4, 3), dtype=np.uint8)))
    assert source.close(timeout=1)
    assert source.latest() is None
    assert capture.releases == ['qorgau-frame-source']
    assert source.close(timeout=0)
    assert len(capture.releases) == 1


def test_close_wakes_a_consumer_waiting_for_new_frames(source):
    _, source = source
    outcome = queue.Queue()
    waiting = threading.Event()

    def consumer():
        waiting.set()
        try:
            source.read(timeout=10)
        except OSError as error:
            outcome.put(str(error))

    thread = threading.Thread(target=consumer)
    thread.start()
    assert waiting.wait(1)
    source.close(timeout=0)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert 'CAMERA_CLOSED' in outcome.get_nowait()


@pytest.mark.parametrize('bad_time', [float('nan'), float('inf'), 99., True])
def test_bad_or_decreasing_capture_timestamps_fail_without_fabricating_freshness(bad_time):
    capture = Capture()
    timestamps = iter([100., bad_time])
    source = LatestFrameSource(capture, clock=lambda: next(timestamps))
    try:
        capture.calls.get(timeout=1)
        publish(capture, 1)
        first = source.read()
        capture.frames.put((True, np.ones((3, 4, 3), dtype=np.uint8)))
        with pytest.raises(OSError, match='CAMERA_CLOCK_INVALID'):
            source.read(after_sequence=first.sequence, timeout=1)
    finally:
        source.close(timeout=0)
        capture.frames.put((False, None))
        assert source.close(timeout=1)


def test_distinct_frames_with_equal_clock_ticks_keep_original_time_and_unique_sequences():
    capture = Capture()
    source = LatestFrameSource(capture, clock=lambda: 100.)
    try:
        capture.calls.get(timeout=1)
        publish(capture, 1)
        first = source.read()
        publish(capture, 2)
        second = source.read(after_sequence=first.sequence)
        assert first.sequence == 1 and second.sequence == 2
        assert first.captured_at == second.captured_at == 100.
        assert np.all(first.frame == 1) and np.all(second.frame == 2)
    finally:
        source.close(timeout=0)
        capture.frames.put((False, None))
        assert source.close(timeout=1)


@pytest.mark.parametrize('kwargs', [{'after_sequence': -1}, {'after_sequence': True},
                                    {'timeout': -1}, {'timeout': float('nan')}])
def test_invalid_read_bounds_are_rejected(source, kwargs):
    _, source = source
    with pytest.raises(ValueError):
        source.read(**kwargs)


@pytest.mark.parametrize('timeout', [-1, 1.1, float('nan'), True])
def test_close_cannot_turn_into_an_unbounded_join(source, timeout):
    _, source = source
    with pytest.raises(ValueError):
        source.close(timeout)
