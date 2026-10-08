"""Single-origin, single-view browser. No arbitrary navigation, popups or downloads."""

from urllib.parse import urlparse

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (QGraphicsDropShadowEffect, QHBoxLayout)
from .localized_widgets import (QLabel, QPushButton)

from .theme import COLORS, TEACHER_BUTTON_QSS, app_font, ui_icon
from .i18n import tr


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
        self.reporting = True

    def report_attempt(self, reason):
        # Chromium must finish its native navigation/file/window callback before
        # the controller can close this view. Closing from inside that callback
        # triggers a fatal WebEngine CHECK on Windows (0x80000003).
        QTimer.singleShot(0, self, lambda: self._deliver_attempt(reason))

    def _deliver_attempt(self, reason):
        if self.reporting:
            self.on_attempt(reason)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        # All frame navigation is restricted: an iframe must not become an escape.
        if origin(url.toString()) == self.allowed_origin:
            return True
        # A page's automatic advertising/login iframe is not a student attempt
        # to leave the exam. Deny it without cancelling the whole main page.
        if is_main_frame:
            self.report_attempt("BROWSER_ATTEMPT")
        return False

    def createWindow(self, window_type):
        self.report_attempt("BROWSER_ATTEMPT")
        return None

    def chooseFiles(self, mode, old_files, accepted_mime_types):
        self.report_attempt("BROWSER_ATTEMPT")
        return []


class ExamBrowser(QWebEngineView):
    def __init__(self, url, on_attempt):
        super().__init__()
        if not origin(url):
            raise ValueError("INVALID_URL")
        self.setWindowTitle(tr("Qorgau Browser — экзамен"))
        from .desktop import icon
        self.setWindowIcon(icon())
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # Off-the-record profile: no password/history/cookie reuse between exams.
        self.profile = QWebEngineProfile()
        self.profile.downloadRequested.connect(lambda download: download.cancel())
        self.exam_page = ExamPage(self.profile, url, on_attempt, self)
        # QObject destroys child objects after the parent's derived destructor.
        # Parenting the profile to its page keeps it alive through page teardown;
        # sibling children of the view would destroy the earlier profile first.
        self.profile.setParent(self.exam_page)
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
        self.teacher_button = QPushButton(self)
        self.teacher_button.setAccessibleName("Позвать преподавателя (Ctrl+Alt+Q)")
        self.teacher_button.setFont(app_font())
        self.teacher_button.setStyleSheet(TEACHER_BUTTON_QSS)
        button_layout = QHBoxLayout(self.teacher_button)
        button_layout.setContentsMargins(14, 9, 14, 9)
        button_layout.setSpacing(10)
        bell = QLabel(self.teacher_button)
        bell.setPixmap(ui_icon("bell", COLORS["surface"]).pixmap(20, 20))
        text = QLabel("Позвать преподавателя", self.teacher_button)
        shortcut = QLabel("Ctrl+Alt+Q", self.teacher_button)
        shortcut.setObjectName("teacherShortcut")
        for widget in (bell, text, shortcut):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            widget.setFont(app_font())
            button_layout.addWidget(widget)
        shadow = QGraphicsDropShadowEffect(self.teacher_button)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow_color = QColor(COLORS["navy900"])
        shadow_color.setAlpha(65)
        shadow.setColor(shadow_color)
        self.teacher_button.setGraphicsEffect(shadow)
        self.teacher_button.clicked.connect(lambda: on_attempt("TEACHER_REQUEST"))
        self.teacher_button.ensurePolished()
        self.teacher_button.setFixedSize(button_layout.sizeHint())
        self.teacher_button.show()
        self.layout_button = QPushButton("Язык ввода · Win+Пробел", self)
        self.layout_button.setStyleSheet(TEACHER_BUTTON_QSS)
        self.layout_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.layout_button.clicked.connect(self.switch_input_language)
        self.layout_button.adjustSize()
        self.layout_button.show()

    def switch_input_language(self):
        from .keyboard_layout import switch_layout
        switch_layout(int(self.winId()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "teacher_button"):
            self.teacher_button.move(max(0, self.width() - self.teacher_button.width() - 12), 8)
            self.teacher_button.raise_()
        if hasattr(self, 'layout_button'):
            self.layout_button.move(12, 8)
            self.layout_button.raise_()

    def closeEvent(self, event):
        if self.released:
            self.exam_page.reporting = False
            super().closeEvent(event)
        else:
            event.ignore()
