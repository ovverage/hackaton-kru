"""Qt presentation adapters: translate at display time, including worker errors."""

from PySide6 import QtWidgets as W
from PySide6.QtCore import Slot
from PySide6.QtGui import QAction as NativeAction
from .i18n import register, tr


class Presentation:
    _constructor_property = 'setText'
    def __init__(self, *args, **kwargs):
        self._source_text = {}
        # Qt overloads take text first or (icon, text, parent).
        values = list(args)
        for index, value in enumerate(values):
            if isinstance(value, str):
                self._source_text[self._constructor_property] = value
                values[index] = tr(value)
                break
        super().__init__(*values, **kwargs)
        register(self)

    def _present(self, method, value):
        self._source_text[method] = value
        return getattr(super(), method)(tr(value))

    def setText(self, value):
        return self._present('setText', value)

    def setWindowTitle(self, value):
        return self._present('setWindowTitle', value)

    def setToolTip(self, value):
        return self._present('setToolTip', value)

    def setAccessibleName(self, value):
        return self._present('setAccessibleName', value)

    def setPlaceholderText(self, value):
        return self._present('setPlaceholderText', value)

    def retranslate(self):
        for method, value in self._source_text.items():
            getattr(super(), method)(tr(value))
        if hasattr(self, 'update'):
            self.update()


class QLabel(Presentation, W.QLabel):
    def setPixmap(self, pixmap):
        self._source_text.pop('setText', None)
        super().setPixmap(pixmap)

    def clear(self):
        self._source_text.pop('setText', None)
        super().clear()


class QPushButton(Presentation, W.QPushButton):
    pass


class QCheckBox(Presentation, W.QCheckBox):
    pass


class QWidget(Presentation, W.QWidget):
    def __init__(self, *args, **kwargs):
        from PySide6.QtCore import Qt
        super().__init__(*args, **kwargs)
        # Native QWidget paints stylesheet backgrounds automatically; a Python
        # subclass must opt in (calibration cards must remain white on navy).
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


class QDialog(Presentation, W.QDialog):
    pass


class QLineEdit(Presentation, W.QLineEdit):
    # Typed text is data (passwords/IDs/paths), never translated.
    def __init__(self, *args, **kwargs):
        self._source_text = {}
        W.QLineEdit.__init__(self, *args, **kwargs)
        register(self)

    def setText(self, value):
        W.QLineEdit.setText(self, value)


class QComboBox(Presentation, W.QComboBox):
    SOURCE_ROLE = 0x1200

    @Slot(int)
    def select_interface_language(self, _index):
        from .i18n import set_language
        set_language(self.currentData())

    def addItem(self, *args):
        values = list(args)
        index = next(i for i, item in enumerate(values) if isinstance(item, str))
        source = values[index]
        values[index] = tr(source)
        super().addItem(*values)
        self.setItemData(self.count() - 1, source, self.SOURCE_ROLE)

    def addItems(self, values):
        for value in values:
            self.addItem(value)

    def retranslate(self):
        super().retranslate()
        for index in range(self.count()):
            value = self.itemData(index, self.SOURCE_ROLE)
            if value is not None:
                self.setItemText(index, tr(value))
        if getattr(self, '_language_picker', False):
            from .i18n import language
            previous = self.blockSignals(True)
            self.setCurrentIndex(self.findData(language()))
            self.blockSignals(previous)


class QListWidget(Presentation, W.QListWidget):
    SOURCE_ROLE = 0x1200

    def addItem(self, value):
        source = value if isinstance(value, str) else None
        super().addItem(tr(value) if source is not None else value)
        if source is not None:
            self.item(self.count() - 1).setData(self.SOURCE_ROLE, source)

    def retranslate(self):
        super().retranslate()
        for index in range(self.count()):
            item = self.item(index)
            source = item.data(self.SOURCE_ROLE)
            if source is not None:
                item.setText(tr(source))


class QSystemTrayIcon(Presentation, W.QSystemTrayIcon):
    def showMessage(self, title, message, *args):
        return super().showMessage(tr(title), tr(message), *args)

class QAction(Presentation, NativeAction):
    pass


class QMenu(Presentation, W.QMenu):
    _constructor_property = 'setTitle'

    def addAction(self, *args):
        # Qt's callable overload creates a native QAction with a hidden functor.
        # Give the action an explicit QObject owner and use a normal signal
        # connection, so bound receivers do not become retained Python closures.
        # This also keeps callback actions in the live translation registry.
        if len(args) in (2, 3) and isinstance(args[-2], str) and callable(args[-1]):
            action = QAction(*args[:-1], self)
            super().addAction(action)
            action.triggered.connect(args[-1])
            return action
        if 1 <= len(args) <= 2 and isinstance(args[-1], str):
            action = QAction(*args, self)
            super().addAction(action)
            return action
        return super().addAction(*(tr(arg) if isinstance(arg, str) else arg for arg in args))


class QMessageBox:
    StandardButton = W.QMessageBox.StandardButton
    Icon = W.QMessageBox.Icon

    @staticmethod
    def _show(kind, parent, title, text, buttons=None, default=None):
        from PySide6.QtCore import Qt
        buttons = buttons if buttons is not None else W.QMessageBox.StandardButton.Ok
        box = W.QMessageBox(kind, tr(title), tr(text), buttons, parent)
        box.setTextFormat(Qt.TextFormat.PlainText)
        names = {'Ok': 'ОК', 'Cancel': 'Отмена', 'Yes': 'Да', 'No': 'Нет',
                 'Close': 'Закрыть', 'Retry': 'Повторить', 'Save': 'Сохранить',
                 'Open': 'Открыть', 'Discard': 'Не сохранять', 'Abort': 'Прервать',
                 'Ignore': 'Игнорировать', 'Apply': 'Применить', 'Reset': 'Сбросить',
                 'Help': 'Справка', 'YesToAll': 'Да для всех', 'NoToAll': 'Нет для всех',
                 'RestoreDefaults': 'По умолчанию', 'SaveAll': 'Сохранить всё'}
        for key, source in names.items():
            button = box.button(getattr(W.QMessageBox.StandardButton, key))
            if button:
                button.setText(tr(source))
        if default is not None:
            box.setDefaultButton(default)
        return box.exec()

    @staticmethod
    def information(parent, title, text, *args):
        return QMessageBox._show(W.QMessageBox.Icon.Information, parent, title, text, *args)

    @staticmethod
    def warning(parent, title, text, *args):
        return QMessageBox._show(W.QMessageBox.Icon.Warning, parent, title, text, *args)

    @staticmethod
    def critical(parent, title, text, *args):
        return QMessageBox._show(W.QMessageBox.Icon.Critical, parent, title, text, *args)

    @staticmethod
    def question(parent, title, text, *args):
        return QMessageBox._show(W.QMessageBox.Icon.Question, parent, title, text, *args)


class QFileDialog:
    @staticmethod
    def getOpenFileName(parent, title, directory, pattern, *args):
        return W.QFileDialog.getOpenFileName(parent, tr(title), directory, tr(pattern), *args)
