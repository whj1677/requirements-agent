"""Read-only freshness for moving saved input into requirement review.

Preparation means the current input received a whole-table drafting attempt,
never human acceptance or a promise of complete model output. Its identity
tracks incoming material instead of the mutable review/output state.
"""
import json

from .core import digest
from .domain_context import active_source_ids


SCHEMA = 1
DRAFT_SCHEMA = 2
ACTIVE = ('queued', 'running')
# Source ownership is established by its birth transaction, not its filename.
# A user's upload named "人工修订.txt" remains incoming material.
REVIEW_SOURCE_REASONS = {
    '人工一次确认需求与验收', '一次核对并落实独立决定',
    '人工编辑或采纳条目', '人工将参考条款纳入本期并采纳',
    '人工新增候选需求', '人工新增关联验收候选',
    '人工一次批量确认需求与验收', '人工确认完善需求表格并进入交付',
}


def _history(db, pid):
    snapshots, review_sources, seen = {}, set(), set()
    for row in db.execute('SELECT revision,payload,reason FROM revisions WHERE project_id=? ORDER BY revision', (pid,)):
        project = json.loads(row['payload'])
        snapshots[row['revision']] = project
        ids = {source['id'] for source in project.get('sources', ())}
        if row['reason'] in REVIEW_SOURCE_REASONS:
            review_sources.update(ids - seen)
        seen.update(ids)
    return snapshots, review_sources


def _identity(project, review_sources):
    effective = active_source_ids(project)
    sources = []
    for source in project.get('sources', ()):
        if (source['id'] not in effective or source['id'] in review_sources
                or source.get('origin') in ('model', 'assistant', 'model_output', 'generated')):
            continue
        sources.append({key: source.get(key) for key in
                        ('id', 'sha256', 'version', 'purpose', 'uri', 'image_mime', 'container_source_id')}
                       | {'excerpts': [dict(id=ex.get('id'), locator=ex.get('locator'),
                                           text_hash=digest(ex.get('text', '')))
                                       for ex in source.get('excerpts', ())]})
    sources.sort(key=lambda source: source['id'])
    intake = project.get('intake') or {}
    draft = project.get('workspace_draft') or {}
    domain = project.get('domain_context') or {}
    user_messages = [dict(text=message.get('text', ''), source_id=message.get('source_id'),
                          attachment_source_ids=sorted(message.get('attachment_source_ids') or []))
                     for message in project.get('messages', ())
                     if message.get('role') == 'user' and message.get('stage') == 'interview']
    value = dict(schema=SCHEMA, sources=sources, user_messages=user_messages,
                 intake={key: intake.get(key, '') for key in ('text', 'platform', 'preserved')},
                 draft={key: draft.get(key, '') for key in ('text', 'source_id')},
                 domain=dict(background=domain.get('background', ''),
                             terms=[{key: term.get(key) for key in ('term', 'meaning', 'status')}
                                    for term in domain.get('terms') or []]),
                 reference_mode=project.get('reference_mode', 'user'))
    return dict(schema=SCHEMA, input_hash=digest(value), source_ids=[s['id'] for s in sources],
                has_input=bool(sources or user_messages or any(value['intake'].values()) or draft.get('text', '').strip()))


def input_identity(store, project, db=None):
    """Can run inside Actions.start's transaction, including its saved message."""
    if db is None:
        with store.connect() as connection:
            return input_identity(store, project, connection)
    _, review_sources = _history(db, project['id'])
    return _identity(project, review_sources)


def standard_organize(task):
    return (task.get('action') == 'organize' and not task.get('benchmark')
            and not task.get('generation_target') and not task.get('target')
            and not task.get('requirement_recommendation_scope')
            and not (task.get('retry_input') or {}).get('target'))


def _applied_ingest(task, runs):
    return next((runs[rid] for rid in reversed(task.get('run_ids', ()))
                 if rid in runs and runs[rid].get('stage') == 'ingest'
                 and runs[rid].get('result_applied') is True
                 and runs[rid].get('status') in ('succeeded', 'partial', 'awaiting_user')), None)


def draft_coverage(project):
    """Availability of reviewable drafts, not business correctness or approval.

    Legacy ingest success alone does not show that each requirement received an
    acceptance draft. Optional behavior dimensions are deliberately not a gate.
    """
    def active(item):
        return (item.get('applies_to') == 'to_be' and not item.get('superseded_by')
                and item.get('selection_status') in ('candidate', 'selected'))
    roots = [item for item in project.get('items', ()) if item.get('kind') == 'requirement'
             and not item.get('target_item_id') and active(item)]
    scope = (project.get('product_context') or {}).get('scope_ids')
    # Before a PM chooses scope, all available original rows are reviewable.
    if scope:
        roots = [item for item in roots if item['id'] in scope]
    acceptance = [item for item in project.get('items', ()) if item.get('kind') == 'acceptance'
                  and not item.get('target_item_id') and active(item)]
    missing = []
    for root in roots:
        linked = [item for item in acceptance if root['id'] in item.get('related_refs', ())
                  or item['id'] in root.get('related_refs', ())]
        targets = {item['id'] for item in linked}
        candidates = [item for item in project.get('items', ()) if item.get('kind') == 'acceptance'
                      and item.get('action') == 'revise' and item.get('target_item_id') in targets
                      and active(item)]
        if not any(isinstance(item.get('statement'), str) and item['statement'].strip()
                   for item in [*linked, *candidates]):
            missing.append(root['id'])
    context = project.get('product_context') or {}
    proposed = project.get('product_context_proposal') or {}
    fields = [field for field in ('intent', 'users', 'change_scope')
              if not any(isinstance(value, str) and value.strip()
                         for value in (context.get(field), proposed.get(field)))]
    usable = any(isinstance(item.get('statement'), str) and item['statement'].strip() for item in roots)
    usable = usable or len(fields) < 3
    return dict(requirement_count=len(roots), missing_acceptance_ids=missing,
                missing_context_fields=fields, has_usable_draft=bool(usable),
                complete=bool(roots) and not missing and not fields)


def _draft_attempted(task):
    result = task.get('preparation_result') or {}
    return (task.get('preparation_draft_schema') == DRAFT_SCHEMA
            or result.get('draft_schema') == DRAFT_SCHEMA)


def _ready(result, limited, coverage):
    if not coverage['has_usable_draft']:
        result.update(status='failed', needs_organize=True, auto_allowed=False,
                      reason='本次整理尚未产出可核对的需求或摘要；已保存资料保留，请查看任务结果后手动重试，也可直接填写。')
        return
    limited = limited or not coverage['complete']
    result.update(status='ready_with_limits' if limited else 'ready', needs_organize=False,
                  auto_allowed=False,
                  reason=('本次整表起草已完成尝试，已有建议可核对；仍缺的内容会保留待补充，不会自动重复生成。'
                          if not coverage['complete'] else
                          '当前信息已整理，仍有资料限制，请核对已有建议。' if limited else
                          '当前信息已整理，可继续核对已有需求。'))


def _upgrade(result):
    result.update(status='needs_organize', needs_organize=True, auto_allowed=True,
                  reason='已有整理结果尚缺整表摘要或部分需求的验收建议，将统一补充一次；已有内容保留，结果仍须人工确认。')


def view(store, pid):
    with store.connect() as db:
        db.execute('BEGIN')
        project = store.get(pid, db)
        snapshots, review_sources = _history(db, pid)
        tasks = store.records(pid, 'user_task', db)
        run_list = store.records(pid, 'run', db)
    identity = _identity(project, review_sources)
    coverage = draft_coverage(project)
    draft = project.get('workspace_draft') or {}
    entry_mode = draft.get('entry_mode') or ('direct' if (project.get('intake') or {}).get('mode') == 'direct' else 'chat')
    result = dict(project_id=pid, revision=project['revision'], input_hash=identity['input_hash'],
                  source_ids=identity['source_ids'], entry_mode=entry_mode,
                  draft_schema=DRAFT_SCHEMA, draft_coverage=coverage,
                  status='needs_organize', needs_organize=True, auto_allowed=True,
                  retry_requires_explicit=True, task_id=None, prior_task_id=None,
                  reason='已保存的信息尚未整理，整理后可逐条核对需求。')
    active = next((task for task in reversed(tasks) if task.get('status') in ACTIVE), None)
    active_run = next((run for run in reversed(run_list) if run.get('status') in ACTIVE), None)
    if active or active_run:
        result.update(status='running', needs_organize=False, auto_allowed=False,
                      task_id=active['id'] if active else active_run.get('user_task_id'),
                      run_id=active_run['id'] if active_run else None,
                      reason='当前项目已有任务正在处理，请等待本次结果。')
        return result
    if not identity['has_input']:
        result.update(status='empty', needs_organize=False, auto_allowed=False,
                      reason='请先保存需求描述或添加资料，也可直接填写需求。')
        return result
    runs = {run['id']: run for run in run_list}

    def matches(record, revision_key, hash_key=None):
        if hash_key and record.get(hash_key):
            return record.get('preparation_schema') == SCHEMA and record[hash_key] == identity['input_hash']
        snapshot = snapshots.get(record.get(revision_key))
        return snapshot is not None and _identity(snapshot, review_sources)['input_hash'] == identity['input_hash']

    # A repeated visit after failure must not create another paid request. Even
    # old tasks can be compared using their exact saved input revision.
    for task in reversed(tasks):
        if not standard_organize(task) or not matches(task, 'input_revision', 'preparation_input_hash'):
            continue
        applied = _applied_ingest(task, runs)
        result.update(task_id=task['id'], prior_task_id=task['id'], auto_allowed=False)
        if task.get('status') in ('succeeded', 'partial') and applied:
            if coverage['complete'] or _draft_attempted(task):
                _ready(result, task['status'] == 'partial' or applied['status'] == 'partial', coverage)
            else:
                _upgrade(result)
        else:
            result.update(status='failed', needs_organize=True,
                          reason=task.get('message') or '本次整理未完成；已有内容保留，需要手动重试。')
        return result
    # Legacy direct ingest runs predate business actions. Reuse only an exact
    # historical input match, never the mere presence of candidate requirements.
    for run in reversed(run_list):
        if (run.get('stage') != 'ingest' or run.get('user_task_id') or run.get('benchmark')
                or not matches(run, 'revision')):
            continue
        result.update(auto_allowed=False, run_id=run['id'])
        if run.get('result_applied') is True and run.get('status') in ('succeeded', 'partial', 'awaiting_user'):
            if coverage['complete'] or _draft_attempted(run):
                _ready(result, run['status'] == 'partial', coverage)
            else:
                _upgrade(result)
        else:
            result.update(status='failed', needs_organize=True,
                          reason=run.get('message') or '本次整理未完成；需要手动重试。')
        return result
    prior = next((task for task in reversed(tasks) if standard_organize(task)), None)
    if prior:
        result['prior_task_id'] = prior['id']
        result['reason'] = '已保存的新信息尚未整理，将保留已确认内容并生成待核对建议。'
    return result


def register(app, store):
    @app.get('/api/projects/{pid}/refinement-preparation')
    async def preparation_status(pid: str):
        return view(store, pid)
