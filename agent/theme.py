"""Shared navy visual language for the student desktop application."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QHBoxLayout)
from .localized_widgets import (QLabel, QWidget)
from PySide6.QtSvg import QSvgRenderer

from .resources import resource_root


COLORS = {
    "bg": "#F3F4F6", "surface": "#FFFFFF", "surface2": "#F7F8FA",
    "navy900": "#0F2245", "navy800": "#153B83", "navy700": "#0F2E68",
    "blue": "#2859BC", "blue_tint": "#EAF0FC", "text": "#1D2C47",
    "muted": "#5F6673", "line": "#E2E4E9", "line_soft": "#ECEEF2",
    "faint": "#A0A3AA", "side_text": "#C0C6D0", "side_muted": "#939EB1",
    "side_line": "#233A65", "blue_light": "#7FA2E8", "step_line": "#B9C1CF",
    "red": "#B4443F", "red_fill": "#C4483F", "red_tint": "#FDEEEC",
    "amber": "#8A5F1C", "amber_fill": "#DFB373", "amber_tint": "#FBF1E0",
    "scene": "#232B39", "logo_letter": "#EDF4E7",
}
# Compatibility names are used by older, non-UI integration code as well.
COLORS.update(paper=COLORS["bg"], sheet=COLORS["surface"], graphite=COLORS["text"],
              pencil=COLORS["muted"], rule=COLORS["line"], rule_soft=COLORS["line_soft"],
              ink=COLORS["navy800"], ink_strong=COLORS["navy700"], ink_tint=COLORS["blue_tint"])
_FONT_FAMILY = ""


def load_fonts() -> str:
    """Use bundled Geologica, with a harmless fallback on incomplete installs."""
    global _FONT_FAMILY
    if _FONT_FAMILY:
        return _FONT_FAMILY
    families: list[str] = []
    for name in ("Geologica-Regular.ttf", "Geologica-Medium.ttf", "Geologica-SemiBold.ttf", "Geologica-Bold.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(resource_root() / "agent" / "fonts" / name))
        if font_id >= 0:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    _FONT_FAMILY = families[0] if families else "Segoe UI"
    return _FONT_FAMILY


def app_font(size: int = 13, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    font = QFont(load_fonts())
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


APP_QSS = f"""
QWidget {{font-family:"Geologica","Segoe UI",sans-serif;font-size:14px;color:{COLORS['text']};}}
QWidget#window {{background:{COLORS['surface']};}}
QLabel {{background:transparent;}}
QLabel#brand {{font-size:24px;font-weight:600;}}
QLabel#title {{font-size:26px;font-weight:600;}}
QLabel#body {{font-size:14px;color:{COLORS['muted']};}}
QLabel#small {{font-size:12px;color:{COLORS['muted']};}}
QLabel#heading {{font-size:16px;font-weight:600;}}
QLabel#field {{font-size:13px;font-weight:500;}}
QFrame#card {{background:{COLORS['surface']};border:1px solid {COLORS['line']};border-radius:12px;}}
QFrame#band {{background:{COLORS['blue_tint']};border:0;border-radius:14px;}}
QFrame#band[pause="true"] {{background:{COLORS['red_tint']};}}
QLabel#notice {{background:{COLORS['blue_tint']};color:{COLORS['navy800']};padding:12px;border-radius:10px;}}
QLabel#warning {{color:{COLORS['amber']};background:{COLORS['amber_tint']};border-radius:10px;padding:12px;}}
QLabel#error {{color:{COLORS['red']};background:{COLORS['red_tint']};border-radius:10px;padding:12px;}}
QLabel#badge {{font-size:12px;color:{COLORS['navy800']};background:{COLORS['blue_tint']};border-radius:14px;padding:7px 10px;}}
QLabel#badge[tone="warn"] {{color:{COLORS['amber']};background:{COLORS['amber_tint']};}}
QLabel#heroTitle {{font-size:24px;font-weight:600;}}
QLineEdit,QComboBox,QSpinBox {{background:{COLORS['surface']};border:1px solid {COLORS['line']};border-radius:8px;padding:10px 12px;color:{COLORS['text']};selection-background-color:{COLORS['blue']};min-height:22px;}}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus {{border:2px solid {COLORS['blue']};padding:9px 11px;}}
QLineEdit:disabled,QComboBox:disabled,QSpinBox:disabled {{background:{COLORS['bg']};color:{COLORS['muted']};}}
QPushButton {{background:{COLORS['surface']};border:1px solid {COLORS['line']};border-radius:10px;padding:10px 14px;color:{COLORS['text']};font-size:14px;font-weight:500;min-height:22px;}}
QPushButton:hover {{background:{COLORS['blue_tint']};border-color:{COLORS['blue']};}}
QPushButton:focus {{border:2px solid {COLORS['blue']};padding:9px 13px;}}
QPushButton:disabled {{background:{COLORS['bg']};color:{COLORS['muted']};border-color:{COLORS['line_soft']};}}
QPushButton#primary {{background:{COLORS['navy800']};color:{COLORS['surface']};border-color:{COLORS['navy800']};}}
QPushButton#primary:hover {{background:{COLORS['navy700']};}}
QPushButton#primary:focus {{border:2px solid {COLORS['blue']};}}
QPushButton#primary:disabled {{background:{COLORS['bg']};color:{COLORS['muted']};border-color:{COLORS['line_soft']};}}
QPushButton#danger {{color:{COLORS['red']};background:transparent;border-color:transparent;}}
QListWidget {{background:{COLORS['surface']};border:1px solid {COLORS['line']};border-radius:12px;outline:0;}}
QListWidget::item {{padding:16px;border-bottom:1px solid {COLORS['line_soft']};min-height:38px;}}
QListWidget::item:selected {{background:{COLORS['blue_tint']};color:{COLORS['navy800']};}}
QListWidget:focus {{border:2px solid {COLORS['blue']};}}
QProgressBar {{background:{COLORS['line_soft']};border:0;border-radius:5px;text-align:center;height:12px;}}
QProgressBar::chunk {{background:{COLORS['navy800']};border-radius:5px;}}
QScrollArea {{border:0;background:transparent;}}
QScrollBar:vertical {{background:{COLORS['surface2']};width:9px;border:0;}}
QScrollBar::handle:vertical {{background:{COLORS['line']};border-radius:4px;min-height:30px;}}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{height:0;}}
QMenu {{background:{COLORS['surface']};border:1px solid {COLORS['line']};padding:8px;}}
QMenu::item {{padding:12px 18px;border-radius:8px;}}
QMenu::item:selected {{background:{COLORS['blue_tint']};color:{COLORS['navy800']};}}
QMenu::item:disabled {{color:{COLORS['muted']};}}
"""


class Bubble(QWidget):
    """Compact status point; the step variant adds a number or check mark."""

    def __init__(self, state: str = "empty", parent=None, size: int = 16):
        super().__init__(parent)
        self.state = state
        self.setFixedSize(size, size)

    def set_state(self, state: str):
        self.state = state
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = {"done": COLORS["navy800"], "on": COLORS["navy800"],
                  "ink": COLORS["blue"], "warn": COLORS["amber_fill"],
                  "pause": COLORS["red_fill"], "danger": COLORS["red_fill"]}
        painter.setPen(QPen(QColor(colors.get(self.state, COLORS['step_line'])), 1.5,
                            Qt.PenStyle.DashLine if self.state == 'off' else Qt.PenStyle.SolidLine))
        painter.setBrush(QColor(colors.get(self.state, COLORS['surface'])))
        painter.drawEllipse(QRectF(1.5, 1.5, self.width() - 3, self.height() - 3))


class StepBubble(Bubble):
    def __init__(self, number: int, state: str = "empty", parent=None):
        super().__init__(state, parent, 36)
        self.number = number

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.state in ('now', 'active')
        if active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS['blue_tint']))
            painter.drawEllipse(QRectF(0, 0, 36, 36))
        border = COLORS['navy800'] if self.state == 'done' else COLORS['blue'] if active else COLORS['step_line']
        painter.setPen(QPen(QColor(border), 1.5, Qt.PenStyle.DashLine if self.state == 'skip' else Qt.PenStyle.SolidLine))
        painter.setBrush(QColor(COLORS['navy800'] if self.state == 'done' else COLORS['surface']))
        painter.drawEllipse(QRectF(4, 4, 28, 28))
        painter.setPen(QPen(QColor(COLORS['surface'] if self.state == 'done' else COLORS['blue'] if active else COLORS['muted']), 2))
        if self.state == 'done':
            painter.drawLine(11, 18, 16, 23)
            painter.drawLine(16, 23, 25, 13)
        else:
            painter.setFont(app_font(13, QFont.Weight.DemiBold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self.number))


def camera_marks(painter, rect):
    """Registration marks belong only to actual camera imagery."""
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(COLORS['surface']))
    for x, y in ((rect.left()+12, rect.top()+12), (rect.right()-21, rect.top()+12),
                 (rect.left()+12, rect.bottom()-21), (rect.right()-21, rect.bottom()-21)):
        painter.drawRect(QRectF(x, y, 9, 9))


def logo_icon(size: int = 64, *, paused=False) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(COLORS['surface'] if paused else COLORS['blue']))
    painter.drawRoundedRect(QRectF(size/32, size/32, size*15/16, size*15/16), size*15/64, size*15/64)
    painter.setPen(QColor(COLORS['red_fill'] if paused else COLORS['logo_letter']))
    painter.setFont(app_font(round(size*40/64), QFont.Weight.Bold))
    painter.drawText(QRectF(0, -size*.10, size, size), Qt.AlignmentFlag.AlignCenter, 'q')
    painter.end()
    return QIcon(pixmap)


def brand_widget(*, paused=False):
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(9)
    tile = QLabel()
    tile.setPixmap(logo_icon(60, paused=paused).pixmap(30, 30))
    row.addWidget(tile)
    word = QLabel(f'qorgau<span style="color:{COLORS["surface"] if paused else COLORS["blue"]}">.</span>')
    word.setObjectName('brand')
    if paused:
        word.setStyleSheet(f"color:{COLORS['surface']};font-size:24px;font-weight:600;")
    row.addWidget(word)
    return widget


_ICON_PATHS = {
    'eye': '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'pause': '<path d="M8 5v14M16 5v14"/>',
    'window': '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 9h20"/>',
    'globe': '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    'bell': '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    'lock': '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4"/>',
    'mic-off': '<path d="m2 2 20 20M9 9v3a3 3 0 0 0 5 2M9 5a3 3 0 0 1 6 0v4M5 10v2a7 7 0 0 0 12 5M19 10v2M12 19v3M8 22h8"/>',
}


def ui_icon(name: str, color=None, size=24):
    """Small Lucide-compatible SVG paths, bundled in source for frozen builds."""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color or COLORS["navy800"]}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">{_ICON_PATHS[name]}</svg>'
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pixmap)


CALIBRATION_QSS = APP_QSS + f"""
QDialog {{background:{COLORS['navy900']};}}
QWidget#calibrationPanel {{background:{COLORS['surface']};border-radius:16px;}}
QLabel#calibrationTitle {{font-size:24px;font-weight:600;}}
QLabel#calibrationInstructions {{color:{COLORS['muted']};font-size:16px;}}
QLabel#calibrationError {{background:{COLORS['red_tint']};color:{COLORS['red']};padding:12px;border-radius:10px;}}
QLabel#calibrationProgress {{color:{COLORS['surface']};background:rgba(15,34,69,210);border-radius:10px;padding:12px;font-size:16px;}}
"""

TEACHER_BUTTON_QSS = f"""
QPushButton {{background:{COLORS['navy900']};color:{COLORS['surface']};border:1px solid {COLORS['side_line']};border-radius:10px;}}
QPushButton:hover {{background:{COLORS['navy800']};}}
QPushButton:focus {{border:2px solid {COLORS['blue']};}}
QPushButton:disabled {{background:{COLORS['scene']};}}
QPushButton QLabel {{color:{COLORS['surface']};background:transparent;font-weight:500;}}
QPushButton:disabled QLabel {{color:{COLORS['side_text']};}}
QLabel#teacherShortcut {{border:1px solid {COLORS['side_line']};border-radius:5px;padding:4px 6px;font-size:12px;}}
"""


class CountdownRing(QWidget):
    """UI-only countdown; observations and rule engine state stay untouched."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.remaining = None
        self.threshold = 5.0  # shared.rules.RuleEngine.observe uses the same fixed threshold.
        self.setFixedSize(54, 54)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4, 4, 46, 46)
        painter.setPen(QPen(QColor(COLORS['side_line']), 4))
        painter.drawEllipse(rect)
        if self.remaining is not None:
            painter.setPen(QPen(QColor(COLORS['amber_fill']), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(rect, 90*16, -round(360*16*self.remaining/self.threshold))
        painter.setPen(QColor(COLORS['surface']))
        painter.setFont(app_font(14, QFont.Weight.Medium))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         f'{self.remaining:.1f}'.replace('.', ',') if self.remaining is not None else '—')


class CameraEvidence(QLabel):
    """An existing evidence thumbnail, with frame-only registration marks."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.pixmap() and not self.pixmap().isNull():
            painter = QPainter(self)
            camera_marks(painter, self.contentsRect())


class StepsPanel(QWidget):
    """A quiet connector makes the three preparation steps one sequence."""

    def paintEvent(self, event):
        from PySide6.QtCore import QPoint
        super().paintEvent(event)
        bubbles = self.findChildren(StepBubble)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(COLORS['line_soft']), 2))
        for first, second in zip(bubbles, bubbles[1:]):
            start = first.mapTo(self, QPoint(18, 33))
            end = second.mapTo(self, QPoint(18, 3))
            painter.drawLine(start, end)


class ProgressPoint(StepBubble):
    """Enrollment progress has a spinner instead of a numbered active step."""

    def __init__(self, state, parent=None):
        super().__init__(0, state, parent)
        self.angle = 0
        if state == 'now':
            from PySide6.QtCore import QTimer
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.advance)
            self.timer.start(80)

    def advance(self):
        self.angle = (self.angle + 24) % 360
        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        if self.state == 'done':
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(COLORS['line']), 2))
        painter.drawEllipse(QRectF(8, 8, 20, 20))
        if self.state == 'now':
            painter.setPen(QPen(QColor(COLORS['blue']), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(8, 8, 20, 20), self.angle*16, 230*16)
