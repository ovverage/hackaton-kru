"""A native WebEngine regression runs separately because old code aborts."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_redirect_cancellation_and_browser_recreation_survive_native_callbacks():
    if importlib.util.find_spec('PySide6') is None:
        pytest.skip('Qt is installed in the Windows packaging environment')
    environment = os.environ.copy()
    if sys.platform == 'win32':
        # Native hidden Windows views reproduce the users' Chromium CHECK.
        # The rest of the GUI suite uses offscreen mocks and cannot cover it.
        environment.pop('QT_QPA_PLATFORM', None)
    else:
        environment.setdefault('QT_QPA_PLATFORM', 'offscreen')
    source = '''
import faulthandler
faulthandler.enable()
from agent.student_entry import configure_webengine
configure_webengine()
from agent.selftest import check_browser
check_browser()
print('BROWSER_NATIVE_RECOVERY_OK', flush=True)
'''
    result = subprocess.run([sys.executable, '-c', source],
                            cwd=Path(__file__).resolve().parents[1],
                            env=environment, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=100)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'BROWSER_NATIVE_RECOVERY_OK' in result.stdout
    assert 'Release of profile requested but WebEnginePage still not deleted' not in result.stderr
