"""Reference mapping must follow actual generated evidence, not section labels."""

from app.document_mapping import build_reference_mapping


def section(sid, *blocks):
    return dict(section_id=sid, title=sid, level=1, parent_section_id=None,
                blocks=list(blocks))


def narrative(value, refs=()):
    return dict(kind='narrative', ref_ids=list(refs), text=value)


def requirement(ref):
    return dict(kind='requirement', ref_ids=[ref], text=None)


def fixture():
    item = dict(id='REQ-1', kind='requirement', selection_status='selected',
                applies_to='to_be', behavior=dict(actor='仓库员', flow='提交后核对库存',
                                                result='生成出库单', data='出库单号',
                                                exceptions='库存不足时拒绝提交'))
    p = dict(items=[item], product_context={'scope_ids':['REQ-1']}, ui=None)
    verified = dict(product='仓储系统', current_state='人工记录出库',
                    users='仓库员', change_scope='支持电子出库')
    sections = [
        section('PRD-CONTEXT', narrative('现有产品：仓储系统'),
                narrative('当前页面与流程：人工记录出库'),
                narrative('目标使用者：仓库员'),
                narrative('本期新增或修改：支持电子出库')),
        section('PRD-FUNCTION', requirement('REQ-1')),
        section('PRD-LIMITS', narrative('业务规则仍待核对')),
    ]
    return p, verified, sections


def mapped(result, dimension):
    return next(m for m in result if m['profile_section_id'] == dimension)


def test_prd_context_and_behavior_map_to_actual_sections_only():
    p, verified, sections = fixture()
    result = build_reference_mapping(p, 'prd', sections,
                                     {'REQ-1':['PRD-FUNCTION']},
                                     verified_context=verified,
                                     pending_section_id='PRD-LIMITS')
    assert mapped(result, 'PRD-2.1')['output_section_ids'] == ['PRD-CONTEXT']
    assert mapped(result, 'PRD-2.2')['output_section_ids'] == ['PRD-CONTEXT']
    for dimension in ('PRD-3.3', 'PRD-4.F.2', 'PRD-4.F.3',
                      'PRD-4.F.5', 'PRD-4.F.7'):
        entry = mapped(result, dimension)
        assert entry['disposition'] == 'merged'
        assert entry['output_section_ids'] == ['PRD-FUNCTION']
        assert '不等于业务已确认' in entry['reason']
    for dimension in ('PRD-1.3', 'PRD-4.F.6', 'PRD-4.F.8',
                      'PRD-4.F.9', 'PRD-5.2'):
        entry = mapped(result, dimension)
        assert entry['disposition'] == 'pending'
        assert entry['output_section_ids'] == ['PRD-LIMITS']
        assert dimension not in entry['reason'] or '尚未' in entry['reason']


def test_missing_evidence_remains_pending_despite_nearby_narrative():
    p, verified, sections = fixture()
    p['items'][0]['behavior']['flow'] = ''
    sections[0]['blocks'] = [narrative('仓储系统面向仓库员；但未经核对')]
    sections[1]['blocks'] = [narrative('REQ-1 提交后核对库存', ['REQ-1'])]
    result = build_reference_mapping(p, 'prd', sections,
                                     {'REQ-1':['PRD-FUNCTION']},
                                     verified_context=verified,
                                     pending_section_id='PRD-LIMITS')
    for dimension in ('PRD-2.1', 'PRD-2.2', 'PRD-3.3',
                      'PRD-4.F.2', 'PRD-4.F.3', 'PRD-4.F.5'):
        assert mapped(result, dimension)['disposition'] == 'pending'
    assert all(m['output_section_ids'] == ['PRD-LIMITS'] for m in result
               if m['disposition'] == 'pending')


def test_mrd_and_retired_sketch_keep_independent_dispositions():
    p, verified, prd_sections = fixture()
    mrd_sections = [dict(s, section_id=s['section_id'].replace('PRD-', 'MRD-'))
                    for s in prd_sections]
    result = build_reference_mapping(p, 'mrd', mrd_sections,
                                     {'REQ-1':['MRD-FUNCTION']},
                                     verified_context=verified,
                                     pending_section_id='MRD-LIMITS')
    assert mapped(result, 'MRD-1')['output_section_ids'] == ['MRD-CONTEXT']
    assert mapped(result, 'MRD-4.1')['output_section_ids'] == ['MRD-CONTEXT', 'MRD-FUNCTION']
    assert mapped(result, 'MRD-5.1.F.4')['output_section_ids'] == ['MRD-FUNCTION']
    assert mapped(result, 'MRD-5.1.F.1')['disposition'] == 'pending'

    p['_sketch_retired'] = True
    result = build_reference_mapping(p, 'prd', prd_sections,
                                     {'REQ-1':['PRD-FUNCTION']},
                                     verified_context=verified,
                                     pending_section_id='PRD-LIMITS')
    picture = mapped(result, 'PRD-4.F.1')
    assert picture['disposition'] == 'not_applicable'
    assert picture['output_section_ids'] == []


def test_invalid_pending_destination_is_rejected():
    p, verified, sections = fixture()
    try:
        build_reference_mapping(p, 'prd', sections, {}, verified_context=verified,
                                pending_section_id='PRD-NOT-PRESENT')
    except ValueError as error:
        assert 'pending_section_id' in str(error)
    else:
        raise AssertionError('unresolved mapping destination was accepted')
