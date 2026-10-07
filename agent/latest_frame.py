"""Continuously drain one camera into one newest timestamped frame slot.

Only the producer thread calls read/release on the native capture object. A
slow inference consumer skips superseded frames instead of draining an old
driver backlog. This does not make the recognition models themselves faster.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
import threading
import time


@dataclass(frozen=True)
class FramePacket:
    sequence: int
    captured_at: float
    # Snapshot owned by this packet. Consumers must copy before drawing on it.
    frame: object


class LatestFrameSource:
    """One capture owner, bounded memory and no duplicate inference frames.

    ``clock`` timestamps successful native reads. Wait deadlines deliberately
    use the real monotonic clock, so an injected/faulty timestamp clock cannot
    make cancellation or read timeouts unbounded.
    """

    def __init__(self, capture, *, clock=time.monotonic):
        self._capture = capture
        self._clock = clock
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._closed = False
        self._latest = None
        self._error = None
        self._thread = threading.Thread(target=self._produce, daemon=True,
                                        name='qorgau-frame-source')
        self._thread.start()

    def _produce(self):
        sequence = 0
        last_at = None
        try:
            while not self._stop.is_set():
                ok, frame = self._capture.read()
                if self._stop.is_set():
                    break
                captured_at = self._clock()
                if not ok or frame is None:
                    raise OSError('CAMERA_CAPTURE_FAILED: не получен кадр камеры')
                if (isinstance(captured_at, bool) or not isinstance(captured_at, Real)
                        or not math.isfinite(captured_at)
                        or (last_at is not None and captured_at < last_at)):
                    raise OSError('CAMERA_CLOCK_INVALID: неверная метка времени кадра')
                # Some capture adapters recycle a backing array. Preserve the
                # pixels associated with this timestamp while inference runs.
                copy_frame = getattr(frame, 'copy', None)
                snapshot = copy_frame() if callable(copy_frame) else frame
                # Fast driver draining can deliver distinct frames within one
                # platform clock tick. Preserve the real equal timestamp;
                # sequence provides identity, downstream time gates decide
                # whether enough measured time elapsed to count an observation.
                sequence += 1
                packet = FramePacket(sequence, float(captured_at), snapshot)
                with self._condition:
                    if self._closed:
                        break
                    self._latest = packet
                    last_at = captured_at
                    self._condition.notify_all()
        except Exception as error:
            with self._condition:
                if not self._closed:
                    self._error = error
                    self._latest = None
                    self._condition.notify_all()
        finally:
            # Never release from close(): the backend may still be inside a
            # blocking native read. Its owning thread releases after return.
            try:
                self._capture.release()
            except Exception as error:
                with self._condition:
                    if self._error is None and not self._closed:
                        self._error = error
                        self._latest = None
            with self._condition:
                self._condition.notify_all()

    def latest(self):
        """Nonblocking preview snapshot, never an old frame after failure/close."""
        with self._condition:
            return None if self._closed or self._error is not None else self._latest

    def read(self, after_sequence=0, timeout=2.0):
        """Return a newer packet or raise; consuming a frame never retimestamps it."""
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError('after_sequence must be a nonnegative integer')
        if (isinstance(timeout, bool) or not isinstance(timeout, Real)
                or not math.isfinite(timeout) or timeout < 0):
            raise ValueError('timeout must be finite and nonnegative')
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                if self._closed:
                    raise OSError('CAMERA_CLOSED: камера отключена')
                if self._error is not None:
                    if isinstance(self._error, OSError):
                        raise OSError(str(self._error)) from self._error
                    raise OSError('CAMERA_CAPTURE_FAILED: ошибка получения кадра') from self._error
                if self._latest is not None and self._latest.sequence > after_sequence:
                    return self._latest
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('CAMERA_TIMEOUT: нет нового кадра камеры')
                self._condition.wait(remaining)

    def close(self, timeout=.5):
        """Request shutdown; a blocked native read retains its own capture.

        Return whether the producer finished. A stalled backend can outlive the
        bounded join as a daemon, and releases only when its read returns. The
        capture must not be released or reused by another owner meanwhile.
        """
        if (isinstance(timeout, bool) or not isinstance(timeout, Real)
                or not math.isfinite(timeout) or not 0 <= timeout <= 1):
            raise ValueError('close timeout must be between zero and one second')
        with self._condition:
            self._closed = True
            self._latest = None
            self._stop.set()
            self._condition.notify_all()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout)
        return not self._thread.is_alive()
