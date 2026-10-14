"""Explicit real-Agent consistency trials on a frozen synthetic cloud scenario.

No listener, no daily project writes, no automatic business approval or rerun.
Each invocation runs one requested action and preserves raw provider evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TITLE = '合成自测：储能云平台派单结果查询'
BRIEF = '''本文件是冻结的工程自测场景，不是用户业务决定。仅验证已存在派单任务的查询，不生成新的派单任务。
储能云平台运维员在有站点查看权限时，可以从派单任务详情查询该任务的结果。每个任务只属于一个站点。
字段：任务编号为系统生成的非空只读唯一字符串；站点名称为资产台账的非空只读字符串；派单结果为成功、失败、结果未知三种枚举，系统记录提供、非空只读，不设默认值。
成功和失败均是终态，不允许再次查询；只有结果未知时显示并启用查询按钮。已展示的成功或失败详情仍可阅读。
查询期间禁止重复点击，查询不可取消，离开页面不撤销已发查询；重新进入以系统记录为准，不自动查询。
查询有结果时显示系统返回的成功或失败；系统返回结果未知时仍显示结果未知且允许再次手工查询。查询请求失败或超过10秒未响应时，显示查询失败提示，原派单结果不变，查询按钮恢复。
无查看权限时不能查看任务详情或查询；查询响应返回时重新校验权限，权限失效则隐藏详情并提示无权限，不展示结果。
本期不含首次派单、失败重试、修改负责人、自动轮询、通知、批量汇总、权限管理及站点台账维护。具体传输协议和实现由研发设计。
'''
CONTRADICTION = '成功、失败和结果未知三种状态都显示并启用查询按钮。'
DECISION = '仅结果未知时显示并启用查询按钮；成功和失败均是终态，不允许再次查询。请同步修正查询交互的前置条件，保留已有流程、状态及验收规则。'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def seed(store):
    from app.sources import save_source
    from app.requirements import behavior_fingerprint
    from app.core import now
    p = store.create(TITLE)
    source = save_source(store, '冻结合成场景.txt', BRIEF.encode('utf-8'), 'goal')
    refs = [dict(source_id=source['id'], excerpt_id=e['id']) for e in source['excerpts']]
    fields = []
    for fid, name, meaning, dtype, src in [
        ('F1', '任务编号', '唯一标识当前派单任务', 'id', '系统记录'),
        ('F2', '站点名称', '该任务所属站点的名称', 'text', '资产台账'),
        ('F3', '派单结果', '当前任务的派单结果', 'enum', '系统记录')]:
        fields.append(dict(id=fid, name=name, meaning=meaning, data_type=dtype, source=src,
            required='非空，只读展示', default_value='无默认值，以系统记录为准',
            constraints='必须对应当前任务' + ('；唯一且非空' if fid == 'F1' else ''),
            visibility='有当前站点查看权限时可见', editability='不可编辑',
            **({'enum_values': ['成功', '失败', '结果未知']} if fid == 'F3' else {})))
    detail = dict(schema_version=1,
        applicability={k: dict(applicable=True, reason='') for k in ('fields','interactions','flow','state_transitions')},
        fields=fields,
        interactions=[dict(id='I1', name='查询派单结果', actor='有当前站点查看权限的运维员',
            entry='派单任务详情', preconditions=CONTRADICTION,
            trigger='点击查询按钮', inputs='当前任务编号', validation='校验站点查看权限；任务结果须为结果未知',
            pending='显示查询中并禁用查询按钮，最长等待10秒', repeat='查询期间禁止重复点击；结束后仅结果未知可再次手工查询',
            cancel='不可取消；离开页面不撤销查询，重入读取系统记录且不自动查询',
            success='响应返回时重新校验权限；有权限则显示系统返回的成功、失败或结果未知，无权限隐藏详情并提示无权限',
            failure='请求失败或超过10秒未响应，显示查询失败提示，派单结果不变，恢复查询按钮',
            effects='更新当前任务的显示结果，不创建派单任务', field_refs=['F1','F3'])],
        flow=dict(nodes=[
            dict(id='N1', kind='start', label='打开任务详情'),
            dict(id='N2', kind='decision', label='是否有站点查看权限', actor='系统'),
            dict(id='N3', kind='end', label='无权限，隐藏详情并提示'),
            dict(id='N4', kind='decision', label='当前派单结果是否未知', actor='系统'),
            dict(id='N5', kind='end', label='成功或失败，只读查看终态，无查询按钮'),
            dict(id='N6', kind='action', label='用户点击查询，查询期间禁重复', actor='运维员', interaction_id='I1'),
            dict(id='N7', kind='decision', label='10秒内取得有效响应且仍有权限', actor='系统'),
            dict(id='N8', kind='end', label='有权限，显示返回结果；仍未知可再次手工查询'),
            dict(id='N9', kind='end', label='失败或超时，原结果不变，提示失败并恢复查询按钮'),
        ], edges=[
            dict(id='E1', **{'from':'N1','to':'N2'}),
            dict(id='E2', **{'from':'N2','to':'N3'}, condition='无权限'),
            dict(id='E3', **{'from':'N2','to':'N4'}, condition='有权限'),
            dict(id='E4', **{'from':'N4','to':'N5'}, condition='成功或失败'),
            dict(id='E5', **{'from':'N4','to':'N6'}, condition='结果未知，用户主动点击查询'),
            dict(id='E6', **{'from':'N6','to':'N7'}),
            dict(id='E7', **{'from':'N7','to':'N8'}, condition='有效响应且有权限'),
            dict(id='E8', **{'from':'N7','to':'N9'}, condition='请求失败或超时且有权限'),
            dict(id='E9', **{'from':'N7','to':'N3'}, condition='权限已失效'),
        ]),
        state_transitions=[
            dict(id='T1', from_state='结果未知', to_state='成功', trigger='查询返回', condition='系统返回成功且用户仍有查看权限', prohibited_actions='成功后禁止再次查询'),
            dict(id='T2', from_state='结果未知', to_state='失败', trigger='查询返回', condition='系统返回失败且用户仍有查看权限', prohibited_actions='失败后禁止再次查询'),
            dict(id='T3', from_state='结果未知', to_state='结果未知', trigger='查询结束', condition='系统仍返回未知或查询请求失败/超时，且用户仍有查看权限', prohibited_actions='查询处理中禁止重复点击；结束后允许再次手工查询'),
        ])
    with store.edit(p['id'], p['revision'], '冻结合成夹具，非业务确认') as (p, _):
        p.update(workflow_mode='shared-draft', reference_mode='builtin',
                 product_context={'product':'储能云平台', 'scope':BRIEF},
                 test_provenance={'type':'synthetic', 'user_business_acceptance':'NOT_RUN'})
        p['sources'].append(source)
        req = dict(id='REQ-QUERY', kind='requirement', title='派单任务结果查询', statement='有站点查看权限的运维员仅能查询结果未知的派单任务；成功和失败均为终态，不允许再次查询。',
            applies_to='to_be', epistemic_status='reported', selection_status='selected',
            source_refs=refs, related_refs=[], change_type='new', content_version=1,
            behavior={'actor':'有站点查看权限的运维员','entry':'派单任务详情',
                'flow':'仅结果未知时手工查询；查询后显示成功、失败或未知。',
                'data':'任务编号、站点名称、派单结果，以系统记录为准。',
                'permissions':'进入及响应返回时检查站点查看权限；失效隐藏详情。',
                'result':'显示返回结果；成功或失败禁止再次查询。',
                'exceptions':'查询失败或超过10秒未响应，结果不变，提示失败并恢复按钮。'},
            detail_required=True, detail_contract=detail)
        req['behavior_review'] = dict(content_hash=behavior_fingerprint(req), actor='SIMULATED_TEST_REVIEW', reviewed_at=now())
        p['items'] = [req]
        for n, statement in enumerate([
            '有权限且派单结果为结果未知时，点击查询后按钮禁用；成功响应显示成功并隐藏查询按钮。',
            '派单结果已经成功或失败时，仅显示结果，不显示查询按钮，禁止发起查询。',
            '查询请求失败或超过10秒未响应时，显示查询失败，结果仍为结果未知，恢复按钮。',
            '系统返回结果未知且仍有权限时，继续显示结果未知并允许再次手工查询。',
            '查询响应返回时用户权限已失效，则隐藏详情并提示无权限，不展示响应结果。',
        ], 1):
            p['items'].append(dict(id=f'AC-QUERY-{n}', kind='acceptance', title=f'查询验收{n}',
                statement=statement, applies_to='to_be', epistemic_status='reported',
                selection_status='selected', source_refs=refs, related_refs=['REQ-QUERY'], content_version=1))
    return store.get(p['id'])


async def run(args):
    from scripts.run_chat_to_prd_validation import load_config, safe_config, call_evidence, assert_idle
    from app.actions import Actions
    from app.core import digest
    from app.platform_knowledge import RELATIVE_PATH
    from app.provider import Provider, origin
    from app.store import Store
    from app.workflow import Workflow
    output = args.output.resolve()
    if not output.is_relative_to((ROOT/'work').resolve()):
        raise ValueError('Output must be under work/')
    folder = output/'isolated-store'
    assert_idle(folder)
    output.mkdir(parents=True, exist_ok=True)
    store = Store(folder)
    state_path = output/'state.json'
    if args.phase == 'seed':
        if state_path.exists():
            raise ValueError('Existing trial is immutable; use another output folder')
        project = seed(store)
        write(state_path, {'project_id': project['id']})
        write(output/'frozen-oracle.json', dict(brief=BRIEF, contradiction=CONTRADICTION,
              simulated_answer=DECISION, user_business_acceptance='NOT_RUN'))
        write(output/'seed-project.json', project)
        print(json.dumps({'project_id': project['id'], 'phase':'seed'}, ensure_ascii=False))
        return
    pid = json.loads(state_path.read_text('utf-8'))['project_id']
    if args.phase == 'answer':
        from app.decision_review import save_raw_answer
        p=store.get(pid)
        if not args.question_id:
            raise ValueError('A reviewed question ID is required')
        with store.edit(pid,p['revision'],'SIMULATED_TEST_REVIEW: 冻结合成答案') as (p,db):
            answer=save_raw_answer(p,args.question_id,DECISION,store)
            store.record(pid,'synthetic_test_review',dict(question_id=args.question_id,
                answer=DECISION,oracle_sha256=hashlib.sha256((output/'frozen-oracle.json').read_bytes()).hexdigest(),
                actor='SIMULATED_TEST_REVIEW',user_business_acceptance='NOT_RUN'),db=db)
        write(output/'saved-synthetic-answer.json',answer)
        (output/'clarify-message.md').write_text(DECISION,encoding='utf-8')
        print(json.dumps(dict(phase='answer',question_id=args.question_id,answer_id=answer['id']),ensure_ascii=False))
        return
    if args.phase == 'confirm':
        from app.core import digest
        from app.requirements import item_content
        import app.core as core
        import httpx
        if not args.review_file:
            raise ValueError('Explicit hash-bound synthetic review manifest required')
        manifest=json.loads(args.review_file.read_text('utf-8'))
        p=store.get(pid)
        if (manifest.get('actor')!='SIMULATED_TEST_REVIEW' or manifest.get('project_id')!=pid
                or manifest.get('revision')!=p['revision']):
            raise ValueError('Review manifest is not for the current synthetic snapshot')
        candidates=[]
        for row in manifest['candidates']:
            item=next(i for i in p['items'] if i['id']==row['id'])
            if row['content_hash']!=digest(item_content(item)) or item['selection_status']!='candidate':
                raise ValueError('Candidate has changed since independent review')
            candidates.append(item)
        qid=manifest['question_id']
        question=next(q for q in p['questions'] if q['id']==qid)
        if question['answer']!=DECISION:
            raise ValueError('The frozen synthetic answer has changed')
        core.DATA=output/'global-import'
        os.environ['RA_DATA_DIR']=str(core.DATA)
        from app.main import create_app
        app=create_app(folder)
        origin_url='http://127.0.0.1:8765'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=origin_url) as client:
            session=(await client.get('/api/session')).json()
            headers={'Origin':origin_url,'X-CSRF-Token':session['csrf']}
            payload=dict(expected_revision=p['revision'],question_id=qid,
                answer_id=question['decision_answer_id'],
                coverage=[dict(decision_id=qid,status='clear',answer_quote=DECISION,
                    candidate_item_ids=[i['id'] for i in candidates])],
                groups=[dict(id='synthetic-reviewed-change',title='核对查询前置条件及关联验收',decision_ids=[qid],
                    target_item_ids=[i['target_item_id'] for i in candidates],candidate_item_ids=[i['id'] for i in candidates])],
                answer_implementations=manifest['answer_implementations'],
                review_behavior_item_ids=[i['id'] for i in candidates if i['kind']=='requirement'])
            response=await client.post('/api/projects/'+pid+'/decision-review',json=payload,headers=headers)
            response.raise_for_status();preview=response.json()
            write(output/'reviewed-change-preview.json',preview)
            if preview.get('blockers') or preview.get('implementation_pending_ids'):
                raise ValueError('Preview still has unresolved changes; nothing committed')
            response=await client.post('/api/projects/'+pid+'/decision-review/commit',headers=headers,json=dict(
                expected_revision=preview['expected_revision'],preview_id=preview['preview_id'],
                preview_hash=preview['preview_hash'],selected_group_ids=['synthetic-reviewed-change'],
                review_confirmed=True,idempotency_key='synthetic-'+digest(manifest)[:28]))
            response.raise_for_status();write(output/'synthetic-review-receipt.json',response.json())
        print(json.dumps(dict(phase='confirm',project_id=pid,reviewed_candidates=len(candidates)),ensure_ascii=False))
        return
    if args.phase == 'inspect':
        p=store.get(pid)
        write(output/'current-project.json', p)
        print(json.dumps(dict(project_id=pid, revision=p['revision'], questions=len(p['questions']),
            prewrite=p.get('prewrite_review'), evidence=call_evidence(folder)), ensure_ascii=False))
        return
    if not args.allow_real_provider:
        raise ValueError('Requires explicit --allow-real-provider')
    config, _ = load_config()
    if origin(config) != 'https://api.deepseek.com':
        raise ValueError('Use the configured official DeepSeek endpoint only')
    provider = Provider(ROOT/'.env', ROOT/'data/.credentials')
    if not provider.key_status(config)['key_configured']:
        raise ValueError('Real provider key is unavailable; no request sent')
    store.setting('model', config)
    src, dest = ROOT/'data'/RELATIVE_PATH, folder/RELATIVE_PATH
    if src.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.read_bytes() != src.read_bytes():
            raise ValueError('Knowledge version changed during the trial')
        dest.write_bytes(src.read_bytes())
    workflow = Workflow(store, provider)
    actions = Actions(store, workflow)
    index = len(list(output.glob('step-*'))) + 1
    step = output/f'step-{index:02d}-{args.phase}'
    step.mkdir()
    message = args.message_file.read_text('utf-8-sig') if args.message_file else ''
    p=store.get(pid)
    body=dict(expected_revision=p['revision'], action=args.phase, message=message, document_type='prd')
    if args.question_id:
        body['target']=dict(kind='question',id=args.question_id)
    if args.allow_review_draft:
        body['allow_review_draft']=True
    report=dict(phase=args.phase, status='running', project_id=pid,
        synthetic=True, user_business_acceptance='NOT_RUN', daily_project_mutation=False,
        listener_started=False, model_cost='unknown', settings=safe_config(config),
        evidence_before=call_evidence(folder),
        implementation_hashes={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT/'app').glob('*.py'))},
        frozen_oracle_sha256=hashlib.sha256((output/'frozen-oracle.json').read_bytes()).hexdigest())
    write(step/'report.json', report)
    write(step/'project-before.json', p)
    try:
        plan=actions.plan(pid,body)
        write(step/'plan.json',plan)
        if plan['missing']:
            raise RuntimeError('Action blocked: '+ '；'.join(plan['missing']))
        task=actions.start(pid,body,plan['plan_hash'],'synthetic-prewrite-'+digest([body,plan['plan_hash']])[:24],True)
        print(json.dumps(dict(event='started', task_id=task['id'], phase=args.phase),ensure_ascii=False),flush=True)
        if task['id'] in actions.jobs:
            await actions.jobs[task['id']]
        task=store.get_record(pid,task['id'],'user_task')
        write(step/'task.json',task)
        report.update(status=task['status'], task_id=task['id'], calls=task['calls'], message=task['message'])
        if task['status'] in ('failed','cancelled','paused_budget'):
            raise RuntimeError(task['message'])
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        write(step/'project-after.json',store.get(pid))
        report['evidence_after']=call_evidence(folder)
        report['phase_model_calls']=len(report['evidence_after'])-len(report['evidence_before'])
        write(step/'report.json',report)
        print(json.dumps({k:report.get(k) for k in ('status','calls','phase_model_calls','message','error')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',required=True,choices=('seed','inspect','answer','confirm','prewrite','clarify','document','review'))
    parser.add_argument('--output',type=Path,default=ROOT/'work/prewrite-real-20261014')
    parser.add_argument('--allow-real-provider',action='store_true')
    parser.add_argument('--allow-review-draft',action='store_true')
    parser.add_argument('--question-id')
    parser.add_argument('--message-file',type=Path)
    parser.add_argument('--review-file',type=Path)
    asyncio.run(run(parser.parse_args()))
