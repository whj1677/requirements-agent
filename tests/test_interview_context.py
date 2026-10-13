"""Same-call chat summaries with frozen loopback model responses only."""
import copy
import json

import httpx
import pytest

from app.interview_context import assess
from tests.test_ui02_actions import case, plan, start, finished


MATERIAL = '大型储能云平台。本次目标是协作记录和查询告警处理过程。目标使用者为两名值班人员。本次改动为新增告警处理记录。'


def reply(model, draft):
    def transform(value, body, context):
        if value['stage'] == 'interview':
            value.update(summary='已理解目标和使用者，摘要建议供你核对。', result={'can_organize': True})
            value['context_draft'] = draft(context)
        return value
    model['transform'] = transform


def ref(context, contains='大型储能云平台'):
    ex = next(e for e in context['excerpts'] if contains in e['text'])
    return dict(source_id=ex['source_id'], excerpt_id=ex['id'])


def send(case, text=MATERIAL):
    client, app, model, root = case
    body, preview = plan(client, root, 'interview', text)
    assert preview['stages'] == ['interview']
    response = start(client, root, body, preview)
    assert response.status_code == 200, response.text
    task = finished(client, root)
    project = client.get(root).json()
    run = app.state.store.get_record(project['id'], task['run_ids'][0], 'run')
    return task, project, run


def test_chat_same_call_proposes_summary_without_approving_then_human_saves_without_model(case):
    c, app, model, root = case
    reply(model, lambda ctx: {key:dict(text=text, source_refs=[ref(ctx)]) for key, text in (
        ('intent', '协作记录和查询告警处理过程。'), ('users', '两名值班人员。'),
        ('change_scope', '新增告警处理记录。'))})
    task, project, run = send(case)
    assert task['status'] == 'succeeded' and task['calls'] == len(model['requests']) == 1
    assert run['interview_context_review']['accepted_fields'] == ['intent', 'users', 'change_scope']
    assert not project.get('product_context') and not project.get('context_field_states')
    assert project['items'] == project['questions'] == project['baselines'] == []
    assert not project['product_flow'][1]['complete']
    assert len(project['messages']) == 2
    meta = project['product_context_proposal_evidence']['intent']
    assert meta['text'] == project['product_context_proposal']['intent']
    assert meta['source_refs'][0]['source_id'] == project['sources'][0]['id']
    assert meta['run_id'] == task['run_ids'][0] and meta['evidence_status'] == 'unreviewed'
    assert meta['uncertain'] is False and meta['conflicts_with_saved'] is False
    header = json.loads(model['requests'][0]['messages'][0]['content'].split('可信任务头：')[1])
    assert 'context_draft' in header['schema']['properties']
    assert 'context_draft' not in header['schema']['required']
    assert '助手消息' in model['requests'][0]['messages'][0]['content']
    snapshot = copy.deepcopy(project['product_context_proposal'])
    for state in ('unknown', 'known'):
        result = c.post(root+'/product-context', json=dict(expected_revision=project['revision'],
            values={'intent':snapshot['intent']}, field_states={'intent':{'status':state}}))
        assert result.status_code == 200, result.text
        project = c.get(root).json()
    assert len(model['requests']) == 1
    assert project['product_context'] == {'intent':snapshot['intent']}
    assert project['context_field_states']['intent']['status'] == 'known'
    assert project['product_context_proposal'] == snapshot
    assert not project['product_flow'][1]['complete']


def test_new_chat_suggestions_preserve_human_values_and_non_applicable_decisions(case):
    c, app, model, root = case
    values = dict(intent='人工原目标', preserve_scope='', out_of_scope='人工已说明不做的部分')
    states = dict(intent={'status':'known'}, preserve_scope={'status':'not_applicable'}, out_of_scope={'status':'none'})
    p = c.get(root).json()
    assert c.post(root+'/product-context', json=dict(expected_revision=p['revision'], values=values,
        field_states=states, scope_profile={'scenario':'new_product','dependencies':'unknown'})).status_code == 200
    before = c.get(root).json()
    reply(model, lambda ctx: {field:dict(text=text, source_refs=[ref(ctx)]) for field, text in (
        ('intent', '用户新说的目标'), ('preserve_scope', '用户新说的保留范围'), ('out_of_scope', '用户新说的排除项'))})
    task, p, _ = send(case, MATERIAL+'用户新说的目标、用户新说的保留范围、用户新说的排除项，仅作为新的讨论。')
    assert task['calls'] == 1
    for key in ('product_context','context_field_states','scope_profile','items','questions','documents','baselines'):
        assert p[key] == before[key]
    assert all(row['conflicts_with_saved'] for row in p['product_context_proposal_evidence'].values())
    assert p['product_context_proposal']['intent'] == '用户新说的目标'


@pytest.mark.parametrize('mode', ['valid', 'empty', 'rejected', 'legacy'])
def test_only_valid_nonempty_context_candidate_bumps_revision_and_rejects_stale_save(case, mode):
    c, _, model, root = case
    if mode != 'legacy':
        reply(model, lambda ctx: dict(intent=dict(
            text='' if mode == 'empty' else '协作记录和查询告警处理过程。',
            source_refs=[] if mode in ('empty', 'rejected') else [ref(ctx)])))
    task, p, _ = send(case)
    # Saving the user message has already advanced the input revision. Only
    # accepted, nonempty suggestions add a model-result revision after that.
    input_revision = task['input_revision']
    assert p['revision'] == input_revision + (1 if mode == 'valid' else 0)
    if mode == 'valid':
        stale = c.post(root+'/product-context', json=dict(expected_revision=input_revision,
            values={'intent':'基于旧页面提交的目标'}, field_states={'intent':{'status':'known'}}))
        assert stale.status_code == 409 and stale.json()['code'] == 'STALE_REVISION'
        after = c.get(root).json()
        assert after['revision'] == p['revision']
        assert not after.get('product_context') and not after.get('context_field_states')
        assert after['product_context_proposal'] == p['product_context_proposal']
    assert len(model['requests']) == task['calls'] == 1


def test_legacy_null_candidate_maps_accept_chat_suggestions_without_changing_canonical(case):
    c, app, model, root = case
    p = c.get(root).json()
    with app.state.store.edit(p['id'], p['revision'], '隔离旧数据空值') as (stored, _):
        stored['product_context_proposal'] = None
        stored['product_context_proposal_evidence'] = None
        stored['product_context'] = {'intent':'人工确认目标'}
        stored['context_field_states'] = {'intent':{'status':'known'}}
    before = c.get(root).json()
    reply(model, lambda ctx: dict(intent=dict(text='协作记录和查询告警处理过程。', source_refs=[ref(ctx)])))
    task, after, _ = send(case)
    assert task['status'] == 'succeeded' and task['calls'] == len(model['requests']) == 1
    assert after['product_context_proposal']['intent'] == '协作记录和查询告警处理过程。'
    assert after['product_context_proposal_evidence']['intent']['conflicts_with_saved'] is True
    assert after['product_context'] == before['product_context']
    assert after['context_field_states'] == before['context_field_states']


def test_invalid_optional_fields_are_quarantined_not_retried_or_deleted_from_raw_reply(case):
    c, app, model, root = case
    def draft(ctx):
        return dict(intent={'text':'协作记录和查询告警处理过程。','source_refs':[ref(ctx)]},
            users={'text':'虚构角色','source_refs':[{'source_id':'SRC-imaginary','excerpt_id':'EX-fake'}]},
            change_scope={'text':'无引用的新增规则','source_refs':[]},
            priority={'text':3,'source_refs':[ref(ctx)]}, secret_business_rule={'text':'未知字段','source_refs':[ref(ctx)]})
    reply(model, draft)
    task, p, run = send(case)
    assert task['status'] == 'partial' and task['calls'] == len(model['requests']) == 1
    assert p['messages'][-1]['text'] == '已理解目标和使用者，摘要建议供你核对。'
    assert list(p['product_context_proposal']) == ['intent']
    audit = p['interview_context_review']
    assert {r['field'] for r in audit['rejected_fields']} == {'users','change_scope','priority','secret_business_rule'}
    assert audit['limitations'] and p['messages'][-1]['interview_context_review'] == audit
    assert p['messages'][-1]['response']['context_draft']['users']['text'] == '虚构角色'
    assert run['response']['context_draft'] == p['messages'][-1]['response']['context_draft']
    evidence = json.loads(next((app.state.store.folder/'evidence/model-calls').glob('*.json')).read_text('utf-8'))
    assert '虚构角色' in evidence['final_output']
    assert evidence['interview_context_review']['rejected_fields']
    assert not p.get('product_context') and not p.get('context_field_states')


@pytest.mark.parametrize('draft', [None, [], 'invalid'])
def test_invalid_optional_container_keeps_valid_chat(case, draft):
    _, _, model, _ = case
    reply(model, lambda _:draft)
    task, p, _ = send(case)
    assert task['status'] == 'partial' and task['calls'] == 1
    assert len(p['messages']) == 2 and not p.get('product_context_proposal')
    assert p['interview_context_review']['rejected_fields'][0]['field'] == 'context_draft'


def test_unknown_does_not_erase_prior_suggestions_and_legacy_chat_stays_compatible(case):
    c, app, model, root = case
    reply(model, lambda ctx:dict(intent={'text':'协作记录和查询告警处理过程。','source_refs':[ref(ctx)]}))
    _, before, _ = send(case)
    reply(model, lambda ctx:dict(intent={'text':'','source_refs':[]},
        users={'text':'使用者尚未确定','source_refs':[ref(ctx, '使用者尚未确定')]}))
    task, current, _ = send(case, '使用者尚未确定，先保留之前的目标。')
    assert task['calls'] == 1
    assert current['product_context_proposal']['intent'] == before['product_context_proposal']['intent']
    assert current['product_context_proposal_evidence']['intent'] == before['product_context_proposal_evidence']['intent']
    assert current['messages'][:len(before['messages'])] == before['messages']
    assert current['sources'][:len(before['sources'])] == before['sources']
    assert current['product_context_proposal_evidence']['users']['uncertain'] is True
    assert not current.get('context_field_states')
    prior = copy.deepcopy(current['product_context_proposal'])
    model['transform'] = lambda value, body, ctx: dict(value, summary='旧合同访谈回复',result={'can_organize':False})
    task, current, _ = send(case, '继续聊。')
    assert task['status'] == 'succeeded' and task['calls'] == 1
    assert current['product_context_proposal'] == prior and len(model['requests']) == 3


def direct_case(text='告警处理结果反馈给值班人员。'):
    source = dict(id='SRC-real', origin='interview', excluded=False)
    ex = dict(source_id=source['id'],id='EX-real',text=text)
    p = dict(sources=[source], messages=[dict(role='assistant',text='仅管理员可见反馈。')])
    return p, [ex], [dict(source_id=source['id'],excerpt_id=ex['id'])]


@pytest.mark.parametrize('mode', ['not_sent','cross_pair','excluded','model_origin','unread_image'])
def test_context_sources_must_be_actual_sent_readable_material(mode):
    p, ex, refs = direct_case()
    if mode == 'not_sent':ex=[]
    if mode == 'cross_pair':refs[0]['excerpt_id']='EX-other'
    if mode == 'excluded':p['sources'][0]['excluded']=True
    if mode == 'model_origin':p['sources'][0]['origin']='assistant'
    if mode == 'unread_image':p['sources'][0]['image_mime']='image/png'
    r = assess({'intent':{'text':'告警处理记录','source_refs':refs}},p,ex)
    assert not r['fields'] and r['rejected_fields'][0]['code'] == 'CONTEXT_REFERENCE_INVALID'


def test_assistant_claim_is_not_source_evidence_and_unknowns_are_field_local():
    p, ex, refs = direct_case()
    bad = assess({'intent':{'text':'仅管理员可见反馈。','source_refs':refs}},p,ex)
    assert bad['rejected_fields'][0]['code'] == 'CONTEXT_SOURCE_FIDELITY'
    p, ex, refs = direct_case('目标是协作记录告警处理过程；权限未知。')
    good = assess({'intent':{'text':'协作记录告警处理过程','source_refs':refs}},p,ex)
    assert good['fields']['intent']['uncertain'] is False
    p, ex, refs = direct_case('目标是避免派单冲突，查询尚未关闭的告警。')
    settled = assess({'intent':{'text':'避免派单冲突，查询尚未关闭的告警。','source_refs':refs}},p,ex)
    assert settled['fields']['intent']['uncertain'] is False
    p, ex, refs = direct_case('使用者尚未确定。')
    unknown = assess({'users':{'text':'两名值班人员','source_refs':refs}},p,ex)
    assert unknown['fields']['users']['uncertain'] is True
    p, ex, refs = direct_case('两份资料对本期范围的描述存在冲突。')
    conflicting = assess({'change_scope':{'text':'新增告警记录','source_refs':refs}},p,ex)
    assert conflicting['fields']['change_scope']['uncertain'] is True


@pytest.mark.parametrize('failure', ['main_schema','main_reference','duplicate_context_key'])
def test_optional_field_isolation_does_not_weaken_main_chat_or_json_integrity(case, monkeypatch, failure):
    c, app, model, root = case
    if failure != 'duplicate_context_key':
        reply(model, lambda ctx:dict(intent={'text':'协作记录告警处理过程','source_refs':[ref(ctx)]}))
        original = model['transform']
        def invalid(value, body, ctx):
            value = original(value,body,ctx)
            if failure == 'main_schema':value['proposals']=[{'statement':'不允许的规范条款'}]
            else:value['used_source_refs']=[{'source_id':'SRC-fake','excerpt_id':'EX-fake'}]
            return value
        model['transform'] = invalid
    else:
        requests = []
        original_client = httpx.AsyncClient
        def response(request):
            requests.append(request)
            raw = '{"schema_version":"1.1","stage":"interview","summary":"正常回复","proposals":[],"questions":[],"findings":[],"used_source_refs":[],"limitations":[],"result":{"can_organize":false},"context_draft":{"intent":{"text":"甲","text":"乙","source_refs":[]}}}'
            return httpx.Response(200,json={'model':'SYNTHETIC','choices':[{'message':{'content':raw},'finish_reason':'stop'}]})
        monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original_client(transport=httpx.MockTransport(response),**kwargs))
    task, p, run = send(case)
    assert task['status'] == 'failed' and task['calls'] == 1
    assert len(p['messages']) == 1 and not p.get('product_context_proposal')
    if failure == 'duplicate_context_key':
        assert len(requests) == 1 and run['error'] == 'SEMANTIC_BLOCKED'


def test_later_organize_replacement_does_not_reuse_stale_chat_field_citations():
    from app.workflow import apply_response
    from tests.test_runtime import empty_ingest
    p = dict(revision=1, items=[], questions=[], options=[], messages=[],
        product_context={'intent':'人工保留'}, product_context_proposal={'intent':'旧候选'},
        product_context_proposal_evidence={'intent':{'text':'旧候选','source_refs':[]}})
    value = empty_ingest()
    value['product_context_proposal']['intent'] = '整理生成的新候选'
    apply_response(p,value)
    assert p['product_context']['intent'] == '人工保留'
    assert p['product_context_proposal']['intent'] == '整理生成的新候选'
    assert 'product_context_proposal_evidence' not in p


@pytest.mark.parametrize('changed', [False, True])
def test_optional_summary_cannot_change_during_main_json_format_repair(case, monkeypatch, changed):
    _, _, _, _ = case
    requests, frozen = [], []
    original_client = httpx.AsyncClient
    def response(request):
        body = json.loads(request.content)
        ctx = json.loads(body['messages'][1]['content'][0]['text'])
        requests.append(body)
        if not frozen:
            value = dict(schema_version='1.1',stage='interview',summary='已保留本次原话，摘要待核对。',
                proposals=[],questions=[],findings=[],used_source_refs=[ref(ctx)],limitations=[],
                result={'can_organize':True},context_draft={'intent':{'text':'协作记录告警处理过程','source_refs':[ref(ctx)]}})
            frozen.append(value)
            raw = json.dumps(value,ensure_ascii=False).replace(', "proposals"','}, "proposals"',1)
        else:
            value = copy.deepcopy(frozen[0])
            if changed:value['context_draft']['intent']['text']='格式修复擅自加入的其他目标'
            raw = json.dumps(value,ensure_ascii=False)
        return httpx.Response(200,json={'model':'SYNTHETIC','choices':[{'message':{'content':raw},'finish_reason':'stop'}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original_client(transport=httpx.MockTransport(response),**kwargs))
    task, p, run = send(case)
    assert task['calls'] == len(requests) == 2
    if changed:
        assert task['status'] == 'failed' and run['error'] == 'SEMANTIC_BLOCKED'
        assert len(p['messages']) == 1 and not p.get('product_context_proposal')
    else:
        assert task['status'] == 'succeeded'
        assert p['product_context_proposal']['intent'] == frozen[0]['context_draft']['intent']['text']
        assert p['messages'][-1]['response'] == frozen[0]
