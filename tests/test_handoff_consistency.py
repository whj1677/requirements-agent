"""Offline frozen reviews; actual local generation, confirmation and ZIP export."""
import asyncio
import base64
import copy
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile

from docx import Document

from app.contracts import gate
from app.core import hashes
from app.detail_rendering import table_markdown
from app.document_reader import reader_document
from app.exports import document_files, handoff, handoff_files
from app.local_prd import generate
from app.prewrite import apply_result, question_resolution_current
from app.product_flow import checkpoint, question_open, review_document, status
from app.requirement_confirmation import row_fingerprint
from app.requirements import delivery_items
from app.response_application import apply_response
from app.store import Store
from app.workflow import confirm
from tests.helpers import prepared
from tests.test_local_prd import body, setup
from tests.test_prewrite_consistency import review


MARKERS = dict(constraints='姓名为 1 至 27 个字符，超限时不提交',
    visibility='仅同站点成员可见联系人姓名',
    failure='保存失败时保留输入，并提示“联系人未更新，请重试”',
    repeat='保存过程中重复点击只处理同一次提交')
RISK = 'AI建议，待核对：联系人保存失败后重新提交的提示是否清楚。'
QUESTION = '请决定文本计数技术细节：是否必须在本期指定 Unicode 码点或字素簇算法？'


def detailed_project(folder):
    store, project = setup(folder)
    with store.edit(project['id'], project['revision'], '合成电站联系人交接资料', bump=False) as (p, _):
        p['product_context'].update(module='联系人管理', intent='允许管理员修改联系人姓名',
            current_state='已有联系人记录', users='管理员', value='维护联系人姓名',
            change_scope='联系人姓名编辑', preserve_scope='只读角色仍不能编辑',
            out_of_scope='联系人删除', priority='本期需求', scope_ids=['REQ-0001'])
        p['delivery_scope'] = {'documents': ['prd']}
        req = p['items'][0]
        req.update(title='联系人姓名编辑', change_type='modified')
        detail = req['detail_contract']
        detail['fields'][0].update({key: MARKERS[key] for key in ('constraints', 'visibility')})
        detail['interactions'][0].update(validation=MARKERS['constraints'],
            failure=MARKERS['failure'], repeat=MARKERS['repeat'])
        p['sources'][0]['excerpts'][0]['text'] = (
            '管理员可以编辑联系人姓名。只读角色不能编辑联系人。\n' + json.dumps(detail, ensure_ascii=False))
        req['requirement_confirmation']['content_hash'] = row_fingerprint(p, req)
        apply_result(p, review(p), 'RUN-FROZEN-READY')
    return store, store.get(project['id'])


def confirm_and_export(store, project, key):
    # Synthetic human actions execute real gates; the baseline is never fabricated.
    with store.edit(project['id'], project['revision'], '合成人工核对与冻结评审', bump=False) as (p, _):
        for step in (1, 2, 3):
            checkpoint(p, step, status(p)[step - 1]['content_hash'])
        review_document(p, 'prd', p['documents']['prd']['id'])
        checkpoint(p, 5, status(p)[4]['content_hash'])
        apply_response(p, review(p))
    p = store.get(project['id'])
    assert gate(p) == []
    result = confirm(store, p['id'], dict(expected_revision=p['revision'], expected_hashes=hashes(p),
        scope_ids=[item['id'] for item in delivery_items(p)], idempotency_key='confirm-' + key))
    exported = handoff(store, p['id'], result['baseline_id'], 'export-' + key)
    with zipfile.ZipFile(store.folder / 'exports' / (exported['id'] + '.zip')) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return result['baseline_id'], files


def assert_markdown_images_resolve(files):
    image_count = 0
    for name, content in files.items():
        if not name.endswith('.md'):
            continue
        for target in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', content.decode('utf-8')):
            image_count += 1
            if target.startswith('data:image/svg+xml;base64,'):
                assert ET.fromstring(base64.b64decode(target.split(',', 1)[1])).tag.endswith('svg')
            elif target.startswith('data:image/png;base64,'):
                assert base64.b64decode(target.split(',', 1)[1]).startswith(b'\x89PNG\r\n\x1a\n')
            else:
                assert target in files, (name, target)
    assert image_count > 0


def test_detailed_handoff_preserves_role_definitions_risk_tables_and_frozen_baseline(tmp_path):
    store, p = detailed_project(tmp_path)
    with store.edit(p['id'], p['revision'], '冻结非阻断评审建议', bump=False) as (current, _):
        response = review(current)
        response['findings'] = [dict(type='other', severity='warning', message=RISK,
            suggested_resolution='结合实际保存失败场景核对提示内容。', related_refs=['REQ-0001'], source_refs=[])]
        assert apply_result(current, response, 'RUN-FROZEN-ADVISORY')['status'] == 'passed'
    p = store.get(p['id'])
    result = generate(store, p['id'], body(p))
    assert result['receipt']['model_calls'] == 0
    p = result['project']
    ordinary = asyncio.run(document_files(p, 'prd'))
    word = Document(io.BytesIO(ordinary['PRD.docx']))
    word_text = '\n'.join(para.text for para in word.paragraphs) + '\n'.join(
        cell.text for table in word.tables for row in table.rows for cell in row.cells)
    baseline_id, files = confirm_and_export(store, p, 'details')
    baseline = store.get_record(p['id'], baseline_id, 'baseline')
    before = copy.deepcopy(baseline)
    for marker in MARKERS.values():
        assert marker in word_text and marker in ordinary['PRD.md'].decode('utf-8')
        for name in ('requirements.md', 'frontend_spec.md', 'backend_spec.md', 'PRD.md'):
            assert marker in files[name].decode('utf-8'), (name, marker)
    for name in ('frontend_spec.md', 'backend_spec.md'):
        text = files[name].decode('utf-8')
        assert '未指定，需技术设计时核对' not in text
        assert '(requirements.md)' in text and '(delivery_scope.json)' in text
    for section in reader_document(p['documents']['prd'])['sections']:
        for node in section['reading_nodes']:
            if node['kind'] == 'table':
                assert table_markdown(node) in files['PRD.md'].decode('utf-8')
                assert table_markdown(node) in ordinary['PRD.md'].decode('utf-8')
    assert RISK in word_text and RISK in files['PRD.md'].decode('utf-8')
    assert '只读' in files['acceptance_criteria.md'].decode('utf-8')
    assert_markdown_images_resolve(files)
    assert_markdown_images_resolve(ordinary)
    manifest = json.loads(files['manifest.json'])
    for name, record in manifest['artifacts'].items():
        assert hashlib.sha256(files[name]).hexdigest() == record['sha256']
    current = store.get(p['id'])
    with store.edit(p['id'], current['revision'], '合成后续范围变更') as (current, _):
        current['product_context']['scope_ids'] = []
    later = handoff(store, p['id'], baseline_id, 'export-frozen-after-change')
    with zipfile.ZipFile(store.folder / 'exports' / (later['id'] + '.zip')) as archive:
        for name in ('PRD.md', 'requirements.md', 'frontend_spec.md', 'backend_spec.md', 'open_questions.md'):
            assert archive.read(name) == files[name]
    assert store.get_record(p['id'], baseline_id, 'baseline') == before


def test_reviewed_dispute_stays_resolved_in_formal_handoff(tmp_path):
    store, p = detailed_project(tmp_path)
    with store.edit(p['id'], p['revision'], '合成异议与冻结复核', bump=False) as (current, _):
        response = review(current, blocker=True)
        response['findings'][0].update(type='gap', message='尚未指定文本计数技术算法。',
            suggested_resolution=QUESTION, decision_question=QUESTION)
        response['result']['required_decisions'] = [QUESTION]
        first = apply_result(current, response, 'RUN-FROZEN-QUESTION')
        current['prewrite_responses'] = [dict(finding_id=first['findings'][0]['id'], review_id=first['id'],
            mode='dispute', reason='业务上限已明确，不需在本期选择文本计数技术算法。', created='synthetic')]
        assert apply_result(current, review(current), 'RUN-FROZEN-RECHECK')['status'] == 'passed'
        assert question_resolution_current(current, current['questions'][0])
    p = store.get(p['id'])
    p = generate(store, p['id'], body(p))['project']
    q = p['questions'][0]
    assert q['status'] == 'open' and q['answer'] is None and not question_open(q)
    baseline_id, files = confirm_and_export(store, p, 'resolved')
    assert QUESTION not in files['open_questions.md'].decode('utf-8')
    assert QUESTION not in files['PRD.md'].decode('utf-8')
    assert all(q['id'] not in row['open_question_ids'] for row in json.loads(files['traceability.json']))
    saved = store.get_record(p['id'], baseline_id, 'baseline')['project']['questions'][0]
    assert saved['status'] == 'open' and saved['answer'] is None
    assert saved['prewrite_resolution']['basis'] == 'reviewed_dispute'


def test_legacy_handoff_question_projection_matches_current_activity(tmp_path):
    p = prepared(Store(tmp_path))
    source = p['questions'][0]
    p['questions'] = [dict(copy.deepcopy(source), id='Q-active', question='本期仍需决定的事项', status='open', answer=None),
        dict(copy.deepcopy(source), id='Q-split', question='已拆分的历史事项', status='open', answer=None, superseded_by=['Q-active']),
        dict(copy.deepcopy(source), id='Q-later', question='本期以外事项', status='open', answer=None, out_of_scope_reason='后续另行处理')]
    before = copy.deepcopy(p)
    files, _ = handoff_files(dict(id='synthetic-legacy-baseline', project=p, hashes=hashes(p), confirmation_id='synthetic'), [])
    assert '本期仍需决定的事项' in files['open_questions.md'].decode('utf-8')
    assert '已拆分的历史事项' not in files['open_questions.md'].decode('utf-8')
    assert '本期以外事项' not in files['open_questions.md'].decode('utf-8')
    traces = json.loads(files['traceability.json'])
    assert [row['requirement_id'] for row in traces] == ['REQ-0001']
    assert traces[0]['open_question_ids'] == ['Q-active']
    assert p == before
