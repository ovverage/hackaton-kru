import os
from pathlib import Path

import pytest

from agent.client import Agent, atomic_json


@pytest.fixture(scope="module")
def app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase

    app = QApplication.instance() or QApplication([])
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    yield app


def test_lock_screen_renders_evidence_and_masks_password(app, tmp_path):
    from PySide6.QtWidgets import QLineEdit
    from agent.exam_ui import LockScreen

    atomic_json(
        tmp_path / "config.json",
        {"server": "http://localhost:8000", "token": "fixture"},
    )
    agent = Agent(tmp_path)
    agent.engine.start()
    agent.engine.lock("PHONE_DETECTED")
    agent.remember(
        [
            {"id": "a", "type": "GAZE_DOWN", "at": 40, "epoch": 1, "duration": 5.2},
            {"id": "b", "type": "PHONE_DETECTED", "at": 74, "epoch": 1},
        ]
    )
    screen = LockScreen(agent)
    screen.resize(1366, 768)
    screen.update_state(agent.snapshot())
    screen.show()
    app.processEvents()
    assert screen.heading.text() == "Позовите преподавателя"
    assert screen.password.echoMode() == QLineEdit.EchoMode.Password
    assert screen.evidence_layout.count() >= 4
    screen.close()
    assert screen.isVisible()  # Alt+F4/window close is not an unlock
    destination = os.getenv("QORGAU_SCREENSHOT_DIR")
    if destination:
        Path(destination).mkdir(parents=True, exist_ok=True)
        assert screen.grab().save(str(Path(destination) / "lock-screen.png"))
    screen.hide()
    agent.http.close()


def test_native_browser_navigation_uses_exact_origin():
    from PySide6.QtCore import QUrl
    from agent.exam_browser import ExamPage, origin
    from types import SimpleNamespace

    reports = []
    page = SimpleNamespace(
        allowed_origin=origin("https://test.example"), on_attempt=reports.append
    )
    assert ExamPage.acceptNavigationRequest(
        page, QUrl("https://test.example/next"), None, True
    )
    for url in (
        "https://test.example.evil.test",
        "https://other.example",
        "file:///C:/",
        "https://test.example:8443",
    ):
        assert not ExamPage.acceptNavigationRequest(page, QUrl(url), None, True)
        assert not ExamPage.acceptNavigationRequest(page, QUrl(url), None, False)
    assert len(reports) == 8
