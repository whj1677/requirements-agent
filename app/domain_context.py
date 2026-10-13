"""Optional project-scoped vocabulary and source selection.

This is input context, never a source of adopted business requirements.
Absent selection preserves the legacy behavior: every non-excluded source is
eligible, subject to the existing grants and excerpt budget.
"""

from __future__ import annotations

import hashlib

from .core import ident, require


DEFAULT_BACKGROUND = '大型储能集成商云平台产品经理；按本次需求和已确认资料理解术语、提出关键追问。行业背景不自动决定功能范围或业务规则。'


def attachment_source_ids(project, source_ids):
    """An attached document includes its extracted images, unless excluded."""
    available = {s['id']: s for s in project.get('sources', ()) if not s.get('excluded')}
    result = set(source_ids) & available.keys()
    for source in available.values():
        parent = available.get(source.get('container_source_id'))
        if parent and parent['id'] in result and source['id'] in parent.get('embedded_image_ids', ()):
            result.add(source['id'])
    return result


def _saved(project):
    value = project.get('domain_context')
    return value if isinstance(value, dict) else {}


def bound_input_source_ids(project):
    """Current user input is eligible even when reference materials are narrowed.

    Only explicit current bindings count. Historical answer refs and unbound
    sources never qualify merely because of a title or purpose label.
    """
    sources = {s['id']: s for s in project.get('sources', ()) if not s.get('excluded')}
    bound = set()

    def add_refs(refs):
        for ref in refs or ():
            if not isinstance(ref, dict):
                continue
            sid, eid = ref.get('source_id'), ref.get('excerpt_id')
            source = sources.get(sid)
            if source and any(ex.get('id') == eid for ex in source.get('excerpts', ())):
                bound.add(sid)

    for question in project.get('questions', ()):
        if (question.get('status') == 'answered' and question.get('answer') is not None
                and not question.get('superseded_by') and not question.get('out_of_scope_reason')):
            add_refs(question.get('answer_source_refs'))

    current_decision_answer_ids = {q.get('decision_answer_id') for q in project.get('questions', ())
                                   if q.get('decision_answer_id') and not q.get('superseded_by')
                                   and not q.get('out_of_scope_reason')}
    for answer in project.get('decision_answers', ()):
        if answer.get('id') in current_decision_answer_ids:
            add_refs(answer.get('source_refs'))

    draft = project.get('workspace_draft') or {}
    if isinstance(draft, dict) and draft.get('source_id') in sources:
        bound.add(draft['source_id'])
    task_source_id = project.get('current_task_source_id')
    if task_source_id in sources:
        bound.add(task_source_id)
    # A pasted file is only local material until the user submits it with a
    # conversation turn. Cancelled previews must not broaden later requests.
    for message in project.get('messages', ()):
        if message.get('role')=='user' and message.get('stage')=='interview':
            bound.update(attachment_source_ids(project, message.get('attachment_source_ids', ())))
    return bound


def active_source_ids(project):
    """Reference choice plus bound user input, before grant/budget checks."""
    saved = _saved(project)
    chosen = saved.get('selected_source_ids')
    allowed = None if chosen is None else set(chosen)
    selected = {source['id'] for source in project.get('sources', ())
                if not source.get('excluded') and
                ((allowed is None and source.get('origin')!='interview_attachment') or
                 (allowed is not None and source['id'] in allowed))}
    return selected | bound_input_source_ids(project)


def filter_excerpts(project, source, excerpts):
    """Apply project selection after existing source-specific excerpt filters."""
    return list(excerpts) if source['id'] in active_source_ids(project) else []


def view(project):
    saved = _saved(project)
    chosen = saved.get('selected_source_ids')
    effective = active_source_ids(project)
    return {
        'project_id': project['id'],
        'revision': project['revision'],
        'background': saved.get('background', ''),
        'suggested_background': DEFAULT_BACKGROUND,
        'terms': list(saved.get('terms') or []),
        'selected_source_ids': chosen,
        'effective_source_ids': sorted(effective),
        'bound_input_source_ids': sorted(bound_input_source_ids(project)),
        'unavailable_selected_source_ids': sorted(set(chosen or ()) - effective),
        'evidence_policy': '背景与术语仅辅助理解和追问；行业参考不是本项目已确认规则。资料引用须保留实际来源和人工审核。',
    }


def model_context(project):
    """Small, explicitly non-normative context for a model request."""
    saved = _saved(project)
    background = saved.get('background', '').strip()
    terms = saved.get('terms') or []
    if not background and not terms:
        return None
    return {
        'project_id': project['id'],
        'background': background,
        'terms': [dict(term=t['term'], meaning=t['meaning'], status=t['status']) for t in terms],
        'policy': 'background 和 terms 只辅助理解当前需求；confirmed 仅表示本项目确认的术语释义，reference 仅供参考。均不构成本次功能范围或规范条款；不得自动追加储能模块、业务规则或验收指标。具体断言需本次需求或有效来源支持并经用户审核。',
    }


def source_snapshot(project, sent_excerpts):
    """Bind *actually sent* excerpts, not every selected or stored source."""
    sources = {s['id']: s for s in project.get('sources', ())}
    refs = []
    for excerpt in sent_excerpts:
        source = sources.get(excerpt.get('source_id'))
        require(source is not None and source['id'] in active_source_ids(project)
                and any(e.get('id') == excerpt.get('id') for e in source.get('excerpts', ())),
                'REFERENCE_INVALID', '发送片段不属于当前项目来源')
        refs.append({
            'project_id': project['id'],
            'source_id': source['id'],
            'excerpt_id': excerpt['id'],
            'source_sha256': source.get('sha256'),
            'excerpt_sha256': hashlib.sha256(str(excerpt.get('text', '')).encode('utf-8')).hexdigest(),
            'source_version': source.get('version'),
            'locator': excerpt.get('locator'),
        })
    return {'project_id': project['id'], 'references': refs}


def update(project, *, background=None, terms=None, selected_source_ids=...):
    """Update supplied fields only; caller owns the Store.edit transaction."""
    saved = dict(_saved(project))
    if background is not None:
        require(isinstance(background, str) and len(background) <= 2000,
                'DOMAIN_CONTEXT_INVALID', '项目背景最多 2000 字')
        saved['background'] = background.strip()
    if terms is not None:
        require(isinstance(terms, list) and len(terms) <= 100,
                'DOMAIN_CONTEXT_INVALID', '术语数量不能超过 100')
        seen = set()
        clean = []
        for entry in terms:
            require(isinstance(entry, dict), 'DOMAIN_CONTEXT_INVALID', '术语格式无效')
            term = str(entry.get('term', '')).strip()
            meaning = str(entry.get('meaning', '')).strip()
            status = entry.get('status')
            require(0 < len(term) <= 100 and 0 < len(meaning) <= 1000 and status in ('confirmed', 'reference'),
                    'DOMAIN_CONTEXT_INVALID', '术语须有名称、解释和 confirmed/reference 状态')
            key = term.casefold()
            require(key not in seen, 'DOMAIN_CONTEXT_INVALID', '同一项目中术语名称重复')
            seen.add(key)
            clean.append({'id': entry.get('id') or ident('TERM'), 'term': term,
                          'meaning': meaning, 'status': status})
        saved['terms'] = clean
    if selected_source_ids is not ...:
        if selected_source_ids is None:
            saved['selected_source_ids'] = None
        else:
            require(isinstance(selected_source_ids, list) and len(selected_source_ids) <= 2000
                    and all(isinstance(x, str) for x in selected_source_ids),
                    'DOMAIN_CONTEXT_INVALID', '本次资料选择格式无效')
            all_ids = {s['id'] for s in project.get('sources', ())}
            require(set(selected_source_ids) <= all_ids, 'REFERENCE_INVALID', '所选来源不属于当前项目')
            saved['selected_source_ids'] = list(dict.fromkeys(selected_source_ids))
    project['domain_context'] = saved
    return view(project)
