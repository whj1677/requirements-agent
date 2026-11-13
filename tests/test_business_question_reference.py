"""Explicit exact-text question reuse is evidence, never a question update."""
import asyncio
import copy
import json
import jsonschema
import pytest
from app import business_draft as bd
from app.core import dumps,Problem
from tests.test_business_draft import setup,draft_row,packet,business_value


def assembled(p,cfg,tmp_path,local):
    messages,excerpts,_=bd.assemble(p,'clarify','',cfg,tmp_path,local_scope=local)
    return messages,json.loads(messages[0]['content'].split('可信任务头：',1)[1]),json.loads(messages[1]['content'][0]['text']),excerpts


def question(text,ref=None):
    result=dict(text=text,reason='本轮核对同一决定',blocking=True,sources=[dict(source='S1',locator='L1')],
        recommendations=[business_value('建议继续核对来源支持的方案','recommendation')])
    if ref is not None:result['existing_question_ref']=ref
    return result


def explicit(tmp_path):
    p,local,cfg,_,_,_=setup(tmp_path)
    messages,h,ctx,ex=assembled(p,cfg,tmp_path,local)
    entry=ctx['current_rows'][0]['existing_question_catalogue'][0]
    row=draft_row();row['questions']=[question(entry['text'],entry['ref'])]
    return p,local,cfg,h,ctx,ex,packet(row)


def test_optional_schema_exact_const_and_read_only_catalogue(tmp_path):
    p,local,cfg,h,ctx,ex,raw=explicit(tmp_path)
    before=copy.deepcopy(p);base=copy.deepcopy(bd.SCHEMA)
    jsonschema.validate(raw,h['schema'])
    legacy=copy.deepcopy(raw);legacy['requirements'][0]['questions'][0].pop('existing_question_ref')
    legacy['requirements'][0]['questions'][0]['text']='这是另外一个真正未知的决定？'
    jsonschema.validate(legacy,h['schema'])
    assert 'existing_question_ref' not in bd.QUESTION['required']
    for fault in ('paraphrase','tamper'):
        bad=copy.deepcopy(raw);part=bad['requirements'][0]['questions'][0]
        if fault=='paraphrase':part['text']+='请确认'
        else:part['existing_question_ref']='Q'+'0'*24
        assert list(jsonschema.Draft202012Validator(h['schema']).iter_errors(bad))
    assembled(p,cfg,tmp_path,local)
    assert p==before and bd.SCHEMA==base
    assert set(ctx['current_rows'][0]['existing_question_catalogue'][0])=={'ref','text'}


def test_reuse_preserves_whole_answered_question_and_records_ai_evidence_only(tmp_path):
    p,local,cfg,_,_,ex,raw=explicit(tmp_path)
    p['questions'][0].update(status='answered',answer='人工已有原话',blocking=False,options=['原始选项'],
        answer_source_refs=copy.deepcopy(p['items'][0]['source_refs']))
    before=copy.deepcopy(p['questions'])
    value,report=bd.adapt(raw,p,ex,'clarify',local)
    assert not value['questions'] and not report['accepted'][0]['question_rejected']
    evidence=report['accepted'][0]['field_evidence']['questions'][0]
    assert evidence['existing_question_id']==p['questions'][0]['id'] and evidence['recommendations']
    assert '不代表人工确认' in evidence['reuse_notice']
    from app.workflow import apply_response
    apply_response(p,value)
    assert p['questions']==before


@pytest.mark.parametrize('fault',['owner','hidden','superseded','out_of_scope','prewrite_resolved','text_changed','removed','tampered_ref'])
def test_current_host_rejects_stale_or_invalid_ref_without_new_question_fallback(tmp_path,fault):
    p,local,_,_,_,ex,raw=explicit(tmp_path)
    q=p['questions'][0]
    if fault=='owner':q['related_refs']=['REQ-other']
    elif fault=='hidden':q['related_refs']=[]
    elif fault=='superseded':q['superseded_by']='Q-next'
    elif fault=='out_of_scope':q['out_of_scope_reason']='当前不涉及'
    elif fault=='prewrite_resolved':q['prewrite_resolution']={'status':'resolved'}
    elif fault=='text_changed':q['question']+='已有业务变更'
    elif fault=='removed':p['questions'].remove(q)
    else:raw['requirements'][0]['questions'][0]['existing_question_ref']='Q'+'0'*24
    before=copy.deepcopy(p)
    value,report=bd.adapt(raw,p,ex,'clarify',local)
    assert not value['questions'] and report['accepted']
    rejected=report['accepted'][0]['question_rejected']
    assert len(rejected)==1 and rejected[0]['code']=='BUSINESS_DRAFT_QUESTION_REFERENCE'
    assert p==before


def test_row_binding_rejects_other_row_and_new_row_even_if_text_identical(tmp_path):
    p,local,cfg,_,ctx,ex,raw=explicit(tmp_path)
    foreign=copy.deepcopy(p['questions'][0]);foreign.update(id='Q-other-exact',related_refs=['REQ-other']);p['questions'].append(foreign)
    _,h,ctx,ex=assembled(p,cfg,tmp_path,None)
    rows={r['row_key']:r for r in ctx['current_rows']}
    assert rows['R1']['existing_question_catalogue'][0]['ref']!=rows['R2']['existing_question_catalogue'][-1]['ref']
    for key in ('R2','new-independent'):
        bad=copy.deepcopy(raw);bad['requirements'][0]['row_key']=key
        # Whole-table summary requirements are irrelevant to the row schema probe.
        assert list(jsonschema.Draft202012Validator(h['schema']['properties']['requirements']['items']).iter_errors(bad['requirements'][0]))
        value,report=bd.adapt(bad,p,ex,'ingest')
        assert not value['questions'] and report['accepted'][0]['question_rejected']


def test_visible_revision_owned_question_reuses_only_explicit_ref_and_catalogue_is_stable(tmp_path):
    p,local,cfg,_,_,_=setup(tmp_path)
    p['items'].append(dict(p['items'][0],id='REQ-candidate',target_item_id='REQ-0001',selection_status='candidate'))
    p['questions'][0]['related_refs']=['REQ-candidate']
    _,_,ctx,ex=assembled(p,cfg,tmp_path,local);entry=ctx['current_rows'][0]['existing_question_catalogue'][0]
    assert entry['text']==ctx['current_rows'][0]['questions'][0]['text']
    p['questions'].reverse()
    assert entry in assembled(p,cfg,tmp_path,local)[2]['current_rows'][0]['existing_question_catalogue']
    row=draft_row();row['questions']=[question(entry['text'],entry['ref'])]
    value,report=bd.adapt(packet(row),p,ex,'clarify',local)
    assert not value['questions'] and report['accepted'][0]['field_evidence']['questions'][0]['existing_question_id']=='Q-0001'


def test_no_ref_legacy_exact_reuse_and_two_real_new_questions_still_work(tmp_path):
    p,local,_,_,_,ex,raw=explicit(tmp_path)
    raw['requirements'][0]['questions'][0].pop('existing_question_ref')
    value,report=bd.adapt(raw,p,ex,'clarify',local)
    assert not value['questions'] and report['accepted'][0]['field_evidence']['questions'][0]['existing_question_id']
    raw['requirements'][0]['questions']=[question('导出文件是否需要脱敏？'),question('导出任务完成后如何通知？')]
    value,report=bd.adapt(raw,p,ex,'clarify',local)
    assert len(value['questions'])==2 and not report['accepted'][0]['question_rejected']


def test_model_input_keeps_latest_answer_and_omits_resolved_history(tmp_path):
    p,local,cfg,_,_,_=setup(tmp_path)
    q=p['questions'][0]
    q.update(status='answered',answer='本次已保存：整批拒绝，不创建资源。',understanding_status='pending',
             answer_history=[dict(answer='旧回答：逐项处理。')])
    p['questions'].append(dict(q,id='Q-resolved',question='旧审查问题？',prewrite_resolution={'basis':'reviewed_dispute'}))
    before=copy.deepcopy(p)
    _,_,ctx,_=assembled(p,cfg,tmp_path,local)
    row=ctx['current_rows'][0]
    assert len(row['questions'])==1
    current=row['questions'][0]
    assert current['answer']==q['answer'] and current['status']=='answered'
    assert current['understanding_status']=='pending' and current['decision_coverage_complete']
    assert current['existing_question_ref']==row['existing_question_catalogue'][0]['ref']
    assert p==before


def test_duplicate_identity_never_silently_selects_a_snapshot(tmp_path):
    p,local,cfg,_,_,ex,raw=explicit(tmp_path)
    p['questions'].append(dict(p['questions'][0],question='同ID的另一个快照'))
    with pytest.raises(Problem) as caught:assembled(p,cfg,tmp_path,local)
    assert caught.value.code=='BUSINESS_DRAFT_QUESTION_REFERENCE'
    value,report=bd.adapt(raw,p,ex,'clarify',local)
    assert not value['questions'] and report['accepted'][0]['question_rejected'][0]['code']=='BUSINESS_DRAFT_QUESTION_REFERENCE'


def test_empty_project_schema_allows_new_questions_without_reference(tmp_path):
    p,_,cfg,_,_,_=setup(tmp_path);p['items']=[];p['questions']=[]
    _,h,_,_=assembled(p,cfg,tmp_path,None)
    jsonschema.Draft202012Validator.check_schema(h['schema'])
    row=draft_row('new-first');row['questions']=[question('新需求的独立业务决定？')]
    shape=h['schema']['properties']['requirements']['items'];jsonschema.validate(row,shape)
    row['questions'][0]['existing_question_ref']='Q'+'0'*24
    assert list(jsonschema.Draft202012Validator(shape).iter_errors(row))


def test_actual_provider_serializes_optional_question_reference_conditions(tmp_path,monkeypatch):
    import httpx
    from app.provider import Provider,DEFAULT,origin
    from tests.provider_transport import reply
    p,local,cfg,h,ctx,ex,raw=explicit(tmp_path);messages=assembled(p,cfg,tmp_path,local)[0]
    real_client=httpx.AsyncClient;seen=[]
    async def respond(request):
        body=json.loads(request.content);seen.append(body)
        assert request.url.path.endswith('/responses') and body['text']['format']['schema']==h['schema']
        jsonschema.validate(raw,body['text']['format']['schema'])
        return reply(request,dumps(raw))
    monkeypatch.setattr('app.provider.httpx.AsyncClient',lambda **kw:real_client(**kw,transport=httpx.MockTransport(respond)))
    provider=Provider(env_path=tmp_path/'absent.env');provider.keys[origin(DEFAULT)]='synthetic-key'
    value,_=asyncio.run(provider.request(cfg,messages))
    assert value==raw and len(seen)==1
