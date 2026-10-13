"""Offline source-to-outcome checks; no live Provider or daily database writes."""
import copy,json,time,uuid
import pytest
from app.core import Problem
from app.contracts import validate_response
from app.result_conditions import facts,item_facts,issues,validate_item,projection
from tests.helpers import EXAMPLES

SOURCE=('本期新增云资源申请规则。租户管理员在同一租户内每批最多20项。'
        '任一项配额不满足则整批拒绝，本批不创建任何资源。'
        '服务明确拒绝时本批不创建且既有资源不变。'
        '响应丢失或超时是结果未知，沿既有申请ID查询，状态确认前不重复提交。')
BAD='全部项配额满足则整批创建；任一项配额不满足则整批拒绝，本批不创建任何资源'
NORMAL='正常成功分支：整批创建；服务明确拒绝时本批不创建；超时结果未知，先查询原申请ID，状态确认前不重复提交。'

def case(text=BAD,source=SOURCE,*,field='behavior.result',stage='ingest'):
    ref=dict(source_id='SRC-condition',excerpt_id='EX-condition')
    ex=dict(source_id=ref['source_id'],id=ref['excerpt_id'],text=source,locator='合成第1段',source_hash='synthetic')
    p=dict(id='P-offline-condition',revision=0,items=[],questions=[],sources=[dict(id=ref['source_id'],excerpts=[ex])])
    value=copy.deepcopy(EXAMPLES[stage]);value.update(summary='本期云资源申请规则。',proposals=[],questions=[],findings=[],used_source_refs=[ref],limitations=[])
    item=dict(temp_id='TMP-resource',action='add',target_item_id=None,kind='requirement',
        title='云资源申请',statement='租户管理员在同一租户内每批最多20项；任一项配额不满足则整批拒绝，本批不创建任何资源。',
        applies_to='to_be',epistemic_status='reported',source_refs=[ref],related_refs=[],answer_refs=[],change_type='new',
        scope_evidence=[dict(state='change',quote='本期新增云资源申请规则。',source_ref=ref)],classification_reason='来源明确本期新增。',
        behavior=dict(actor='租户管理员',entry='',flow='',data='同租户每批最多20项',permissions='',result=NORMAL,
                      exceptions='服务明确拒绝时本批不创建且既有资源不变；响应丢失或超时是结果未知，先查询原申请ID，状态确认前不重复提交。'))
    value['proposals']=[item]
    if stage=='ingest':
        from app.ingest import schema
        value['product_context_proposal']={k:'' for k in schema()['properties']['product_context_proposal']['properties']}
        value['result']=dict(understanding='本期同租户云资源申请规则。',material_limits=[])
    if stage=='change':
        value['result']['changes']=[]
        value['result']['impacted_refs']=['TMP-resource']
        value['result']['unimpacted_refs']=[]
        value['result']['unresolved_effects']=[]
    if field.startswith('behavior.'):item['behavior'][field.split('.',1)[1]]=text
    elif field=='statement':item['statement']=text
    elif field=='result.understanding':value['result']['understanding']=text
    else:value[field]=text
    return p,value,[ex]

def test_frozen_real_response_must_not_infer_sufficient_creation():
    # Exact failed predicate and preserved rejection exception; full original
    # CALL replay is separately archived, never a dependency on ignored files.
    p,value,excerpts=case();before=copy.deepcopy((p,value,excerpts))
    with pytest.raises(Problem) as caught:validate_response(value,'ingest',p,excerpts)
    assert caught.value.code=='SEMANTIC_BLOCKED'
    assert 'behavior.result' in caught.value.message
    assert (p,value,excerpts)==before


@pytest.mark.parametrize('text',[
    BAD,'各项配额都满足就创建本批资源。','所有配额均充足便整批创建。',
    '配额足够即可创建资源。','全部额度充裕则创建资源。',
])
@pytest.mark.parametrize('field',['statement','behavior.result','behavior.flow','summary','result.understanding'])
def test_necessary_condition_is_not_sufficient_across_fields(text,field):
    p,v,ex=case(text,field=field);before=copy.deepcopy((p,v,ex))
    with pytest.raises(Problem) as error:validate_response(v,'ingest',p,ex)
    assert error.value.code=='SEMANTIC_BLOCKED' and field in error.value.message
    assert error.value.model_repair_allowed is False and (p,v,ex)==before


@pytest.mark.parametrize('text',[
    NORMAL,'正常成功分支：全部项配额满足则整批创建；服务明确拒绝时不创建；超时未知先查询原申请ID。',
    '服务正常受理且处理成功的分支，配额满足则整批创建；服务明确拒绝时不创建；超时未知先查询原申请ID。',
    '配额全部满足是必要条件，不保证创建成功；服务明确拒绝时不创建；超时未知不能断言创建或未创建。',
])
def test_explicit_normal_branch_and_separate_exceptions_are_allowed(text):
    p,v,ex=case(text);before=copy.deepcopy((p,v,ex));validate_response(v,'ingest',p,ex)
    assert (p,v,ex)==before


@pytest.mark.parametrize('text',[
    '支持一次提交多项云资源申请，预期减少操作量；尚无量化收益证据。',
    '本期目标是方便租户管理员申请资源，具体收益待验证。',
    '建议提高申请便利性，尚未采纳为交付义务。',
])
def test_qualitative_value_and_unadopted_suggestions_are_not_success_assertions(text):
    p,v,ex=case(NORMAL);v['product_context_proposal']['value']=text
    validate_response(v,'ingest',p,ex)


@pytest.mark.parametrize('text',[
    '超时即创建资源。','结果未知时本批不创建。','响应丢失时既有资源不变。',
    '服务明确拒绝时仍创建资源。',
])
def test_rejection_or_unknown_does_not_guarantee_business_effect(text):
    p,v,ex=case(text)
    with pytest.raises(Problem) as error:validate_response(v,'ingest',p,ex)
    assert error.value.code=='SEMANTIC_BLOCKED'


@pytest.mark.parametrize('text',[
    '超时结果未知，不能据此断言创建或未创建；保留输入并先查询原申请ID。',
    '超时后查询确认服务明确拒绝，本批不创建。',
    '超时后查询确认成功，本批资源创建成功。',
    '超时未知时保存草稿，不重复提交，先查询原申请ID。',
])
def test_unknown_no_assertion_or_confirmed_query_is_allowed(text):
    p,v,ex=case(text);validate_response(v,'ingest',p,ex)


def test_explicit_sufficient_source_is_allowed_only_in_its_scope():
    src='本期新增云资源申请规则。同一租户每批最多20项时，全部配额满足则整批创建资源。'
    p,v,ex=case('同一租户每批最多20项时，全部配额满足则整批创建资源。',source=src)
    v['proposals'][0]['behavior']['exceptions']=''
    validate_response(v,'ingest',p,ex)
    v['proposals'][0]['statement']='租户管理员申请云资源。'
    v['proposals'][0]['behavior']['result']='配额满足则创建资源。'
    with pytest.raises(Problem) as caught:validate_response(v,'ingest',p,ex)
    assert 'sufficient_scope_weakened' in caught.value.message


def test_explicit_sufficient_source_cannot_supply_other_object():
    src=SOURCE+'工单权限通过则创建工单。'
    p,v,ex=case('云资源配额满足则创建资源。',source=src)
    with pytest.raises(Problem):validate_response(v,'ingest',p,ex)
    p,v,ex=case('正常成功分支：创建资源。',source=src)
    validate_response(v,'ingest',p,ex)


def test_proposed_sufficient_source_is_not_adopted_evidence():
    src=SOURCE+'方案建议：全部配额满足则创建资源；尚待产品经理采纳。'
    p,v,ex=case(BAD,source=src)
    with pytest.raises(Problem):validate_response(v,'ingest',p,ex)


@pytest.mark.parametrize('prefix',['方案建议：','假设：','待确认：'])
def test_tentative_necessary_or_rejection_evidence_does_not_define_current_rule(prefix):
    basis=facts(prefix+'权限不通过则不保存记录。'+prefix+'服务明确拒绝时不保存记录。',
                location='source',origin='source_statement')
    assert basis and all(f['role']=='proposal' for f in basis)
    claim=facts('权限通过即可保存记录。',location='behavior.result')
    assert not issues(claim,basis)
    actual=facts('权限不通过则不保存记录。',location='source',origin='source_statement')
    assert issues(claim,actual)[0]['reason']=='necessary_is_not_sufficient'


def test_source_conflict_needs_one_jointly_bound_question_not_silent_choice():
    src=SOURCE+'产品经理已回答：全部配额满足则整批创建资源；服务明确拒绝时本批不创建。'
    p,v,ex=case(NORMAL,source=src)
    with pytest.raises(Problem) as caught:validate_response(v,'ingest',p,ex)
    assert 'source_conditions_conflict_requires_bound_question' in caught.value.message
    q=dict(temp_id='QTMP-success',topic_key='success-condition',question='配额满足且服务明确拒绝时是否创建，本期成功条件如何界定？',
           why='同一来源中充分成功与拒绝例外冲突；配额满足而服务拒绝时无法同时创建及不创建。',
           options=[],blocking=True,related_refs=['TMP-resource'],source_refs=v['used_source_refs'],blocking_stage=3,decision_points=[],target_question_id=None)
    v['questions']=[q];validate_response(v,'ingest',p,ex)
    q['related_refs']=[]
    with pytest.raises(Problem):validate_response(v,'ingest',p,ex)


def test_clear_current_source_does_not_require_artificial_questions():
    p,v,ex=case(NORMAL);assert not v['questions'];validate_response(v,'ingest',p,ex)


def test_unchanged_local_conflict_cannot_be_adopted_by_review_checkbox():
    p,v,ex=case();item=v['proposals'][0]
    item['behavior_review_required']=True
    from app.requirements import behavior_fingerprint,require_behavior_review
    item['behavior_review']={'content_hash':behavior_fingerprint(item)}
    before=copy.deepcopy(item)
    with pytest.raises(Problem) as caught:require_behavior_review(item)
    assert caught.value.code=='SEMANTIC_BLOCKED' and item==before
    item['behavior']['result']=NORMAL
    item['behavior_review']['content_hash']=behavior_fingerprint(item)
    require_behavior_review(item)


def test_pending_success_condition_remains_candidate_not_formal_rule():
    p,v,ex=case('待澄清：配额满足则创建资源。')
    validate_response(v,'ingest',p,ex)
    with pytest.raises(Problem):validate_item(v['proposals'][0])


def test_input_projection_has_roles_sources_quotes_and_no_schema_mutation(tmp_path):
    from app.provider import DEFAULT,assemble,request_schema
    from app.store import Store
    p,v,ex=case(NORMAL);p.update(Store(tmp_path).create('纯合成条件角色'))
    # Isolated function input only: fill synthetic sources in the returned dict.
    p['sources']=[dict(id='SRC-condition',title='纯合成来源',purpose='goal',excluded=False,parse_status='read',
                       failure_reason='',image_mime=None,excerpts=ex)]
    p['items']=[];p['documents']={};p['messages']=[]
    original=copy.deepcopy(request_schema('ingest'))
    messages,_,_=assemble(p,'ingest','',DEFAULT,tmp_path)
    context=json.loads(messages[1]['content'][0]['text']);header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    projection=context['result_condition_facts']
    assert projection['version']=='result-conditions-1' and projection['facts']
    assert {'necessary_condition','rejection_exception','unknown_result'}<={f['role'] for f in projection['facts']}
    assert all(f['source_refs'] and f['quote'] and f['location'] for f in projection['facts'])
    assert 'result_condition_facts' in header['response_boundary']['input_only']
    assert request_schema('ingest')==original


@pytest.mark.parametrize('cap',[2,None])
def test_semantic_conflict_is_not_a_paid_format_repair(tmp_path,monkeypatch,cap):
    import httpx
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.provider import DEFAULT
    requests=[];real_client=httpx.AsyncClient
    p,v,ex=case()
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json=dict(model='offline-mock',choices=[dict(message=dict(content=json.dumps(v,ensure_ascii=False)),finish_reason='stop')],
            usage=dict(prompt_tokens=11,completion_tokens=7,total_tokens=18)))
    transport=httpx.MockTransport(respond)
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=transport))
    app=create_app(tmp_path,access_token='offline-condition-login',env_path=tmp_path/'absent.env');app.state.provider.set_key(DEFAULT,'synthetic-condition-key')
    # This frozen HTTP response is the legacy canonical contract. Keep this
    # isolated Provider on that protocol so the test reaches semantic checking;
    # the real Provider class/default still uses the business draft protocol.
    monkeypatch.setattr(app.state.provider,'business_draft_protocol',None)
    real=app.state.store.create('纯合成公开条件拦截')
    with app.state.store.edit(real['id'],real['revision'],'offline fixture sources') as (real,_):
        real['sources']=[dict(id='SRC-condition',title='纯合成',purpose='goal',excluded=False,parse_status='read',failure_reason='',image_mime=None,excerpts=ex)]
    root='/api/projects/'+real['id']
    with TestClient(app) as client:
        csrf=client.post('/api/session/login',json={'key':'offline-condition-login'}).json()['csrf'];client.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
        before=client.get(root).json()
        body=dict(expected_revision=before['revision'],action='organize',max_calls=cap)
        plan=client.post(root+'/actions/plan',json=body).json();assert not plan['missing']
        response=client.post(root+'/actions',json=dict(body,plan_hash=plan['plan_hash'],idempotency_key=uuid.uuid4().hex,authorize=True));assert response.status_code==200
        for _ in range(200):
            task=client.get(root+'/actions').json()[-1]
            if task['status'] not in ('queued','running'):break
            time.sleep(.01)
        run=client.get(root+'/runs').json()[-1];after=client.get(root).json()
        assert len(requests)==task['calls']==run['calls']==1 and task['status']=='failed' and task['error']=='SEMANTIC_BLOCKED'
        assert run['repair_count']==0 and not run.get('result_applied') and len(run['attempts'])==1
        assert run['attempts'][0]['validation_result']=='SEMANTIC_BLOCKED'
        assert after['revision']==before['revision'] and after['items']==before['items'] and after['questions']==before['questions'] and after['documents']==before['documents']
        assert run['attempts'][0]['usage']['total_tokens']==18
        if cap is not None:assert task['request_budget']['used']==1 and task['request_budget']['limit']==2
        else:assert 'request_budget' not in task and run['max_calls'] is None


@pytest.mark.parametrize('stage',['ingest','clarify','change'])
def test_analysis_stage_summary_cannot_undo_normal_branch(stage):
    p,v,ex=case(NORMAL,stage=stage);v['summary']='所有配额都充足就创建资源。'
    with pytest.raises(Problem) as caught:validate_response(v,stage,p,ex)
    assert 'summary' in caught.value.message and caught.value.code=='SEMANTIC_BLOCKED'
    v['summary']='正常成功分支：整批创建资源；服务明确拒绝不创建；超时结果未知，查询原申请ID。'
    validate_response(v,stage,p,ex)


@pytest.mark.parametrize('source,claim',[
    ('权限不通过时拒绝保存记录。','权限通过即可保存记录。'),
    ('只有权限通过才允许保存记录。','权限通过就保存记录。'),
    ('校验不通过时不写入记录。','校验通过便写入记录。'),
])
def test_general_necessary_vs_sufficient_is_not_one_fixed_quota_sentence(source,claim):
    basis=facts(source,location='source',fallback_subject='记录',origin='source_statement')
    assertions=facts(claim,location='behavior.result',fallback_subject='记录')
    found=issues(assertions,basis)
    assert found and found[0]['reason']=='necessary_is_not_sufficient'
    good=facts('正常成功分支：'+claim,location='behavior.result',fallback_subject='记录')
    assert not issues(good,basis)


@pytest.mark.parametrize('bad_binding',['object','source','dimension','split'])
def test_source_conflict_question_binding_cannot_be_split_or_other_dimension(bad_binding):
    src=SOURCE+'产品经理已回答：配额满足则创建资源。'
    p,v,ex=case(NORMAL,source=src)
    q=dict(temp_id='QTMP-condition',topic_key='condition',question='配额满足而服务拒绝时是否创建，成功条件是什么？',
        why='同一对象存在冲突。',options=[],blocking=True,related_refs=['TMP-resource'],source_refs=v['used_source_refs'],blocking_stage=3,decision_points=[],target_question_id=None)
    v['questions']=[q]
    if bad_binding=='object':q['related_refs']=[]
    if bad_binding=='source':q['source_refs']=[]
    if bad_binding=='dimension':q['question']='成功提示应该显示什么颜色？'
    if bad_binding=='split':
        q['source_refs']=[]
        v['questions'].append(dict(q,temp_id='QTMP-source',related_refs=[],source_refs=v['used_source_refs']))
    with pytest.raises(Problem) as error:validate_response(v,'ingest',p,ex)
    assert error.value.code=='SEMANTIC_BLOCKED'


@pytest.mark.parametrize('kind',['mrd','prd'])
def test_public_document_compiler_reuses_same_cited_condition_check(tmp_path,kind):
    from tests.test_semantic_quality_regressions import project
    from tests.test_semantic_quality_boundaries import compile_explanation
    p=project(tmp_path,SOURCE);before=copy.deepcopy(p)
    with pytest.raises(Problem) as error:compile_explanation(p,BAD,kind)
    assert error.value.code=='SEMANTIC_BLOCKED' and 'explanations[0]' in error.value.message
    response=compile_explanation(p,NORMAL,kind)
    assert NORMAL in json.dumps(response,ensure_ascii=False) and p==before


def test_stored_condition_finding_has_only_actual_document_and_location(tmp_path):
    from tests.test_semantic_quality_regressions import project
    from tests.test_semantic_quality_boundaries import compile_explanation
    from app.workflow import apply_response
    from app.semantic_quality import current_document_checks
    p=project(tmp_path,SOURCE)
    for kind in ('mrd','prd'):apply_response(p,compile_explanation(p,NORMAL,kind))
    artifact=p['documents']['prd']
    section=next(s for s in artifact['content']['sections'] if any(b['kind']=='explanation' for b in s['blocks']))
    index=next(i for i,b in enumerate(section['blocks']) if b['kind']=='explanation')
    section['blocks'][index]['text']=BAD;before=copy.deepcopy(p)
    checks=[c for c in current_document_checks(p) if c['dimension']=='outcome_condition']
    assert checks and {c['document_type'] for c in checks}=={'prd'}
    assert all(c['location']==f"{artifact['id']}.{section['section_id']}.blocks[{index}]" for c in checks)
    assert all(c['quote'] in BAD for c in checks) and p==before


@pytest.mark.parametrize('text',[BAD,'超时即创建资源。'])
def test_actual_condition_review_is_accepted_without_inventing_additional_dimension(tmp_path,text):
    from tests.test_semantic_quality_regressions import project
    from tests.test_semantic_quality_boundaries import compile_explanation,review_for_checks
    from app.workflow import apply_response
    from app.semantic_quality import current_document_checks
    p=project(tmp_path,SOURCE);apply_response(p,compile_explanation(p,NORMAL,'prd'))
    artifact=p['documents']['prd']
    block=next(b for s in artifact['content']['sections'] for b in s['blocks'] if b['kind']=='explanation')
    block['text']=text;before=copy.deepcopy(p)
    checks=current_document_checks(p);assert len(checks)==1
    value=review_for_checks(p)
    check=checks[0]
    value['findings'][0]['message']=f'{check["check_id"]}：PRD {check["location"]} 原句“{check["quote"]}”有无条件结果断言错误。'
    assert validate_response(value,'review',p,p['sources'][0]['excerpts'])==value and p==before
