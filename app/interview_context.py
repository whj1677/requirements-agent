"""Source-bound interview summaries, isolated from the valid chat response.

These are unreviewed suggestions, not facts or completed product decisions.
The original model response remains intact in the message and call evidence.
"""
import copy
import re

import jsonschema

from .scope_context import FIELDS
from .source_fidelity import source_rows, text_issues

REF_SCHEMA = dict(type='object', additionalProperties=False,
    required=['source_id', 'excerpt_id'], properties=dict(
        source_id=dict(type='string', minLength=1), excerpt_id=dict(type='string', minLength=1)))
FIELD_SCHEMA = dict(type='object', additionalProperties=False,
    required=['text', 'source_refs'], properties=dict(
        text=dict(type='string', maxLength=6000),
        source_refs=dict(type='array', maxItems=30, uniqueItems=True, items=REF_SCHEMA)))
SCHEMA = dict(type='object', additionalProperties=False,
    properties={field: copy.deepcopy(FIELD_SCHEMA) for field in FIELDS})

UNKNOWN = re.compile(r'待(?:确认|确定|决定|核对|定)|未(?:明确|确定|决定)|尚未(?:确认|明确|确定|决定)|未知|不确定|unknown', re.I)
CONFLICT = re.compile(r'冲突|矛盾|不一致')
FIELD_TERMS = dict(product='产品|系统', module='模块|页面', intent='目标|目的',
    current_state='现状|现有', users='用户|使用者|人员|角色', value='价值|收益',
    change_scope='范围|改动', preserve_scope='保持|保留', out_of_scope='排除|不做|暂不涉及', priority='优先级')
MODEL_ORIGINS = {'assistant', 'model', 'model_output', 'generated'}
EXECUTABLE = re.compile(r'<!doctype\b|</?[a-z][a-z0-9:-]*(?:\s[^<>]*)?\s*/?>|```\s*(?:html|javascript|typescript|python|jsx|tsx)\b', re.I)


def _unresolved(text):
    if UNKNOWN.search(text):
        return True
    # "避免派单冲突" is an explicit goal, not an unresolved product decision.
    for part in re.split(r'[。；;\n]', text):
        for match in CONFLICT.finditer(part):
            if not re.search(r'避免|防止|消除|解决|没有|不存在|无', part[:match.start()]):
                return True
    return False


def uncertainty(field, text, evidence):
    """Conservative field-local hints; not a general entailment engine."""
    if _unresolved(text):
        return True
    terms = FIELD_TERMS[field]
    markers = UNKNOWN.pattern + '|' + CONFLICT.pattern
    pattern = re.compile(r'(?:' + terms + r')[^，,。；;\n]{0,14}(?:' + markers + r')|(?:' + markers + r')[^，,。；;\n]{0,14}(?:' + terms + r')', re.I)
    return any(pattern.search(part) and _unresolved(part)
               for row in evidence for part in re.split(r'[，,。；;\n]', row['text']))


def assess(draft, project, excerpts):
    """Reject only malformed optional fields after strict main-chat validation."""
    review = dict(version='interview-context-1', accepted_fields=[], empty_fields=[],
                  rejected_fields=[], fields={}, limitations=[])

    def reject(field, code, message):
        review['rejected_fields'].append(dict(field=field, code=code, message=message))

    if not isinstance(draft, dict):
        reject('context_draft', 'CONTEXT_DRAFT_INVALID', '摘要建议不是字段对象，本次未采用摘要；聊天回复保留。')
    else:
        actual = {(e['source_id'], e['id']): e for e in excerpts}
        sources = {s['id']: s for s in project.get('sources', [])}
        for field, row in draft.items():
            if field not in FIELDS:
                reject(field, 'CONTEXT_FIELD_INVALID', '未知摘要字段，本次未采用。')
                continue
            if list(jsonschema.Draft202012Validator(FIELD_SCHEMA).iter_errors(row)):
                reject(field, 'CONTEXT_FIELD_INVALID', '摘要字段结构不正确，本次未采用。')
                continue
            text, refs = row['text'], row['source_refs']
            if not text.strip():
                review['empty_fields'].append(field)
                continue  # Unknown does not clear an earlier suggestion or human text.
            if not refs:
                reject(field, 'CONTEXT_SOURCE_REQUIRED', '摘要没有实际来源片段，本次未采用。')
                continue
            bad_source = False
            for ref in refs:
                source = sources.get(ref['source_id'])
                if ((ref['source_id'], ref['excerpt_id']) not in actual or not source
                        or source.get('excluded') or source.get('superseded_by')
                        or source.get('origin') in MODEL_ORIGINS
                        or source.get('image_mime') and not source.get('vision_run_id')):
                    bad_source = True
                    break
            if bad_source:
                reject(field, 'CONTEXT_REFERENCE_INVALID', '摘要引用不是本次实际读取的有效资料；助手历史回复不作为来源。')
                continue
            if EXECUTABLE.search(text):
                reject(field, 'CONTEXT_CONTENT_INVALID', '摘要不能包含可执行代码或页面源码，本次未采用。')
                continue
            evidence = source_rows(project, excerpts, refs)
            if not evidence or not any(r['text'].strip() for r in evidence):
                reject(field, 'CONTEXT_SOURCE_REQUIRED', '摘要引用没有可读片段，本次未采用。')
                continue
            issues = text_issues(text, evidence, 'context_draft.' + field, normative=False)
            if issues:
                reject(field, 'CONTEXT_SOURCE_FIDELITY', '摘要含来源未支持的业务断言，本次未采用。')
                review['rejected_fields'][-1]['issues'] = issues
                continue
            review['fields'][field] = dict(text=text, source_refs=copy.deepcopy(refs),
                uncertain=uncertainty(field, text, evidence))
            review['accepted_fields'].append(field)
    if review['rejected_fields']:
        review['limitations'].append('聊天回复已保留；' + str(len(review['rejected_fields'])) + ' 项摘要建议未通过字段或来源核对，未采用。')
    return review


def apply(project, review, run_id, input_revision):
    """Merge candidates only. Canonical values, states and stage checks stay put."""
    for field, row in review['fields'].items():
        saved = str((project.get('product_context') or {}).get(field, ''))
        state = (project.get('context_field_states') or {}).get(field, {}).get('status')
        if project.get('product_context_proposal') is None:
            project['product_context_proposal'] = {}
        if project.get('product_context_proposal_evidence') is None:
            project['product_context_proposal_evidence'] = {}
        project['product_context_proposal'][field] = row['text']
        project['product_context_proposal_evidence'][field] = dict(
            copy.deepcopy(row), run_id=run_id, input_revision=input_revision,
            evidence_status='unreviewed', conflicts_with_saved=(
                bool(saved.strip()) and saved != row['text'] or state in ('none', 'not_applicable')))
    audit = {key: copy.deepcopy(value) for key, value in review.items() if key != 'fields'}
    audit.update(run_id=run_id, input_revision=input_revision)
    project['interview_context_review'] = audit
    if project.get('messages'):
        project['messages'][-1]['interview_context_review'] = copy.deepcopy(audit)
