"""Saved-content capacity regressions with isolated, synthetic offline input."""
import copy
import json

import jsonschema
import pytest

from app.contracts import validate_document, validate_response
from app.core import Problem
from app.document_reader import reader_document
from app.document_schema import SCHEMA
from app.local_prd import generate
from app.prewrite import apply_result, review_project
from app.prd import compile_plan, saved_prd_plan
from app.prd_sections import risk_register_blocks
from app.provider import DEFAULT, assemble
from app.requirement_confirmation import row_fingerprint
from app.response_application import apply_response
from app.understanding import save_questions
from tests.test_local_prd import body, setup
from tests.test_prewrite_consistency import review


def fixture(tmp_path, requirements=1, rules=0, acceptance=0):
    store, project = setup(tmp_path)
    with store.edit(project['id'], project['revision'], '合成容量回归输入', bump=False) as (p, _):
        templates = {item['kind']: copy.deepcopy(item) for item in p['items']
                     if item['selection_status'] == 'selected'}
        requirement = templates['requirement']
        requirement.pop('detail_contract')
        requirement.pop('detail_required')
        requirement['behavior'] = dict(actor='管理员', entry='在联系人详情点击编辑。',
            data='联系人姓名为文本。', permissions='管理员可以编辑，只读角色不能编辑。',
            flow='编辑姓名后保存。', exceptions='保存失败时保留输入并提示原因。', result='显示保存后的姓名。')
        p['items'] = []
        for kind, count, prefix in (('requirement', requirements, 'REQ'), ('rule', rules, 'RULE'),
                                    ('acceptance', acceptance, 'AC')):
            for index in range(count):
                item = copy.deepcopy(templates[kind])
                item.update(id=f'{prefix}-{index + 1:04d}', title=f'联系人维护{index + 1:04d}',
                    related_refs=[] if kind == 'requirement' else ['REQ-0001'])
                if kind == 'requirement':
                    item['statement'] = f'管理员可以编辑第{index + 1:04d}类联系人的姓名。'
                p['items'].append(item)
        p['questions'] = []
        p['messages'] = []
        p['sources'][0]['excerpts'][0]['text'] = '\n'.join(item['statement'] for item in p['items'])
        freeze_review(p)
    return store, store.get(project['id'])


def freeze_review(project):
    for item in project['items']:
        if item['kind'] == 'requirement':
            item['requirement_confirmation']['content_hash'] = row_fingerprint(project, item)
    frozen = review(project)
    validate_response(frozen, 'review', project, excerpts(project))
    apply_result(project, frozen, 'RUN-OFFLINE-CAPACITY')


def excerpts(project):
    return [excerpt for source in project['sources'] for excerpt in source['excerpts']]


def add_question(project, index, related=None):
    question = dict(temp_id=f'QTMP-CAPACITY-{index:04d}', topic_key=f'contact-decision-{index:04d}',
        question=f'第{index:04d}类联系人是否允许重复提交？', why='影响本期联系人维护的重复操作处理。',
        options=[], blocking=False, related_refs=related or ['REQ-0001'], source_refs=[])
    jsonschema.Draft202012Validator(SCHEMA['$defs']['Question']).validate(question)
    save_questions(project, [question])
    return project['questions'][-1]


def saved_document(store, project):
    before = copy.deepcopy(project)
    result = generate(store, project['id'], body(project))
    assert result['receipt']['model_calls'] == 0
    for key in ('items', 'sources', 'questions', 'prewrite_review'):
        assert result['project'][key] == before[key]
    return result['project']['documents']['prd']


@pytest.mark.parametrize('counts', [(102, 0, 0), (1, 50, 50)])
def test_complete_review_and_inventory_scale_with_saved_references(tmp_path, counts):
    store, project = fixture(tmp_path, *counts)
    frozen = review(project)
    messages, _, _ = assemble(review_project(project), 'review', '', DEFAULT, tmp_path)
    advertised = json.loads(messages[0]['content'].split('可信任务头：', 1)[1])['schema']
    jsonschema.Draft202012Validator(advertised).validate(frozen)
    validate_response(frozen, 'review', project, excerpts(project))

    incomplete = copy.deepcopy(frozen)
    incomplete['result']['reviewed_refs'].pop()
    validate_response(incomplete, 'review', project, excerpts(project))
    with pytest.raises(Problem) as caught:
        apply_result(copy.deepcopy(project), incomplete, 'RUN-INCOMPLETE')
    assert caught.value.code == 'PREWRITE_COVERAGE_REQUIRED'
    for invalid, expected in (([*frozen['result']['reviewed_refs'], frozen['result']['reviewed_refs'][0]], 'SCHEMA_INVALID'),
                              ([*frozen['result']['reviewed_refs'][:-1], 'REQ-NOT-IN-PROJECT'], 'REFERENCE_INVALID')):
        value = copy.deepcopy(frozen)
        value['result']['reviewed_refs'] = invalid
        with pytest.raises(Problem) as caught:
            validate_response(value, 'review', project, excerpts(project))
        assert caught.value.code == expected

    artifact = saved_document(store, project)
    document = artifact['content']
    assert {row['item_id'] for row in document['coverage']} == {item['id'] for item in project['items']}
    inventory = [block for section in document['sections'] for block in section['blocks']
                 if block.get('table_role') == 'function_inventory']
    assert [row for block in inventory for row in block['rows']] == [
        [item['id'], item['title'], item['statement']] for item in project['items'] if item['kind'] == 'requirement']
    if counts[0] == 102:
        assert [len(block['ref_ids']) for block in inventory] == [102]


def test_inventory_row_chunks_preserve_complete_coverage_and_reject_tampering(tmp_path):
    _, project = fixture(tmp_path, 201)
    # Short, complete saved behavior keeps the cloud PRD's required interaction
    # and flow contract while isolating table/mapping capacity.
    for item in project['items']:
        item['behavior'] = dict(actor='管理员', entry='编辑姓名。', data='姓名。',
            permissions='仅管理员。', flow='保存姓名。', exceptions='失败保留。', result='显示姓名。')
    freeze_review(project)
    # Exercise the compiler and validation without materializing a large reader,
    # artifact history or Word document for a table-capacity assertion.
    response = compile_plan(saved_prd_plan(project), project, 'prd', include_sketch=False, saved_content=True)
    validate_response(response, 'prd', project, excerpts(project))
    document = response['result']
    inventory = [block for section in document['sections'] for block in section['blocks']
                 if block.get('table_role') == 'function_inventory']
    assert [len(block['rows']) for block in inventory] == [200, 1]
    assert [row for block in inventory for row in block['rows']] == [
        [item['id'], item['title'], item['statement']] for item in project['items']]
    mappings = document['reference_mapping']
    assert len(mappings) > 1000
    assert len(mappings) == len({(row['profile_section_id'], row['scope_ref']) for row in mappings})
    for fault in ('missing', 'duplicate', 'rewritten'):
        altered = copy.deepcopy(document)
        tables = [block for section in altered['sections'] for block in section['blocks']
                  if block.get('table_role') == 'function_inventory']
        if fault == 'missing':
            tables[-1]['ref_ids'].pop(); tables[-1]['rows'].pop()
        elif fault == 'duplicate':
            tables[-1]['ref_ids'].append(tables[0]['ref_ids'][0])
            tables[-1]['rows'].append(tables[0]['rows'][0])
        else:
            tables[-1]['rows'][0][2] = '不属于已保存需求的改写'
        with pytest.raises(Problem) as caught:
            validate_document(altered, project, 'prd')
        assert caught.value.code == 'REFERENCE_INVALID'
    for fault in ('duplicate', 'dangling'):
        changed = copy.deepcopy(response)
        mappings = changed['result']['reference_mapping']
        if fault == 'duplicate':
            mappings.append(copy.deepcopy(mappings[0]))
        else:
            mapped = next(row for row in mappings if row['output_section_ids'])
            mapped['output_section_ids'] = ['PRD-NOT-IN-DOCUMENT']
        with pytest.raises(Problem) as caught:
            validate_response(changed, 'prd', project, excerpts(project))
        assert caught.value.code == 'REFERENCE_INVALID'


def test_api_saved_long_answer_and_statement_generate_without_truncation(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv('RA_DATA_DIR', str(tmp_path / 'unused-default'))
    from app.main import create_app
    from tests.test_runtime import FakeProvider
    store, project = fixture(tmp_path / 'project')
    with store.edit(project['id'], project['revision'], '保存合法问题', bump=False) as (p, _):
        question = add_question(p, 1)
    project = store.get(project['id'])
    provider = FakeProvider([])
    with TestClient(create_app(store.folder, access_token=None, provider=provider)) as client:
        csrf = client.get('/api/session').json()['csrf']
        client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': csrf})
        root = f'/api/projects/{project["id"]}'
        edited = client.post(root + '/items/REQ-0001', json=dict(expected_revision=project['revision'],
            selection_status='selected', statement='姓' * 4000))
        assert edited.status_code == 200, edited.text
        project = store.get(project['id'])
        answered = client.post(root + '/questions/' + question['id'], json=dict(
            expected_revision=project['revision'], answer='答' * 4000))
        assert answered.status_code == 200, answered.text
        project = store.get(project['id'])
        with store.edit(project['id'], project['revision'], '冻结合法检查', bump=False) as (p, _):
            freeze_review(p)
        project = store.get(project['id'])
        generated = client.post(root + '/documents/prd/local-review', json=body(project))
        assert generated.status_code == 200, generated.text
    artifact = generated.json()['project']['documents']['prd']
    blocks = [block for section in artifact['content']['sections'] if section['title'] == '已有回答与未决问题'
              for block in section['blocks']]
    assert len(blocks) == 2 and all(len(block['text']) <= 8000 for block in blocks)
    audit = ''.join(block['text'] for block in blocks)
    assert '姓' * 4000 in audit and '答' * 4000 in audit and '回答来源：' in audit and '关联条目当前快照：' in audit
    assert all(block['ref_ids'] == [question['id']] for block in blocks)
    assert generated.json()['project']['questions'] == project['questions']
    assert not provider.received


@pytest.mark.parametrize('count', [99, 100, 101])
def test_real_history_application_chunks_without_exposing_audit(tmp_path, count):
    store, project = fixture(tmp_path)
    with store.edit(project['id'], project['revision'], '累积合法历史回复', bump=False) as (p, _):
        for index in range(count):
            frozen = review(p)
            frozen['limitations'] = [f'第{index + 1:04d}次合成历史限制，不是当前业务规则。']
            validate_response(frozen, 'review', p, excerpts(p))
            apply_response(p, frozen)
        freeze_review(p)
    project = store.get(project['id'])
    artifact = saved_document(store, project)
    history = [section for section in artifact['content']['sections'] if section['title'] == '历史分析提示（非当前事实）']
    assert len(history) == (1 if count == 99 else 2)
    assert all(len(section['blocks']) <= 100 for section in history)
    combined = ''.join(block['text'] for section in history for block in section['blocks'])
    for index in range(count):
        assert combined.count(f'第{index + 1:04d}次合成历史限制') == 1
    assert '合成历史限制' not in str(reader_document(artifact))


@pytest.mark.parametrize('count', [99, 100, 101])
def test_saved_questions_keep_risk_rows_and_all_refs(tmp_path, count):
    store, project = fixture(tmp_path)
    with store.edit(project['id'], project['revision'], '逐个保存合法非阻断问题', bump=False) as (p, _):
        for index in range(count):
            add_question(p, index)
        freeze_review(p)
    project = store.get(project['id'])
    artifact = saved_document(store, project)
    tables = [block for section in artifact['content']['sections'] for block in section['blocks']
              if block.get('table_role') == 'risk_register']
    assert len(tables) == 1
    assert [row[1] for table in tables for row in table['rows']] == [q['question'] for q in project['questions']]
    assert {ref for table in tables for ref in table['ref_ids']} == {'REQ-0001', *[q['id'] for q in project['questions']]}
    assert all(len(table['ref_ids']) == len(set(table['ref_ids'])) and len(table['rows']) <= 200 for table in tables)


def test_single_question_keeps_all_100_related_items_in_one_risk_row(tmp_path):
    _, project = fixture(tmp_path, 100)
    refs = [item['id'] for item in project['items']]
    question = add_question(project, 1, refs)
    tables = risk_register_blocks(project)
    assert len(tables) == 1 and len(tables[0]['rows']) == 1
    assert tables[0]['ref_ids'] == [question['id'], *refs]
    table_schema = next(block for block in SCHEMA['$defs']['prdResult']['properties']['sections']['items']
                        ['properties']['blocks']['items']['oneOf'] if block['properties']['kind'].get('const') == 'table')
    jsonschema.Draft202012Validator(table_schema).validate(tables[0])


def test_question_merges_keep_more_than_100_related_items_and_reject_unknown_refs(tmp_path):
    _, project = fixture(tmp_path, 101)
    refs = [item['id'] for item in project['items']]
    question = add_question(project, 1, refs[:100])
    # Both incoming questions satisfy the current 100-link input contract;
    # save_questions combines their links under the same existing question ID.
    add_question(project, 1, refs[100:])
    assert len(project['questions']) == 1 and question['related_refs'] == refs
    freeze_review(project)
    response = compile_plan(saved_prd_plan(project), project, 'prd', include_sketch=False, saved_content=True)
    validate_response(response, 'prd', project, excerpts(project))
    tables = [block for section in response['result']['sections'] for block in section['blocks']
              if block.get('table_role') == 'risk_register']
    assert len(tables) == 1 and len(tables[0]['rows']) == 1
    assert tables[0]['ref_ids'] == [question['id'], *refs]
    for extra, code in (('REQ-NOT-IN-PROJECT', 'REFERENCE_INVALID'), (refs[0], 'SCHEMA_INVALID')):
        changed = copy.deepcopy(response)
        table = next(block for section in changed['result']['sections'] for block in section['blocks']
                     if block.get('table_role') == 'risk_register')
        table['ref_ids'].append(extra)
        with pytest.raises(Problem) as caught:
            validate_response(changed, 'prd', project, excerpts(project))
        assert caught.value.code == code


def test_risk_row_limit_splits_sources_even_without_item_references(tmp_path):
    _, project = fixture(tmp_path)
    project['sources'] = [dict(id=f'SRC-PARTIAL-{index}', title=f'合成读取不全资料{index}',
        parse_status='partial', excluded=False) for index in range(201)]
    project.pop('prewrite_review', None)
    tables = risk_register_blocks(project)
    assert [len(table['rows']) for table in tables] == [200, 1]
    assert all(not table['ref_ids'] for table in tables)
    assert len({row[1] for table in tables for row in table['rows']}) == 201


def test_long_question_group_stays_together_at_audit_chapter_boundary(tmp_path):
    store, project = fixture(tmp_path)
    with store.edit(project['id'], project['revision'], '99个短问题后保存长回答', bump=False) as (p, _):
        p['items'][0]['statement'] = '姓' * 4000
        p['sources'][0]['excerpts'][0]['text'] = p['items'][0]['statement']
        for index in range(100):
            question = add_question(p, index)
        question.update(status='answered', answer='答' * 4000, understanding_status='pending')
        freeze_review(p)
    project = store.get(project['id'])
    artifact = saved_document(store, project)
    chapters = [section for section in artifact['content']['sections'] if section['title'] == '已有回答与未决问题']
    assert [len(section['blocks']) for section in chapters] == [99, 2]
    assert {block['ref_ids'][0] for block in chapters[1]['blocks']} == {question['id']}
    visible = [block['question_id'] for section in reader_document(artifact)['sections'] for block in section['blocks']
               if block.get('question_id')]
    assert visible.count(question['id']) == 1


def test_reader_projects_a_question_once_across_audit_continuations(tmp_path):
    _, project = fixture(tmp_path)
    question = add_question(project, 1)
    freeze_review(project)
    response = compile_plan(saved_prd_plan(project), project, 'prd', include_sketch=False, saved_content=True)
    first = next(section for section in response['result']['sections'] if section['title'] == '已有回答与未决问题')
    continuation = copy.deepcopy(first)
    continuation['section_id'] += '-CONTINUED'
    response['result']['sections'].append(continuation)
    validate_response(response, 'prd', project, excerpts(project))
    apply_response(project, response)
    artifact = project['documents']['prd']
    before = copy.deepcopy(artifact)
    visible = [block for section in reader_document(artifact, include_locations=True)['sections']
               for block in section['blocks'] if block.get('question_id') == question['id']]
    assert len(visible) == 1
    assert visible[0]['source_location'] == f'{artifact["id"]}.{first["section_id"]}.blocks[0]'
    assert artifact == before


def test_narrative_schema_error_reports_the_narrative_limit(tmp_path):
    _, project = fixture(tmp_path)
    response = compile_plan(saved_prd_plan(project), project, 'prd', include_sketch=False, saved_content=True)
    response['result']['sections'][0]['blocks'].append(dict(kind='narrative', ref_ids=[], text='界' * 8001))
    with pytest.raises(Problem) as caught:
        validate_response(response, 'prd', project, excerpts(project))
    assert caught.value.code == 'SCHEMA_INVALID'
    assert 'maxLength=8000' in caught.value.message and '实际长度=8001' in caught.value.message
