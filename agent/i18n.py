"""Desktop presentation translations. Protocol values and evidence stay original.

The catalog includes complete sentences and explicit fragments used by dynamic
status messages; numbers, paths, IDs and unknown user-provided text are retained.
No network translation or changes to model confidence are involved.
"""

from pathlib import Path
import os
import re
import weakref

from .profile import read_object
from .translations import MESSAGES

LANGUAGES = {'ru': 'Русский', 'kk': 'Қазақша', 'en': 'English'}
_language = 'ru'
_preference_path = None
_widgets = weakref.WeakSet()
_pattern = re.compile('|'.join(
    ('(?<!\\w)' if source[0].isalnum() else '') + re.escape(source)
    + ('(?!\\w)' if source[-1].isalnum() else '')
    for source in sorted(MESSAGES, key=len, reverse=True)
))


def language():
    return _language


def tr(text):
    if _language == 'ru' or not isinstance(text, str):
        return text
    index = 0 if _language == 'en' else 1
    return _pattern.sub(lambda match: MESSAGES[match.group()][index], text)


def set_language(value, *, persist=True):
    global _language
    if value not in LANGUAGES:
        raise ValueError('Unsupported interface language')
    if persist and _preference_path is not None:
        from shared.storage import atomic_json
        atomic_json(_preference_path, {'language': value})
    _language = value
    for widget in list(_widgets):
        try:
            widget.retranslate()
        except RuntimeError:
            _widgets.discard(widget)  # Qt may have deleted the underlying QObject.


def initialize(path=None):
    global _preference_path
    _preference_path = Path(path) if path else Path.home() / '.qorgau' / 'ui-preferences.json'
    requested = os.environ.get('QORGAU_LANGUAGE', read_object(_preference_path).get('language', 'ru'))
    set_language(requested if requested in LANGUAGES else 'ru', persist=False)


def register(widget):
    _widgets.add(widget)


def language_selector(parent=None):
    from .localized_widgets import QComboBox
    picker = QComboBox(parent)
    picker._language_picker = True
    picker.setAccessibleName('Язык интерфейса')
    for code, name in LANGUAGES.items():
        picker.addItem(name, code)
    picker.setCurrentIndex(picker.findData(_language))
    picker.currentIndexChanged.connect(picker.select_interface_language)
    return picker
