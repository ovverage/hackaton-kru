"""Keep camera/inference stalls out of the command and watchdog loop."""
import queue
import threading
import time


class CapturePump:
    def __init__(self, camera):
        self.camera = camera
        self.stop = threading.Event()
        self.queue = queue.Queue(maxsize=1)
        self.started = time.monotonic()
        self.last_frame = self.started
        self.thread = threading.Thread(target=self.run, daemon=True, name="qorgau-camera")
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                item = self.camera.read()
            except Exception as error:
                item = error
                self.stop.wait(.1)
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(item)

    def poll(self):
        try:
            item = self.queue.get_nowait()
        except queue.Empty:
            item = None
        if item is not None and not isinstance(item, Exception):
            self.last_frame = time.monotonic()
            return item
        if time.monotonic() - self.last_frame > 2:
            raise OSError("CAMERA_TIMEOUT: нет обработанных кадров более двух секунд") from item
        return None

    def close(self):
        self.stop.set()
        self.thread.join(timeout=.5)
