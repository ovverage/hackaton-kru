"""Shared Qorgau visual language for the student desktop application."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QWidget

from .resources import resource_root


COLORS = {
    "paper": "#F4F5F7",
    "sheet": "#FFFFFF",
    "graphite": "#22252B",
    "pencil": "#5B6270",
    "faint": "#9AA0AA",
    "rule": "#D9DDE3",
    "rule_soft": "#E9ECF0",
    "ink": "#0B6474",
    "ink_strong": "#08505D",
    "ink_tint": "#E2F0F1",
    "amber": "#9A5B07",
    "amber_fill": "#F0B33E",
    "amber_tint": "#FCF1DC",
    "red": "#B5302F",
    "red_fill": "#C83A36",
    "red_tint": "#FBE9E7",
    "scene": "#2A2E35",
}

_FONT_FAMILY = ""


def load_fonts() -> str:
    """Load bundled Geologica faces and return the usable Qt family name."""
    global _FONT_FAMILY
    if _FONT_FAMILY:
        return _FONT_FAMILY
    font_dir = resource_root() / "agent" / "fonts"
    families: list[str] = []
    for name in ("Geologica-Regular.ttf", "Geologica-Medium.ttf", "Geologica-SemiBold.ttf", "Geologica-Bold.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(font_dir / name))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    _FONT_FAMILY = families[0] if families else "Segoe UI"
    return _FONT_FAMILY


def app_font(size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    return QFont(load_fonts(), size, weight)


APP_QSS = f"""
QWidget {{font-family: "Geologica", "Segoe UI", sans-serif;font-size:13px;color:{COLORS['graphite']};}}
QWidget#window {{background:{COLORS['paper']};}}
QFrame#sidebar {{background:{COLORS['sheet']};border:0;border-right:1px solid {COLORS['rule']};}}
QLabel#brand {{font-size:24px;font-weight:700;color:{COLORS['graphite']};}}
QLabel#sideHeading {{font-size:11px;font-weight:600;color:{COLORS['faint']};letter-spacing:1px;}}
QLabel#sideStep {{color:{COLORS['pencil']};font-size:13px;padding:11px 0;}}
QLabel#sideNote {{color:{COLORS['faint']};font-size:11px;}}
QLabel#eyebrow {{font-size:10px;letter-spacing:2px;color:{COLORS['ink']};font-weight:600;}}
QLabel#title {{font-size:27px;font-weight:600;color:{COLORS['graphite']};}}
QLabel#body {{font-size:13px;color:{COLORS['pencil']};}}
QLabel#small {{font-size:11px;color:{COLORS['faint']};}}
QLabel#heading {{font-size:16px;font-weight:600;color:{COLORS['graphite']};}}
QLabel#field {{font-size:12px;font-weight:500;color:{COLORS['pencil']};}}
QFrame#card {{background:{COLORS['sheet']};border:1px solid {COLORS['rule']};border-radius:10px;}}
QLabel#notice {{background:{COLORS['ink_tint']};color:{COLORS['ink_strong']};padding:13px;border:1px solid #C8E0E2;border-radius:8px;font-size:11px;}}
QLabel#error {{color:{COLORS['red']};background:{COLORS['red_tint']};border:1px solid #EBC9C6;border-radius:8px;padding:12px;}}
QLabel#badge {{font-size:11px;color:{COLORS['ink_strong']};background:{COLORS['ink_tint']};border:1px solid #C8E0E2;border-radius:8px;padding:6px 10px;}}
QLabel#heroTitle {{font-size:23px;font-weight:600;}}
QLabel#count {{font-size:32px;font-weight:600;color:{COLORS['ink']};}}
QLabel#countDanger {{font-size:32px;font-weight:600;color:{COLORS['red']};}}
QLineEdit,QComboBox {{background:{COLORS['sheet']};border:1px solid {COLORS['rule']};border-radius:8px;padding:11px 12px;color:{COLORS['graphite']};selection-background-color:{COLORS['ink']};}}
QLineEdit:focus,QComboBox:focus {{border:1px solid {COLORS['ink']};}}
QLineEdit:disabled,QComboBox:disabled {{background:{COLORS['paper']};color:{COLORS['faint']};}}
QPushButton {{background:{COLORS['sheet']};border:1px solid {COLORS['rule']};border-radius:8px;padding:11px 15px;color:{COLORS['graphite']};font-size:13px;font-weight:600;min-height:22px;}}
QPushButton:hover {{background:{COLORS['ink_tint']};border-color:{COLORS['ink']};}}
QPushButton:disabled {{background:{COLORS['paper']};color:{COLORS['faint']};border-color:{COLORS['rule_soft']};}}
QPushButton#primary {{background:{COLORS['ink']};color:white;border-color:{COLORS['ink']};}}
QPushButton#primary:hover {{background:{COLORS['ink_strong']};}}
QPushButton#danger {{background:{COLORS['red_fill']};color:white;border-color:{COLORS['red_fill']};}}
QScrollArea {{border:0;background:transparent;}}
QScrollBar:vertical {{background:{COLORS['paper']};width:9px;border:0;}}
QScrollBar::handle:vertical {{background:{COLORS['rule']};border-radius:4px;min-height:30px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;}}
"""


class Bubble(QWidget):
    """A compact OMR-style state marker used throughout the desktop UI."""

    def __init__(self, state: str = "empty", parent=None, size: int = 18):
        super().__init__(parent)
        self.state = state
        self.setFixedSize(size, size)

    def set_state(self, state: str):
        self.state = state
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = {"done": COLORS["ink"], "active": COLORS["amber_fill"], "danger": COLORS["red_fill"]}
        fill = colors.get(self.state, COLORS["sheet"])
        border = colors.get(self.state, COLORS["rule"])
        painter.setPen(QPen(QColor(border), 1.5))
        painter.setBrush(QColor(fill))
        painter.drawEllipse(QRectF(1.5, 1.5, self.width() - 3, self.height() - 3))
        if self.state == "done":
            painter.setPen(QPen(Qt.GlobalColor.white, 1.7))
            painter.drawLine(self.width() * .28, self.height() * .52, self.width() * .44, self.height() * .68)
            painter.drawLine(self.width() * .44, self.height() * .68, self.width() * .74, self.height() * .34)


class StepBubble(Bubble):
    def __init__(self, number: int, state: str = "empty", parent=None):
        super().__init__(state, parent, 28)
        self.number = number

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.state != "done":
            painter = QPainter(self)
            painter.setPen(QColor(COLORS["graphite"] if self.state == "empty" else COLORS["graphite"]))
            painter.setFont(app_font(10, QFont.Weight.DemiBold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.number))


class RegMarks(QFrame):
    """Sheet with unobtrusive registration marks for watched areas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(COLORS["ink"]), 2))
        margin, length = 10, 13
        w, h = self.width(), self.height()
        for x, y, sx, sy in ((margin, margin, 1, 1), (w-margin, margin, -1, 1), (margin, h-margin, 1, -1), (w-margin, h-margin, -1, -1)):
            painter.drawLine(x, y, x + sx * length, y)
            painter.drawLine(x, y, x, y + sy * length)


def logo_icon(size: int = 64) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(COLORS["ink"]))
    painter.drawRoundedRect(QRectF(size * .10, size * .10, size * .80, size * .80), size * .19, size * .19)
    painter.setBrush(QColor(COLORS["sheet"]))
    painter.drawEllipse(QRectF(size * .27, size * .25, size * .46, size * .46))
    painter.setBrush(QColor(COLORS["ink"]))
    painter.drawEllipse(QRectF(size * .40, size * .38, size * .20, size * .20))
    painter.end()
    return QIcon(pixmap)
