import copy
import pytest
from jsonschema import Draft202012Validator
from app import detail_semantics as s,detail_review_repair as repair,detail_business_scope as scope
from app.business_draft import adapt
from app.core import Problem,digest
from app.requirement_details import detail_issues,require_complete
from tests.test_detail_semantics import fixture,model_response
from tests.test_detail_semantic_workflow import generated_detail
from tests.test_detail_review_repair import patch_for


def reviewed_outcomes(plan,response):
    outcomes=s.validate(plan,response)
    if any(r['status']!='passed' for r in outcomes.values()):return outcomes
    packet=s.challenge_plan(plan,outcomes)
    return s.challenge_outcomes(plan,packet,dict(candidate_hash=packet['candidate_hash'],input_hash=packet['input_hash'],
        checked_field_ids=[f['id'] for f in packet['fields']],findings=[]),outcomes)


def sample(tmp_path,classification='technical_only',extra_unknowns=(),protocol=scope.PROTOCOL):
    p,local,excerpts,raw,_,_,_=fixture(tmp_path)
    raw['requirements'][0]['detail_contract']=generated_detail()
    raw['requirements'][0]['detail_contract']['fields'][0]['evidence']['default_value']=dict(status='unknown',reason='业务继承规则已给出，存储实现未定。')
    for section,prop,text in extra_unknowns:
        obj=raw['requirements'][0]['detail_contract'][section][0]
        obj[prop]=text;obj['evidence'][prop]=dict(status='unknown',reason=text)
    canonical,report=adapt(raw,p,excerpts,'clarify',local)
    plan=s.prepare(raw,canonical,report,excerpts,project=p,protocol_version=3,reference_encoding=s.REFERENCE_ENCODING,unknown_scope=s.UNKNOWN_SCOPE,default_scope=protocol)
    good=model_response(plan);check=next(c for c in good['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    for entry in check['default_scopes']:
        entry.update(classification=classification,known_business='合成来源所述当前字段业务规则。',unknown_technical='具体存储默认实现未指定。',source_quote_keys=[q['key'] for q in plan['quote_pool'] if q['kind']=='source'][:1])
    outcome=reviewed_outcomes(plan,good)['R1'];root=next(i for i in p['items'] if i['id']==plan['rows'][0]['requirement_id'])
    detail=copy.deepcopy(plan['rows'][0]['detail_contract']);candidate=copy.deepcopy(root)
    candidate.update(id='REQ-SCOPE-CANDIDATE',action='revise',target_item_id=root['id'],selection_status='candidate',detail_contract=detail)
    root['detail_contract']=copy.deepcopy(detail);p['items'].append(candidate)
    p['business_draft_reviews']=[dict(accepted=[dict(row_key='R1',requirement_id=root['id'],candidate_temp_id=candidate['id'],detail_semantic_review=outcome)])]
    scope.bind_stored_reviews(p['business_draft_reviews'][0],dict(proposals=[dict(candidate,temp_id=candidate['id'])]),{},p)
    return p,root,candidate,plan,good


def test_full_pass_only_and_raw_unknown_preserved(tmp_path):
    p,root,candidate,plan,good=sample(tmp_path);before=copy.deepcopy(p)
    assert not list(Draft202012Validator(s.wire_schema(plan)).iter_errors(good))
    assert any(i['code']=='DETAIL_PROPERTY_UNKNOWN' for i in detail_issues(root))
    require_complete(root,p)
    assert len(scope.projection(p))==1 and p==before
    assert root['detail_contract']['fields'][0]['evidence']['default_value']['status']=='unknown'
    p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']['status']='failed'
    with pytest.raises(Problem):require_complete(root,p)


@pytest.mark.parametrize('classification',['business_gap','unclear'])
def test_real_or_uncertain_business_gap_still_blocks(tmp_path,classification):
    p,root,_,_,_=sample(tmp_path,classification)
    assert scope.projection(p)==[]
    with pytest.raises(Problem):require_complete(root,p)


@pytest.mark.parametrize('fault',['detail','source_text','source_version','source_sha','source_identity','excluded','wrong_owner','tamper','forged_item','forged_top'])
def test_stale_and_client_forged_projection_do_not_authorize(tmp_path,fault):
    p,root,candidate,plan,good=sample(tmp_path)
    if fault=='detail':root['detail_contract']['fields'][0]['default_value']+='人工改动'
    elif fault=='source_text':p['sources'][0]['excerpts'][0]['text']+='changed'
    elif fault=='source_version':p['sources'][0]['version']='changed'
    elif fault=='source_sha':p['sources'][0]['sha256']='changed'
    elif fault=='source_identity':p['sources'][0]['id']='another'
    elif fault=='excluded':p['sources'][0]['excluded']=True
    elif fault=='wrong_owner':candidate['target_item_id']='another'
    elif fault=='tamper':p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']['default_scope_review']['properties'][0]['known_business']='changed'
    else:
        fake=scope.projection(p);p['business_draft_reviews']=[]
        (root if fault=='forged_item' else p)['detail_unknown_scope_reviews']=fake
    assert scope.current_reviews(p,root)==[]
    with pytest.raises(Problem):require_complete(root,p)


def test_unrelated_new_material_and_documents_do_not_revoke(tmp_path):
    p,root,_,_,_=sample(tmp_path);before=scope.current_reviews(p,root)
    p['revision']+=1;p['sources'].append(dict(id='UNRELATED',excerpts=[dict(id='X',text='irrelevant')]))
    p['documents']={'prd':{'new':'unrelated'}}
    p['questions'].append(dict(id='Q-unrelated',answer='保存的无关回答'))
    assert scope.current_reviews(p,root)==before
    require_complete(root,p)


def test_other_property_unknown_is_never_exempt(tmp_path):
    p,root,_,_,_=sample(tmp_path)
    root['detail_contract']['fields'][0]['evidence']['meaning']=dict(status='unknown',reason='真实业务含义未定')
    with pytest.raises(Problem) as caught:require_complete(root,p)
    assert any(i['path'].endswith('.meaning') for i in caught.value.issues)


@pytest.mark.parametrize('fault',['missing','empty_quote','wrong_kind','duplicate','other_property','empty_known','empty_technical'])
def test_strict_scope_protocol_and_same_slot_patch(tmp_path,fault):
    _,_,_,plan,good=sample(tmp_path);bad=copy.deepcopy(good)
    check=next(c for c in bad['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    if fault=='missing':check.pop('default_scopes')
    elif fault=='duplicate':check['default_scopes']*=2
    else:
        entry=check['default_scopes'][0]
        if fault=='empty_quote':entry['source_quote_keys']=[]
        elif fault=='wrong_kind':entry['source_quote_keys']=['H0001']
        elif fault=='other_property':entry['property_key']='P9999'
        elif fault=='empty_known':entry['known_business']=''
        else:entry['unknown_technical']=''
    with pytest.raises(Problem):s.validate(plan,bad)
    packet=repair.prepare(plan,bad)
    assert packet and len(packet['slots'])==1 and packet['slots'][0]['identity']==dict(scope='detail',dimension='field_conditions')
    merged,outcomes,audit=repair.apply(plan,packet,patch_for(packet,good))
    assert merged==good and outcomes['R1']['status']=='passed' and audit['preserved_slots']


def test_prewrite_and_renderer_preserve_unknown_and_scope_notice(tmp_path):
    from app.prewrite import structural_findings
    from app.detail_rendering import detail_nodes
    p,root,_,_,_=sample(tmp_path)
    assert not [f for f in structural_findings(p) if f.get('code')=='DETAIL_PROPERTY_UNKNOWN' and root['id'] in f['related_refs']]
    nodes=detail_nodes(root,'fields',scope_reviews=scope.projection(p))
    text=str(nodes)
    assert '技术未定，业务确认保留' in text and '具体存储默认实现未指定' in text
    assert root['detail_contract']['fields'][0]['default_value'] in text
    assert root['detail_contract']['fields'][0]['evidence']['default_value']['reason'] in text
    assert root['detail_contract']['fields'][0]['evidence']['default_value']['status']=='unknown'


def test_saved_human_exact_binding_and_draft_never_authorizes(tmp_path):
    p,root,_,plan,_=sample(tmp_path,extra_unknowns=TECHNICAL_UNKNOWNS)
    question=dict(id='Q-scope',question='该字段本期业务规则是什么？',answer='沿用已有业务值；存储实现尚未决定。',status='answered',related_refs=[root['id']],understanding_status='saved_unreviewed')
    p['questions'].append(question)
    material=dict(statement_id='Q-scope:answer',kind='saved_answer',row_key='R1',text=question['answer'],question_id=question['id'],question_text=question['question'],related_refs=question['related_refs'],decision_status='saved_unreviewed')
    plan['human_statements']=[material];plan['quote_pool']=s.quote_pool(plan);plan['quote_pool_hash']=digest(plan['quote_pool']);plan['default_scope_review']=scope.prepare_bindings(plan,p)
    response=model_response(plan);entry=next(c for c in response['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')['default_scopes']
    for decision in entry:
        decision.update(classification='technical_only',known_business='已保存回答的合成业务范围',unknown_technical='存储实现仍未决定',source_quote_keys=[],human_quote_keys=[q['key'] for q in plan['quote_pool'] if q['kind']=='human'])
    outcome=reviewed_outcomes(plan,response)['R1'];p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']=outcome
    candidate=next(i for i in p['items'] if i['id']=='REQ-SCOPE-CANDIDATE')
    scope.bind_stored_reviews(p['business_draft_reviews'][0],dict(proposals=[dict(candidate,temp_id=candidate['id'])]),{},p)
    assert len(scope.current_reviews(p,root)[0]['properties'])==7
    # Normal saved-answer application metadata does not change the cited words.
    question['understanding_status']='applied';assert scope.current_reviews(p,root)
    question['answer']+=' changed';assert not scope.current_reviews(p,root)
    question['answer']=material['text'];question['status']='open';assert not scope.current_reviews(p,root)
    material['kind']='current_user_words';plan['quote_pool']=s.quote_pool(plan);plan['quote_pool_hash']=digest(plan['quote_pool']);plan['default_scope_review']=scope.prepare_bindings(plan,p)
    assert not plan['default_scope_review']['allowed_saved_human_quote_keys']
    with pytest.raises(Problem):s.validate(plan,response)


def test_confirmation_preview_commit_snapshot_and_unknown_value_survive(tmp_path):
    from app.requirement_confirmation import build_preview,apply_preview,FIELDS,linked_acceptance,confirmation_current
    from app.store import Store
    p,root,candidate,_,_=sample(tmp_path);p['questions']=[]
    payload=dict(expected_revision=p['revision'],requirement_id=root['id'],candidate_id=candidate['id'],statement=root['statement'],
        behavior={k:root.get('behavior',{}).get(k,'') for k in FIELDS},detail_contract=copy.deepcopy(root['detail_contract']),
        acceptance_items=[dict(id=i['id'],statement=i['statement']) for i in linked_acceptance(p,root)])
    view=build_preview(p,payload)
    assert view['detail_scope_snapshot'] and view['after']['detail_contract']==root['detail_contract']
    stale=copy.deepcopy(p);stale['sources'][0]['version']='new-version'
    with pytest.raises(Problem):apply_preview(stale,view,Store(tmp_path/'stale-store'))
    p['sources'].append(dict(id='UNRELATED',excerpts=[]))
    apply_preview(p,view,Store(tmp_path/'commit-store'))
    selected=next(i for i in p['items'] if i['id']==root['id'])
    assert confirmation_current(p,selected)
    assert selected['detail_contract']==view['after']['detail_contract']
    assert selected['detail_contract']['fields'][0]['evidence']['default_value']['status']=='unknown'
    require_complete(selected,p)
    p['sources'][0]['excerpts'][0]['text']+=' changed'
    assert not confirmation_current(p,selected)
    with pytest.raises(Problem):require_complete(selected,p)


def test_client_confirmation_payload_scope_claim_cannot_self_authorize(tmp_path):
    from app.requirement_confirmation import build_preview,FIELDS,linked_acceptance
    p,root,_,_,_=sample(tmp_path);forged=scope.projection(p);p['business_draft_reviews']=[];p['questions']=[]
    payload=dict(expected_revision=p['revision'],requirement_id=root['id'],statement=root['statement'],behavior={k:root.get('behavior',{}).get(k,'') for k in FIELDS},
        detail_contract=copy.deepcopy(root['detail_contract']),detail_unknown_scope_reviews=forged,
        acceptance_items=[dict(id=i['id'],statement=i['statement']) for i in linked_acceptance(p,root)])
    with pytest.raises(Problem) as caught:build_preview(p,payload)
    assert any(i['code']=='DETAIL_PROPERTY_UNKNOWN' for i in caught.value.issues)


def test_any_failed_review_does_not_produce_scope_authority(tmp_path):
    p,root,_,plan,good=sample(tmp_path)
    check=next(c for c in good['rows'][0]['checks'] if c['dimension']=='operation_results')
    check.update(verdict='unsupported',affected_keys=check['candidate_keys'],correction='合成反例须纠正本项业务结果。')
    outcome=s.validate(plan,good)['R1'];assert outcome['status']=='failed' and 'default_scope_review' not in outcome
    p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']=outcome
    assert not scope.current_reviews(p,root)


def test_project_api_ignores_forged_topfield_and_exposes_current_server_projection(tmp_path):
    from app.main import create_app
    from fastapi.testclient import TestClient
    from tests.test_document_publication import login
    p,root,_,_,_=sample(tmp_path);p['detail_unknown_scope_reviews']=[{'forged':True}]
    app=create_app(tmp_path,access_token='synthetic',env_path=tmp_path/'absent.env');store=app.state.store
    with store.edit(p['id'],p['revision'],'synthetic scope',bump=False) as (current,_):current.update(p)
    with TestClient(app) as client:
        login(client);value=client.get('/api/projects/'+p['id']).json()
        assert value['detail_unknown_scope_reviews']==scope.projection(store.get(p['id']))
        assert all('forged' not in r for r in value['detail_unknown_scope_reviews'])
        current=store.get(p['id'])
        with store.edit(p['id'],current['revision'],'remove trusted review',bump=False) as (edited,_):edited['business_draft_reviews']=[]
        value=client.get('/api/projects/'+p['id']).json();assert 'detail_unknown_scope_reviews' not in value


def test_docx_md_actual_exports_preserve_unknown_scope_and_reason(tmp_path):
    import io,asyncio
    from docx import Document
    from app.requirement_confirmation import row_fingerprint
    from app.exports import document_files
    from app.document_reader import export_readiness
    from tests.test_detail_document import compile_fixture
    p,root,_,_,_=sample(tmp_path,extra_unknowns=TECHNICAL_UNKNOWNS);p['questions']=[];p['product_context']=dict(product='合成储能云平台',scope_ids=[root['id']])
    root['selection_status']='selected'
    root['requirement_confirmation']=dict(schema='requirement-confirmation-1',actor='authenticated_local_user',content_hash=row_fingerprint(p,root))
    compile_fixture(p)
    assert any(i['code']=='DETAIL_SCOPE_VERSION_INVALID' for i in export_readiness(p,'prd')['issues'])
    # Same server-derived snapshot attached by the document Workflow path.
    p['documents']['prd']['detail_unknown_scope_reviews']=scope.projection(p)
    assert export_readiness(p,'prd')['ready']
    files=asyncio.run(document_files(p,'prd'))
    md=files['PRD.md'];md=md.decode('utf-8') if isinstance(md,bytes) else md
    word=Document(io.BytesIO(files['PRD.docx']));text='\n'.join([*(x.text for x in word.paragraphs),*(c.text for t in word.tables for row in t.rows for c in row.cells)])
    for value in [md,text]:
        assert value.count('技术未定，业务确认保留')>=7
        for section,prop,original in TECHNICAL_UNKNOWNS:assert original in value
        assert '技术未定，业务确认保留' in value
        assert root['detail_contract']['fields'][0]['default_value'] in value
        assert root['detail_contract']['fields'][0]['evidence']['default_value']['reason'] in value
    p['sources'][0]['version']='changed'
    assert not export_readiness(p,'prd')['ready']
    with pytest.raises(Problem):asyncio.run(document_files(p,'prd'))


@pytest.mark.parametrize('mode,limit,expected',[('pass',7,5),('budget',3,3),('cancel',7,4)])
def test_real_provider_mock_http_scope_review_patch_budget_cancel(tmp_path,monkeypatch,mode,limit,expected):
    import asyncio,json,httpx
    from app.provider import Provider,DEFAULT,origin
    from app.workflow import Workflow
    from app.core import dumps
    from app.business_draft import PROTOCOL as BUSINESS
    from tests.provider_transport import reply
    from tests.test_business_draft import draft_row,packet
    from tests.test_requirement_recommendation import project,target
    bodies=[];good_reviews=[];real_client=httpx.AsyncClient
    async def run():
        store,p=project(tmp_path);provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
        workflow=Workflow(store,provider);config=dict(workflow.config('clarify'),max_calls=limit)
        assert config['detail_default_scope_protocol']==scope.PROTOCOL
        async def respond(request):
            body=json.loads(request.content);bodies.append(body);messages=body.get('messages',body.get('input'))
            header=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);payload=json.loads(messages[1]['content'][0]['text'])
            if header.get('grounding_protocol'):
                result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                    checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
            elif header.get('response_protocol')==BUSINESS:
                row=draft_row();row['detail_contract']=generated_detail();row['detail_contract']['fields'][0]['evidence']['default_value']=dict(status='unknown',reason='业务范围已明，技术实现未定。');row['detail_contract']['fields'][0]['acceptance_refs']=['A1'];row['detail_contract']['fields'][0]['evidence']['acceptance_refs']=dict(status='proposal',reason='合成关联本行新验收');result=packet(row)
            else:
                assert request.url.path.endswith('/responses') and body['text']['format']['schema']==header['schema']
                assert body['reasoning']['effort']=='high' and body['max_output_tokens']==65536
                if header.get('response_protocol')==repair.PROTOCOL:
                    rp=payload['repair_packet'];assert len(rp['slots'])==1 and rp['slots'][0]['identity']==dict(scope='detail',dimension='field_conditions')
                    result=patch_for(rp,good_reviews[-1]);assert not list(Draft202012Validator(header['schema']).iter_errors(result))
                    if mode=='cancel':store.update_run(p['id'],task['id'],status='cancelled')
                else:
                    result=model_response(payload);check=next(c for c in result['rows'][0]['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
                    check['default_scopes'][0].update(classification='technical_only',known_business='合成已知业务范围',unknown_technical='合成未定存储实现',source_quote_keys=[q['key'] for q in payload['quote_pool'] if q['kind']=='source'][:1])
                    assert not list(Draft202012Validator(header['schema']).iter_errors(result));good_reviews.append(copy.deepcopy(result));check.pop('default_scopes')
            return reply(request,dumps(result))
        monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await workflow.tasks[task['id']];saved=store.get_record(p['id'],task['id'],'run')
        assert saved['calls']==saved['request_budget']['used']==len(bodies)==expected
        final=store.get(p['id'])
        if mode=='pass':
            assert saved['business_draft_review']['detail_semantic_reviews']['R1']['status']=='passed'
            assert scope.projection(final)
            candidate=next(i for i in final['items'] if i['id']==scope.projection(final)[0]['candidate_id'])
            assert candidate['detail_contract']['fields'][0]['evidence']['default_value']['status']=='unknown'
            semantic=saved['business_draft_review']['detail_semantic_reviews']['R1']
            assert semantic['candidate_hash']!=digest(candidate['detail_contract'])
            stored=final['business_draft_reviews'][-1]['accepted'][0]['detail_semantic_review']['default_scope_review']
            assert stored['storage_binding']['reviewed_detail_hash']==semantic['candidate_hash']
            assert stored['storage_binding']['stored_detail_hash']==digest(candidate['detail_contract'])
            assert stored['review_hash']==semantic['default_scope_review']['review_hash']
        else:
            assert not scope.projection(final)
            if mode=='cancel':assert saved['status']=='cancelled' and not saved.get('result_applied')
        records=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert records[2]['validation_result']=='DETAIL_REVIEW_INVALID'
        assert records[2]['final_output'] and records[2]['review_entry_diagnostics']['slots']
        if mode=='pass':assert records[3]['review_entry_repair']['full_strict_validation']=='passed'
    asyncio.run(run())


def test_review_context_only_current_normative_scope_and_unchanged_output_contract(tmp_path):
    import json
    from app.request_context import assemble
    from app.provider import DEFAULT
    from app.prewrite import review_project
    p,root,_,_,_=sample(tmp_path);root['selection_status']='selected';p['questions']=[]
    config=dict(DEFAULT,budget_mode='unlimited');before=copy.deepcopy(config)
    messages,_,_=assemble(review_project(p),'review','',config,tmp_path)
    context=json.loads(messages[1]['content'][0]['text']);header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    assert context['detail_unknown_scope_reviews']==scope.current_reviews(p,root)
    assert '不强迫ready' in context['review_scope']['instruction'] and config==before
    for fault in ('source','unselected','forged'):
        changed=copy.deepcopy(p)
        if fault=='source':changed['sources'][0]['version']='changed'
        elif fault=='unselected':next(i for i in changed['items'] if i['id']==root['id'])['selection_status']='candidate'
        else:changed['business_draft_reviews']=[];changed['detail_unknown_scope_reviews']=context['detail_unknown_scope_reviews']
        other,_,_=assemble(review_project(changed),'review','',config,tmp_path)
        assert 'detail_unknown_scope_reviews' not in json.loads(other[1]['content'][0]['text'])
        assert json.loads(other[0]['content'].split('可信任务头：',1)[1])['schema']==header['schema']


def test_storage_binding_rejects_changes_beyond_existing_id_substitution(tmp_path):
    p,root,candidate,plan,good=sample(tmp_path)
    review=p['business_draft_reviews'][0];record=review['accepted'][0]['detail_semantic_review']['default_scope_review']
    record.pop('storage_binding');record['record_hash']=digest({k:v for k,v in record.items() if k!='record_hash'})
    candidate['detail_contract']['fields'][0]['default_value']='different'
    original=dict(candidate,temp_id=candidate['id'],detail_contract=copy.deepcopy(plan['rows'][0]['detail_contract']))
    with pytest.raises(Problem):scope.bind_stored_reviews(review,dict(proposals=[original]),{},p)
    assert not scope.current_reviews(p,root)


@pytest.mark.parametrize('fault',['failed','tampered','gap'])
def test_latest_matching_review_never_falls_back(tmp_path,fault):
    p,root,_,_,_=sample(tmp_path)
    latest=copy.deepcopy(p['business_draft_reviews'][0])
    semantic=latest['accepted'][0]['detail_semantic_review']
    if fault=='failed':semantic['status']='failed'
    elif fault=='tampered':semantic['default_scope_review']['record_hash']='bad'
    else:
        record=semantic['default_scope_review'];record['properties'][0]['classification']='business_gap'
        record['record_hash']=s.digest({k:v for k,v in record.items() if k!='record_hash'})
    p['business_draft_reviews'].append(latest)
    assert scope.current_reviews(p,root)==[]
    assert any(i['code']=='DETAIL_PROPERTY_UNKNOWN' for i in detail_issues(root,p))


def test_add_self_owner_projection_survives_normal_confirmation(tmp_path):
    from app.requirement_confirmation import build_preview,apply_preview,FIELDS,linked_acceptance,confirmation_current
    from app.store import Store
    p,root,candidate,_,_=sample(tmp_path);p['questions']=[]
    candidate.update(action='add',target_item_id=None)
    p['business_draft_reviews'][0]['accepted'][0]['requirement_id']=candidate['id']
    for item in p['items']:
        if item['kind']=='acceptance':item['related_refs']=[candidate['id'] if r==root['id'] else r for r in item.get('related_refs',[])]
    assert scope.current_reviews(p,candidate)[0]['candidate_id']==candidate['id']
    payload=dict(expected_revision=p['revision'],requirement_id=candidate['id'],statement=candidate['statement'],
        behavior={k:candidate.get('behavior',{}).get(k,'') for k in FIELDS},detail_contract=copy.deepcopy(candidate['detail_contract']),
        acceptance_items=[dict(id=i['id'],statement=i['statement']) for i in linked_acceptance(p,candidate)])
    view=build_preview(p,payload);apply_preview(p,view,Store(tmp_path/'add-store'))
    selected=next(i for i in p['items'] if i['id']==candidate['id'])
    assert selected['selection_status']=='selected' and not selected.get('target_item_id')
    assert scope.current_reviews(p,selected) and confirmation_current(p,selected)
    require_complete(selected,p)


def test_local_prd_http_chain_snapshots_current_scope_without_model_calls(tmp_path):
    import io,zipfile
    from docx import Document
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.requirement_confirmation import FIELDS,linked_acceptance
    from app.prewrite import apply_result
    from tests.test_prewrite_consistency import review
    from tests.test_document_publication import login
    p,root,candidate,_,_=sample(tmp_path)
    p.update(questions=[],workflow_mode='shared-draft',product_context=dict(product='合成储能云平台',scope_ids=[root['id']]))
    app=create_app(tmp_path,access_token='synthetic',env_path=tmp_path/'absent.env');store=app.state.store
    with store.edit(p['id'],p['revision'],'synthetic local scope fixture',bump=False) as (current,_):current.update(p)
    pid=p['id'];rid=root['id']
    with TestClient(app) as client:
        login(client)
        body=dict(expected_revision=p['revision'],requirement_id=rid,candidate_id=candidate['id'],statement=root['statement'],
            behavior={k:root.get('behavior',{}).get(k,'') for k in FIELDS},detail_contract=copy.deepcopy(root['detail_contract']),
            acceptance_items=[dict(id=i['id'],statement=i['statement']) for i in linked_acceptance(p,root)])
        response=client.post(f'/api/projects/{pid}/requirement-confirmation/preview',json=body)
        assert response.status_code==200,response.text
        view=response.json()
        response=client.post(f'/api/projects/{pid}/requirement-confirmation/commit',json=dict(expected_revision=p['revision'],
            preview_id=view['preview_id'],preview_hash=view['preview_hash'],idempotency_key='local-scope-confirm-001',review_confirmed=True))
        assert response.status_code==200,response.text
        p=store.get(pid)
        with store.edit(pid,p['revision'],'synthetic prewrite',bump=False) as (current,_):apply_result(current,review(current),'RUN-LOCAL-SCOPE')
        p=store.get(pid)
        response=client.post(f'/api/projects/{pid}/documents/prd/local-preview',json=dict(expected_revision=p['revision']))
        assert response.status_code==200,response.text
        response=client.post(f'/api/projects/{pid}/documents/prd/local-review',json=dict(expected_revision=p['revision'],
            idempotency_key='local-scope-prd-001',review_draft_confirmed=True,preview_hash=response.json()['preview_hash']))
        assert response.status_code==200,response.text
        current=store.get(pid);artifact=current['documents']['prd'];root=next(i for i in current['items'] if i['id']==rid)
        assert artifact['detail_unknown_scope_reviews']==scope.current_reviews(current,root)
        assert artifact['model_calls']==0 and not store.records(pid,'run')
        assert root['detail_contract']==body['detail_contract']
        for fmt in ['md','docx','zip']:
            response=client.get(f'/api/projects/{pid}/documents/prd/{fmt}?mode=review')
            assert response.status_code==200,response.text[:400]
            if fmt=='docx':
                doc=Document(io.BytesIO(response.content));text='\n'.join(c.text for t in doc.tables for row in t.rows for c in row.cells)
            elif fmt=='zip':
                z=zipfile.ZipFile(io.BytesIO(response.content));text=z.read(next(n for n in z.namelist() if n.endswith('.md'))).decode('utf-8')
            else:text=response.content.decode('utf-8')
            assert '技术未定，业务确认保留' in text
            assert root['detail_contract']['fields'][0]['default_value'] in text
            assert root['detail_contract']['fields'][0]['evidence']['default_value']['reason'] in text
        with store.edit(pid,current['revision'],'changed cited source') as (changed,_):changed['sources'][0]['version']='changed'
        assert scope.current_reviews(store.get(pid),root)==[]
        response=client.get(f'/api/projects/{pid}/documents/prd/md?mode=formal')
        assert response.status_code==409,response.text
        assert not store.records(pid,'run')


@pytest.mark.parametrize('status',['failed','pending'])
def test_unaccepted_scope_review_is_preserved_without_storage_binding(tmp_path,status):
    p,root,candidate,plan,good=sample(tmp_path)
    review=p['business_draft_reviews'][0];semantic=review['accepted'][0]['detail_semantic_review']
    record=semantic['default_scope_review'];record.pop('storage_binding')
    record['record_hash']=digest({k:v for k,v in record.items() if k!='record_hash'})
    semantic.update(status=status,findings=[dict(explanation='来源仍有缺口')])
    before=copy.deepcopy(review)
    scope.bind_stored_reviews(review,dict(proposals=[]),{},dict(items=[]))
    assert review==before and 'storage_binding' not in record
    assert scope.current_reviews(p,root)==[]
    with pytest.raises(Problem):require_complete(root,p)
    semantic.update(status='passed',findings=[])
    with pytest.raises(Problem,match='缺少对应已保存候选'):
        scope.bind_stored_reviews(review,dict(proposals=[]),{},dict(items=[]))


def test_workflow_challenge_rejection_with_scope_record_keeps_only_independent_content(tmp_path):
    import asyncio,json
    from app.workflow import Workflow
    from app.core import dumps
    from tests.test_detail_semantic_workflow import SemanticProvider
    from tests.test_business_draft import packet,draft_row,PROTOCOL
    from tests.test_requirement_recommendation import project,target
    async def run():
        class Provider(SemanticProvider):
            async def request(self,config,messages,evidence=None):
                evidence.before_send(config);self.received.append(copy.deepcopy(messages))
                header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
                payload=json.loads(messages[1]['content'][0]['text'])
                if header.get('response_protocol')==PROTOCOL and not header.get('grounding_protocol'):
                    row=draft_row();row['detail_contract']=generated_detail()
                    row['detail_contract']['fields'][0]['evidence']['default_value']=dict(status='unknown',reason='业务默认未知')
                    result=packet(row)
                elif header.get('grounding_protocol') and payload['fields'][0]['path'][0]=='requirements':
                    result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],
                        checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
                elif header.get('grounding_protocol'):
                    field=next(f for f in payload['fields'] if f['path'][1]=='detail_contract.fields.F1.required')
                    result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],checked_field_ids=[f['id'] for f in payload['fields']],
                        findings=[dict(id=field['id'],quote=field['text'],reason='仍遗漏来源适用前提',sources=[])])
                else:result=model_response(payload)
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
                return result,dict(finish_reason='stop',usage={'total_tokens':10})
        store,p=project(tmp_path);provider=Provider();workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=4)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await workflow.tasks[task['id']];run=store.get_record(p['id'],task['id'],'run');final=store.get(p['id'])
        assert run['calls']==run['request_budget']['used']==len(provider.received)==4
        assert run.get('result_applied') and run.get('error')!='DETAIL_SCOPE_BINDING_INVALID'
        outcome=run['business_draft_review']['detail_semantic_reviews']['R1']
        assert outcome['status']=='failed' and outcome['findings'] and outcome['default_scope_review']
        assert outcome['source_challenge']['status']=='blocked' and outcome['code']=='BUDGET_EXHAUSTED'
        assert final['items'][:len(p['items'])]==p['items']
        new=final['items'][len(p['items']):];assert new and not any(i.get('detail_contract') for i in new)
        saved=final['business_draft_reviews'][-1]['accepted'][0]['detail_semantic_review']
        assert saved['status']=='failed' and saved['findings'] and 'storage_binding' not in saved['default_scope_review']
        assert not scope.projection(final)
    asyncio.run(run())


TECHNICAL_UNKNOWNS = (
    ('fields','constraints','资料未说明已有字段的序列化格式。'),
    ('fields','editability','资料未说明额外编辑入口，本候选不新增编辑行为。'),
    ('fields','required','资料未说明底层存储列非空约束，不改变已知业务字段。'),
    ('fields','source','资料未说明读取已知业务值的接口。'),
    ('interactions','cancel','资料未说明额外取消机制，不新增取消业务。'),
    ('interactions','pending','资料未说明同步或异步实现，不新增恢复、超时或重试业务。'),
)


def test_v2_bounded_technical_properties_normal_confirmation_and_raw_preservation(tmp_path):
    from app.requirement_confirmation import build_preview,apply_preview,FIELDS,linked_acceptance,confirmation_current
    from app.store import Store
    p,root,candidate,plan,good=sample(tmp_path,extra_unknowns=TECHNICAL_UNKNOWNS)
    before=copy.deepcopy(root['detail_contract']);p['questions']=[]
    assert len(scope.projection(p)[0]['properties'])==7
    assert scope.projection(p)[0]['protocol']==scope.PROTOCOL
    assert len({i['path'] for i in detail_issues(root) if i['code']=='DETAIL_PROPERTY_UNKNOWN'})==7
    require_complete(root,p)
    payload=dict(expected_revision=p['revision'],requirement_id=root['id'],candidate_id=candidate['id'],statement=root['statement'],
        behavior={k:root.get('behavior',{}).get(k,'') for k in FIELDS},detail_contract=copy.deepcopy(before),
        acceptance_items=[dict(id=i['id'],statement=i['statement']) for i in linked_acceptance(p,root)])
    preview=build_preview(p,payload);apply_preview(p,preview,Store(tmp_path/'commit'))
    assert confirmation_current(p,root) and root['detail_contract']==before
    assert all(obj['evidence'][prop]['status']=='unknown' for section,prop,_ in TECHNICAL_UNKNOWNS for obj in [root['detail_contract'][section][0]])


@pytest.mark.parametrize('protocol',[scope.LEGACY_PROTOCOL])
def test_legacy_scope_never_authorizes_new_unknown_properties(tmp_path,protocol):
    p,root,_,plan,_=sample(tmp_path,extra_unknowns=TECHNICAL_UNKNOWNS,protocol=protocol)
    assert len(scope.eligible(plan,plan['rows'][0]))==1
    assert [x['path'].split('.')[-1] for x in scope.projection(p)[0]['properties']]==['default_value']
    assert 'protocol' not in scope.projection(p)[0]
    with pytest.raises(Problem):require_complete(root,p)


@pytest.mark.parametrize('section,prop,text',[
    ('fields','required','资料未说明本期提交缺少资产编号是否应拒绝。'),
    ('fields','editability','资料未说明本期是否允许改写已生效归属。'),
    ('interactions','cancel','资料未说明已受理操作取消是否撤销业务生效结果。'),
    ('interactions','pending','资料未说明处理中是否允许另一人执行互斥操作。'),
])
@pytest.mark.parametrize('classification',['business_gap','unclear'])
def test_observable_business_unknown_not_exempt(tmp_path,section,prop,text,classification):
    p,root,_,_,_=sample(tmp_path,classification,extra_unknowns=[(section,prop,text)])
    assert not scope.current_reviews(p,root)
    with pytest.raises(Problem):require_complete(root,p)
    assert root['detail_contract'][section][0][prop]==text


@pytest.mark.parametrize('section,prop',[('fields','meaning'),('interactions','actor'),('interactions','effects')])
def test_other_unknown_business_properties_are_not_classification_candidates(tmp_path,section,prop):
    p,root,_,plan,_=sample(tmp_path,extra_unknowns=[(section,prop,'资料未说明本期业务决定。')])
    assert len(scope.eligible(plan,plan['rows'][0]))==1
    with pytest.raises(Problem):require_complete(root,p)


@pytest.mark.parametrize('fault',['source_version','source_text','pending','candidate','protocol'])
def test_v2_new_properties_lose_authority_on_binding_change(tmp_path,fault):
    p,root,candidate,plan,good=sample(tmp_path,extra_unknowns=TECHNICAL_UNKNOWNS)
    if fault=='source_version':p['sources'][0]['version']='new'
    elif fault=='source_text':p['sources'][0]['excerpts'][0]['text']+='changed'
    elif fault=='candidate':candidate['detail_contract']['fields'][0]['source']+='changed'
    elif fault=='protocol':
        record=p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']['default_scope_review']
        record['protocol']='unrecognized';record['record_hash']=digest({k:v for k,v in record.items() if k!='record_hash'})
    else:
        # A pending review cannot retain earlier technical authority.
        p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']['status']='pending'
    assert not scope.current_reviews(p,root)
    with pytest.raises(Problem):require_complete(root,p)



def test_v2_normal_refinement_api_accepts_reviewed_technical_unknowns(tmp_path):
    from app.store import Store
    from app.requirement_confirmation import FIELDS,linked_acceptance
    from tests.test_refinement_confirmation import api
    p,root,candidate,_,_=sample(tmp_path,extra_unknowns=TECHNICAL_UNKNOWNS)
    p['questions']=[];store=Store(tmp_path)
    with store.edit(p['id'],p['revision'],'synthetic reviewed scope',bump=False) as (current,_):current.update(p)
    before=copy.deepcopy(root['detail_contract'])
    body=dict(expected_revision=p['revision'],idempotency_key='technical-scope-confirm-1',review_confirmed=True,
        context=dict(values=dict(intent='值班人员协作查看云平台运行信息',users='值班人员',change_scope='新增云平台列表查看'),
            field_states={field:dict(status='known') for field in ('intent','users','change_scope')},
            scope_profile=dict(scenario='new_product',dependencies='unknown'),scope_ids=[root['id']]),
        rows=[dict(requirement_id=root['id'],candidate_id=candidate['id'],statement=root['statement'],
            behavior={k:root.get('behavior',{}).get(k,'') for k in FIELDS},change_type='new',detail_contract=before,
            acceptance_items=[dict(id=i['id'],statement=i['statement']) for i in linked_acceptance(p,root)])],answers=[])
    with api(store) as client:
        response=client.post('/api/projects/'+p['id']+'/refinement-confirmation',json=body)
        assert response.status_code==200,response.text
        assert response.json()['confirmed'],response.json()
    saved=store.get(p['id']);selected=next(i for i in saved['items'] if i['id']==root['id'])
    assert selected['detail_contract']==before
    assert len(scope.current_reviews(saved,selected)[0]['properties'])==7
    assert saved['revision']==p['revision']+1


@pytest.mark.parametrize('protocol',[None,scope.LEGACY_PROTOCOL,'unknown',scope.PROTOCOL])
def test_rendered_technical_notes_obey_record_protocol(tmp_path,protocol):
    from app.detail_rendering import detail_nodes
    p,root,_,_,_=sample(tmp_path,extra_unknowns=TECHNICAL_UNKNOWNS)
    projection=scope.projection(p)
    if protocol is None:projection[0].pop('protocol')
    else:projection[0]['protocol']=protocol
    text=str(detail_nodes(root,'fields',scope_reviews=projection)+detail_nodes(root,'interactions',scope_reviews=projection))
    assert text.count('技术未定，业务确认保留')== (7 if protocol==scope.PROTOCOL else 0 if protocol=='unknown' else 1)
    for _,_,original in TECHNICAL_UNKNOWNS:assert original in text


@pytest.mark.parametrize('protocol,expected',[
    (scope.LEGACY_PROTOCOL,['fields.*.default_value']),
    (scope.PROTOCOL,['fields.*.default_value','fields.*.constraints','fields.*.editability',
                     'fields.*.required','fields.*.source','interactions.*.cancel','interactions.*.pending']),
    ('unknown-protocol',[]),
])
def test_action_plan_scope_attributes_match_actual_finite_eligibility(tmp_path,monkeypatch,protocol,expected):
    from app.actions import Actions
    from app.workflow import Workflow
    from tests.test_requirement_recommendation import project,target
    from tests.test_detail_semantic_workflow import SemanticProvider
    store,p=project(tmp_path);provider=SemanticProvider();workflow=Workflow(store,provider)
    original=workflow.config
    monkeypatch.setattr(workflow,'config',lambda stage:dict(original(stage),detail_default_scope_protocol=protocol))
    plan=Actions(store,workflow).plan(p['id'],dict(action='clarify',expected_revision=p['revision'],
        target=dict(target(),detail_required=True),message='',document_type='prd',max_calls=4))
    described=plan['detail_default_scope_review']['clarify']
    assert described['protocol']==protocol and described['attributes']==expected
    assert described['extra_requests_max']==0 and described['preserve_unknown'] and described['within_task_budget']
    assert plan['max_calls']==4 and not provider.received
    for section in ('fields','interactions','state_transitions'):
        for prop in ('default_value','constraints','editability','required','source','cancel','pending','actor','condition'):
            assert scope.eligible_path('detail_contract.'+section+'.X1.'+prop,protocol)==(section+'.*.'+prop in expected)
    assert not scope.eligible_path('detail_contract.fields..source',protocol)
    assert not scope.eligible_path('detail_contract.fields.X1.source.extra',protocol)
    described['attributes'].append('fields.*.actor')
    assert scope.scope_attributes(protocol)==expected  # A plan caller cannot mutate the shared catalog.



@pytest.mark.parametrize('classification',['technical_only','business_gap','unclear'])
def test_scope_decision_challenge_bound_to_full_review_and_no_candidate_write(tmp_path,classification):
    import json
    from app import detail_correction
    p,root,_,plan,good=sample(tmp_path,classification,extra_unknowns=TECHNICAL_UNKNOWNS)
    outcomes=s.validate(plan,good);packet=s.challenge_plan(plan,outcomes)
    scope_fields=[f for f in packet['fields'] if 'scope_review' in f]
    assert len(scope_fields)==7 and len(packet['fields'])==len(plan['rows'][0]['property_refs'])+7
    field=scope_fields[0];record=outcomes['R1']['default_scope_review']
    assert field['scope_record_hash']==record['record_hash'] and field['scope_review']['quotes']
    assert field['scope_review']['classification']==classification
    assert field['sources']==[dict(source=q['material']['source_id'],locator=q['material']['excerpt_id']) for q in field['scope_review']['quotes'] if q['kind']=='source']
    missing=copy.deepcopy(outcomes);missing['R1'].pop('default_scope_review')
    with pytest.raises(Problem):s.challenge_plan(plan,missing)
    messages=s.challenge_messages(packet);header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    assert set(header['scope_review_fields'])=={f['id'] for f in scope_fields}
    sent=json.loads(messages[1]['content'][0]['text'])
    sent_field=next(f for f in sent['fields'] if f['id']==field['id'])
    assert [r['text'] for r in sent_field['own_sources']]==[q['text'] for q in field['scope_review']['quotes'] if q['kind']=='source']
    reply=dict(candidate_hash=packet['candidate_hash'],input_hash=packet['input_hash'],checked_field_ids=[f['id'] for f in packet['fields']],
        findings=[dict(id=field['id'],quote=field['text'],reason='所选资料仅是纠错反馈，不能证明本期已知业务。',sources=[])])
    result=s.challenge_outcomes(plan,packet,reply,outcomes)
    finding=result['R1']['findings'][0]
    assert finding['dimension']=='scope_source_challenge' and finding['affected_paths']==[] and finding['candidate_quotes']==[]
    assert detail_correction.allowed(plan['rows'][0]['detail_contract'],[finding])==({},[])
    assert s.candidate_correction_outcomes(result)['R1']['status']=='passed'
    assert s.scope_correction_feedback(result)['R1']['findings']==[finding]
    assert outcomes['R1']['status']=='passed'  # Invalidated result is separate; old evidence is unchanged.
    bad=copy.deepcopy(reply);bad['checked_field_ids'].remove(field['id'])
    with pytest.raises(Problem):s.challenge_outcomes(plan,packet,bad,outcomes)
    tampered=copy.deepcopy(outcomes);tampered['R1']['default_scope_review']['properties'][0]['known_business']='changed'
    with pytest.raises(Problem):s.challenge_plan(plan,tampered)
    altered=copy.deepcopy(good)
    check=next(c for c in altered['rows'][0]['checks'] if 'default_scopes' in c)
    check['default_scopes'][0]['explanation']+='新解释'
    current=s.validate(plan,altered);other=s.challenge_plan(plan,current)
    assert other['candidate_hash']!=packet['candidate_hash']
    with pytest.raises(Problem):s.challenge_outcomes(plan,packet,reply,current)


@pytest.mark.parametrize('mode,limit,calls,passed',[
    ('once',17,6,True),('twice',17,8,True),('always',17,8,False),
    ('once',5,4,False),('cancel',17,5,False),('timeout',17,5,False),('mixed',17,7,True),
])
def test_scope_evidence_correction_uses_shared_semantic_limit_not_format_retry(tmp_path,mode,limit,calls,passed):
    import asyncio,json
    from app.workflow import Workflow
    from app.core import dumps
    from tests.test_detail_semantic_workflow import SemanticProvider
    from tests.test_business_draft import packet,draft_row,PROTOCOL
    from tests.test_requirement_recommendation import project,target
    async def run():
        class Provider(SemanticProvider):
            def __init__(self):super().__init__();self.full=[];self.challenges=0;self.patches=0
            async def request(self,config,messages,evidence=None):
                header=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);payload=json.loads(messages[1]['content'][0]['text'])
                if not header.get('grounding_protocol') and header.get('response_protocol')!=PROTOCOL and 'required_checks' not in str(payload.get('rows',[])):
                    self.patches+=1
                    return await super().request(config,messages,evidence)
                evidence.before_send(config);self.received.append(copy.deepcopy(messages))
                if header.get('grounding_protocol'):
                    result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
                    fields=[f for f in payload['fields'] if 'scope_review' in f]
                    if fields:
                        self.challenges+=1
                        should_fail=mode=='always' or self.challenges <= (2 if mode=='twice' else 1)
                        if should_fail:
                            field=fields[0];result['findings']=[dict(id=field['id'],quote=field['text'],reason='范围分类自身引文不足，原unknown不改。',sources=[])]
                            if mode=='mixed':
                                ordinary=next(f for f in payload['fields'] if len(f['path'])==2 and f['path'][1]=='detail_contract.fields.F1.required')
                                result['findings'].append(dict(id=ordinary['id'],quote=ordinary['text'],reason='必填条件须保留原适用前提。',sources=[]))
                elif header.get('response_protocol')==PROTOCOL:
                    row=draft_row();row['detail_contract']=generated_detail();row['detail_contract']['fields'][0]['evidence']['default_value']=dict(status='unknown',reason='存储技术未定');result=packet(row)
                else:
                    self.full.append(copy.deepcopy(payload))
                    if len(self.full)>1:
                        assert payload['scope_review_correction']['authority']=='prior_failed_review_diagnostics_not_business_source'
                        if mode=='cancel':raise asyncio.CancelledError()
                        if mode=='timeout':raise Problem('TIMEOUT','offline scope review timeout')
                    result=model_response(payload)
                    for check in result['rows'][0]['checks']:
                        for decision in check.get('default_scopes',[]):decision.update(classification='technical_only',known_business='当前合成来源给定业务',unknown_technical='存储实现未定',source_quote_keys=[q['key'] for q in payload['quote_pool'] if q['kind']=='source'][:1])
                evidence.response(dumps(result),http_status=200,finish_reason='stop',usage={'total_tokens':10})
                return result,dict(finish_reason='stop',usage={'total_tokens':10})
        store,p=project(tmp_path);provider=Provider();workflow=Workflow(store,provider)
        config=dict(workflow.config('clarify'),max_calls=limit)
        task=workflow.start(p['id'],p['revision'],'clarify','',discussion_target=dict(target(),detail_required=True),config_override=config)
        await workflow.tasks[task['id']];run=store.get_record(p['id'],task['id'],'run');final=store.get(p['id'])
        assert run['calls']==run['request_budget']['used']==len(provider.received)==calls
        if mode=='cancel':assert run['status']=='cancelled' and not run.get('result_applied')
        else:
            outcome=run['business_draft_review']['detail_semantic_reviews']['R1']
            assert (outcome['status']=='passed')==passed
            assert bool(scope.projection(final))==passed
            history=run['business_draft_review']['detail_semantic_history']
            assert history[0]['outcomes']['R1']['findings'][0]['dimension']=='scope_source_challenge'
            assert len(history)<=3
            if mode!='mixed':assert not provider.patches
            if not passed:assert not any(i.get('detail_contract') for i in final['items'][len(p['items']):])
        if mode!='mixed':assert len({r['rows'][0]['candidate_hash'] for r in provider.full})==1
        else:assert provider.patches==1
        assert not any('protocol_recovery' in a for a in run['attempts'])
    asyncio.run(run())


@pytest.mark.parametrize('fault',['missing','decision','source_identity','review_hash','challenge','keys','status','actor'])
def test_v2_read_side_requires_exact_fresh_scope_challenge_proof(tmp_path,fault):
    p,root,_,_,_=sample(tmp_path,extra_unknowns=TECHNICAL_UNKNOWNS)
    semantic=p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']
    record=semantic['default_scope_review']
    assert scope.current_reviews(p,root)
    if fault=='missing':semantic.pop('scope_source_audit')
    elif fault=='decision':record['properties'][0]['classification']='business_gap'
    elif fault=='source_identity':record['properties'][0]['quotes'][0]['source_identity']['version']='different'
    elif fault=='review_hash':record['review_hash']='other-review'
    elif fault=='challenge':semantic['source_challenge']['candidate_hash']='other-candidate'
    elif fault=='keys':semantic['scope_source_audit']['property_keys']=[]
    elif fault=='status':semantic['scope_source_audit']['status']='pending'
    else:root['detail_contract']['interactions'][0]['actor']='新增角色'
    # Even a locally recomputed record checksum cannot replace independent audit identity.
    record['record_hash']=digest({k:v for k,v in record.items() if k!='record_hash'})
    assert scope.current_reviews(p,root)==[]
    with pytest.raises(Problem):require_complete(root,p)


def test_legacy_default_review_remains_compatible_without_new_scope_audit(tmp_path):
    p,root,_,_,_=sample(tmp_path,protocol=scope.LEGACY_PROTOCOL)
    semantic=p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']
    assert 'scope_source_audit' not in semantic
    require_complete(root,p)
    assert len(scope.projection(p)[0]['properties'])==1


def test_legacy_confirmation_fingerprint_preserves_original_projection_shape(tmp_path,monkeypatch):
    from app.requirement_confirmation import row_fingerprint,confirmation_current
    p,root,_,_,_=sample(tmp_path,protocol=scope.LEGACY_PROTOCOL)
    root['selection_status']='selected'
    current=scope.current_reviews(p,root)
    legacy=[{k:v for k,v in record.items() if k!='protocol'} for record in current]
    assert legacy and set(legacy[0])=={'requirement_id','candidate_id','detail_hash','review_hash','properties'}
    with monkeypatch.context() as patch:
        patch.setattr(scope,'current_reviews',lambda project,item:copy.deepcopy(legacy))
        root['requirement_confirmation']=dict(schema='requirement-confirmation-1',actor='authenticated_local_user',content_hash=row_fingerprint(p,root))
    assert scope.current_reviews(p,root)==legacy
    assert confirmation_current(p,root)
    assert p['business_draft_reviews'][0]['accepted'][0]['detail_semantic_review']['default_scope_review']['protocol']==scope.LEGACY_PROTOCOL
    p['sources'][0]['version']='changed'
    assert not confirmation_current(p,root)
