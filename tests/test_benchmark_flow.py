"""User-directed reference analysis over an isolated loopback model, never a paid provider."""
import copy
import json
import jsonschema
import pytest

from tests.test_ui02_actions import case, plan, start, finished, image


def material(c, root, purpose='reference'):
    return c.post(root+'/sources/text',json=dict(expected_revision=c.get(root).json()['revision'],
        text='管理员维护联系人列表。只读角色不能修改。只读角色进入列表，不允许修改。',purpose=purpose)).json()


def comparison(model, invalid=None):
    def transform(value, body, context):
        if value['stage']!='ingest':return value
        if not context.get('benchmark_request'):return value
        source_ids=context['benchmark_request']['source_ids']
        ex=next(e for e in context['excerpts'] if e['source_id'] in source_ids)
        ref=dict(source_id=ex['source_id'],excerpt_id=ex['id'])
        for row in value['proposals']:
            row['source_refs']=[ref]
            row['epistemic_status']='proposed'
        value['used_source_refs']=[ref]
        value['benchmark_analysis']=dict(summary='对标资料支持参考权限与列表；本项目是否采用待核对。',
            observations=[dict(title='列表与只读操作',reference_quote=ex['text'],source_ref=ref,
                difference='',suggested_application='可作为列表需求和权限验收的候选。',proposal_refs=['TMP-R','TMP-AC'])],limitations=[])
        if invalid=='promotion':value['proposals'][0]['epistemic_status']='reported'
        elif invalid=='intent_promotion':
            intent=next(e for e in context['excerpts'] if e['source_id'] not in source_ids)
            value['proposals'][0]['source_refs'].append(dict(source_id=intent['source_id'],excerpt_id=intent['id']))
            value['proposals'][0]['epistemic_status']='reported'
        elif invalid=='current':value['proposals'][0]['applies_to']='as_is'
        elif invalid=='quote':value['benchmark_analysis']['observations'][0]['reference_quote']='竞品每天必须推送三次'
        elif invalid=='missing':del value['benchmark_analysis']
        return value
    model['transform']=transform


def test_benchmark_end_to_end_keeps_human_context_and_saves_reviewable_report(case):
    c,app,model,root=case
    source=material(c,root)
    before=c.get(root).json()
    assert c.post(root+'/product-context',json=dict(expected_revision=before['revision'],
        values={'intent':'我的联系人模块只增加备注','preserve_scope':'已有导出原样保留'})).status_code==200
    original=copy.deepcopy(c.get(root).json()['product_context'])
    comparison(model)
    request=dict(target='  示例联系人产品  ',focus='列表与只读操作',source_ids=[source['id']])
    body,preview=plan(c,root,'organize','想对标示例产品的列表操作，生成待审草稿。',benchmark=request)
    assert preview['label']=='对标并填充需求草稿'
    assert preview['benchmark']['target']=='示例联系人产品'
    assert not preview['missing'] and not model['requests']
    assert start(c,root,body,preview,authorize=False).status_code==403
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='succeeded'
    p=c.get(root).json()
    assert p['product_context']==original
    assert all(i['selection_status']=='candidate' for i in p['items'])
    assert all(i['epistemic_status']=='proposed' for i in p['items'])
    assert not p['baselines'] and not p['documents']
    report=p['benchmark_analysis']
    assert report['request']['target']=='示例联系人产品' and report['run_id'] in task['run_ids']
    assert all(ref in {i['id'] for i in p['items']} for ref in report['observations'][0]['proposal_refs'])
    assert p['sources'][0]['purpose']=='reference' and p['sources'][0]['benchmark_reference']
    assert p['messages'][0]['stage']=='interview' and p['messages'][0]['text']==body['message']
    assert sum(m['text']==body['message'] for m in p['messages'])==1
    ctx=json.loads(model['requests'][0]['messages'][1]['content'][0]['text'])
    assert ctx['benchmark_request']==report['request']
    header=json.loads(model['requests'][0]['messages'][0]['content'].split('可信任务头：',1)[1])
    assert 'benchmark_analysis' in header['schema']['required']
    jsonschema.Draft202012Validator(header['schema']).validate(header['ingest_contract']['example'])


@pytest.mark.parametrize('invalid',['promotion','intent_promotion','current'])
def test_invalid_reference_claim_is_not_activated_or_repaired(case,invalid):
    c,app,model,root=case;source=material(c,root);comparison(model,invalid)
    body,preview=plan(c,root,'organize','对标竞品列表',benchmark=dict(target='竞品',focus='',source_ids=[source['id']]))
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root);p=c.get(root).json()
    assert task['status']=='failed' and task['error']=='SEMANTIC_BLOCKED' and task['calls']==1
    assert not p['items'] and not p.get('benchmark_analysis')
    assert p['messages'][0]['text']==body['message']


def test_empty_chat_saves_explicit_task_intent_and_requires_fresh_source_authorization(case):
    c,app,model,root=case;source=material(c,root);comparison(model)
    request=dict(target='示例产品',focus='只读列表',source_ids=[source['id']])
    body,preview=plan(c,root,'organize','',benchmark=request)
    assert all(r['needs_authorization'] for r in preview['recipients'])
    assert start(c,root,body,preview,authorize=False).status_code==403 and not model['requests']
    assert start(c,root,body,preview).status_code==200 and finished(c,root)['status']=='succeeded'
    p=c.get(root).json()
    assert p['messages'][0]['text']=='对标对象：示例产品\n参考范围：只读列表'
    assert p['sources'][-1]['origin']=='benchmark_intake'
    body,preview=plan(c,root,'organize','',benchmark=request)
    assert all(r['needs_authorization'] for r in preview['recipients'])
    count=len(model['requests'])
    assert start(c,root,body,preview,authorize=False).status_code==403
    assert len(model['requests'])==count


def test_screenshot_benchmark_uses_actual_vision_observation_and_keeps_candidates(case):
    c,app,model,root=case;image(c,root)
    source=c.get(root).json()['sources'][0]
    comparison(model)
    base=model['transform']
    def visual(value,body,context):
        value=base(value,body,context)
        if value['stage']=='ingest':
            row=value['benchmark_analysis']['observations'][0]
            observed=next(o for m in context['vision_observations'] for o in m['result']['observations']
                if o['source_ref']==row['source_ref'] and o['uncertain'] is False)
            row['reference_quote']=observed['observation']
        return value
    model['transform']=visual
    body,preview=plan(c,root,'organize','',benchmark=dict(target='截图示例',focus='列表入口',source_ids=[source['id']]))
    assert preview['stages']==['vision','ingest']
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root);p=c.get(root).json()
    assert task['status']=='succeeded' and task['calls']==2 and len(model['requests'])==2
    row=p['benchmark_analysis']['observations'][0]
    assert row['reference_quote']=='合成图中可见列表和新增按钮'
    assert row['evidence_kind']=='visual_observation'
    assert all(i['selection_status']=='candidate' for i in p['items'])
    assert not p['baselines'] and not p['documents']


def test_failed_source_and_scope_change_cannot_start_benchmark(case):
    c,app,model,root=case;source=material(c,root)
    benchmark=dict(target='竞品',focus='',source_ids=[source['id']])
    body,preview=plan(c,root,'organize','',benchmark=benchmark)
    changed={**body,'benchmark':{**benchmark,'focus':'改为计费规则'}}
    assert start(c,root,changed,preview).status_code==409
    with app.state.store.edit(root.rsplit('/',1)[1],c.get(root).json()['revision'],'合成读取失败') as (p,db):
        p['sources'][0].update(parse_status='failed',excerpts=[],failure_reason='合成网页读取失败')
    r=c.post(root+'/actions/plan',json={**body,'expected_revision':c.get(root).json()['revision']})
    assert r.status_code==400 and r.json()['code']=='BENCHMARK_SOURCE_UNREADABLE'
    assert not model['requests']


def test_benchmark_scope_only_accepted_on_organize_and_later_roles_stay_guarded(case):
    c,app,model,root=case;source=material(c,root);comparison(model)
    request=dict(target='竞品',focus='',source_ids=[source['id']])
    r=c.post(root+'/actions/plan',json=dict(expected_revision=c.get(root).json()['revision'],action='interview',message='对标',benchmark=request))
    assert r.status_code==400 and r.json()['code']=='BENCHMARK_INVALID'
    body,preview=plan(c,root,'organize','对标后核对',benchmark=request)
    assert start(c,root,body,preview).status_code==200 and finished(c,root)['status']=='succeeded'
    previous=copy.deepcopy(c.get(root).json()['benchmark_analysis'])
    model.pop('transform')
    body,preview=plan(c,root,'organize','')
    assert start(c,root,body,preview).status_code==200
    task=finished(c,root)
    assert task['status']=='failed' and task['error']=='SEMANTIC_BLOCKED'
    assert c.get(root).json()['benchmark_analysis']==previous
