"""Synthetic offline regressions for human-workflow repair; no external model."""
import asyncio
import copy
import io

import pytest
from docx import Document

from app.answer_implementation import location_suggestions, validate_implementations
from app.business_draft import adapt, _item_input, VALUE
from app.core import Problem, brief_hash, digest
from app.detail_draft import canonical_detail
from app.detail_rendering import detail_nodes
from app.exports import document_files
from app.interview import input_title
from app.prewrite import apply_result, structural_findings
from app.store import Store
from app.workspace_draft import discussion
from tests.test_answer_implementation import project as answer_project
from tests.test_business_draft import setup, packet, draft_row
from tests.test_detail_business_draft import sample_detail
from tests.test_prewrite_consistency import review
from tests.test_detail_document import project_fixture
from tests.helpers import prepared


def test_source_title_and_discussion_preserve_chat_and_separate_history(tmp_path):
    p=Store(tmp_path).create('告警认领')
    p['messages']=[dict(role='user',text='本期做认领释放',created='2026-10-31'),dict(role='assistant',text='建议只读')]
    assert input_title(p,'处理记录：并发失败提示负责人').startswith('对话 第2轮 处理记录')
    p['items']=[dict(id='R1',kind='requirement',title='本期认领',statement='仅一人认领',selection_status='selected',applies_to='to_be'),
        dict(id='R2',kind='requirement',title='历史提议',statement='电话跟进',selection_status='deferred',applies_to='to_be')]
    original=copy.deepcopy(p);text=discussion(p)
    assert '尚未填写' not in text and '本期做认领释放' in text
    assert '历史提议' not in text.split('## 暂缓、未采纳及历史条目')[0]
    assert p==original


def test_only_missing_nonfact_sources_is_normalized(tmp_path):
    p,local,_,_,ex,_=setup(tmp_path)
    row=draft_row();row['behavior']['permissions']={'text':'建议保持本站权限','basis':'recommendation'}
    raw=packet(row);before=copy.deepcopy(raw)
    value,report=adapt(raw,p,ex,'clarify',local)
    assert report['value_normalized'] and not report['rejected'] and raw==before
    assert next(x for x in value['proposals'] if x['kind']=='requirement')['epistemic_status']=='proposed'
    row=draft_row();row['statement'].pop('sources')
    _,report=adapt(packet(row),p,ex,'clarify',local)
    assert report['rejected'] or report['field_rejected']
    assert not report['value_normalized']


@pytest.mark.parametrize('fault',['start_incoming','end_outgoing','unreachable'])
def test_ai_topology_rejected_before_save_without_business_rewrite(fault):
    detail=sample_detail()
    if fault=='start_incoming':detail['flow']['edges'].append(dict(id='E9',**{'from':'N2','to':'N1'},condition='重新进入'))
    if fault=='end_outgoing':detail['flow']['edges'].append(dict(id='E9',**{'from':'N4','to':'N2'},condition='继续处理'))
    if fault=='unreachable':detail['flow']['nodes'].append(dict(id='N9',label='孤立结束',kind='end'))
    before=copy.deepcopy(detail)
    with pytest.raises(Problem) as error:canonical_detail(detail,{}, {})
    assert error.value.code=='BUSINESS_DRAFT_DETAIL_FLOW' and detail==before
    assert 'N9' not in error.value.message


def test_location_candidates_are_valid_readonly_navigation_not_confirmation():
    p=answer_project();q=p['questions'][0];q['answer']='需要核对后保存。'
    p['items'][0]['behavior']['flow']='需要核对后保存。'
    other=dict(p['items'][0],id='R-other');p['items'].append(other)
    before=copy.deepcopy(p);rows=location_suggestions(p,q)
    assert rows and rows[0]['field']=='behavior.flow'
    assert {r['item_id'] for r in rows}=={'REQ-1'} and len(rows)<=8 and p==before
    validate_implementations(p,q,[{k:rows[0][k] for k in ('item_id','field','quote')}])
    assert 'answer_applications' not in q


def test_negative_assessment_does_not_promote_warning_to_user_decision(tmp_path):
    p=project_fixture(tmp_path);value=review(p,assessment='needs_changes')
    value['findings']=[dict(type='other',severity='warning',message='章节可缩短',suggested_resolution='保留业务含义',related_refs=['REQ-0001'],source_refs=[])]
    result=apply_result(p,value,'RUN-test')
    advisory=next(x for x in result['findings'] if x['severity']=='warning')
    assert not advisory.get('blocks_document') and not advisory.get('question_id')
    assert p['questions']==[] and result['status']=='needs_confirmation'


def test_proposal_status_passes_to_model_and_detail_reading():
    item=dict(id='R1',kind='requirement',title='记录',statement='只读记录',epistemic_status='proposed',selection_status='candidate',detail_contract=sample_detail())
    assert _item_input(item,{})['epistemic_status']=='proposed'
    field=item['detail_contract']['fields'][0];field['editability']='只读'
    field['evidence']={'editability':{'status':'proposal'},'default_value':{'status':'unknown'}}
    nodes=detail_nodes(item,'fields');rows=nodes[-1]['rows']
    assert any(row[1]=='编辑权限' and '建议来源' in row[2] and row[3]=='只读' for row in rows)
    assert any(row[2]=='待确认' for row in rows)


@pytest.mark.parametrize('kind',['prd','mrd'])
def test_nonstructured_review_exports_actual_pending_list(tmp_path,kind):
    p=prepared(Store(tmp_path));p['ui']=None
    p['questions']=[dict(id='Q-pending',question='处理记录是否允许编辑？',status='open',answer=None,related_refs=[],blocking=True)]
    artifact=p['documents'][kind];artifact['brief_hash']=brief_hash(p);artifact['sketch_policy']='excluded'
    original=copy.deepcopy(p)
    files=asyncio.run(document_files(p,kind,mode='review'))
    md=files[kind.upper()+'.md'].decode();doc=Document(io.BytesIO(files[kind.upper()+'.docx']))
    assert '处理记录是否允许编辑？' in md
    assert any('处理记录是否允许编辑？' in para.text for para in doc.paragraphs)
    assert p==original


def test_context_completion_cannot_turn_unreviewed_priority_into_fact(monkeypatch):
    from app.prd import verified_context
    monkeypatch.setattr('app.prd.flow_status',lambda p:[{},dict(complete=True)])
    p=dict(product_context={'priority':'优先做告警','intent':'支持认领'},context_field_states={},
        product_context_proposal_evidence={'priority':dict(text='优先做告警',basis='recommendation',evidence_status='unreviewed')})
    assert verified_context(p)=={'intent':'支持认领'}
    p['context_confirmations']={'priority':dict(actor='authenticated_local_user',
        content_hash=digest(dict(text='优先做告警',state=None)))}
    assert verified_context(p)['priority']=='优先做告警'
    p['product_context']['priority']='建议优先告警，待产品确认'
    assert 'priority' not in verified_context(p)


def test_navigation_projection_does_not_change_document_hash(tmp_path):
    p=Store(tmp_path).create('告警');p['questions']=answer_project()['questions']
    before=brief_hash(p)
    projected=copy.deepcopy(p)
    projected['questions'][0]['answer_location_suggestions']=[dict(item_id='REQ-1',field='statement',quote='需要核对')]
    assert brief_hash(projected)==before
    p['context_confirmations']={'priority':dict(actor='authenticated_local_user',content_hash='new')}
    assert brief_hash(p)!=before


@pytest.mark.parametrize('value,schema,expected',[
    ({'text':'已明确认领','basis':'fact'}, VALUE, '资料引用'),
    ('直接字符串', VALUE, '内容类型不正确'),
    ({'text':'建议认领','basis':'wrong','sources':[]}, VALUE, '依据类型'),
])
def test_schema_failure_has_product_message_and_separate_exact_diagnostic(value,schema,expected):
    from app.business_draft import _validate_shape
    before=copy.deepcopy(value)
    with pytest.raises(Problem) as caught:_validate_shape(value,schema)
    error=caught.value
    assert expected in error.message and '重新整理相关需求草稿' in error.message
    assert 'is a required property' not in error.message and '[]' not in error.message
    assert error.code=='BUSINESS_DRAFT_INVALID' and not error.model_repair_allowed
    assert error.diagnostic['validator'] in ('required','type','enum')
    assert isinstance(error.diagnostic['path'],list) and error.diagnostic['detail']
    assert value==before


def test_invalid_row_report_keeps_raw_path_but_user_reason_is_readable(tmp_path):
    p,local,_,_,ex,_=setup(tmp_path)
    row=draft_row();row['title']=123
    value,report=adapt(packet(row),p,ex,'clarify',local)
    assert not value['proposals'] and not report['accepted']
    rejected=report['rejected'][0]
    assert rejected['raw']['title']==123
    assert rejected['diagnostic']['path']==['title']
    assert rejected['diagnostic']['validator']=='type'
    assert '需求名称' in rejected['reason'] and 'is not of type' not in rejected['reason']
