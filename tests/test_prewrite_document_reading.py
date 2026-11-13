"""Review exports retain this document's unresolved consistency-check snapshot."""
import asyncio
import copy
import io

import pytest
from docx import Document

from app.document_reader import reader_document
from app.exports import document_files
from app.semantic_quality import readable_document_blocks
from app.store import Store
from tests.helpers import prepared


TITLE = '成文检查待核对事项'


@pytest.fixture
def project(tmp_path):
    p = prepared(Store(tmp_path))
    artifact = p['documents']['prd']
    artifact['item_snapshot'] = copy.deepcopy(p['items'])
    artifact['question_snapshot'] = copy.deepcopy(p['questions'])
    artifact['sketch_policy'] = 'excluded'
    artifact['prewrite_snapshot'] = dict(status='needs_confirmation', id='PWR-SNAPSHOT', findings=[
        dict(id='PW-WARNING', severity='warning', status='open',
             message='流程节点写“有响应”，但失败或超时出边表示未取得响应，两处冲突。',
             suggested_resolution='请核对后拆分响应与权限判断，建议尚未采纳。',
             related_refs=['REQ-0001'], question_id='Q-0001'),
        dict(id='PW-BLOCKER', severity='blocker', status='open',
             message='正文与验收对终态是否允许查询的约定不一致。',
             suggested_resolution='核对终态查询条件后同步修改验收。', related_refs=['AC-0001']),
        dict(id='PW-RESOLVED', severity='blocker', status='resolved',
             message='已解决问题不应进入待核对章节。', suggested_resolution='已解决建议不重复。',
             related_refs=['REQ-0001']),
        dict(id='PW-INFO', severity='info', status='open',
             message='信息提示并非待决事项，不应列入本章。', related_refs=['REQ-0001']),
    ])
    return p


def pending_section(artifact, **kwargs):
    return next(s for s in reader_document(artifact, **kwargs)['sections'] if s['title'] == TITLE)


def test_pending_projection_preserves_exact_findings_without_inventing_questions(project):
    artifact = project['documents']['prd']
    before = copy.deepcopy(artifact)
    section = pending_section(artifact, include_locations=True)
    assert section['level'] == 1 and section['parent_section_id'] is None
    text = '\n'.join(b['text'] for b in section['blocks'])
    assert len(section['blocks']) == 2
    for finding in artifact['prewrite_snapshot']['findings'][:2]:
        assert finding['message'] in text and finding['suggested_resolution'] in text
    assert 'REQ-0001（电价时段维护）' in text
    assert 'AC-0001（重叠验收）' in text
    assert '建议（待确认）' in text
    assert '已解决问题不应进入' not in text and '信息提示并非' not in text
    assert all(not b.get('ref_ids') and 'question_id' not in b for b in section['blocks'])
    assert section['blocks'][0]['source_location'] == 'DOC-prd.prewrite_snapshot.findings[0]'
    assert section['blocks'][1]['source_location'] == 'DOC-prd.prewrite_snapshot.findings[1]'
    locations = readable_document_blocks(artifact)
    assert locations[section['blocks'][0]['source_location']] == section['blocks'][0]['text']
    assert locations[section['blocks'][1]['source_location']] == section['blocks'][1]['text']
    assert artifact == before


@pytest.mark.parametrize('status', ['open', 'answered', 'awaiting_recheck'])
def test_answered_or_applied_but_unrechecked_finding_remains_visible(project, status):
    artifact = project['documents']['prd']
    artifact['prewrite_snapshot']['findings'] = [dict(
        artifact['prewrite_snapshot']['findings'][0], status=status)]
    assert artifact['prewrite_snapshot']['findings'][0]['message'] in str(pending_section(artifact))


def test_current_project_check_does_not_rewrite_historical_document(project):
    artifact = project['documents']['prd']
    frozen = copy.deepcopy(artifact)
    before = pending_section(artifact)
    project['prewrite_review'] = dict(status='passed', findings=[])
    project['items'][0]['title'] = '当前项目的新标题不得倒灌'
    project['questions'][0]['status'] = 'answered'
    assert pending_section(artifact) == before
    assert '当前项目的新标题不得倒灌' not in str(pending_section(artifact))
    assert artifact == frozen


def test_long_risk_uses_prose_and_only_exact_duplicate_pending_finding_is_omitted(project):
    artifact=project['documents']['prd']
    finding=artifact['prewrite_snapshot']['findings'][0]
    finding['message']=('正常、保护、数据异常及恢复等待期间均须周期发布电流和状态。'
        '现有验收仅在正常运行且数据有效时检查发布节奏，没有单独覆盖保护与恢复等待期间。'
        '实现若在这些周期停发，会触发接收侧超时，测试应核对每个周期的实际电流与状态。'
        '不能把发送成功作为接收侧已经执行的依据，也不能把通信恢复计时替换为数据有效性计时。'
        '本条是待核对的验收补充建议，未改变已保存的业务规则、数值、状态优先级或恢复条件。'
        '用户仍需阅读建议并判断是否补充相应验收场景；本次呈现调整不代表已经采纳或解决该问题。')
    artifact['content']['sections'].append(dict(section_id='RISKS',title='风险点与待确认',level=1,
        parent_section_id=None,blocks=[dict(kind='table',table_role='risk_register',ref_ids=['REQ-0001'],
            text='风险点与待确认',title='风险点与待确认',columns=['风险来源','风险点','影响及状态','建议处理'],
            rows=[['成文检查 · AI建议，待核对',finding['message'],'关联对象：REQ-0001',finding['suggested_resolution']]])]))
    before=copy.deepcopy(artifact)
    view=reader_document(artifact)
    pending=next(s for s in view['sections'] if s['title']==TITLE)
    assert len(pending['blocks'])==1 and '终态' in pending['blocks'][0]['text']
    nodes=[n for s in view['sections'] for n in s['reading_nodes']]
    assert not any(n.get('table_role')=='risk_register' for n in nodes)
    assert sum(finding['message'] in n.get('text','') for n in nodes)==1
    assert any(finding['suggested_resolution'] in n.get('text','') for n in nodes)
    assert artifact==before
    artifact['content']['sections'][-1]['blocks'][0]['rows'][0][3]='不同的建议，不能当作完整重复。'
    assert len(pending_section(artifact)['blocks'])==2


@pytest.mark.parametrize('snapshot', [None, {}, {'findings': []}, {'findings': [
    dict(severity='blocker', status='resolved', message='历史已解决'),
    dict(severity='warning', status='resolved', message='历史提醒已解决'),
    dict(severity='info', status='open', message='仅信息提示'),
]}])
def test_legacy_or_no_unresolved_findings_adds_no_section(project, snapshot):
    artifact = project['documents']['prd']
    artifact.pop('prewrite_snapshot')
    legacy_view = reader_document(artifact)
    if snapshot is not None:
        artifact['prewrite_snapshot'] = copy.deepcopy(snapshot)
    before = copy.deepcopy(artifact)
    assert reader_document(artifact) == legacy_view
    assert not any(s['title'] == TITLE for s in legacy_view['sections'])
    assert artifact == before


def test_project_current_findings_do_not_appear_in_legacy_document(project):
    artifact = project['documents']['prd']
    project['prewrite_review'] = artifact.pop('prewrite_snapshot')
    assert not any(s['title'] == TITLE for s in reader_document(artifact)['sections'])


def test_section_identity_does_not_collide_with_existing_product_chapter(project):
    artifact = project['documents']['prd']
    artifact['content']['sections'].append(dict(section_id='PRODUCT-PREWRITE-PENDING',
        title='既有业务章节', level=1, parent_section_id=None,
        blocks=[dict(kind='narrative', ref_ids=[], text='既有业务内容保留。')]))
    view = reader_document(artifact)
    assert len({s['section_id'] for s in view['sections']}) == len(view['sections'])
    assert pending_section(artifact)['section_id'] != 'PRODUCT-PREWRITE-PENDING'


def test_review_markdown_and_word_include_frozen_pending_details(project):
    before = copy.deepcopy(project)
    files = asyncio.run(document_files(project, 'prd', mode='review'))
    markdown = files['PRD.md'].decode('utf-8')
    word = '\n'.join(p.text for p in Document(io.BytesIO(files['PRD.docx'])).paragraphs)
    for text in (markdown, word):
        assert TITLE in text and '需求评审稿／非正式交付' in text
        assert '待核对问题（阻塞项）' in text and '建议（待确认）' in text
        for finding in project['documents']['prd']['prewrite_snapshot']['findings'][:2]:
            assert finding['message'] in text and finding['suggested_resolution'] in text
        assert 'REQ-0001（电价时段维护）' in text
        assert '已解决问题不应进入' not in text and '信息提示并非' not in text
    assert project == before


def test_non_prd_reader_is_unchanged(project):
    artifact = project['documents']['mrd']
    before = reader_document(artifact)
    artifact['prewrite_snapshot'] = copy.deepcopy(project['documents']['prd']['prewrite_snapshot'])
    assert reader_document(artifact) == before

def test_semantic_review_includes_generated_prewrite_section_without_assuming_canonical_id(project):
    from app.semantic_quality import validate_document_findings
    artifact = project['documents']['prd']
    before = copy.deepcopy(artifact)
    validate_document_findings([], project, [])
    # The generated warnings remain visible; avoiding the crash must not remove
    # their frozen text or mutate the signed canonical document.
    assert any('.prewrite_snapshot.findings[' in location for location in readable_document_blocks(artifact))
    assert artifact == before


def test_prewrite_future_document_risk_is_not_current_document_assertion(project):
    from app.prewrite import review_project, validate_result
    from app.semantic_quality import validate_document_findings
    from app.core import Problem
    from app.requirements import delivery_items
    working = review_project(project)
    item = working['items'][0]
    finding = dict(message='当前条款导出到 PRD 后若被单独引用为结果断言，可能造成误解。',
        severity='warning', related_refs=[item['id']], source_refs=[],
        evidence_quotes=[dict(object_id=item['id'], path='statement', quote=item['statement'])])
    response = dict(findings=[finding], result=dict(assessment='ready_for_human_review',
        reviewed_refs=[i['id'] for i in delivery_items(working)],
        perspectives=[dict(role='测试', considerations=['核对当前条款'])], required_decisions=[]))
    before = copy.deepcopy(response)
    validate_document_findings(response['findings'], working, [])
    validate_result(working, response)
    assert response == before and project['documents'] and not working['documents']
    # Scope selection never relaxes real item quotes or explicit document IDs.
    finding['evidence_quotes'][0]['quote'] = '不存在的当前条款原文'
    with pytest.raises(Problem, match='成文检查引文不在'):
        validate_result(working, response)
    finding['message'] = 'DOC-prd.SEC-001.blocks[0] 存在错误。'
    with pytest.raises(Problem, match='复核文档段落定位不存在'):
        validate_document_findings(response['findings'], working, [])
    finding['message'] = 'DOC:DOC-prd.SEC-001.blocks[0]:condition 存在错误。'
    with pytest.raises(Problem, match='不存在的语义检查'):
        validate_document_findings(response['findings'], working, [])
    finding['message'] = before['findings'][0]['message']
    with pytest.raises(Problem, match='复核声称错误的文档不存在'):
        validate_document_findings(response['findings'], dict(working, _prewrite_review=False), [])
