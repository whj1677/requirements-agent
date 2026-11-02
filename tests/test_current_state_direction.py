"""Isolated state/provenance and short-check contracts; semantic replies are synthetic."""
import asyncio,copy,json
import pytest
from app import intake_grounding as g,business_draft as bd
from app.core import digest
from app.workflow import Workflow
from tests.test_business_draft_workflow import BusinessProvider
from tests.test_business_draft import packet,draft_row,business_value
from tests.test_requirement_recommendation import project

@pytest.mark.parametrize('mode',['replace','empty','missing','invalid','other_field','reviewed','saved','confirmed_empty','known_state','edited_text','no_run'])
def test_unknown_baseline_only_replaces_unreviewed_ai_candidate(tmp_path,mode):
    async def scenario():
        store,p=project(tmp_path);field='users' if mode=='other_field' else 'current_state'
        old='旧AI推断的现状';new='' if mode=='empty' else '资料未说明现有相关能力。'
        with store.edit(p['id'],p['revision'],'合成旧AI摘要') as (current,_):
            current['product_context_proposal']={field:old,'intent':'保持其他字段'}
            evidence=dict(text=old,basis='fact',run_id='RUN-old',evidence_status='unreviewed')
            if mode=='reviewed':evidence['evidence_status']='reviewed'
            if mode=='edited_text':evidence['text']='旧证据已不匹配当前文本'
            if mode=='no_run':evidence.pop('run_id')
            current['product_context_proposal_evidence']={field:evidence}
            current['business_draft_reviews']=[dict(run_id='RUN-old',summary_fields={field:copy.deepcopy(evidence)},accepted=[])]
            if mode=='saved':current.setdefault('product_context',{})[field]='人工保存的明确现状'
            if mode=='known_state':current.setdefault('context_field_states',{})[field]={'status':'known'}
            if mode=='confirmed_empty':
                current.setdefault('product_context',{})[field]=''
                current['context_confirmations']={field:dict(actor='authenticated_local_user',content_hash=digest(dict(text='',state=None)))}
        before=store.get(p['id']);raw=packet(draft_row('R1'),draft_row('R2'));raw['summary_fields']={field:business_value(new,'unknown')}
        if mode=='missing':raw['summary_fields']={}
        if mode=='invalid':raw['summary_fields']={field:business_value(new,'fact',sources=[dict(source='S404',locator='L404')])}
        provider=BusinessProvider([raw]);workflow=Workflow(store,provider)
        run=workflow.start(p['id'],before['revision'],'ingest','请整理初稿')
        await workflow.tasks[run['id']];saved=store.get_record(p['id'],run['id'],'run')
        if mode in ('empty','missing','invalid'):
            assert not saved.get('result_applied'),saved
            assert saved['error']=='BUSINESS_DRAFT_CURRENT_STATE_UNCHECKED'
            assert len(provider.received)==1
            after=store.get(p['id'])
            assert after['product_context_proposal']==before['product_context_proposal']
            assert after['product_context_proposal_evidence']==before['product_context_proposal_evidence']
            return
        assert saved.get('result_applied'),saved
        after=store.get(p['id']);assert after['product_context_proposal'][field]==(new if mode=='replace' else old)
        assert after['product_context_proposal']['intent']=='保持其他字段'
        assert after.get('product_context')==before.get('product_context')
        assert after.get('context_confirmations')==before.get('context_confirmations')
        assert after['business_draft_reviews'][:-1]==before['business_draft_reviews']
        if mode=='replace':
            proof=after['product_context_proposal_evidence'][field]
            assert proof['basis']=='unknown' and proof['uncertain'] is True and proof['evidence_status']=='unreviewed'
            assert proof['run_id']==run['id'] and proof['text']==new
        else:assert after['product_context_proposal_evidence']==before['product_context_proposal_evidence']
        first_payload=json.loads(provider.received[0][1]['content'][0]['text'])
        assert first_payload['context_proposal_evidence'][field]==before['product_context_proposal_evidence'][field]
        assert '不是独立来源' in first_payload['context_proposal_notice']
    asyncio.run(scenario())


@pytest.mark.parametrize('source,text,unsupported',[
 ('本期计划新增报表能力。','现有系统此前没有报表能力。',True),
 ('现有系统尚未提供报表功能。','目前系统没有报表功能。',False),
 ('现有系统已提供报表功能。','目前已有报表能力。',False)])
def test_current_state_direct_support_contract_preserves_legal_paraphrases(source,text,unsupported):
    raw=packet(draft_row());raw['summary_fields']={'current_state':business_value(text,'fact')}
    plan=g.prepare(raw,dict(excerpts=[dict(source='S1',locator='L1',text=source)]))
    messages=g.challenge_messages(plan);header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    assert header['grounding_protocol']=='intake-grounding-challenge-2'
    assert '不能推出此前没有' in messages[0]['content'] and '同义改写' in messages[0]['content']
    field=next(f for f in plan['fields'] if f['path']==['summary_fields','current_state'])
    response=dict(candidate_hash=plan['candidate_hash'],input_hash=plan['input_hash'],checked_field_ids=[f['id'] for f in plan['fields']],findings=[])
    if unsupported:response['findings']=[dict(id=field['id'],quote=text,reason='计划新增不能证明过去缺失。',sources=[dict(source='S1',locator='L1')])]
    assert (g.validate_challenge(plan,response)['status']=='blocked')==unsupported
    assert plan['candidate']['summary_fields']['current_state']['text']==text


def test_generation_and_challenge_share_temporal_direction_boundary():
    assert bd.INITIAL_INTAKE_SOURCE_CHECK_VERSION=='initial-intake-source-check-2'
    assert '不能反向证明此前没有' in bd.INITIAL_INTAKE_SOURCE_CHECK
    assert 'unreviewed' in bd.INITIAL_INTAKE_SOURCE_CHECK
    assert '不增加追问、不改本期范围' in bd.INITIAL_INTAKE_SOURCE_CHECK

@pytest.mark.parametrize('mode,required',[
 ('unreviewed',True),('sourced',True),('unknown_state',True),('saved',False),
 ('confirmed_empty',False),('known',False),('none',False),('not_applicable',False),
 ('reviewed',False),('edited',False),('no_run',False)])
def test_current_state_required_uses_human_and_ai_provenance(tmp_path,mode,required):
    from app.provider import DEFAULT
    _,p=project(tmp_path);old='既有资料中的现状背景'
    p['product_context_proposal']={'current_state':old}
    proof=dict(text=old,basis='fact',run_id='RUN-old',evidence_status='unreviewed')
    p['product_context_proposal_evidence']={'current_state':proof}
    if mode=='sourced':proof['source_refs']=copy.deepcopy(p['items'][0]['source_refs'])
    if mode=='saved':p.setdefault('product_context',{})['current_state']='已保存内容'
    if mode=='confirmed_empty':
        p.setdefault('product_context',{})['current_state']=''
        p['context_confirmations']={'current_state':dict(actor='authenticated_local_user',content_hash=digest(dict(text='',state=None)))}
    if mode in ('known','none','not_applicable','unknown_state'):
        p.setdefault('context_field_states',{})['current_state']={'status':'unknown' if mode=='unknown_state' else mode}
    if mode=='reviewed':proof['evidence_status']='reviewed'
    if mode=='edited':proof['text']='不匹配'
    if mode=='no_run':proof.pop('run_id')
    before=copy.deepcopy(p)
    assert bd._requires_current_state_recheck(p) is required
    messages,ex,_=bd.assemble(p,'ingest','请整理初稿',dict(DEFAULT,budget_mode='unlimited'),tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    assert ('current_state' in header['required_summary_fields']) is required
    assert header['schema']['properties']['summary_fields']['required']==header['required_summary_fields']
    for kind in ('missing','empty','invalid','valid'):
        raw=packet(draft_row())
        if kind!='missing':raw['summary_fields']['current_state']=business_value('' if kind=='empty' else '资料未说明既有能力。','fact' if kind=='invalid' else 'unknown',sources=[dict(source='S404',locator='L404')] if kind=='invalid' else [])
        if required and kind!='valid':
            from app.core import Problem
            with pytest.raises(Problem) as error:bd.adapt(raw,p,ex,'ingest',initial_intake_scope=True)
            assert error.value.code=='BUSINESS_DRAFT_CURRENT_STATE_UNCHECKED'
        else:bd.adapt(raw,p,ex,'ingest',initial_intake_scope=True)
    bd.adapt(packet(draft_row()),p,ex,'ingest',initial_intake_scope=False)
    messages,_,_=bd.assemble(p,'clarify','',dict(DEFAULT,budget_mode='unlimited'),tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    assert header['required_summary_fields']==bd.missing_summary_fields(p)
    assert p==before
