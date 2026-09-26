"""Read-only stop guard; nonzero exit when tasks are still active."""
import json
import sqlite3
import sys
from pathlib import Path

database = Path(sys.argv[1])
if not database.exists():
    raise SystemExit(0)
with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
    active = [rid for rid, payload in db.execute("SELECT id,payload FROM records WHERE kind IN ('run','user_task')")
              if json.loads(payload).get('status') in ('queued', 'running')]
if active:
    print('仍有活动任务：' + '、'.join(active))
    raise SystemExit(2)
