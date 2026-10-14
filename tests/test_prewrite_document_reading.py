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
        assert TITLE in text and '需求评审稿／非正式交付／含待确认项' in text
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
