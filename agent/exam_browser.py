"""Single-origin, single-view browser. No arbitrary navigation, popups or downloads."""

from urllib.parse import urlparse

from PySide6.QtCore import QUrl, Qt
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QPushButton


def origin(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username:
        return None
    try:
        return (
            parsed.scheme,
            parsed.hostname.lower(),
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except ValueError:
        return None


class ExamPage(QWebEnginePage):
    def __init__(self, profile, allowed_url, on_attempt, parent):
        super().__init__(profile, parent)
        self.allowed_origin = origin(allowed_url)
        self.on_attempt = on_attempt

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        # All frame navigation is restricted: an iframe must not become an escape.
        if origin(url.toString()) == self.allowed_origin:
            return True
        self.on_attempt("BROWSER_ATTEMPT")
        return False

    def createWindow(self, window_type):
        self.on_attempt("BROWSER_ATTEMPT")
        return None

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        self.on_attempt("BROWSER_ATTEMPT")
        return []


class ExamBrowser(QWebEngineView):
    def __init__(self, url, on_attempt):
        super().__init__()
        if not origin(url):
            raise ValueError("INVALID_URL")
        self.setWindowTitle("Qorgau Browser — экзамен")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # Off-the-record profile: no password/history/cookie reuse between exams.
        self.profile = QWebEngineProfile(self)
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        self.exam_page = ExamPage(self.profile, url, on_attempt, self)
        self.setPage(self.exam_page)
        settings = self.settings()
        for attribute in (
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls,
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled,
        ):
            settings.setAttribute(attribute, False)
        self.setUrl(QUrl(url))
        self.released = False
        self.teacher_button = QPushButton("Преподаватель · Ctrl+Alt+Q", self)
        self.teacher_button.setStyleSheet("background:#183052;color:white;padding:8px;border-radius:5px;")
        self.teacher_button.clicked.connect(lambda: on_attempt("TEACHER_REQUEST"))
        self.teacher_button.adjustSize()
        self.teacher_button.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "teacher_button"):
            self.teacher_button.move(max(0, self.width() - self.teacher_button.width() - 12), 8)
            self.teacher_button.raise_()

    def closeEvent(self, event):
        if self.released:
            super().closeEvent(event)
        else:
            event.ignore()
