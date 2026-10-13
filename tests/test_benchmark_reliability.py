"""Offline reproductions of malformed benchmark output; no private call fixtures."""
import asyncio
import copy
import json

import httpx
import pytest

from app.core import Problem
from app.model_evidence import ModelCallEvidence
from app.provider import DEFAULT, Provider, normalize_ingest_result_tail
from tests.test_runtime import empty_ingest
from tests.test_benchmark_flow import comparison, material
from tests.test_ui02_actions import case, plan, start, finished


def split_root():
    value=empty_ingest()
    value['summary']='保留原始总结与 {} 字面括号'
    value['benchmark_analysis']=dict(summary='对标总结',observations=[],limitations=['尚无确定差异'])
    prefix=copy.deepcopy(value)
    suffix={key:prefix.pop(key) for key in ('product_context_proposal','benchmark_analysis','result')}
    raw=json.dumps(prefix,ensure_ascii=False)+','+json.dumps(suffix,ensure_ascii=False)[1:]
    return value,raw


def test_ingest_one_root_close_is_normalized_without_changing_any_field():
    expected,raw=split_root()
    value,offset=normalize_ingest_result_tail(' \n'+raw)
    assert value==expected
    assert (' \n'+raw)[offset]=='}'
    assert json.loads((' \n'+raw)[:offset]+(' \n'+raw)[offset+1:])==expected


@pytest.mark.parametrize('variant',[
    'duplicate_prefix','duplicate_tail','duplicate_across','extra_field','extra_object',
    'truncated','nested_bracket','wrong_stage','missing_required','non_json_constant'])
def test_ingest_normalization_refuses_ambiguous_or_incomplete_content(variant):
    _,raw=split_root()
    if variant=='duplicate_prefix':raw=raw.replace('"summary":','"summary":"重复", "summary":',1)
    elif variant=='duplicate_tail':raw=raw[:-1]+',"result":{"understanding":"第二份","material_limits":[]}}'
    elif variant=='duplicate_across':raw=raw.replace('"stage": "ingest"','"stage": "ingest", "result": {}',1)
    elif variant=='extra_field':raw=raw[:-1]+',"unexpected":null}'
    elif variant=='extra_object':raw+='{}'
    elif variant=='truncated':raw=raw[:-3]
    elif variant=='nested_bracket':raw=raw.replace('"proposals": []','"proposals": [}]',1)
    elif variant=='wrong_stage':raw=raw.replace('"ingest"','"review"',1)
    elif variant=='missing_required':raw=raw.replace('"schema_version": "1.1", ','',1)
    elif variant=='non_json_constant':raw=raw.replace('"understanding": "合成验证"','"understanding": NaN',1)
    assert normalize_ingest_result_tail(raw) is None


def test_provider_keeps_raw_benchmark_evidence_and_records_syntax_derivation(tmp_path,monkeypatch):
    expected,raw=split_root()
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'model':'synthetic','usage':{'total_tokens':2},'choices':[{
            'message':{'content':raw},'finish_reason':'stop'}]}))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=transport,**kw))
    provider=Provider(tmp_path/'absent.env');provider.keys['https://api.deepseek.com']='synthetic-test-key'
    proof=ModelCallEvidence(tmp_path,'CALL-test','RUN-test','ingest','synthetic','https://api.deepseek.com',None,'synthetic-test-key')
    actual,meta=asyncio.run(provider.request(DEFAULT,[],evidence=proof))
    assert actual==expected
    assert meta['syntax_normalization']==proof.value['syntax_normalization']
    saved=json.loads(proof.path.read_text('utf-8'))
    assert saved['final_output']==raw
    assert saved['syntax_normalization']['rule']=='ingest-premature-root-close-before-metadata-1'


def reference_errors(model, mutation=None):
    comparison(model)
    base=model['transform']
    original={}
    def transform(value,body,context):
        nonlocal original
        value=base(value,body,context)
        if len(model['requests'])==1:
            ref=copy.deepcopy(value['used_source_refs'][0])
            value['questions']=[dict(temp_id='QTMP-memory',topic_key='memory',
                question='是否保存历史？是否按学科隔离？',why='分别决定保存和隔离范围。',
                options=[],blocking=True,blocking_stage=3,related_refs=['TMP-R'],source_refs=[ref],decision_points=[])]
            value['benchmark_analysis']['observations'][0]['proposal_refs']+=['QTMP-memory','TMP-absent']
            value['proposals'][0]['related_refs']+=['TMP-live-absent']
            original=copy.deepcopy(value)
            return value
        value=copy.deepcopy(original)
        value['benchmark_analysis']['observations'][0]['proposal_refs']=['TMP-R','TMP-AC']
        value['proposals'][0]['related_refs'].remove('TMP-live-absent')
        parent=value['questions'][0]
        for index,question in enumerate(['是否保存历史？','是否按学科隔离？']):
            child={k:copy.deepcopy(v) for k,v in parent.items() if k!='decision_points'}
            child.update(temp_id=f'QTMP-memory-{index}',topic_key=f'memory-{index}',question=question)
            parent['decision_points'].append(child)
        if mutation=='conclusion':value['benchmark_analysis']['summary']='已确认采用竞品规则'
        elif mutation=='quote':value['benchmark_analysis']['observations'][0]['reference_quote']='只读角色不能修改。'
        elif mutation=='drop_valid_link':value['benchmark_analysis']['observations'][0]['proposal_refs']=['TMP-R']
        elif mutation=='invent_link':value['proposals'][0]['related_refs'].append('QTMP-memory')
        return value
    model['transform']=transform


def test_benchmark_repairs_all_reference_errors_and_compound_question_in_one_retry(case):
    c,app,model,root=case;source=material(c,root);reference_errors(model)
    body,preview=plan(c,root,'organize','对标资料',benchmark=dict(target='参考产品',focus='',source_ids=[source['id']]))
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='succeeded',task
    assert task['calls']==2
    repair=model['requests'][1]['messages'][-1]['content']
    assert 'benchmark_analysis.observations[0].proposal_refs 不存在或类型错误' in repair
    assert 'proposals[0].related_refs 引用不存在' in repair
    assert 'questions[0] QTMP-memory 含多个独立问句' in repair
    p=c.get(root).json()
    assert all(row['selection_status']=='candidate' for row in p['items'])
    assert not p['baselines'] and not p['documents']
    assert len([q for q in p['questions'] if not q.get('superseded_by')])>=2
    assert set(p['benchmark_analysis']['observations'][0]['proposal_refs'])<={row['id'] for row in p['items']}


@pytest.mark.parametrize('mutation',['conclusion','quote','drop_valid_link','invent_link'])
def test_benchmark_format_repair_cannot_change_conclusions_or_valid_links(case,mutation):
    c,app,model,root=case;source=material(c,root);reference_errors(model,mutation)
    body,preview=plan(c,root,'organize','对标资料',benchmark=dict(target='参考产品',focus='',source_ids=[source['id']]))
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='failed' and task['error']=='SEMANTIC_BLOCKED',task
    assert task['calls']==2
    p=c.get(root).json()
    assert not p['items'] and not p.get('benchmark_analysis')


@pytest.mark.parametrize('broken',['observations','source_ref','temp_id','missing_analysis'])
def test_unreadable_benchmark_anchor_stops_without_internal_error_or_paid_repair(case,broken):
    c,app,model,root=case;source=material(c,root);comparison(model)
    base=model['transform']
    def transform(value,body,context):
        value=base(value,body,context)
        value.pop('result')
        if broken=='observations':value['benchmark_analysis']['observations']=None
        elif broken=='source_ref':value['benchmark_analysis']['observations'][0]['source_ref']=None
        elif broken=='temp_id':value['proposals'][0].pop('temp_id')
        else:value.pop('benchmark_analysis')
        return value
    model['transform']=transform
    body,preview=plan(c,root,'organize','对标资料',benchmark=dict(target='参考产品',focus='',source_ids=[source['id']]))
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='failed' and task['error']=='SEMANTIC_BLOCKED',task
    assert task['calls']==1 and len(model['requests'])==1
    assert not c.get(root).json()['items']


def test_missing_other_root_field_is_repaired_with_complete_analysis_preserved(case):
    c,app,model,root=case;source=material(c,root);comparison(model)
    base=model['transform']
    def transform(value,body,context):
        value=base(value,body,context)
        if len(model['requests'])==1:value.pop('result')
        return value
    model['transform']=transform
    body,preview=plan(c,root,'organize','对标资料',benchmark=dict(target='参考产品',focus='',source_ids=[source['id']]))
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='succeeded' and task['calls']==2,task
