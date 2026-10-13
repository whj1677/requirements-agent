"""MRD annex capacity checks using synthetic storage-cloud tariff material only."""
import copy
from collections import Counter

import pytest

from app.contracts import SCHEMA, document_gaps, validate_response
from app.core import Problem
from app.document_reader import reader_document
from app.prd import MRD_NORMATIVE_APPENDIX_TITLE, compile_plan, plan_contract
from app.product_flow import checkpoint, status
from tests.test_document_quality_v4 import compile_fixture_plan, plan
from tests.test_prd_v2_semantics import checked_project


def tariff_project(tmp_path, count):
    project = checked_project(tmp_path)
    project['name'] = '合成大型储能云平台电价管理'
    templates = {item['kind']: item for item in project['items']
                 if item['selection_status'] == 'selected'}
    source = project['sources'][0]
    source['title'] = '合成储能场站电价说明（仅作工程回归）'
    excerpt_template = source['excerpts'][0]
    source['excerpts'] = []
    project['items'] = []
    project['questions'] = []
    project['messages'] = []
    statements = {
        'requirement': '运维人员在合成场站 {site} 查看电价模板。',
        'rule': '合成场站 {site} 的重叠电价时段拒绝保存。',
        'acceptance': '合成场站 {site} 输入重叠电价时段并保存，应显示拒绝提示，原数据不变。',
    }
    prefixes = {'requirement': 'REQ', 'rule': 'RULE', 'acceptance': 'AC'}
    current_requirement = None
    for index in range(count):
        kind = ('requirement', 'rule', 'acceptance')[index % 3]
        site = f'T{index + 1:03}'
        item = copy.deepcopy(templates[kind])
        item.update(id=f'{prefixes[kind]}-TARIFF-{index + 1:03}',
                    title=f'合成场站 {site} 电价{kind}',
                    statement=statements[kind].format(site=site), related_refs=[])
        excerpt = dict(excerpt_template, id=f'EX-TARIFF-{index + 1:03}',
                       locator=f'合成电价说明第 {index + 1} 条', text=item['statement'])
        source['excerpts'].append(excerpt)
        item['source_refs'] = [dict(source_id=source['id'], excerpt_id=excerpt['id'])]
        if kind == 'requirement':
            current_requirement = item
            item['behavior'] = dict(flow='运维人员打开场站电价模板列表。',
                                    result='展示对应场站的电价模板。')
        else:
            item['related_refs'] = [current_requirement['id']]
            current_requirement['related_refs'].append(item['id'])
        project['items'].append(item)
    project['product_context'].update(
        product='大型储能集成商云平台', module='电价管理',
        intent='核对储能场站电价模板与重叠时段处理。',
        current_state='合成资料仅提供本期电价管理需求，既有实现待核对。',
        users='储能场站运维人员', value='减少电价模板配置歧义。',
        change_scope='本期核对合成场站电价模板和时段处理。',
        preserve_scope='本次不修改场站资产资料。', out_of_scope='不涉及储能控制策略。',
        priority='先核对电价模板和重叠时段处理，收益数据待补充。',
        scope_ids=[item['id'] for item in project['items'] if item['kind'] == 'requirement'])
    for step in (1, 2):
        checkpoint(project, step, status(project)[step - 1]['content_hash'])
    assert status(project)[1]['complete']
    return project


@pytest.mark.parametrize('count, block_counts', [(50, [100]), (51, [100, 2]), (59, [100, 18])])
def test_mrd_annex_splits_without_losing_originals_or_reference_locations(tmp_path, count, block_counts):
    project = tariff_project(tmp_path, count)
    before = copy.deepcopy(project)
    proposal = plan('4', normative=[item['id'] for item in project['items']])
    response = compile_fixture_plan(proposal, project, 'mrd')
    validate_response(response, 'prd', project, project['sources'][0]['excerpts'], 'mrd')
    document = response['result']
    annexes = [section for section in document['sections']
               if section['title'].startswith(MRD_NORMATIVE_APPENDIX_TITLE)]
    assert [len(section['blocks']) for section in annexes] == block_counts
    assert annexes[0]['title'] == MRD_NORMATIVE_APPENDIX_TITLE
    if count > 50:
        assert annexes[1]['title'] == MRD_NORMATIVE_APPENDIX_TITLE + '（续 2）'
    assert SCHEMA['$defs']['prdResult']['properties']['sections']['items']['properties']['blocks']['maxItems'] == 100
    assert all(len(section['blocks']) <= 100 for section in document['sections'])

    expected_ids = [item['id'] for item in project['items']]
    actual_ids = []
    actual_locations = {}
    items = {item['id']: item for item in project['items']}
    for section in annexes:
        blocks = section['blocks']
        for normative, provenance in zip(blocks[::2], blocks[1::2]):
            item_id = normative['ref_ids'][0]
            item = items[item_id]
            actual_ids.append(item_id)
            actual_locations[item_id] = [section['section_id']]
            assert normative == dict(kind=item['kind'], ref_ids=[item_id], text=None)
            expected_source = '、'.join(ref['source_id'] + '/' + ref['excerpt_id']
                                      for ref in item['source_refs'])
            assert provenance == dict(kind='narrative', ref_ids=[item_id],
                text=f'{item_id}：{item["epistemic_status"]}；草稿采纳不代表业务负责人批准；来源：{expected_source}')
    assert actual_ids == expected_ids
    all_normative_ids = [ref for section in document['sections'] for block in section['blocks']
                        if block['kind'] in ('requirement', 'rule', 'acceptance') for ref in block['ref_ids']]
    assert Counter(all_normative_ids) == Counter(expected_ids)
    assert len(document['coverage']) == count
    assert {row['item_id']: row['section_ids'] for row in document['coverage']} == actual_locations
    assert document_gaps(document, project) == []

    sections = {section['section_id']: section for section in document['sections']}
    scope_mapping = next(row for row in document['reference_mapping'] if row['profile_section_id'] == 'MRD-4.1')
    assert scope_mapping['disposition'] == 'merged'
    for item in project['items']:
        if item['kind'] != 'requirement':
            continue
        mapping = next(row for row in document['reference_mapping']
                       if row['profile_section_id'] == 'MRD-5.1.F.4' and row['scope_ref'] == item['id'])
        assert mapping['disposition'] == 'merged'
        assert mapping['output_section_ids'] == actual_locations[item['id']]
        assert set(actual_locations[item['id']]) <= set(scope_mapping['output_section_ids'])
        for section_id in mapping['output_section_ids']:
            assert any(block['kind'] == 'requirement' and block['ref_ids'] == [item['id']]
                       for block in sections[section_id]['blocks'])

    readable = reader_document(dict(content=document, assembly_version='prd-plan-4',
        item_snapshot=project['items'], question_snapshot=[], delivery_item_ids=expected_ids,
        product_context_snapshot=project['product_context']))
    reader_blocks = [block for section in readable['sections'] for block in section['blocks']]
    for item in project['items']:
        assert sum(item['statement'] in (block.get('text') or '') for block in reader_blocks) == 1
    assert project == before


def test_mrd_capacity_fix_does_not_accept_unknown_item_reference(tmp_path):
    project = tariff_project(tmp_path, 59)
    before = copy.deepcopy(project)
    proposal = plan('4', normative=['AC-999305e063474c04'])
    with pytest.raises(Problem) as error:
        compile_plan(proposal, project, 'mrd')
    assert error.value.code == 'REFERENCE_INVALID'
    assert 'AC-999305e063474c04' in error.value.message
    assert project == before
    assert len(plan_contract(project, 'mrd')['normative_item_ids']) == 59
