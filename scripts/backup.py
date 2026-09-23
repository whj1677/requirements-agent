import sqlite3
import sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.core import DATA
import shutil
target=DATA/'backups'/datetime.now().strftime('%Y%m%d-%H%M%S')
target.mkdir(parents=True,exist_ok=False)
with sqlite3.connect(DATA/'requirements.sqlite3') as source, sqlite3.connect(target/'requirements.sqlite3') as destination:
    source.backup(destination)
for name in ('sources','exports'):
    if (DATA/name).exists():shutil.copytree(DATA/name,target/name)
print(target)
