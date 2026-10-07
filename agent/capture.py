"""Keep camera/inference stalls out of the command and watchdog loop."""
import queue
import threading
import time


class CapturePump:
    def __init__(self, camera, *, recognize=True, phone_review=None):
        self.camera = camera
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=1)
        self.mode_lock = threading.Lock()
        self.recognize = recognize
        self.phone_review = phone_review if not recognize else None
        self.generation = 0
        self.started = time.monotonic()
        self.last_frame = self.started
        self.last_captured_at = None
        self.thread = threading.Thread(target=self.run, daemon=True, name="qorgau-camera")
        self.thread.start()

    def set_recognition(self, enabled, *, phone_review=None):
        """Invalidate queued and in-flight inference on either mode transition."""
        with self.mode_lock:
            phone_review = phone_review if not enabled else None
            if self.recognize == enabled and self.phone_review == phone_review:
                return
            self.recognize = enabled
            self.phone_review = phone_review
            self.generation += 1
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass

    def run(self):
        while not self.stop.is_set():
            with self.mode_lock:
                recognize, generation = self.recognize, self.generation
                phone_review = self.phone_review
            captured_at = time.monotonic()
            try:
                if not recognize and phone_review and captured_at <= phone_review[1]:
                    item = self.camera.read(analyze=False, phone_review=True, phone_review_until=phone_review[1])
                    if item[1] is not None:
                        item[1]["phone_review_lock_id"] = phone_review[0]
                else:
                    item = self.camera.read(analyze=recognize)
                if item[1] is not None:
                    captured_at = item[1].get("captured_at", captured_at)
            except Exception as error:
                item = error
                self.stop.wait(.1)
            with self.mode_lock:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    pass
                self.queue.put_nowait((generation, captured_at, item))

    def poll(self):
        with self.mode_lock:
            try:
                generation, captured_at, item = self.queue.get_nowait()
            except queue.Empty:
                item = None
            if item is not None and not isinstance(item, Exception):
                frame, observation = item
                # A completed inference cannot cross LOCK/UNLOCK, even if both
                # transitions occurred while the model was processing a frame.
                review_allowed = (self.phone_review is not None
                                  and time.monotonic() <= self.phone_review[1]
                                  and observation is not None
                                  and observation.get("phone_review_only") is True
                                  and observation.get("phone_review_lock_id") == self.phone_review[0])
                if generation != self.generation or (not self.recognize and not review_allowed):
                    observation = None
                self.last_captured_at = captured_at
                item = frame, observation
        if item is not None and not isinstance(item, Exception):
            self.last_frame = time.monotonic()
            return item
        if time.monotonic() - self.last_frame > 2:
            raise OSError("CAMERA_TIMEOUT: нет кадров камеры более двух секунд") from item
        return None

    def close(self):
        self.stop.set()
        self.thread.join(timeout=.5)
