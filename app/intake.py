"""Current intake has one active source; previous revisions remain traceable."""
import json

from .core import now, require
from .sources import save_source

SAVE_REASON = '保存本次诉求（未授权模型分析）'


def intake_sources(p, db):
    known = {s['id'] for s in p['sources'] if s.get('origin') == 'intake'}
    # Legacy intake.source_ids included unrelated attachments. Only a committed
    # intake revision's newly introduced source proves ownership; names do not.
    previous = set()
    for row in db.execute('SELECT payload,reason FROM revisions WHERE project_id=? ORDER BY revision', (p['id'],)):
        snapshot = json.loads(row['payload'])
        ids = {s['id'] for s in snapshot['sources']}
        if row['reason'] == SAVE_REASON:
            known.update(ids - previous)
        previous = ids
    return [s for s in p['sources'] if s['id'] in known]


def update_intake(store, p, db, values):
    text = '\n\n'.join(v for v in (
        values['text'].strip(),
        '平台/页面：' + values['platform'].strip() if values['platform'].strip() else '',
        '保持/不涉及：' + values['preserved'].strip() if values['preserved'].strip() else '',
    ) if v)
    owned = intake_sources(p, db)
    owned_ids = {s['id'] for s in owned}
    require(text or any(not s['excluded'] and s.get('excerpts') and s['id'] not in owned_ids for s in p['sources']),
            'INPUT_REQUIRED', '请先描述本次需求或添加相关材料')
    import hashlib
    sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
    current = next((s for s in reversed(owned) if not s['excluded'] and s['sha256'] == sha), None) if text else None
    version = max([s.get('intake_version', 0) for s in owned] + [len(owned), 0])
    if text and current is None:
        current = save_source(store, '本次诉求.txt', text.encode('utf-8'), 'goal')
        version += 1
        current.update(origin='intake', intake_version=version)
        p['sources'].append(current)
    for index, source in enumerate(owned, 1):
        source.setdefault('origin', 'intake')
        source.setdefault('intake_version', index)
        if current and source['id'] == current['id']:
            continue
        source.update(excluded=True, superseded_by=current['id'] if current else 'intake-cleared')
    p['intake'] = dict(**values, source_ids=[s['id'] for s in p['sources'] if not s['excluded']],
                       current_source_id=current['id'] if current else None, saved_at=now())
