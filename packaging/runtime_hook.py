"""Use only browser binaries delivered with the installed application."""
import os
from pathlib import Path
import sys

if getattr(sys, 'frozen', False):
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(sys._MEIPASS) / 'browsers')
    os.environ['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'] = '1'
    # Do not allow developer-machine Node overrides into a customer runtime.
    os.environ.pop('PLAYWRIGHT_NODEJS_PATH', None)
