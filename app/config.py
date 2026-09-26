"""Read only this project's dotenv file; never search parent directories."""
import os
from pathlib import Path

from dotenv import dotenv_values

from .core import ROOT, USER_HOME, FROZEN


def usable_key(value):
    if not value or not value.strip():
        return False
    value = value.strip()
    if value.startswith(('<', '${')) or value.endswith('>'):
        return False
    return value.casefold() not in {
        'your-key', 'your-api-key', 'your_key', 'your_api_key',
        'replace-me', 'changeme', 'placeholder', 'sk-xxx', 'example',
    }


class ProjectEnvironment:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else (USER_HOME if FROZEN else ROOT) / '.env'
        self.values = dotenv_values(dotenv_path=self.path, interpolate=False)

    def credential(self, name):
        process = os.environ.get(name)
        if usable_key(process):
            return process.strip(), 'process_env'
        file_value = self.values.get(name)
        if usable_key(file_value):
            return file_value.strip(), '.env'
        return '', 'none'
