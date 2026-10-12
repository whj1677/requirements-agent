"""Read-only evidence and readiness for manual answer application review."""
import copy

from .core import Problem
from .requirements import behavior_review_current, require_behavior_review
from .understanding import answer_targets, application_hash


def _sources(project, item):
    available = {
        (source['id'], excerpt['id']): dict(
            source_id=source['id'], excerpt_id=excerpt['id'],
            title=source.get('title'), text=excerpt.get('text'),
            locator=excerpt.get('locator'))
        for source in project.get('sources', [])
        if not source.get('excluded') and not source.get('superseded_by')
        for excerpt in source.get('excerpts', [])
    }
    return [copy.deepcopy(available[key]) for ref in item.get('source_refs', [])
            if (key := (ref.get('source_id'), ref.get('excerpt_id'))) in available]


def _readiness(project, item):
    selected = item.get('selection_status') == 'selected'
    issues = []
    if not selected:
        issues.append('请先采纳此条款')
    guard_ok = True
    try:
        require_behavior_review(item, project)
    except Problem as exc:
        guard_ok = False
        issues.append(exc.message)
    if not selected and item.get('applies_to') != 'to_be':
        issues.append('请到需求详情核对适用范围；仅本期目标条款可在此单独采纳')
    if not selected and item.get('target_item_id'):
        issues.append('修订条目需在需求详情单独核对原条目')
    return dict(
        can_adopt=not selected and item.get('applies_to') == 'to_be'
        and not item.get('target_item_id') and guard_ok,
        can_confirm=selected and guard_ok,
        behavior_review_required=bool(item.get('behavior_review_required')
                                      and not behavior_review_current(item)),
        issues=issues,
    )


def review_targets(project, question):
    return [dict(item=copy.deepcopy(item), clause_hash=application_hash(item),
                 sources=_sources(project, item), readiness=_readiness(project, item))
            for item in answer_targets(project, question)]
