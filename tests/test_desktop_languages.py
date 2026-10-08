"""Exercise presentation language switches without starting camera or input hooks."""

import ast
import json
import re
from pathlib import Path

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from agent.i18n import set_language
    application = QApplication.instance() or QApplication([])
    set_language('ru', persist=False)
    yield application
    set_language('ru', persist=False)


def test_language_persists_and_updates_existing_widgets_without_touching_data(app, tmp_path):
    from agent.i18n import initialize, language, language_selector
    from agent.localized_widgets import QLabel, QLineEdit, QComboBox
    path = tmp_path / 'language.json'
    initialize(path)
    label = QLabel('Верните взгляд на монитор')
    entry = QLineEdit('Пароль преподавателя')
    entry.setPlaceholderText('Пароль преподавателя')
    model = QComboBox()
    model.addItem('Камера 2', {'camera_id': 2})
    first, second = language_selector(), language_selector()
    first.setCurrentIndex(first.findData('kk'))
    assert label.text() == 'Көзіңізді мониторға қайтарыңыз'
    assert entry.text() == 'Пароль преподавателя'
    assert entry.placeholderText() == 'Оқытушы құпиясөзі'
    assert model.currentData() == {'camera_id': 2}
    assert second.currentData() == 'kk'
    assert json.loads(path.read_text()) == {'language': 'kk'}
    initialize(path)
    assert language() == 'kk'
    first.setCurrentIndex(first.findData('en'))
    assert label.text() == 'Return your gaze to the monitor'
    assert entry.placeholderText() == 'Teacher password'
    assert model.currentText() == 'Camera 2'
    assert second.currentData() == 'en'


def test_all_bundled_russian_status_fragments_have_english_and_kazakh_catalog_entries(app):
    from agent.i18n import set_language, tr
    from agent.translations import MESSAGES
    root = Path(__file__).resolve().parents[1]
    strings = set()
    for path in (root / 'agent').glob('*.py'):
        if path.stem in ('selftest', 'translations', 'i18n'):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search('[А-Яа-яЁё]', node.value):
                strings.add(node.value)
    set_language('en', persist=False)
    assert not [value for value in strings if re.search('[А-Яа-яЁё]', tr(value))]
    assert all(len(values) == 2 and all(values) for values in MESSAGES.values())
    set_language('kk', persist=False)
    assert tr('Пароль преподавателя') == 'Оқытушы құпиясөзі'


def test_dynamic_diagnostics_preserve_angles_codes_and_names(app):
    from agent.i18n import set_language, tr
    from agent.desktop import public_gaze_status_text
    from agent.calibration_diagnostics import format_target_retry
    set_language('en', persist=False)
    value = public_gaze_status_text(dict(source='public_gaze_model', reference_ready=True,
        gaze_tracking_status='tracked', gaze_observed_direction='LEFT', gaze_observation_uncertain=True,
        gaze_display_yaw_degrees=12.5, gaze_display_pitch_degrees=-9.2))
    translated = tr(value)
    assert '12.5°' in translated and '9.2°' in translated
    assert 'no strike is added' in translated
    assert not re.search('[А-Яа-яЁё]', translated)
    assert tr('https://example.test/quiz?id=123 · PC14 · HEAD_TURN') == 'https://example.test/quiz?id=123 · PC14 · HEAD_TURN'
    # Import proves runtime diagnostics and localization share no dependency cycle.
    assert callable(format_target_retry)


def test_packaged_presentation_check(app):
    from agent.selftest import check_presentation
    assert check_presentation() == {'ui_languages': ['ru', 'kk', 'en'], 'attention_warning_hold_seconds': 1.0}


def test_language_change_preserves_preview_and_item_roles_and_menu_title(app):
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import Qt
    from agent.i18n import set_language
    from agent.localized_widgets import QLabel, QMenu, QListWidget
    label = QLabel('Предпросмотр камеры появится здесь')
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.blue)
    label.setPixmap(pixmap)
    menu = QMenu('Для преподавателя')
    action = menu.addAction('Открыть Qorgau')
    listing = QListWidget()
    listing.addItem('Камера 2')
    listing.item(0).setData(Qt.ItemDataRole.UserRole + 1, True)
    set_language('en', persist=False)
    assert not label.pixmap().isNull() and label.text() == ''
    assert menu.title() == 'For the teacher' and action.text() == 'Open Qorgau'
    assert listing.item(0).text() == 'Camera 2'
    assert listing.item(0).data(Qt.ItemDataRole.UserRole + 1) is True


def test_message_box_uses_selected_language_for_standard_buttons(app, monkeypatch):
    from PySide6.QtWidgets import QMessageBox as NativeBox
    from agent.localized_widgets import QMessageBox
    from agent.i18n import set_language
    result = []
    def capture(box):
        result.append((box.windowTitle(), box.text(), box.button(NativeBox.StandardButton.Cancel).text()))
        return NativeBox.StandardButton.Cancel
    monkeypatch.setattr(NativeBox, 'exec', capture)
    set_language('kk', persist=False)
    assert QMessageBox.question(None, 'Пароль преподавателя', 'Продолжить тест',
        NativeBox.StandardButton.Ok | NativeBox.StandardButton.Cancel) == NativeBox.StandardButton.Cancel
    assert result == [('Оқытушы құпиясөзі', 'Тестті жалғастыру', 'Бас тарту')]


def test_language_picker_signal_does_not_retain_its_own_widget(app):
    """A lambda capturing the picker leaks a Qt connection/receiver cycle."""
    import gc
    import weakref
    from PySide6.QtCore import QCoreApplication, QEvent
    from agent.i18n import language_selector, set_language
    from agent.localized_widgets import QWidget
    for _ in range(30):
        picker = language_selector()
        reference = weakref.ref(picker)
        del picker
        # Bound @Slot receivers are owned by Qt, not retained by a Python
        # closure. The old connection leaves this object alive even after GC.
        assert reference() is None
        parent = QWidget()
        child = language_selector(parent)
        parent_reference, child_reference = weakref.ref(parent), weakref.ref(child)
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        del child, parent
        gc.collect()
        assert parent_reference() is None and child_reference() is None
        set_language('en', persist=False)
        set_language('ru', persist=False)


def test_callback_menu_actions_translate_and_do_not_retain_receiver(app):
    import gc
    import weakref
    from PySide6.QtCore import Slot
    from PySide6.QtGui import QIcon
    from agent.i18n import set_language
    from agent.localized_widgets import QMenu, QWidget

    class Owner(QWidget):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.menu = QMenu(self)
            self.menu.addAction('Открыть Qorgau', self.selected)
            self.menu.addAction(QIcon(), 'Выйти из Qorgau', self.selected)

        @Slot()
        def selected(self):
            self.calls += 1

    for _ in range(30):
        set_language('ru', persist=False)
        owner = Owner()
        reference = weakref.ref(owner)
        set_language('en', persist=False)
        assert [action.text() for action in owner.menu.actions()] == ['Open Qorgau', 'Quit Qorgau']
        for action in owner.menu.actions():
            assert action.parent() is owner.menu
            action.trigger()
        assert owner.calls == 2
        del action, owner
        assert reference() is None
        gc.collect()
        set_language('kk', persist=False)
