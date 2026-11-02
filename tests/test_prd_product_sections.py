"""PRD reader summaries and diagrams use only saved, structured evidence."""
from app.contracts import validate_response
from app.document_reader import reader_document
from app.prd import compile_plan, plan_contract
from app.workflow import apply_response
from tests.test_detail_document import compile_fixture, project_fixture
from tests.test_prd01 import state


def test_checked_context_and_cross_context_explanations_keep_reader_chapters(tmp_path):
    from tests.test_prd_v2_semantics import checked_project
    from app.prd import CONTEXT_SECTION_V4, SECTION_TITLES
    import copy
    project=checked_project(tmp_path)
    before=copy.deepcopy(project)
    contexts=plan_contract(project,'prd')['context_ref_ids']
    sections=[dict(section_key='background',context_refs=contexts,
        normative_refs=plan_contract(project,'prd')['normative_item_ids'],discussion_refs=[],explanations=[])]
    for key,refs in [('goal',['intent','value','preserve_scope']),('users',['users','preserve_scope'])]:
        sections.append(dict(section_key=key,context_refs=refs,normative_refs=[],discussion_refs=[],
            explanations=[dict(text='；'.join(project['product_context'][ref] for ref in refs),
                evidence_refs=dict(context_refs=refs,normative_refs=[],business_claim_refs=[]))]))
    result=compile_plan(dict(plan_version='4',sections=sections),project,'prd',include_sketch=False)
    chapters={section['title']:section['blocks'] for section in result['result']['sections']}
    for key in ('goal','users'):
        explanations=[block for block in chapters[SECTION_TITLES[key]] if block['kind']=='explanation']
        assert explanations and explanations[0]['text']==sections[1 if key=='goal' else 2]['explanations'][0]['text']
    # Context not covered verbatim by an explanation still goes to its fixed
    # reader chapter, even when the model first references everything above.
    for ref in ('product','current_state','change_scope','out_of_scope'):
        assert any(project['product_context'][ref] in (block.get('text') or '')
                   for block in chapters[SECTION_TITLES[CONTEXT_SECTION_V4[ref]]])
    assert not any(block['kind']=='explanation' for block in chapters['背景与现状'])
    assert project==before


def nodes(document):
    return [node for section in document['sections'] for node in section['reading_nodes']]


def test_structured_prd_has_inventory_function_flow_and_real_risks(tmp_path):
    project = project_fixture(tmp_path)
    project['questions'] = [dict(id='Q-UNBIND', status='open',
        question='解绑后历史数据如何处理？', why='影响解绑后的历史数据可用性',
        related_refs=['REQ-0001'], source_refs=[])]
    project['sources'][0].update(parse_status='partial', failure_reason='一张流程图片尚未读取')

    compile_fixture(project)
    project['documents']['prd']['assembly_version']='prd-plan-4'
    view = reader_document(project['documents']['prd'])
    titles = {section['title'] for section in view['sections']}
    assert {'主要功能清单', '功能流程图', '风险点与待确认'} <= titles
    inventory = next(node for node in nodes(view) if node.get('table_role') == 'function_inventory')
    assert inventory['rows'] == [['R01', project['items'][0]['title']]]
    assert inventory['item_ids']==['REQ-0001']
    flow_section = next(section for section in view['sections'] if section['title'] == '功能流程图')
    assert any(node['kind'] == 'diagram' for node in flow_section['reading_nodes'])
    risks = next(node for node in nodes(view) if node.get('table_role') == 'risk_register')
    assert any('解绑后历史数据' in row[1] and '历史数据可用性' in row[2] for row in risks['rows'])
    assert any('部分内容尚未成功读取' in row[2] for row in risks['rows'])
    assert project['sources'][0]['failure_reason']=='一张流程图片尚未读取'
    assert all('重复绑定' not in str(row) for row in risks['rows'])


def test_legacy_prd_marks_missing_structured_flow_without_inventing_diagram(tmp_path):
    _, project = state(tmp_path)
    plan = dict(plan_version='4', sections=[dict(section_key='function', context_refs=[],
        normative_refs=plan_contract(project, 'prd')['normative_item_ids'],
        discussion_refs=[], explanations=[])])
    response = compile_plan(plan, project, 'prd', include_sketch=False)
    validate_response(response, 'prd', project, project['sources'][0]['excerpts'])
    apply_response(project, response)

    view = reader_document(project['documents']['prd'])
    flow = next(section for section in view['sections'] if section['title'] == '功能流程图')
    assert not any(node['kind'] == 'diagram' for node in flow['reading_nodes'])
    assert any('未生成流程图' in node.get('text', '') for node in flow['reading_nodes'])
    risks = next(node for node in nodes(view) if node.get('table_role') == 'risk_register')
    assert any('重复姓名如何处理' in row[1] for row in risks['rows'])


def test_empty_risk_record_is_explicit_without_invented_levels(tmp_path):
    project = project_fixture(tmp_path)
    compile_fixture(project)
    view = reader_document(project['documents']['prd'])
    section = next(section for section in view['sections'] if section['title'] == '风险点与待确认')
    text = ''.join(node.get('text', '') for node in section['reading_nodes'])
    assert '当前没有已记录' in text
    assert not any(node.get('table_role') == 'risk_register' for node in section['reading_nodes'])
    assert '高风险' not in text and '中风险' not in text


def test_advisory_risk_is_visible_without_becoming_a_business_decision(tmp_path):
    from app.document_preview import build_prd_preview
    from app.prewrite import apply_result
    from app.prd_sections import risk_register_blocks
    from tests.test_prewrite_consistency import review
    import copy

    project = project_fixture(tmp_path)
    before_items = copy.deepcopy(project['items'])
    before_questions = copy.deepcopy(project['questions'])
    response = review(project)
    response['findings'] = [dict(type='other', severity='warning',
        message='AI建议，待核对：可核对当前操作重复提交时的提示是否清楚。',
        suggested_resolution='结合实际操作方式核对是否需要补充提示说明。',
        related_refs=['REQ-0001'], source_refs=[])]

    report = apply_result(project, response, 'RUN-advisory-risk')

    assert report['status'] == 'passed' and report['blocking_count'] == 0
    row = risk_register_blocks(project)[0]['rows'][0]
    assert row[0] == '成文检查 · AI建议，待核对'
    assert row[1] == response['findings'][0]['message']
    suggestions = build_prd_preview(project)['ai_suggestions']
    assert any(row['kind'] == 'prewrite' and row['text'] == response['findings'][0]['suggested_resolution']
               for row in suggestions)
    assert project['items'] == before_items and project['questions'] == before_questions
