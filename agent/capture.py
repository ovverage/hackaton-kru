"""Keep camera/inference stalls out of the command and watchdog loop."""
import queue
import threading
import time


class CapturePump:
    def __init__(self, camera, *, recognize=True):
        self.camera = camera
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=1)
        self.mode_lock = threading.Lock()
        self.recognize = recognize
        self.generation = 0
        self.started = time.monotonic()
        self.last_frame = self.started
        self.last_captured_at = None
        self.thread = threading.Thread(target=self.run, daemon=True, name="qorgau-camera")
        self.thread.start()

    def set_recognition(self, enabled):
        """Invalidate queued and in-flight inference on either mode transition."""
        with self.mode_lock:
            if self.recognize == enabled:
                return
            self.recognize = enabled
            self.generation += 1
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass

    def run(self):
        while not self.stop.is_set():
            with self.mode_lock:
                recognize, generation = self.recognize, self.generation
            captured_at = time.monotonic()
            try:
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
                if generation != self.generation or not self.recognize:
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
