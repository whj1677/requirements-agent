import copy
import json
import pytest
from app.core import Problem
from app.business_draft import adapt
from app import detail_semantics as review
from tests.test_business_draft import setup,draft_row,packet
from tests.test_detail_business_draft import sample_detail


def fixture(tmp_path):
    p,local,_,_,excerpts,_=setup(tmp_path)
    row=draft_row();row['detail_contract']=sample_detail()
    raw=packet(row);canonical,report=adapt(raw,p,excerpts,'clarify',local)
    plan=review.prepare(raw,canonical,report,excerpts)
    return p,local,excerpts,raw,canonical,report,plan


def test_short_challenge_covers_unknown_and_binds_four_false_green_properties(tmp_path):
    from app import intake_grounding,detail_correction
    from app.core import digest
    *_,raw,canonical,report,plan=fixture(tmp_path)
    row=plan['rows'][0];detail=row['detail_contract'];source=plan['sources'][0]
    own={k:source[k] for k in ('source_id','excerpt_id')}
    detail['fields'][0]['id']='F2'
    detail['fields'][0]['editability']='历史记录仅查看；资料未说明是否可编辑。'
    detail['fields'][0].setdefault('evidence',{})['editability']=dict(status='unknown',reason='资料未说明是否可编辑。')
    detail['interactions'][0]['id']='I1'
    detail['interactions'][0]['effects']='处理记录形成认领记录。'
    detail['interactions'][0]['repeat']='同一告警重复认领不生效。'
    detail['interactions'][0].setdefault('evidence',{}).update({k:dict(status='source',source_refs=[own]) for k in ('effects','repeat')})
    other=copy.deepcopy(detail['interactions'][0]);other.update(id='I2',effects='处理记录形成释放记录。')
    detail['interactions'].append(other)
    plan['sources'].append(dict(source_id='SRC-T5',excerpt_id='EX-T5',text='认领与释放各自形成处理记录。'))
    row['property_refs']={f'P{i:03d}':dict(path=p,value=v) for i,(p,v) in enumerate(sorted(review.properties(detail).items()),1)}
    row['candidate_hash']=digest(detail)
    raw['requirements'][0]['detail_contract']=copy.deepcopy(detail)
    before=copy.deepcopy((plan,raw))
    packet=review.challenge_plan(plan)
    assert len(packet['fields'])==len(row['property_refs'])
    paths=['detail_contract.fields.F2.editability','detail_contract.interactions.I1.effects',
        'detail_contract.interactions.I1.repeat','detail_contract.interactions.I2.effects']
    selected=[next(f for f in packet['fields'] if f['path'][1]==path) for path in paths]
    assert selected[0]['basis']=='unknown' and selected[0]['sources']==[]
    assert all(f['sources']==[dict(source=own['source_id'],locator=own['excerpt_id'])] for f in selected[1:])
    sent=json.loads(intake_grounding.challenge_messages(packet)[1]['content'][0]['text'])
    strict=review.challenge_messages(packet,transport_protocol='responses-json-schema-v1')
    from app.provider import business_draft_schema
    header=json.loads(strict[0]['content'].split('可信任务头：',1)[1])
    assert business_draft_schema(strict)==header['schema']
    assert header['stage']=='review' and header['strict_json_protocol']=='detail-review-strict-json-1'
    assert all(not any(s['source']=='SRC-T5' for s in f['own_sources']) for f in sent['fields'])
    assert any(s['source']=='SRC-T5' for s in sent['context']['excerpts'])
    reply=dict(candidate_hash=packet['candidate_hash'],input_hash=packet['input_hash'],
        checked_field_ids=[f['id'] for f in packet['fields']],findings=[dict(id=f['id'],quote=f['text'],
            reason='属性自身缺少限定条件或支持来源。',sources=[]) for f in selected])
    old=dict(R1=dict(status='passed',checks=[],findings=[]))
    merged=review.challenge_outcomes(plan,packet,reply,old)
    assert merged['R1']['status']=='failed' and len(merged['R1']['findings'])==4
    _,scopes=detail_correction.contract(raw,merged)
    expected=set(paths)|{p.rsplit('.',1)[0]+'.evidence.'+p.rsplit('.',1)[1] for p in paths}
    assert set(scopes['R1']['allowed_write_paths'])==expected
    assert scopes['R1']['new_object_kinds']==[]
    assert (plan,raw)==before and old['R1']['status']=='passed'
    prior=copy.deepcopy(merged);empty=dict(reply,findings=[])
    assert review.challenge_outcomes(plan,packet,empty,prior)['R1']['findings']==prior['R1']['findings']
    for fault in ('hash','id','quote','source'):
        bad=copy.deepcopy(reply)
        if fault=='hash':bad['candidate_hash']='changed'
        elif fault=='id':bad['checked_field_ids'].pop()
        elif fault=='quote':bad['findings'][0]['quote']='not present'
        else:bad['findings'][0]['sources']=[dict(source='absent',locator='absent')]
        with pytest.raises(Problem):review.challenge_outcomes(plan,packet,bad,old)


def material_plan(plan):
    if 'sources' in plan:return plan
    plan=copy.deepcopy(plan);plan['sources']=[];plan['human_statements']=[]
    groups={}
    for quote in plan['quote_pool']:
        identity=(quote['kind'],repr(sorted(quote['material'].items())))
        if identity not in groups:
            material=copy.deepcopy(quote['material']);material['text']='';groups[identity]=material
            plan['sources' if quote['kind']=='source' else 'human_statements'].append(material)
        groups[identity]['text']+=quote['text']
    return plan


def response(plan):
    plan=material_plan(plan)
    rows=[]
    for row in plan['rows']:
        texts=row.get('property_text') or {r['path']:r['value'] for r in row['property_refs'].values()}
        path,text=next(iter(texts.items()))
        source=plan['sources'][0]
        checks=[]
        for expected in row['required_checks']:
            if expected['scope']!='detail':
                path='detail_contract.interactions.'+expected['scope']+'.name';text=texts[path]
            quotes=([dict(path=k,quote=v) for k,v in texts.items() if k.startswith('detail_contract.applicability.') and k.endswith('.applicable')]
                if expected['dimension']=='coverage' else [dict(path=path,quote=text)])
            checks.append(dict(**expected,verdict='supported',candidate_quotes=quotes,
                source_quotes=[dict(source_id=source['source_id'],excerpt_id=source['excerpt_id'],quote=source['text'])],
                human_quotes=[],
                affected_paths=[],traces=([dict(kind='normal',edge_ids=['E1','E2','E3'],explanation='正常保存路径'),
                         dict(kind='exception',edge_ids=['E1','E2','E4'],explanation='校验失败路径')]
                        if expected['dimension'] in ('operation_results','actor_paths') else
                        [dict(kind='not_applicable',edge_ids=[],explanation='当前合成来源未给未知后恢复义务，不发明分支。')] if expected['dimension'] in review.RECOVERY_DIMENSIONS else []),explanation='离线协议夹具，不证明业务语义。',correction='无需纠正'))
        rows.append(dict(row_key=row['row_key'],checks=checks))
    return dict(rows=rows)


def model_response(plan,expanded=None):
    """Compact protocol fixture; source judgments here are synthetic only."""
    plan=material_plan(plan)
    expanded=expanded or response(plan);sources={};humans={};rows=[]
    def pool_key(pool,quote,prefix):
        identity=repr(sorted(quote.items()))
        if identity not in pool:pool[identity]=dict(key=prefix+str(len(pool)+1),**quote)
        return pool[identity]['key']
    for row in expanded['rows']:
        origin=next(r for r in plan['rows'] if r['row_key']==row['row_key'])
        by_path={r['path']:(key,r['value']) for key,r in origin['property_refs'].items()}
        def key_for(quote):
            found=by_path.get(quote['path'])
            return found[0] if found and quote['quote'] in found[1] else 'INVALID_PROPERTY_REFERENCE'
        checks=[]
        for check in row['checks']:
            checks.append(dict(scope=check['scope'],dimension=check['dimension'],verdict=check['verdict'],
                candidate_keys=list(dict.fromkeys(key_for(q) for q in check['candidate_quotes'])),
                affected_keys=[by_path[p][0] if p in by_path else 'INVALID_AFFECTED_KEY' for p in check['affected_paths']],
                source_quote_keys=[pool_key(sources,q,'Q') for q in check['source_quotes']],
                human_quote_keys=[pool_key(humans,q,'H') for q in check['human_quotes']],
                traces=copy.deepcopy(check['traces']),explanation=check['explanation'],correction=check['correction']))
        source_checks=[]
        for claim in origin['source_claims']:
            ref=claim['source_refs'][0]
            source=next(s for s in plan['sources'] if all(s[k]==ref[k] for k in ('source_id','excerpt_id')))
            quote=dict(**ref,quote=source['text'])
            source_checks.append(dict(key=claim['key'],verdict='supported',source_quote_keys=[pool_key(sources,quote,'Q')],
                affected_keys=[],explanation='离线来源属性协议夹具。',correction='无需纠正'))
        state_checks=[]
        for state in origin['detail_contract']['state_transitions']:
            keys=[k for k,r in origin['property_refs'].items() if r['path'].startswith('detail_contract.state_transitions.'+state['id']+'.')]
            state_checks.append(dict(transition_id=state['id'],verdicts={k:'supported' for k in ('object_identity','success_timing','actor_perspective')},
                candidate_keys=keys,affected_keys=[],source_quote_keys=checks[0]['source_quote_keys'],human_quote_keys=[],
                explanation='离线逐状态协议夹具。',correction='无需纠正'))
        rows.append(dict(row_key=row['row_key'],candidate_hash=origin['candidate_hash'],checks=checks,source_checks=source_checks,state_checks=state_checks))
    if plan['version']==review.POOL_VERSION:
        lookup={}
        for kind,quotes in (('source',sources.values()),('human',humans.values())):
            for quote in quotes:
                fields=('source_id','excerpt_id') if kind=='source' else ('statement_id',)
                matches=[q for q in plan['quote_pool'] if q['kind']==kind and all(q['material'][k]==quote[k] for k in fields)
                    and (q['text'] in quote['quote'] or quote['quote'] in q['text'])]
                lookup[quote['key']]=[q['key'] for q in matches] or ['INVALID_QUOTE_KEY']
        for row in rows:
            for group in ('checks','source_checks','state_checks'):
                for check in row[group]:
                    for field in ('source_quote_keys','human_quote_keys'):
                        if field in check:check[field]=list(dict.fromkeys(k for old in check[field] for k in lookup[old]))
        if plan.get('default_scope_review'):
            from app.detail_business_scope import eligible
            for row in rows:
                scope=next(r for r in plan['unknown_scope']['rows'] if r['row_key']==row['row_key'])
                check=next(c for c in row['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
                for prop in scope['properties']:
                    if prop['key'] not in check['candidate_keys']:check['candidate_keys'].append(prop['key'])
                check['default_scopes']=[dict(property_key=p['key'],classification='business_gap',known_business='',unknown_technical='',source_quote_keys=[],human_quote_keys=[],explanation='合成协议回复：真实业务未知仍保留阻塞。') for p in eligible(plan,row)]
        return dict(rows=rows,quote_pool_hash=plan['quote_pool_hash'])
    return dict(rows=rows,source_quotes=list(sources.values()),human_quotes=list(humans.values()))


def validate_fixture(plan,value):
    return review.validate(plan,model_response(plan,value))


def correction_response(payload,detail):
    if payload.get('correction_protocol',1)==1:return dict(rows=[dict(row_key='R1',detail_contract=detail)])
    from app.requirement_details import _objects
    before={'detail_contract.'+s+'.'+o['id']:o for s,o in _objects(payload['rows'][0]['detail_contract'])}
    updates=[];new_objects=[]
    for section,item in _objects(detail):
        base='detail_contract.'+section+'.'+item['id']
        if base not in before:new_objects.append(dict(section=section,value=copy.deepcopy(item)));continue
        for key,value in item.items():
            if value==before[base].get(key):continue
            if key=='evidence':
                updates.extend(dict(path=base+'.evidence.'+attribute,value=copy.deepcopy(proof)) for attribute,proof in value.items()
                    if proof!=before[base].get('evidence',{}).get(attribute))
            else:updates.append(dict(path=base+'.'+key,value=copy.deepcopy(value)))
    return dict(rows=[dict(row_key='R1',updates=updates,new_objects=new_objects)])


def test_review_is_complete_quote_and_trace_bound_without_mutation(tmp_path):
    *_,plan=fixture(tmp_path);value=response(plan);before=copy.deepcopy((plan,value))
    outcomes=validate_fixture(plan,value)
    assert outcomes['R1']['status']=='passed'
    assert (plan,value)==before


@pytest.mark.parametrize('fault',['missing_dimension','duplicate','source_quote','candidate_quote','path','edge','disconnected','missing_scope_trace','no_source'])
def test_review_cannot_fake_coverage_or_witnesses(tmp_path,fault):
    *_,plan=fixture(tmp_path);value=response(plan);check=value['rows'][0]['checks'][0]
    if fault=='missing_dimension':value['rows'][0]['checks'].pop()
    elif fault=='duplicate':value['rows'][0]['checks'][-1]=copy.deepcopy(check)
    elif fault=='source_quote':check['source_quotes'][0]['quote']='不是原来源原句'
    elif fault=='candidate_quote':check['candidate_quotes'][0]['quote']='不是候选原句'
    elif fault=='path':check['candidate_quotes'][0]['path']='detail_contract.fields.F404.name'
    elif fault=='edge':check['traces'][0]['edge_ids']=['E404']
    elif fault=='disconnected':check['traces'][0]['edge_ids']=['E1','E3']
    elif fault=='missing_scope_trace':
        for c in value['rows'][0]['checks']:
            if c['scope']=='I1':c['traces']=[]
    else:check['source_quotes']=[]
    with pytest.raises(Problem):validate_fixture(plan,value)


@pytest.mark.parametrize('ids,expected',[(['E404'],'edge_id=E404'),(['E1','E3'],'首个不连续边对')])
def test_invalid_trace_diagnostic_identifies_exact_location_without_repair(tmp_path,ids,expected):
    *_,plan=fixture(tmp_path);value=response(plan);check=value['rows'][0]['checks'][0]
    check['traces'][0]['edge_ids']=ids
    before=copy.deepcopy(value)
    with pytest.raises(Problem) as caught:validate_fixture(plan,value)
    message=caught.value.message
    assert f"row=R1 scope={check['scope']} dimension={check['dimension']} trace_index=0" in message
    assert expected in message
    if len(ids)>1:
        edges={e['id']:e for e in plan['rows'][0]['detail_contract']['flow']['edges']}
        assert all(f"{eid}({edges[eid]['from']}->{edges[eid]['to']})" in message for eid in ids)
    assert value==before


@pytest.mark.parametrize('bindings,accepted',[(('I1','I1'),True),(('I1',None),True),(('I2','I2'),False),((None,None),False)])
def test_operation_edge_quote_requires_explicit_incident_binding(tmp_path,bindings,accepted):
    from app.core import digest
    *_,plan=fixture(tmp_path);origin=plan['rows'][0];detail=origin['detail_contract']
    edge=next(e for e in detail['flow']['edges'] if e.get('condition'));nodes={n['id']:n for n in detail['flow']['nodes']}
    for endpoint,binding in zip(('from','to'),bindings):
        if binding is None:nodes[edge[endpoint]].pop('interaction_id',None)
        else:nodes[edge[endpoint]]['interaction_id']=binding
    origin['candidate_hash']=digest(detail)
    value=response(plan);check=next(c for c in value['rows'][0]['checks'] if c['scope']=='I1' and c['dimension']=='operation_results')
    path='detail_contract.flow.edges.'+edge['id']+'.condition'
    check.update(verdict='unclear',candidate_quotes=[dict(path=path,quote=origin['property_text'][path])],
        affected_paths=[path],traces=[],correction='仅核对已引用连线条件，不授权另一操作。')
    if accepted:assert validate_fixture(plan,value)['R1']['status']=='failed'
    else:
        with pytest.raises(Problem) as caught:validate_fixture(plan,value)
        assert caught.value.code=='DETAIL_REVIEW_INVALID' and '逐操作检查须引用' in caught.value.message


@pytest.mark.parametrize('verdict',['contradicted','unsupported','unclear'])
def test_nonpassing_review_preserves_original_detail_and_no_new_question(tmp_path,verdict):
    p,local,excerpts,raw,_,_,plan=fixture(tmp_path)
    p['items'][0]['detail_contract']=sample_detail()
    before=copy.deepcopy((p,raw));value=response(plan)
    value['rows'][0]['checks'][0]['verdict']=verdict
    value['rows'][0]['checks'][0]['affected_paths']=[value['rows'][0]['checks'][0]['candidate_quotes'][0]['path']]
    outcomes=validate_fixture(plan,value)
    canonical,report=adapt(raw,p,excerpts,'clarify',local,detail_semantic_reviews=outcomes)
    assert report['status']=='partial' and report['field_rejected'][0]['raw']==raw['requirements'][0]['detail_contract']
    assert not canonical['questions']
    assert all(i.get('detail_contract')==p['items'][0]['detail_contract'] for i in canonical['proposals'] if i['kind']=='requirement')
    assert (p,raw)==before


def test_pending_review_does_not_create_a_usable_new_detail(tmp_path):
    p,local,excerpts,raw,_,_,plan=fixture(tmp_path)
    canonical,report=adapt(raw,p,excerpts,'clarify',local,
        detail_semantic_reviews=review.pending(plan,'BUDGET_EXHAUSTED','请求额度不足，详细定义尚待语义核对。'))
    assert report['status']=='partial'
    assert all('detail_contract' not in i for i in canonical['proposals'])
    assert report['field_rejected'][0]['semantic_review']['status']=='pending'


def test_correction_cannot_touch_body_or_nonfailed_row(tmp_path):
    *_,raw,canonical,report,plan=fixture(tmp_path)
    outcomes=review.pending(plan,'x','x');outcomes['R1']['status']='failed'
    outcomes['R1']['findings']=[dict(dimension='field_conditions',scope='detail',
        affected_paths=['detail_contract.fields.F1.required'],traces=[])]
    # The full generation schema is intentionally strict; only wrong-row shape
    # is tested here, not a fabricated semantic approval.
    schema=dict(type='object',required=['rows'],properties={'rows':dict(type='array')})
    with pytest.raises(Problem):review.apply_correction(raw,dict(rows=[dict(row_key='R404',detail_contract={})]),schema,outcomes)
    fixed=copy.deepcopy(raw['requirements'][0]['detail_contract']);fixed['fields'][0]['required']='资料约定时必填'
    result=review.apply_correction(raw,dict(rows=[dict(row_key='R1',detail_contract=fixed)]),schema,outcomes)
    assert result['requirements'][0]['statement']==raw['requirements'][0]['statement']
    assert result['requirements'][0]['questions']==raw['requirements'][0]['questions']
    assert raw['requirements'][0]['detail_contract']!=fixed


@pytest.mark.parametrize('fault',['applicability','delete_object','unrelated_property','downgrade_source'])
def test_correction_cannot_bypass_proven_content(tmp_path,fault):
    *_,raw,canonical,report,plan=fixture(tmp_path)
    raw['requirements'][0]['detail_contract']['fields'][0]['evidence']={'required':{'status':'source','sources':[{'source':'S1','locator':'L1'}]}}
    outcomes={'R1':dict(status='failed',findings=[dict(dimension='field_conditions',scope='detail',
        affected_paths=['detail_contract.fields.F1.required'],traces=[])])}
    fixed=copy.deepcopy(raw['requirements'][0]['detail_contract'])
    if fault=='applicability':fixed['applicability']['fields']['applicable']=False
    elif fault=='delete_object':fixed['fields']=[]
    elif fault=='unrelated_property':fixed['interactions'][0]['success']='提交即成功'
    else:fixed['fields'][0]['evidence']['required']={'status':'proposal'}
    schema=dict(type='object',required=['rows'],properties={'rows':dict(type='array')})
    with pytest.raises(Problem):review.apply_correction(raw,dict(rows=[dict(row_key='R1',detail_contract=fixed)]),schema,outcomes)


def test_source_review_cannot_skip_another_source_property(tmp_path):
    *_,plan=fixture(tmp_path)
    row=plan['rows'][0]
    key=next(k for k,r in row['property_refs'].items() if r['path']=='detail_contract.fields.F1.required')
    row['source_claims']=[dict(key=key,path='detail_contract.fields.F1.required',value='必填',
        source_refs=[{k:plan['sources'][0][k] for k in ('source_id','excerpt_id')}])]
    value=model_response(plan);value['rows'][0]['source_checks']=[]
    with pytest.raises(Problem):review.validate(plan,value)


def test_current_manual_answer_without_excerpt_remains_reviewable(tmp_path):
    p,local,excerpts,raw,canonical,report,_=fixture(tmp_path)
    question=p['questions'][0]
    question.update(answer='结果未知时只核对原请求，不自动重发。',understanding_status='applied',answer_source_refs=[])
    context=dict(user_message='核对已有回答，不重新决定业务。',current_rows=[dict(row_key='R1',questions=[
        dict(text=question['question'],answer=question['answer'],saved_group_words=None)])])
    plan=review.prepare(raw,canonical,report,excerpts,project=p,generation_context=context)
    assert plan['human_statements'][1]['text']==question['answer']
    assert plan['human_statements'][1]['project_revision']==p['revision']
    assert plan['human_statements'][1]['question_text']==question['question']
    value=response(plan);check=value['rows'][0]['checks'][0]
    check['source_quotes']=[];check['human_quotes']=[dict(statement_id=question['id']+':answer',quote=question['answer'])]
    assert validate_fixture(plan,value)['R1']['status']=='passed'
    check['human_quotes'][0]['quote']='这是旧候选自己的说法'
    with pytest.raises(Problem):validate_fixture(plan,value)
    context['current_rows'][0]['questions'][0]['answer']='旧版本回答'
    changed=review.prepare(raw,canonical,report,excerpts,project=p,generation_context=context)
    assert all(r['kind']!='saved_answer' for r in changed['human_statements'])
    question['answer']='是';context['current_rows'][0]['questions'][0]['answer']='是'
    short=review.prepare(raw,canonical,report,excerpts,project=p,generation_context=context)['human_statements'][1]
    assert short['text']=='是' and short['question_id']==question['id'] and short['question_text']==question['question']


def test_interaction_finding_does_not_authorize_unrelated_entry(tmp_path):
    *_,raw,canonical,report,plan=fixture(tmp_path)
    value=response(plan);check=value['rows'][0]['checks'][0]
    check.update(verdict='unclear',affected_paths=['detail_contract.interactions.I1.pending'],
        candidate_quotes=[dict(path='detail_contract.interactions.I1.pending',quote=plan['rows'][0]['property_text']['detail_contract.interactions.I1.pending'])])
    outcomes=validate_fixture(plan,value)
    fixed=copy.deepcopy(raw['requirements'][0]['detail_contract'])
    fixed['interactions'][0].update(pending='提交后显示处理中。',entry='全局跨站点编辑入口')
    schema=dict(type='object',required=['rows'],properties={'rows':dict(type='array')})
    with pytest.raises(Problem):review.apply_correction(raw,dict(rows=[dict(row_key='R1',detail_contract=fixed)]),schema,outcomes)
    fixed['interactions'][0]['entry']=raw['requirements'][0]['detail_contract']['interactions'][0]['entry']
    assert review.apply_correction(raw,dict(rows=[dict(row_key='R1',detail_contract=fixed)]),schema,outcomes)


@pytest.mark.parametrize('kind',['normal','exception'])
def test_result_tail_is_not_complete_role_reachability(tmp_path,kind):
    *_,plan=fixture(tmp_path)
    plan['rows'][0]['detail_contract']['flow']['nodes'][3]['interaction_id']='I1'
    from app.core import digest
    plan['rows'][0]['candidate_hash']=digest(plan['rows'][0]['detail_contract'])
    value=response(plan);check=value['rows'][0]['checks'][0]
    trace=next(t for t in check['traces'] if t['kind']==kind)
    trace['edge_ids']=['E3']
    with pytest.raises(Problem):validate_fixture(plan,value)
    check.update(verdict='contradicted',affected_paths=[check['candidate_quotes'][0]['path']])
    assert validate_fixture(plan,value)['R1']['status']=='failed'


def test_operation_entry_need_not_repeat_global_open_page(tmp_path):
    *_,plan=fixture(tmp_path);value=response(plan)
    for check in value['rows'][0]['checks']:
        for trace in check['traces']:trace['edge_ids']=trace['edge_ids'][1:]
    assert validate_fixture(plan,value)['R1']['status']=='passed'


def test_empty_operations_still_require_source_bound_coverage(tmp_path):
    p,local,excerpts,raw,_,_,_=fixture(tmp_path)
    detail=raw['requirements'][0]['detail_contract']
    for section in ('interactions','flow','state_transitions'):
        detail['applicability'][section]=dict(applicable=False,reason='生成器声称不适用，不是业务决定。')
        detail[section]=dict(nodes=[],edges=[]) if section=='flow' else []
    canonical,report=adapt(raw,p,excerpts,'clarify',local)
    plan=review.prepare(raw,canonical,report,excerpts)
    value=response(plan)
    value['rows'][0]['checks']=[c for c in value['rows'][0]['checks'] if c['dimension']!='coverage']
    with pytest.raises(Problem):validate_fixture(plan,value)
    complete=response(plan);coverage=next(c for c in complete['rows'][0]['checks'] if c['dimension']=='coverage')
    coverage.update(verdict='contradicted',affected_paths=['detail_contract.applicability.interactions.applicable'])
    assert validate_fixture(plan,complete)['R1']['status']=='failed'


def test_retry_diagnosis_is_bound_to_exact_latest_candidate_and_source_version(tmp_path):
    from app.business_draft import _input
    from app.core import digest
    p,local,excerpts,raw,_,_,plan=fixture(tmp_path)
    value=response(plan);finding=value['rows'][0]['checks'][0]
    finding.update(verdict='unclear',affected_paths=[finding['candidate_quotes'][0]['path']],
        explanation='该操作的结果含义不清。',correction='按已有来源明确结果归属。')
    outcomes=validate_fixture(plan,value)
    outcomes['R1'].update(raw_candidate_hash=digest(raw['requirements'][0]['detail_contract']),input_revision=p['revision'])
    _,report=adapt(raw,p,excerpts,'clarify',local,detail_semantic_reviews=outcomes)
    assert report['field_rejected'][0]['reason'].startswith('本次详细定义需纠正：')
    assert '结果含义不清' in report['field_rejected'][0]['reason']
    p['business_draft_reviews']=[report]
    candidate=_input(p,excerpts,'重新核对',local)['current_rows'][0]['rejected_detail_candidate']
    diagnostic=candidate['semantic_diagnostics']
    assert diagnostic['raw_candidate_hash']==digest(report['field_rejected'][0]['raw'])
    assert diagnostic['status']=='previous_model_diagnosis_not_human_decision'
    assert diagnostic['source_bindings_current'] and diagnostic['requires_current_source_recheck']
    p['sources'][0]['excerpts'][0]['text']='已更改的来源，不能继续使用旧引句作为确认依据。'
    changed=_input(p,excerpts,'重新核对',local)['current_rows'][0]['rejected_detail_candidate']
    assert changed['semantic_diagnostics']['source_bindings_current'] is False
    report['field_rejected'][0]['raw']['interactions'][0]['name']='另一份候选'
    mismatched=_input(p,excerpts,'重新核对',local)['current_rows'][0]['rejected_detail_candidate']
    assert 'semantic_diagnostics' not in mismatched and mismatched['semantic_diagnostic_notice']


@pytest.mark.parametrize('change',['question','related_refs','status'])
def test_short_answer_binding_changes_when_its_question_or_decision_context_changes(tmp_path,change):
    from app.business_draft import _rejected_detail_candidate
    from app.core import digest
    p,local,excerpts,raw,canonical,report,_=fixture(tmp_path)
    question=p['questions'][0];question.update(answer='是',understanding_status='applied')
    context=dict(current_rows=[dict(row_key='R1',questions=[dict(text=question['question'],answer='是')])])
    plan=review.prepare(raw,canonical,report,excerpts,project=p,generation_context=context)
    human=plan['human_statements'][0];content_hash=human['content_hash']
    value=response(plan);finding=value['rows'][0]['checks'][0]
    finding.update(verdict='unclear',affected_paths=[finding['candidate_quotes'][0]['path']],
        human_quotes=[dict(statement_id=human['statement_id'],quote='是')])
    outcomes=validate_fixture(plan,value)
    outcomes['R1'].update(raw_candidate_hash=digest(raw['requirements'][0]['detail_contract']),input_revision=p['revision'])
    _,report=adapt(raw,p,excerpts,'clarify',local,detail_semantic_reviews=outcomes)
    p['business_draft_reviews']=[report]
    assert _rejected_detail_candidate(p,local['requirement_id'])['semantic_diagnostics']['human_bindings_current']
    if change=='question':question['question']='相反操作是否允许？'
    elif change=='related_refs':question['related_refs']=[*question.get('related_refs',[]),'REQ-other']
    else:question['understanding_status']='pending'
    latest=_rejected_detail_candidate(p,local['requirement_id'])['semantic_diagnostics']
    assert latest['human_bindings_current'] is False
    assert latest['human_bindings'][0]['content_hash']==content_hash==digest(question['answer'])
