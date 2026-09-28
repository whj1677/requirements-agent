"""Focused contracts for user-facing document boundaries."""
import copy

import pytest

from app.core import dumps
from app.document_reader import reader_document
from app.store import Store
from tests.helpers import prepared


@pytest.mark.parametrize('kind', ['prd', 'mrd'])
@pytest.mark.parametrize('appendix_first', [False, True])
def test_promoted_goal_uses_only_matching_root_product_section_and_keeps_audit(
        tmp_path, kind, appendix_first):
    p = prepared(Store(tmp_path))
    artifact = p['documents'][kind]
    goal = dict(id='GOAL-DELIVERED', kind='goal', title='交接目标',
                statement='现场人员可以快速交接完整信息。', applies_to='to_be',
                epistemic_status='reported', selection_status='selected',
                source_refs=[dict(source_id='SRC-GOAL', excerpt_id='EX-GOAL')],
                related_refs=[], content_version=4)
    candidate = dict(goal, id='GOAL-CANDIDATE', title='候选目标',
                     statement='候选文字不得进入正文。', epistemic_status='proposed')
    historical = dict(goal, id='GOAL-HISTORICAL', title='历史目标',
                      statement='历史文字不得进入正文。', applies_to='past')
    artifact['item_snapshot'] = copy.deepcopy(p['items']) + [goal, candidate, historical]
    artifact['delivery_item_ids'] = [goal['id'], candidate['id'], historical['id']]
    product_section = dict(section_id='PRODUCT-GOALS', title='目标与价值', level=1,
                           parent_section_id=None, blocks=[
                               dict(kind='narrative', ref_ids=[], text='已有目标段落。')])
    child_section = dict(section_id='NESTED-SAME-TITLE', title='目标与价值', level=2,
                         parent_section_id='PRODUCT-GOALS',
                         blocks=[dict(kind='narrative', ref_ids=[], text='子章节原文保留。')])
    appendix = dict(section_id='AUDIT-APPENDIX', title='附录：条目身份与历史讨论', level=1,
                    parent_section_id=None, blocks=[
                        dict(kind='goal', ref_ids=[goal['id']], text='audit goal'),
                        dict(kind='goal', ref_ids=[candidate['id']], text='候选讨论原文。'),
                        dict(kind='goal', ref_ids=[historical['id']], text='历史讨论原文。'),
                    ])
    ordered = [appendix, product_section, child_section] if appendix_first else [product_section, child_section, appendix]
    artifact['content']['sections'].extend(ordered)
    canonical_before = copy.deepcopy(artifact)

    view = reader_document(artifact)
    text = dumps(view)
    roots = [s for s in view['sections']
             if s['title'] == '目标与价值' and s['level'] == 1 and s['parent_section_id'] is None]
    assert len(roots) == 1
    assert roots[0]['section_id'] == 'PRODUCT-GOALS'
    assert len([s for s in view['sections'] if s['section_id'] == 'PRODUCT-GOALS']) == 1
    assert len({s['section_id'] for s in view['sections']}) == len(view['sections'])
    assert any(b.get('text') == '已有目标段落。' for b in roots[0]['blocks'])
    assert any(b.get('text') == '交接目标\n现场人员可以快速交接完整信息。'
               for b in roots[0]['blocks'])
    nested = next(s for s in view['sections'] if s['section_id'] == 'NESTED-SAME-TITLE')
    assert nested['parent_section_id'] == 'PRODUCT-GOALS'
    assert nested['blocks'] == [dict(kind='narrative', ref_ids=[], text='子章节原文保留。')]
    assert '候选文字不得进入正文。' not in text
    assert '候选讨论原文。' not in text
    assert '历史文字不得进入正文。' not in text
    assert '历史讨论原文。' not in text
    # The reader is a projection: canonical identity, original statement, and
    # source references remain intact in the audit appendix and item snapshot.
    assert artifact == canonical_before
    appendix = next(s for s in artifact['content']['sections'] if s['section_id'] == 'AUDIT-APPENDIX')
    assert appendix['blocks'][0]['ref_ids'] == ['GOAL-DELIVERED']
    assert artifact['item_snapshot'][-3]['id'] == 'GOAL-DELIVERED'
    assert artifact['item_snapshot'][-3]['source_refs'] == [
        dict(source_id='SRC-GOAL', excerpt_id='EX-GOAL')]


def test_partial_material_notices_name_file_and_declared_purpose_without_guessing(tmp_path):
    p = prepared(Store(tmp_path))
    artifact = p['documents']['prd']
    sources = [
        dict(id='SRC-CURRENT', title='contacts-current.docx', purpose='current', parse_status='partial'),
        dict(id='SRC-REFERENCE', title='layout-reference.png', purpose='reference', parse_status='partial'),
        dict(id='SRC-FAILED', title='unreadable.csv', purpose='goal', parse_status='failed'),
        dict(id='SRC-OFFICE', title='protected.xlsx', purpose='current', parse_status='office_required'),
        dict(id='SRC-DENIED', title='restricted.docx', purpose='reference', parse_status='permission_denied'),
        dict(id='SRC-VISION', title='pending.png', purpose='reference', parse_status='awaiting_vision'),
        dict(id='SRC-TEMPLATE', title='sample-template.docx', purpose='template', parse_status='partial'),
    ]
    artifact['source_snapshot'] = copy.deepcopy(sources)
    limits = dict(section_id='TEST-LIMITS', title='文档边界与资料限制',
                  level=1, parent_section_id=None, blocks=[])
    artifact['content']['sections'].append(limits)
    for source in sources:
        limits['blocks'].append(dict(
            kind='narrative', ref_ids=[],
            text=f'材料「{source["title"]}」尚有读取限制：{source["id"]} PRIVATE_METHOD PRIVATE_EXCEPTION'))
    before = copy.deepcopy(artifact)

    view = reader_document(artifact)
    text = dumps(view)
    assert '《contacts-current.docx》（现状资料）：部分内容未能完整读取；未读取内容不作为本版产品结论的依据。' in text
    assert '《layout-reference.png》（参考资料）：部分内容未能完整读取；未读取内容不作为本版产品结论的依据。' in text
    assert '《unreadable.csv》（本期诉求资料）：当前无法读取；未读取内容不作为本版产品结论的依据。' in text
    assert '《protected.xlsx》（现状资料）：需要在本机 Office 中读取；未读取内容不作为本版产品结论的依据。' in text
    assert '《restricted.docx》（参考资料）：因权限限制未能读取；未读取内容不作为本版产品结论的依据。' in text
    assert '《pending.png》（参考资料）：图片内容尚未完成读取；未读取内容不作为本版产品结论的依据。' in text
    assert '《sample-template.docx》（模板资料）：部分内容未能完整读取；未读取内容不作为本版产品结论的依据。' in text
    for internal in ('SRC-CURRENT', 'SRC-REFERENCE', 'PRIVATE_METHOD', 'PRIVATE_EXCEPTION',
                     '尚待核对', '全部规则已验证'):
        assert internal not in text
    assert artifact == before
