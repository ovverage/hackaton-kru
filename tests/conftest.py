"""Keep Qt object destruction on the GUI thread in the mixed GUI/API suite."""

import gc
import sys

import pytest


@pytest.fixture(autouse=True)
def collect_gui_cycles_on_owner_thread():
    yield
    # API TestClient starts worker threads after the GUI tests. Python may run
    # cyclic GC there, including signal/slot cycles holding abandoned widgets.
    # Collect those cycles here, after function fixtures finish, while we still
    # own the GUI thread; do not delete objects retained by other fixtures.
    widgets = sys.modules.get("PySide6.QtWidgets")
    core = sys.modules.get("PySide6.QtCore")
    if widgets is None or core is None:
        return
    app = widgets.QApplication.instance()
    if app is None or core.QThread.currentThread() != app.thread():
        return
    gc.collect()
    core.QCoreApplication.sendPostedEvents(None, core.QEvent.Type.DeferredDelete)
    gc.collect()
