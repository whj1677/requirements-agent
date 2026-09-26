"""Consistent local backup of the database and its referenced file evidence."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core import DATA

INCLUDED = ('sources', 'exports', 'evidence/model-calls')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(folder):
    result = {}
    for name in INCLUDED:
        root = folder / name
        if root.exists():
            for path in root.rglob('*'):
                if path.is_file():
                    result[path.relative_to(folder).as_posix()] = dict(size=path.stat().st_size, sha256=sha256(path))
    return result


def database_copy(source, destination):
    with closing(sqlite3.connect(source)) as input_db, closing(sqlite3.connect(destination)) as output_db:
        input_db.backup(output_db)


def database_fingerprint(path):
    with closing(sqlite3.connect(path)) as db:
        return [list(db.execute(f'SELECT * FROM {name} ORDER BY rowid'))
                for name in ('projects', 'revisions', 'records', 'settings', 'migrations')]


def verify(folder):
    manifest = json.loads((folder / 'manifest.json').read_text('utf-8'))
    expected = manifest['files']
    if inventory(folder) != expected or sha256(folder / 'requirements.sqlite3') != manifest['database_sha256']:
        raise RuntimeError('备份校验失败：文件或数据库哈希不一致')
    with closing(sqlite3.connect(folder / 'requirements.sqlite3')) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('备份数据库完整性检查失败')
        for (payload,) in db.execute('SELECT payload FROM projects'):
            project = json.loads(payload)
            for source in project.get('sources', []):
                if source.get('sha256'):
                    path = folder / 'sources' / source['id']
                    if not path.is_file() or sha256(path) != source['sha256']:
                        raise RuntimeError('备份来源文件与项目记录不一致：' + source['id'])
        for (payload,) in db.execute("SELECT payload FROM records WHERE kind='run'"):
            run = json.loads(payload)
            for attempt in run.get('attempts', []):
                call_id = attempt.get('call_id') if isinstance(attempt, dict) else None
                if call_id and not (folder / 'evidence' / 'model-calls' / (call_id + '.json')).is_file():
                    raise RuntimeError('备份缺少运行记录的模型调用证据：' + call_id)
    return manifest


def create(data, target):
    data, target = data.resolve(), target.resolve()
    if target == data or target in data.parents:
        raise RuntimeError('备份目录不得为活动数据目录或其上级目录')
    if target.exists():
        raise FileExistsError('备份目标已存在，不会覆盖：' + str(target))
    if not (data / 'requirements.sqlite3').is_file():
        raise RuntimeError('没有可备份的项目数据库')
    before = inventory(data)
    db_before = database_fingerprint(data / 'requirements.sqlite3')
    for (rid, payload) in [(row[0], row[3]) for row in db_before[2] if row[2] in ('run', 'user_task')]:
        if json.loads(payload).get('status') in ('queued', 'running'):
            raise RuntimeError('存在活动任务，备份需等待其结束：' + rid)
    target.mkdir(parents=True, exist_ok=False)
    try:
        database_copy(data / 'requirements.sqlite3', target / 'requirements.sqlite3')
        if database_fingerprint(target / 'requirements.sqlite3') != db_before:
            raise RuntimeError('数据库备份与复制前的快照不一致；本次快照已废弃')
        for name in INCLUDED:
            if (data / name).exists():
                shutil.copytree(data / name, target / name)
        if before != inventory(data) or db_before != database_fingerprint(data / 'requirements.sqlite3'):
            raise RuntimeError('备份期间数据发生变化；本次快照已废弃，请空闲时重试')
        copied = inventory(target)
        if copied != before:
            raise RuntimeError('备份文件与原始快照不一致；本次快照已废弃')
        manifest = dict(version=1, created_at=datetime.now().astimezone().isoformat(), files=copied,
                        database_sha256=sha256(target / 'requirements.sqlite3'))
        (target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), 'utf-8')
        verify(target)
    except BaseException:
        shutil.rmtree(target)
        raise
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, default=DATA)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args()
    if args.verify:
        verify(args.verify.resolve())
        print('备份校验通过：' + str(args.verify.resolve()))
    else:
        data = args.data_dir.resolve()
        target = args.target.resolve() if args.target else data / 'backups' / datetime.now().strftime('%Y%m%d-%H%M%S')
        print(create(data, target))


if __name__ == '__main__':
    main()
