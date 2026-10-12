"""Version-bound review and atomic adoption of independent requirements."""
import copy

from .core import Problem, digest, now, require
from .requirements import (behavior_fingerprint, behavior_review_current,
                           item_content, require_behavior_review)


def _ids(item_ids):
    require(item_ids and all(isinstance(i, str) and i.strip() and i == i.strip()
                             for i in item_ids) and len(set(item_ids)) == len(item_ids),
            'REFERENCE_INVALID', '请选择非空且不重复的需求编号', 409)


def _sources(project, item):
    sources = {s['id']: s for s in project['sources']
               if not s.get('excluded') and not s.get('superseded_by')}
    result = []
    for ref in item.get('source_refs') or []:
        source = sources.get(ref.get('source_id'))
        excerpt = next((e for e in source.get('excerpts', [])
                        if e['id'] == ref.get('excerpt_id')), None) if source else None
        require(excerpt is not None, 'REFERENCE_INVALID', '来源摘录不存在或已停用', 409)
        result.append(dict(source_id=source['id'], excerpt_id=excerpt['id'],
                           title=source.get('title', ''), text=excerpt.get('text', ''),
                           locator=excerpt.get('locator'), source_hash=excerpt.get('source_hash')))
    require(result and item.get('epistemic_status'), 'REFERENCE_INVALID',
            '条目缺少来源或假设状态，不可批量采纳', 409)
    return result


def _check(project, item):
    require(item is not None, 'NOT_FOUND', '需求不存在', 404)
    require(item.get('kind') == 'requirement' and item.get('applies_to') == 'to_be'
            and not item.get('target_item_id'), 'REFERENCE_INVALID',
            '只能批量采纳独立的目标需求；修订条目需单独核对', 409)
    require(bool((item.get('statement') or '').strip()), 'INPUT_REQUIRED',
            '需求陈述不能为空', 409)
    sources = _sources(project, item)
    prospective = copy.deepcopy(item)
    if prospective.get('behavior_review_required') and not behavior_review_current(prospective):
        prospective['behavior_review'] = dict(content_hash=behavior_fingerprint(prospective))
    require_behavior_review(prospective, project)
    return sources


def review(project, item_ids, expected_revision):
    require(project['revision'] == expected_revision, 'STALE_REVISION',
            '页面版本已过期，请查看最新内容及差异', 409)
    _ids(item_ids)
    by_id = {i['id']: i for i in project['items']}
    items, blockers = [], []
    for iid in item_ids:
        item = by_id.get(iid)
        sources = []
        try:
            if item is not None:
                sources = _sources(project, item)
            sources = _check(project, item)
        except Problem as error:
            blockers.append(dict(id=iid, title=(item or {}).get('title', ''),
                                 message=error.message))
        if item is not None:
            items.append(dict(item=copy.deepcopy(item), content_hash=digest(item_content(item)),
                              sources=sources, review_required=bool(
                                  item.get('behavior_review_required') and
                                  not behavior_review_current(item))))
    return dict(expected_revision=expected_revision, items=items, blockers=blockers)


def adopt(project, item_ids, content_hashes, review_confirmed):
    _ids(item_ids)
    require(review_confirmed is True, 'REVIEW_REQUIRED', '请先确认逐条核对内容与来源', 409)
    require(set(content_hashes) == set(item_ids), 'SNAPSHOT_INVALID',
            '提交的条目快照与核对列表不一致', 409)
    by_id = {i['id']: i for i in project['items']}
    # Validate every member before changing even an in-memory project item.
    for iid in item_ids:
        item = by_id.get(iid)
        _check(project, item)
        require(content_hashes[iid] == digest(item_content(item)), 'STALE_CONTENT',
                '条目内容已变化，请重新核对：' + iid, 409)
    for iid in item_ids:
        item = by_id[iid]
        if item.get('behavior_review_required') and not behavior_review_current(item):
            item['behavior_review'] = dict(
                content_hash=behavior_fingerprint(item), actor='authenticated_local_user',
                reviewed_at=now(), meaning='人工核对模型行为展开，不是程序证明原文蕴含或正式业务批准')
        item['selection_status'] = 'selected'
