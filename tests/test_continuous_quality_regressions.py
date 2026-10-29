"""Public contracts: keep genuine witnesses and reject invented evidence."""
import copy
import json
import jsonschema
import pytest

from app.contracts import validate_response
from app.core import Problem, brief_hash
from app.document_reader import export_readiness
from app.prd import plan_contract, plan_schema
from app.provider import request_schema
from app.semantic_quality import current_document_checks, quote_matches, counting_question
from tests.helpers import EXAMPLES
from tests.test_semantic_quality_boundaries import compile_explanation, review_for_checks, stored_documents
from tests.test_semantic_quality_regressions import project
from app.product_flow import checkpoint, status
from app.workflow import apply_response
from fastapi.testclient import TestClient
from app.main import create_app
from tests.test_semantic_quality_boundaries import analysis_case, question, REF
from app.semantic_quality import readable_document_blocks
from app.interview import format_atoms
from tests.test_ui02_actions import case, plan as action_plan, start, finished
from tests.test_atomic_question_repair import response_with_compound_question, repaired_with_decision_points
from app import ingest


@pytest.mark.parametrize('quote,accepted',[
    ('……失败场景原记录不变……不能自动重发',True),
    ('失败场景原记录不变，不能自动重发',True),
    ('不能自动重发……失败场景原记录不变',False),
    ('失败场景原记录改变……不能自动重发',False),
    ('……',False),
    ('失败……重发',False),
])
def test_ellipsis_keeps_exact_words_order_and_nonempty_witness(quote,accepted):
    assert quote_matches(quote,'失败场景原记录不变，不能自动重发。') is accepted


@pytest.mark.parametrize('variant,accepted',[
    ('title_and_original',True),('ellipsis',True),('fabricated',False),
    ('wrong_title',False),('wrong_document',False),('wrong_block',False),
])
def test_public_review_title_and_body_have_their_own_real_location(tmp_path,variant,accepted):
    p=stored_documents(tmp_path)
    value=review_for_checks(p)
    check=current_document_checks(p)[0]
    section=next(s for s in p['documents']['prd']['content']['sections']
                 if s['section_id']==check['location'].split('.')[1])
    title=section['title'];quote=check['quote'];location=check['location']
    if variant=='ellipsis':quote='……'+quote
    elif variant=='fabricated':quote='原记录被删除，必须自动重发'
    elif variant=='wrong_title':title='其它不存在的章节'
    elif variant=='wrong_document':location=location.replace(p['documents']['prd']['id'],p['documents']['mrd']['id'])
    elif variant=='wrong_block':location=location.rsplit('[',1)[0]+'[0]'
    value['findings'][0]['message']=check['check_id']+'；位置 '+location+'（PRD「'+title+'」），原句“'+quote+'”，失败条件被泛化。'
    before=copy.deepcopy(p)
    if accepted:assert validate_response(value,'review',p,p['sources'][0]['excerpts'])==value
    else:
        with pytest.raises(Problem) as error:validate_response(value,'review',p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    assert p==before


@pytest.mark.parametrize('kind',['prd','mrd'])
@pytest.mark.parametrize('existing,claim,accepted',[
    ('原有单站点可设置生效日期。','同日未来安排等既有约束在提交时校验。',False),
    ('同日未来安排是否已有校验尚未确认。','同日未来安排等既有约束在提交时校验。',False),
    ('现有单站点针对同一生效日期的未来安排冲突整次拒绝。','同日未来安排等既有约束在提交时校验。',True),
    ('原有单站点可设置生效日期。','本期批量提交校验同日未来安排，有冲突时整批拒绝。',True),
    ('原有单站点可设置生效日期。','本期规则不证明同日未来安排已成为既有约束。',True),
    ('现有同日未来安排没有校验。','同日未来安排等既有约束在提交时校验。',False),
    ('现有同日未来安排不能校验冲突。','同日未来安排等既有约束在提交时校验。',False),
    ('现有规则不允许同一生效日期的未来安排发生冲突。','同日未来安排等既有约束在提交时校验。',True),
])
def test_current_rules_need_the_corresponding_affirmative_current_evidence(tmp_path,kind,existing,claim,accepted):
    p=project(tmp_path,'本期批量提交校验同日未来安排，有冲突时整批拒绝。')
    p['product_context']['current_state']=existing
    for step in (1,2):checkpoint(p,step,status(p)[step-1]['content_hash'])
    before=copy.deepcopy(p)
    if accepted:assert compile_explanation(p,claim,kind,['current_state'])
    else:
        with pytest.raises(Problem) as error:compile_explanation(p,claim,kind,['current_state'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    assert p==before


@pytest.mark.parametrize('risk',[False,True])
def test_download_gate_reports_actual_document_semantics_without_rewriting_history(tmp_path,risk):
    p=stored_documents(tmp_path)
    if not risk:
        for s in p['documents']['prd']['content']['sections']:
            for b in s['blocks']:
                if b['kind']=='explanation':b['text']='服务明确失败时原记录不变。'
    p['questions']=[]
    for artifact in p['documents'].values():artifact['brief_hash']=brief_hash(p)
    before=copy.deepcopy(p)
    result=export_readiness(p,'prd')
    assert result['ready'] is not risk
    assert any(i['code']=='SEMANTIC_BLOCKED' for i in result['issues']) is risk
    assert p==before


@pytest.mark.parametrize('risk',[False,True])
@pytest.mark.parametrize('fmt',['md','docx','zip'])
def test_public_download_rejects_semantic_risk_and_keeps_reader_available(tmp_path,risk,fmt):
    p=stored_documents(tmp_path/'fixture')
    if not risk:
        for s in p['documents']['prd']['content']['sections']:
            for b in s['blocks']:
                if b['kind']=='explanation':b['text']='服务明确失败时原记录不变。'
    p['questions']=[]
    for artifact in p['documents'].values():artifact['brief_hash']=brief_hash(p)
    app=create_app(tmp_path/'http', access_token='synthetic', env_path=tmp_path/'absent.env')
    created=app.state.store.create('合成语义下载验收')
    p['id']=created['id']
    with app.state.store.edit(p['id'],0,'合成文档风险输入',bump=False) as (state,_):
        state.clear();state.update(p)
    current=app.state.store.get(p['id'])
    with app.state.store.edit(p['id'],current['revision'],'合成派生文档版本',bump=False) as (state,_):
        for artifact in state['documents'].values():artifact['brief_hash']=brief_hash(state)
    before=copy.deepcopy(app.state.store.get(p['id']))
    with TestClient(app) as client:
        token=client.post('/api/session/login',json={'key':'synthetic'}).json()['csrf']
        client.headers.update({'Origin':'http://testserver','X-CSRF-Token':token})
        root='/api/projects/'+p['id']
        reader=client.get(root).json()['documents']['prd']['reader']
        assert reader['sections']
        response=client.get(root+'/documents/prd/'+fmt)
        assert response.status_code==(409 if risk else 200)
        if risk:
            assert response.json()['code']=='SEMANTIC_BLOCKED'
            assert not app.state.store.records(p['id'],'document_export')
    assert app.state.store.get(p['id'])==before


@pytest.mark.parametrize('stage',['clarify','change','brainstorm','vision'])
def test_model_wire_contract_selects_exact_stage_without_relaxing_runtime_schema(stage):
    schema=request_schema(stage)
    good=copy.deepcopy(EXAMPLES[stage]);before=copy.deepcopy(good)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(good)
    wrong=copy.deepcopy(good);wrong['result']={'unadvertised':'not a legal result'}
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(wrong,schema)
    assert good==before and schema['properties']['stage']=={'const':stage}
    assert stage+'Result' in schema['$defs'] and 'prdResult' not in schema['$defs']


@pytest.mark.parametrize('kind',['prd','mrd'])
def test_document_wire_schema_uses_actual_project_whitelists(tmp_path,kind):
    p=project(tmp_path,'本期备注允许空值。')
    contract=plan_contract(p,kind);schema=plan_schema(p,kind)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(contract['example'])
    for field in ('context_refs','normative_refs','discussion_refs'):
        bad=copy.deepcopy(contract['example']);bad['sections'][0][field]=['NOT-A-REAL-REF']
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(bad,schema)


@pytest.mark.parametrize('case,risk',[('review_role_in_flow',True),('operator_confirms',False),('explicit_pm_approval',False)])
def test_frozen_prd_keeps_role_ambiguity_for_review_without_rewriting_it(tmp_path,case,risk):
    p=project(tmp_path,'运营人员预览后确认提交。')
    p['items'][0]['behavior']={'actor':'运营人员','flow':'展示预览；PM 人工核对后确认提交；取消不应用。'}
    if case=='operator_confirms':p['items'][0]['behavior']['flow']='展示预览；运营人员核对后确认提交；取消不应用。'
    elif case=='explicit_pm_approval':p['items'][0]['statement']='运营人员发起，PM业务审批后提交应用。'
    apply_response(p,compile_explanation(p,'本期运营人员预览后确认提交。','prd'))
    before=copy.deepcopy(p)
    checks=current_document_checks(p)
    assert any(c['dimension']=='operator_role' for c in checks) is risk
    assert p==before


@pytest.mark.parametrize('text,accepted',[
    ('备注最多100个的长度上限按哪种计数单位判定？',True),
    ('备注长度采用哪种计数方式？',True),
    ('Unicode码点要显示什么颜色？',False),
    ('备注的计数单位要显示什么字体？',False),
])
def test_explicit_counting_decision_does_not_require_a_unit_name(text,accepted):
    assert counting_question({'blocking':True,'question':text}) is accepted


@pytest.mark.parametrize('contradictory',[False,True])
def test_public_clarification_options_cannot_repeat_the_counting_contradiction(tmp_path,contradictory):
    p,value=analysis_case(tmp_path,'clarify')
    q=question('QTMP-A','TMP-COUNT',REF,'备注最多100个的长度上限按哪种计数单位判定？')
    q['options']=['按Unicode码点逐个计数；99个A加👍🏽计101码点，拒绝保存。',
        '按扩展字素簇计数；99个A加👍🏽计100字素簇，允许保存。']
    if contradictory:q['options'][0]='按Unicode码点计数，与现有emoji按一个码点计的表述一致。'
    value['questions']=[q]
    if contradictory:
        with pytest.raises(Problem) as error:validate_response(value,'clarify',p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    else:assert validate_response(value,'clarify',p,p['sources'][0]['excerpts'])==value


@pytest.mark.parametrize('key',['priority','validation'])
@pytest.mark.parametrize('case',['valid','empty','wrong_basis'])
def test_mrd_wire_analysis_sections_need_their_actual_evidence(tmp_path,key,case):
    p=project(tmp_path,'本期备注允许空值。')
    contract=plan_contract(p,'mrd');schema=plan_schema(p,'mrd')
    assert key in contract['required_analysis_sections']
    basis=key if key=='priority' else 'value'
    plan=dict(plan_version='4',sections=[dict(section_key=key,context_refs=[basis],
        normative_refs=[],discussion_refs=[],explanations=[dict(text='合成有据的分析。',
            evidence_refs=dict(context_refs=[basis],normative_refs=[],business_claim_refs=[]))])])
    if case=='empty':plan['sections'][0]['explanations']=[]
    elif case=='wrong_basis':plan['sections'][0]['explanations'][0]['evidence_refs']['context_refs']=['users']
    if case=='valid':jsonschema.validate(plan,schema)
    else:
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(plan,schema)


@pytest.mark.parametrize('swap',[False,True])
def test_public_review_multiple_locations_bind_each_own_quote(tmp_path,swap):
    p=stored_documents(tmp_path)
    value=review_for_checks(p);check=current_document_checks(p)[0]
    other=next((loc,text) for loc,text in readable_document_blocks(p['documents']['mrd']).items()
               if text=='服务明确失败时原记录不变。')
    quote=check['quote'];other_quote=other[1]
    if swap:quote,other_quote=other_quote,quote
    value['findings'][0]['message']=check['check_id']+'；逐段对比：'+check['location']+'（“'+quote+'”）、'+other[0]+'（“'+other_quote+'”）。'
    if swap:
        with pytest.raises(Problem) as error:validate_response(value,'review',p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    else:assert validate_response(value,'review',p,p['sources'][0]['excerpts'])==value


def test_chat_format_anchor_keeps_literals_and_does_not_parse_fragments():
    raw='{"summary":"保留\\\"引号\\\"与中文"},"result":{"can_organize":false}}'
    valid='{"summary":"保留\\\"引号\\\"与中文","result":{"can_organize":false}}'
    assert format_atoms(raw)==format_atoms(valid)
    assert format_atoms(valid.replace('false','true'))!=format_atoms(raw)
    assert format_atoms('{"summary":"未结束') is None
    assert format_atoms('not-json') is None


@pytest.mark.parametrize('mutate',[False,True])
def test_public_chat_syntax_repair_preserves_every_business_literal(case,mutate):
    c,app,model,root=case
    from app.core import Problem
    calls=[];original=app.state.provider.request
    async def response(config,messages,**kwargs):
        if not calls:
            value,meta=await original(config,messages,**kwargs)
            value['summary']='图片尚未提供；当前只聊需求。这个页面给谁使用？'
            value['limitations']=['未提供图片，无法核对布局。']
            value['result']={'can_organize':False}
            calls.append(copy.deepcopy(value))
            raw=json.dumps(value,ensure_ascii=False)
            raw=raw.replace(', "proposals"','}, "proposals"',1)
            kwargs['evidence'].response(raw,http_status=200,finish_reason='stop',usage=meta.get('usage'))
            raise Problem('SCHEMA_INVALID','合成Premature closure；格式修复保持原文。')
        fixed=copy.deepcopy(calls[0])
        if mutate:fixed['summary']='已经看过图片，已准备好生成HTML。'
        calls.append(fixed)
        return fixed,{'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2},'finish_reason':'stop'}
    app.state.provider.request=response
    body,preview=action_plan(c,root,'interview','我想照着未提供的图做HTML，先帮我聊用途。')
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root);p=c.get(root).json()
    runs=[app.state.store.get_record(p['id'],rid,'run') for rid in task['run_ids']]
    assert task['calls']==2,[(r.get('error'),r.get('message')) for r in runs]
    assert task['status']==('failed' if mutate else 'partial')
    assert p['messages'][0]['text']==body['message'] and not p['items']
    if mutate:assert len(p['messages'])==1
    else:assert p['messages'][-1]['text']==calls[0]['summary']


@pytest.mark.parametrize('stage',['ingest','clarify'])
@pytest.mark.parametrize('explicit_empty',[False,True])
@pytest.mark.parametrize('rewrite',[False,True])
def test_compound_question_empty_and_missing_children_have_the_same_repair_contract(tmp_path,stage,explicit_empty,rewrite):
    p=project(tmp_path,'本期备注允许空值。');excerpts=p['sources'][0]['excerpts']
    ref=dict(source_id=excerpts[0]['source_id'],excerpt_id=excerpts[0]['id'])
    original=response_with_compound_question(stage,ref)
    if explicit_empty:original['questions'][0]['decision_points']=[]
    else:original['questions'][0].pop('decision_points',None)
    fixed=repaired_with_decision_points(original,ref)
    if rewrite:fixed['questions'][0]['question']='只需决定一个展示问题？'
    before=copy.deepcopy(original)
    validate_response(fixed,stage,p,excerpts)
    if stage=='ingest':
        anchor=ingest.business_anchor(original,excerpts)
        check=lambda:ingest.preserve(anchor,fixed,excerpts)
    else:
        anchor=ingest.response_anchor(original,excerpts,stage,p)
        check=lambda:ingest.preserve_response(anchor,fixed,excerpts,stage,original=original,project=p)
    if rewrite:
        with pytest.raises(Problem) as error:check()
        assert error.value.code=='SEMANTIC_BLOCKED'
    else:check()
    assert original==before


@pytest.mark.parametrize('kind',['prd','mrd'])
@pytest.mark.parametrize('claim,accepted',[
    ('不声称原审计已覆盖备注。',True),
    ('不宣称原审计已覆盖备注。',True),
    ('不主张原审计已覆盖备注。',True),
    ('原审计已覆盖备注，不声称已经支持其它能力。',False),
    ('不声称原审计已覆盖备注，但原审计已覆盖备注。',False),
])
def test_disclaimer_negates_only_its_own_implementation_predicate(tmp_path,kind,claim,accepted):
    p=project(tmp_path,'本期成功备注变更应记录备注前后内容。')
    if accepted:assert compile_explanation(p,claim,kind)
    else:
        with pytest.raises(Problem) as error:compile_explanation(p,claim,kind)
        assert error.value.code=='SEMANTIC_BLOCKED'


@pytest.mark.parametrize('rewrite',[False,True])
def test_compound_repair_wire_schema_keeps_the_complete_parent_question(tmp_path,rewrite):
    p=project(tmp_path,'本期备注允许空值。');excerpts=p['sources'][0]['excerpts']
    ref=dict(source_id=excerpts[0]['source_id'],excerpt_id=excerpts[0]['id'])
    original=response_with_compound_question('ingest',ref)
    original['questions'][0]['decision_points']=[]
    schema=ingest.compound_repair_schema(ingest.schema(),original)
    fixed=repaired_with_decision_points(original,ref)
    if rewrite:fixed['questions'][0]['question']='备注给谁看？'
    before=copy.deepcopy(original)
    if rewrite:
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(fixed,schema)
    else:jsonschema.validate(fixed,schema)
    assert original==before


@pytest.mark.parametrize('variant,accepted',[
    ('result_tail',True),('duplicate_prefix',False),('duplicate_result',False),
    ('other_tail_field',False),('partial_tail',False),('nested_close',False),
    ('wrong_stage',False),('extra_object',False),
    ('limitations_extra_tail',False),
])
def test_review_single_root_close_normalization_keeps_every_field(variant,accepted):
    from app.provider import normalize_review_result_tail
    value=copy.deepcopy(EXAMPLES['review'])
    result=value.pop('result')
    raw=json.dumps(value,ensure_ascii=False)+',"result":'+json.dumps(result,ensure_ascii=False)+'}'
    if variant=='duplicate_prefix':raw=raw.replace('"summary":','"summary":"重复", "summary":',1)
    if variant=='duplicate_result':raw=raw[:-1]+',"result":'+json.dumps(result)+'}'
    if variant=='other_tail_field':raw=raw[:-1]+',"other":[]}'
    if variant=='partial_tail':raw=raw[:-4]
    if variant=='nested_close':raw=raw.replace('"proposals": []','"proposals": [}]',1)
    if variant=='wrong_stage':raw=raw.replace('"stage": "review"','"stage": "clarify"',1)
    if variant=='extra_object':raw+='{}'
    if variant=='limitations_extra_tail':raw=json.dumps(dict(value,result=result))[:-1]+'],"":null}'
    fixed=normalize_review_result_tail(raw)
    if accepted:
        assert fixed[0]==dict(value,result=result)
        assert raw[fixed[1]]=='}'
        assert json.loads(raw[:fixed[1]]+raw[fixed[1]+1:])==fixed[0]
    else:assert fixed is None


@pytest.mark.parametrize('stage',['review','clarify'])
def test_provider_preserves_raw_proof_of_narrow_review_normalization(tmp_path,monkeypatch,stage):
    import asyncio
    import httpx
    from app.provider import Provider,DEFAULT
    from app.model_evidence import ModelCallEvidence
    value=copy.deepcopy(EXAMPLES['review']);result=value.pop('result')
    raw=json.dumps(value,ensure_ascii=False)+',"result":'+json.dumps(result,ensure_ascii=False)+'}'
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'model':'synthetic','usage':{'total_tokens':2},'choices':[{
            'message':{'content':raw},'finish_reason':'stop'}]}))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    provider=Provider(tmp_path/'absent.env');provider.keys['https://api.deepseek.com']='synthetic-test-key'
    proof=ModelCallEvidence(tmp_path,'CALL-test','RUN-test',stage,'synthetic','https://api.deepseek.com',None,'synthetic-test-key')
    if stage=='review':
        derived,meta=asyncio.run(provider.request(DEFAULT,[],evidence=proof))
        assert derived==dict(value,result=result)
        assert meta['syntax_normalization']==proof.value['syntax_normalization']
        assert proof.value['syntax_normalization']['rule']=='review-premature-root-close-before-result-1'
    else:
        with pytest.raises(Problem) as error:asyncio.run(provider.request(DEFAULT,[],evidence=proof))
        assert error.value.code=='SCHEMA_INVALID'
        assert 'syntax_normalization' not in proof.value
    assert json.loads(proof.path.read_text('utf-8'))['final_output']==raw


def test_review_counting_quote_index_is_the_actual_per_location_text(tmp_path):
    from app.provider import assemble,DEFAULT
    from app.semantic_quality import counting_witness
    p=stored_documents(tmp_path);before=copy.deepcopy(p)
    messages,_,_=assemble(p,'review','',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    expected=[dict(location=location,quote=text)
        for doc in p['documents'].values()
        for location,text in readable_document_blocks(doc).items() if counting_witness(text)]
    assert context['document_quote_witnesses']==expected
    assert p==before


@pytest.mark.parametrize('old_parent',[True,False])
def test_owned_cleanup_verifies_entire_creation_chain_before_waiting_or_stopping(tmp_path,monkeypatch,old_parent):
    from scripts import verify_windows_install as verifier
    executable=(tmp_path/'owned.exe').resolve();unowned=(tmp_path/'system.exe').resolve()
    opened=[];closed=[];stopped=[];waited=[]
    class Process:
        pid=10
        def poll(self):return None
        def terminate(self):stopped.append(10)
        def wait(self,timeout):waited.append(10)
    class Kernel:
        def OpenProcess(self,_access,_inherit,pid):opened.append(pid);return pid
        def CloseHandle(self,handle):closed.append(handle)
        def TerminateProcess(self,handle,_code):stopped.append(handle);return True
        def WaitForSingleObject(self,handle,_timeout):waited.append(handle);return 0
    process=verifier.OwnedProcess.__new__(verifier.OwnedProcess)
    process.process=Process();process.handle=10;process.created=100;process.executable=executable;process.roots=[]
    identities={10:(executable,100),11:(unowned,90 if old_parent else 110),12:(executable,120),13:(executable,115)}
    monkeypatch.setattr(verifier,'kernel_api',lambda:Kernel())
    monkeypatch.setattr(verifier,'process_parents',lambda:{11:10,12:11,13:10})
    monkeypatch.setattr(verifier,'process_identity',lambda handle:identities[handle])
    process.terminate_owned()
    if old_parent:
        assert set(opened)=={11,13} and 12 not in opened
        assert stopped==[13,10] and waited==[10]
        assert set(closed)=={11,13}
    else:
        assert set(opened)=={11,12,13}
        assert stopped==[12,13,10] and waited==[10,11]
        assert set(closed)=={11,12,13}


@pytest.mark.parametrize('kind',['prd','mrd'])
@pytest.mark.parametrize('existing,claim,accepted',[
    ('运营已能登录、查看和导出现有工单详情；目前标题仅可查看，不能在详情中编辑。',
     '现有工单详情已支持登录、查看和导出，但标题在这一页面只能查看。',True),
    ('没有证据表明运营已能登录、查看和导出现有工单详情。',
     '现有工单详情已支持登录、查看和导出。',False),
    ('运营是否已能登录、查看和导出现有工单详情尚未确认。',
     '现有工单详情已支持登录、查看和导出。',False),
    ('运营已能登录、查看和导出现有工单详情。',
     '现有工单详情已支持标题变更审计。',False),
    ('运营已能登录、查看和导出现有工单详情。',
     '现有工单详情已支持登录、查看、导出和失败审计。',False),
])
def test_affirmative_current_ability_still_binds_its_capability_object_and_scenario(tmp_path,kind,existing,claim,accepted):
    p=project(tmp_path,'本期工单详情允许手工修改标题，成功标题变更应记录前后内容。')
    p['product_context']['current_state']=existing
    p['product_context']['intent']='运营想就地修改工单标题，减少退回发起人修改。'
    for step in (1,2):checkpoint(p,step,status(p)[step-1]['content_hash'])
    if accepted:assert compile_explanation(p,claim,kind,['current_state','intent'])
    else:
        with pytest.raises(Problem) as error:compile_explanation(p,claim,kind,['current_state','intent'])
        assert error.value.code=='SEMANTIC_BLOCKED'


@pytest.mark.parametrize('binding,accepted',[
    ('same_question',True),('parent_only',False),('colour',False),('split_questions',False),
])
def test_conditional_new_counting_candidate_needs_direct_joint_question_binding(tmp_path,binding,accepted):
    p,value=analysis_case(tmp_path,'clarify')
    candidate=value['proposals'][0]
    candidate.update(action='add',target_item_id=None,kind='acceptance',epistemic_status='inferred',
        related_refs=['REQ-0001'],statement='按最终决定计数：若选码点99个A加👍🏽为101拒绝；若选字素簇则为100允许。同一口径须修订“emoji按一个码点计”表述。')
    q=question('QTMP-unit','REQ-0001',REF,'备注长度计数单位确定为哪一种？')
    value['questions']=[q]
    if binding in ('same_question','colour'):q['related_refs'].append(candidate['temp_id'])
    if binding=='colour':q['question']='Unicode码点要显示什么颜色？'
    if binding=='split_questions':value['questions'].append(question('QTMP-other',candidate['temp_id'],dict(source_id=REF['source_id'],excerpt_id='EX-OTHER')))
    before=copy.deepcopy((p,value))
    if accepted:assert validate_response(value,'clarify',p,p['sources'][0]['excerpts'])==value
    else:
        with pytest.raises(Problem) as error:validate_response(value,'clarify',p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    assert (p,value)==before


@pytest.mark.parametrize('stage',['ingest','clarify','change'])
@pytest.mark.parametrize('text,accepted',[
    ('备注长度用什么单位计数？',True),
    ('联系人备注最多100个的长度按哪个单位计数？',True),
    ('备注应以哪一种单位统计长度？',True),
    ('Unicode码点要显示什么颜色？',False),
    ('长度按哪个单位计数并显示什么颜色？',False),
    ('备注最多100个需要哪些信息？',False),
])
def test_public_counting_unit_question_word_orders_do_not_accept_colour_or_unrelated_decisions(tmp_path,stage,text,accepted):
    p,value=analysis_case(tmp_path,stage)
    value['questions']=[question('QTMP-count',value['proposals'][0]['temp_id'],REF,text)]
    before=copy.deepcopy((p,value))
    if accepted:assert validate_response(value,stage,p,p['sources'][0]['excerpts'])==value
    else:
        with pytest.raises(Problem) as error:validate_response(value,stage,p,p['sources'][0]['excerpts'])
        assert error.value.code=='SEMANTIC_BLOCKED'
    assert (p,value)==before


@pytest.mark.parametrize('limit',[80,100,250])
def test_verified_counting_alternatives_are_not_choices_and_have_actual_boundaries(tmp_path,limit):
    import subprocess
    from app.provider import assemble,DEFAULT
    from app.semantic_quality import counting_witness,counting_choice
    p,value=analysis_case(tmp_path,'clarify')
    p['sources'][0]['excerpts'][0]['text']=f'备注最多{limit}个Unicode码点，emoji按一个码点计。'
    value['proposals'][0]['statement']=p['sources'][0]['excerpts'][0]['text']
    before=copy.deepcopy(p)
    messages,excerpts,_=assemble(p,'clarify','',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    example=context['counting_option_examples'][0]
    assert example['source_refs']==[REF] and example['check_id'] is None
    assert [counting_choice(option) for option in example['options']]==['codepoints','graphemes']
    assert all(counting_witness(option) is None for option in example['options'])
    sample='A'*(limit-1)+'👍🏽'
    assert len(sample)==limit+1
    observed=subprocess.run(['node','-e',
        'const n=[...new Intl.Segmenter("en",{granularity:"grapheme"}).segment(process.argv[1])].length;process.stdout.write(String(n));',sample],
        capture_output=True,text=True,check=True)
    assert int(observed.stdout)==limit
    value['questions']=[question('QTMP-count',value['proposals'][0]['temp_id'],REF)]
    value['questions'][0]['options']=example['options']
    assert validate_response(value,'clarify',p,excerpts)==value
    assert p==before and p['questions'][0]['answer']=='重复姓名可保存。'


def test_counting_options_cannot_invent_a_source_for_an_unsourced_current_item(tmp_path):
    from app.provider import assemble,DEFAULT
    p=project(tmp_path,'备注最多100个Unicode码点，emoji按一个码点计。')
    p['items'][0]['source_refs']=[];before=copy.deepcopy(p)
    messages,_,_=assemble(p,'clarify','',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text'])
    check=next(x for x in context['counting_option_examples'] if x['check_id']=='COUNT:REQ-0001:statement')
    assert check['source_refs']==[] and len(check['options'])==2
    assert p==before


@pytest.mark.parametrize('kind',['prd','mrd'])
@pytest.mark.parametrize('text,accepted',[
    ('超限、服务明确未提交的失败、超时或结果未知等情形下，原记录不变并保留输入，并遵循既定的处理路径。',False),
    ('超时或结果未知时原记录不变，不能自动重发。',False),
    ('超时或结果未知，原记录不变，例如网络超时。',False),
    ('失败场景原记录不变，不能自动重发。',False),
    ('服务明确返回未提交的失败时原记录不变；超时或结果未知时先查询请求结果。',True),
    ('超时或结果未知时，查询确认未提交后原记录不变，并保留输入。',True),
    ('超时或结果未知时，查询结果明确为未提交后原记录不变。',True),
    ('超时或结果未知时，尚未查询确认未提交，原记录不变。',False),
    ('超时或结果未知不代表原记录不变，应先查询请求结果。',True),
    ('超时结果未知时，不要断言原记录不变。',True),
    ('超时后查询确认提交成功，原记录不变。',False),
    ('超时或结果未知、服务明确未提交的失败时原记录不变。',False),
    ('超时或结果未知时，查询确认未提交并未完成，原记录不变。',False),
    ('超时后收到后台明确未提交的结果时，原记录不变。',True),
])
def test_unknown_results_cannot_be_grouped_with_confirmed_uncommitted_failures(tmp_path,kind,text,accepted):
    p=project(tmp_path,'服务明确返回未提交的失败时原记录不变且输入保留；超时或结果未知时先查询同一请求结果，禁止自动重发；查询确认未提交后可再次确认。')
    before=copy.deepcopy(p)
    if accepted:assert compile_explanation(p,text,kind)
    else:
        with pytest.raises(Problem) as error:compile_explanation(p,text,kind)
        assert error.value.code=='SEMANTIC_BLOCKED'
    assert p==before


@pytest.mark.parametrize('kind',['prd','mrd'])
@pytest.mark.parametrize('claim,accepted',[
    ('现有工单详情中，运营已能登录、查看和导出工单，但标题仅可查看，无法在详情内编辑。',True),
    ('运营已能在合成工单管理系统中登录、查看和导出工单详情，但目前标题只可查看。',True),
    ('现有工单详情已支持失败标题变更审计。',False),
    ('现有工单详情已支持超时或未知结果查询。',False),
])
def test_named_existing_abilities_do_not_inherit_unrelated_future_failure_scenarios(tmp_path,kind,claim,accepted):
    p=project(tmp_path,'本期工单详情可手工修改标题；成功保存显示新标题，服务明确未提交失败时原记录不变，超时或结果未知先查询同一请求。登录、查看和导出保持。')
    p['product_context']['current_state']='运营已能登录、查看和导出现有工单详情；目前标题仅可查看，不能在详情中编辑。'
    for step in (1,2):checkpoint(p,step,status(p)[step-1]['content_hash'])
    if accepted:assert compile_explanation(p,claim,kind,['current_state'])
    else:
        with pytest.raises(Problem) as error:compile_explanation(p,claim,kind,['current_state'])
        assert error.value.code=='SEMANTIC_BLOCKED'
