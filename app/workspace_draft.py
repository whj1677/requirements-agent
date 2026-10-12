"""Editable working copy, deliberately separate from formal delivery gates."""
from .core import now, require
from .sources import save_source


def view(project):
    saved = project.get('workspace_draft') or {}
    fallback = (project.get('intake') or {}).get('text') or (project.get('product_context') or {}).get('intent') or ''
    return dict(text=saved.get('text', fallback), entry_mode=saved.get('entry_mode', 'direct'),
                revision=project['revision'], saved_at=saved.get('saved_at'))


def save(project, store, text, entry_mode):
    require(isinstance(text, str) and len(text) <= 300000, 'INPUT_REQUIRED', '底稿文本过长或格式不正确')
    require(entry_mode in ('chat', 'direct'), 'INPUT_REQUIRED', '请选择聊天或直接填写')
    previous = project.get('workspace_draft') or {}
    source_id = previous.get('source_id')
    if text != previous.get('text') and text.strip():
        source = save_source(store, '共享工作底稿.txt', text.encode('utf-8'), 'goal')
        source.update(origin='workspace_draft', workspace_revision=project['revision'] + 1)
        # Keep historical input evidence without resending stale versions as current.
        for old in project['sources']:
            if old['id'] == source_id and old.get('origin') == 'workspace_draft':
                old.update(superseded_by=source['id'], excluded=True)
        project['sources'].append(source)
        source_id = source['id']
    elif not text.strip() and source_id:
        for old in project['sources']:
            if old['id'] == source_id and old.get('origin') == 'workspace_draft':
                old['excluded'] = True
        source_id = None
    project['workspace_draft'] = dict(text=text, entry_mode=entry_mode, source_id=source_id, saved_at=now())
    project['workflow_mode'] = 'shared-draft'


def discussion(project):
    """A factual working export, not a generated or approved MRD/PRD."""
    lines = [f'# {project["name"]} · 讨论稿', '', '**待确认 / 非正式交付**',
             f'工作版本：{project["revision"]}。保留候选与未决项，不代表需求已获确认。', '', '## 原始工作底稿', '', view(project)['text'] or '尚未填写。', '', '## 已有条目', '']
    statuses = {'candidate': '待核对', 'selected': '已采纳', 'deferred': '暂缓', 'rejected': '未采纳'}
    for item in project['items']:
        if item.get('selection_status') == 'rejected':
            continue
        lines += [f'### {item.get("title") or item["id"]} · {statuses.get(item.get("selection_status"), "待核对")}',
                  '', item.get('statement', ''), '']
    lines += ['## 问题与落实状态', '']
    from .product_flow import question_open
    for question in project['questions']:
        if question.get('superseded_by'):
            continue
        state = '仍待决定' if question_open(question) else '待核对条款' if question.get('understanding_status') != 'applied' else '已核对落实'
        lines += [f'- {question["question"]}（{state}）']
    return '\n'.join(lines)


def register(app, store):
    @app.get('/api/projects/{pid}/workspace-draft')
    async def get_draft(pid: str):
        return view(store.get(pid))

    @app.post('/api/projects/{pid}/workspace-draft')
    async def save_draft(pid: str, body: dict):
        require(type(body.get('expected_revision')) is int, 'INPUT_REQUIRED', '缺少底稿版本')
        with store.edit(pid, body['expected_revision'], '保存共享工作底稿') as (project, _):
            save(project, store, body.get('text'), body.get('entry_mode'))
        return dict(**view(project), project=project)

    @app.get('/api/projects/{pid}/discussion-draft')
    async def get_discussion_draft(pid: str):
        project = store.get(pid)
        return dict(project_id=pid, revision=project['revision'], formal=False,
                    filename=project['name'] + '-讨论稿.md', text=discussion(project))
