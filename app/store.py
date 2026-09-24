import json
import sqlite3
from contextlib import contextmanager
from .core import DATA, Problem, dumps, ident, now, require


class Store:
    def __init__(self, folder=DATA):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / 'requirements.sqlite3'
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, revision INTEGER, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS revisions(project_id TEXT, revision INTEGER, payload TEXT, reason TEXT, created TEXT, PRIMARY KEY(project_id,revision));
              CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, payload TEXT NOT NULL, created TEXT);
              CREATE TABLE IF NOT EXISTS settings(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied TEXT);
              INSERT OR IGNORE INTO migrations VALUES(1,datetime('now'));
            ''')
            # Never replay a possibly charged request after process death.
            for row in db.execute("SELECT id,payload FROM records WHERE kind IN ('run','user_task')").fetchall():
                run = json.loads(row['payload'])
                if run['status'] in ('running', 'queued'):
                    run.update(status='paused_budget', error='RESTARTED', message='服务已重启。已发请求可能计费；请明确续跑。')
                    db.execute('UPDATE records SET payload=? WHERE id=?', (dumps(run), row['id']))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, name):
        p = dict(id=ident('P'), name=name, revision=0, schema_version='1.1', mode='explore',
                 items=[], questions=[], options=[], sources=[], messages=[], documents={}, ui=None,
                 review=None, active_baseline_id=None, grants={}, reference_mode='user', created=now())
        with self.connect() as db:
            db.execute('INSERT INTO projects VALUES(?,?,?)', (p['id'], 0, dumps(p)))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (p['id'], 0, dumps(p), '创建项目', now()))
        return p

    def get(self, pid, db=None):
        if db is None:
            with self.connect() as conn:
                return self.get(pid, conn)
        row = db.execute('SELECT payload FROM projects WHERE id=?', (pid,)).fetchone()
        require(row is not None, 'NOT_FOUND', '项目不存在', 404)
        p = json.loads(row[0])
        require(p.get('schema_version', '1.0') in ('1.0', '1.1'), 'VERSION_UNSUPPORTED', '不支持的历史版本')
        return p

    def list(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM projects ORDER BY rowid DESC')]

    @contextmanager
    def edit(self, pid, revision, reason, bump=True):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            p = self.get(pid, db)
            require(p['revision'] == revision, 'STALE_REVISION', '页面版本已过期，请查看最新内容及差异', 409)
            yield p, db
            if bump:
                p['revision'] += 1
            db.execute('UPDATE projects SET revision=?,payload=? WHERE id=?', (p['revision'], dumps(p), pid))
            if bump:
                db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (pid, p['revision'], dumps(p), reason, now()))
            self.record(pid, 'audit', dict(actor='local_user' if not reason.startswith('模型') else 'model', action=reason, revision=p['revision']), db=db)

    def record(self, pid, kind, value, rid=None, db=None):
        if db is None:
            with self.connect() as conn:
                return self.record(pid, kind, value, rid, conn)
        rid = rid or ident(kind.upper())
        db.execute('INSERT INTO records VALUES(?,?,?,?,?)', (rid, pid, kind, dumps(value), now()))
        return rid

    def records(self, pid, kind, db=None):
        if db is None:
            with self.connect() as conn:
                return self.records(pid, kind, conn)
        return [dict(json.loads(r['payload']), id=r['id']) for r in db.execute('SELECT id,payload FROM records WHERE project_id=? AND kind=? ORDER BY rowid', (pid, kind))]

    def get_record(self, pid, rid, kind=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM records WHERE id=? AND project_id=?', (rid, pid)).fetchone()
            require(row is not None and (kind is None or row['kind'] == kind), 'NOT_FOUND', '记录不存在', 404)
            return dict(json.loads(row['payload']), id=rid)

    def update_run(self, pid, rid, **changes):
        self.update_record(pid, rid, 'run', **changes)

    def update_record(self, pid, rid, kind, **changes):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT payload FROM records WHERE id=? AND project_id=? AND kind=?", (rid, pid, kind)).fetchone()
            require(row is not None, 'NOT_FOUND', '任务不存在', 404)
            run = json.loads(row[0])
            run.update(changes)
            db.execute('UPDATE records SET payload=? WHERE id=?', (dumps(run), rid))

    def setting(self, key, value=None):
        with self.connect() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, dumps(value)))
            row = db.execute('SELECT payload FROM settings WHERE id=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None
