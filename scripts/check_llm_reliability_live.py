"""Authorized synthetic input -> AI draft -> table confirmation -> PRD export.

No daily project copies, no listener and no automatic rerun of a failed task.
Resume uses the same isolated project and retains all prior request evidence.
"""
import argparse
import asyncio
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MATERIAL = '''这是冻结的合成测试资料，不是真实客户数据。本次新增一个独立的储能云平台网页模块：告警详情只读处理记录。
目标：值班人员在同一网页查看一条告警已经保存的处理记录，避免在其它页面查找。使用者为场站值班人员和只读查看人员，两类人员都能查看本场站已授权告警的记录；都不能在本模块新增、编辑、删除或导出记录。
入口：已授权用户从告警列表点开一条告警，进入详情，点击“处理记录”页签。只有这一种入口。本模块无写操作，不改变告警状态、原有告警列表、其它模块或已有记录。
正常流程：选择告警、打开详情、进入处理记录页签、读取该告警既有记录、显示记录。每条记录只显示处理人、服务器记录时间、处理内容三个字段；原文展示，不新增字段或推断处理结果。按服务器记录时间从早到晚展示，该告警全部记录一次展示，不分页、不筛选、不排序切换、不自动刷新。
记录非空时，完整显示该告警记录；为空时显示“暂无处理记录”；读取失败时显示“记录加载失败，请重试”及重试按钮，不显示旧告警的数据；点击重试仅重新读取当前告警记录。切换告警后仅显示新告警的记录，不混入上条记录。
权限：仅查看已经授权的当前场站及当前告警；无权限时显示“无权查看”，不显示记录字段。前后端都执行授权边界，本模块只读，不触发确认、处置或其它控制操作。
可见结果只由读取响应决定；客户端超时或未收到响应时只显示加载失败，不推断服务器有无记录。这里没有写入操作或操作日志新规则。记录时间按服务器返回文本显示，不做时区转换。
验收：有两条已保存记录时，按时间顺序看到两条及三个字段；无记录时看到空提示；失败时看到失败提示并可重试；无权限时看不到记录；切换告警时不显示上一告警记录；页面没有新增、编辑、删除、导出操作。
交付是网页前端及只读后端查询能力的需求评审稿；接口路径、数据库选型、性能阈值不属于本轮产品决定，不虚构这些值。现有查询响应仅作为真实记录的来源，本期不修改记录生产流程。优先级不设值。业务页面不要求新增流程图，需求文档如有流程图必须与上述正常、空、失败、无权限分支一致。
'''


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), 'utf-8')


def displayed_rows(project, roots, fields, linked_acceptance):
    """Confirm the actual proposed table, including detailed revision drafts.

    A successful AI completion does not mutate the saved root until the user
    confirms it. Reading only the old root would test a different table.
    """
    candidates=set((project.get('refinement_completion') or {}).get('candidate_ids',[]))
    revisions={i['target_item_id']:i for i in project['items']
        if i['id'] in candidates and i.get('target_item_id') and i.get('selection_status')=='candidate'}
    rows=[]
    for root in roots:
        visible=revisions.get(root['id'],root)
        acceptance=[]
        for ac in linked_acceptance(project,root):
            if ac.get('selection_status') not in ('selected','candidate'):continue
            shown=revisions.get(ac['id'],ac)
            acceptance.append(dict(id=ac['id'],statement=shown['statement'],
                **({'candidate_id':shown['id']} if shown is not ac else {})))
        row=dict(requirement_id=root['id'],statement=visible['statement'],
            change_type=(project.get('refinement_completion') or {}).get('change_types',{}).get(root['id'],root.get('change_type','new')),
            behavior={k:visible.get('behavior',{}).get(k,'') for k in fields},acceptance_items=acceptance,confirmed_question_ids=[])
        if visible is not root:row['candidate_id']=visible['id']
        if visible.get('detail_contract') is not None:row['detail_contract']=visible['detail_contract']
        rows.append(row)
    return rows


async def run(args):
    assert args.execute_real_model
    session = args.session.resolve()
    assert session.is_relative_to(ROOT/'evidence')
    if not args.resume:
        assert not session.exists(), 'Preserve prior paid evidence'
        session.mkdir(parents=True)
    os.environ['RA_DATA_DIR'] = str(session/'import-data')
    import httpx
    from app.main import create_app
    from app.provider import DEFAULT, Provider, origin
    from app.requirement_confirmation import FIELDS
    from app.requirement_confirmation import linked_acceptance
    from scripts.check_shared_draft_live import daily_fingerprint
    before = daily_fingerprint()
    with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        saved = json.loads(db.execute("SELECT payload FROM settings WHERE id='model'").fetchone()[0])
    config = dict(DEFAULT, **saved)
    assert origin(config) == 'https://api.deepseek.com' and config['model'] == 'deepseek-flash'
    provider = Provider(ROOT/'.env', credential_dir=ROOT/'data/.credentials')
    assert provider.key(config)
    app = create_app(session/'data', provider=provider)
    store = app.state.store
    store.setting('model', config)
    outcome = dict(status='running', synthetic_only=True, listener_started=False,
                   human_business_acceptance='NOT_RUN', stages=[])
    if args.resume and (session/'outcome.json').exists():
        outcome = json.loads((session/'outcome.json').read_text('utf-8'))
        outcome['status'] = 'running'
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver', timeout=180) as client:
            csrf = (await client.get('/api/session')).json()['csrf']
            headers = {'origin':'http://testserver', 'x-csrf-token':csrf}
            if args.resume:
                pid = (session/'project-id.txt').read_text('utf-8')
            else:
                response = await client.post('/api/projects', headers=headers, json={'name':'合成全流程验收｜告警记录只读查看'})
                response.raise_for_status()
                pid = response.json()['id']
                (session/'project-id.txt').write_text(pid, 'utf-8')
            root = '/api/projects/'+pid

            async def post(path, payload):
                result = await client.post(root+path, headers=headers, json=payload)
                assert result.status_code == 200, path+': '+result.text[:1200]
                return result.json()

            async def action(name, message, draft_request=None):
                index = len(store.records(pid, 'user_task'))
                body = dict(expected_revision=store.get(pid)['revision'], action=name, message=message,
                            document_type='prd', option_id=None)
                if draft_request:
                    body['draft_request'] = draft_request
                plan = await post('/actions/plan', body)
                write(session/f'{index}-{name}-plan.json', plan)
                assert not plan['missing'], plan['missing']
                task = await post('/actions', dict(body, plan_hash=plan['plan_hash'], authorize=True,
                                  idempotency_key='synthetic-e2e-'+str(uuid.uuid4())))
                job = app.state.actions.jobs.get(task['id'])
                if job is not None:
                    await job
                task = store.get_record(pid, task['id'], 'user_task')
                write(session/f'{index}-{name}-task.json', task)
                runs = [store.get_record(pid, rid, 'run') for rid in task['run_ids']]
                write(session/f'{index}-{name}-runs.json', runs)
                outcome['stages'].append(dict(action=name, task_id=task['id'], status=task['status'], calls=task['calls']))
                print(json.dumps(outcome['stages'][-1], ensure_ascii=False), flush=True)
                assert task['status'] in ('succeeded','partial','awaiting_user'), task.get('message')
                assert all(r.get('result_applied') for r in runs), 'A stage did not save a usable result'
                return store.get(pid)

            if not args.resume:
                await post('/sources/text', dict(expected_revision=0, title='冻结合成验收资料', purpose='goal', text=MATERIAL))
                await post('/workspace-draft', dict(expected_revision=store.get(pid)['revision'], text=MATERIAL, entry_mode='direct'))
                await action('organize', '按合成资料只整理一条“告警详情只读处理记录”需求；已有明确决定不重复提问，完整填写有依据的业务说明和验收草稿，用户随后核对。')
            elif not store.get(pid)['items']:
                await action('organize', '按合成资料只整理一条“告警详情只读处理记录”需求；已有明确决定不重复提问，完整填写有依据的业务说明和验收草稿，用户随后核对。')
            p = store.get(pid)
            pending = [q for q in p['questions'] if q.get('prewrite_finding_id') and not q.get('answer')]
            if p.get('prewrite_review',{}).get('status')=='needs_confirmation' and not (session/'operator-correction.json').exists():
                # A frozen test-operator corrects omissions against MATERIAL, as a user
                # may edit an unsuitable AI draft. No provider or daily data is used.
                pending = [q for q in p['questions'] if not q.get('answer') and q.get('blocking')]
                reqs = [i for i in p['items'] if i['kind']=='requirement' and i.get('selection_status')=='selected' and not i.get('target_item_id')]
                assert len(reqs)==1 and pending
                req = reqs[0]
                statement = req['statement'].replace('读取失败时', '读取失败（含客户端超时或未收到响应）时')
                behavior = {k:req.get('behavior',{}).get(k,'') for k in FIELDS}
                behavior['flow'] += '失败分支只显示加载失败提示及重试按钮，不显示空提示或记录字段。'
                acs = linked_acceptance(p,req)
                revised = []
                for ac in acs:
                    if ac.get('target_item_id') or ac['selection_status']!='selected': continue
                    text = ac['statement']
                    if '两条' in text:
                        text += '页面只显示处理人、服务器记录时间、处理内容三个字段，文本与服务器返回原文一致，无额外字段或推断结果；全部记录一次展示，无分页、筛选、排序切换或自动刷新。'
                    if '超时' in text or '未收到响应' in text:
                        text += '此时仅显示加载失败提示及重试按钮，不显示“暂无处理记录”或任何记录字段，不推断服务器有无记录。'
                    if '无授权' in text:
                        text = '打开处理记录页签的查询返回未授权时，显示“无权查看”，且不显示任何记录字段。'
                    revised.append(dict(id=ac['id'],statement=text))
                answer='按冻结合成资料核对并修正：失败含客户端超时或未收到响应时仅显示加载失败及重试，不显示空提示或记录字段；数据只显示三个字段、原文展示，无分页、筛选、排序切换或自动刷新。'
                body=dict(expected_revision=p['revision'],idempotency_key='synthetic-operator-correction-v1',review_confirmed=True,
                    context=dict(values={k:v for k,v in p['product_context'].items() if isinstance(v,str)},
                        field_states=p.get('context_field_states',{}),scope_profile=p.get('scope_profile'),scope_ids=[req['id']]),
                    rows=[dict(requirement_id=req['id'],statement=statement,change_type=req['change_type'],behavior=behavior,
                        acceptance_items=revised,confirmed_question_ids=[q['id'] for q in pending if req['id'] in q.get('related_refs',[])])],
                    answers=[dict(question_id=q['id'],answer=answer) for q in pending])
                corrected=await post('/refinement-confirmation',body)
                write(session/'operator-correction-attempt.json',corrected)
                assert corrected['confirmed'],corrected.get('issues')
                write(session/'operator-correction.json',dict(source='MATERIAL frozen synthetic oracle',body=body,result=corrected))
                outcome['stages'].append(dict(action='test_operator_corrects_source_omissions',status='passed',calls=0))
                p=store.get(pid)
            if not p.get('refinement_completion') and any(i.get('change_type')=='unspecified' for i in p['items'] if i['kind']=='requirement'):
                p = await action('organize', '按原始合成资料补齐整表供核对：本期明确新增独立模块，补全需求类型、本期范围、摘要及所有必填内容；保留原需求身份，不另建同义需求。', dict(mode='all'))
            write(session/'draft-project.json', p)
            if not p.get('refinement_confirmation') and not p.get('stage_checks', {}).get('3'):
                roots = [i for i in p['items'] if i['kind']=='requirement' and i.get('applies_to')=='to_be'
                         and not i.get('target_item_id') and i.get('selection_status') in ('candidate','selected')]
                assert roots, 'No requirement draft'
                values = {**p.get('product_context_proposal', {}), **p.get('product_context', {})}
                values = {k:v for k,v in values.items() if isinstance(v,str)}
                # Frozen test-operator confirmations come from MATERIAL, never new business facts.
                values.update(intent='值班人员在同一网页查看当前告警已保存处理记录，避免在其它页面查找。',
                              users='已授权的场站值班人员和只读查看人员。', change_scope='新增独立的告警详情只读处理记录模块，不改变其它模块或记录生产流程。')
                rows = displayed_rows(p, roots, FIELDS, linked_acceptance)
                # Unanswered blocking decisions must be exposed; never mark them answered just to pass.
                answers = [dict(question_id=q['id'], answer=q['answer']) for q in p['questions'] if q.get('answer')]
                for row in rows:
                    row['confirmed_question_ids']=[q['id'] for q in p['questions'] if q.get('answer') and row['requirement_id'] in q.get('related_refs',[])]
                witnesses={q['id']:[dict(item_id=i['id'],field='statement',quote=i['statement']) for i in p['items']
                    if i['id'] in q.get('related_refs',[]) and not i.get('target_item_id') and i['kind'] in ('requirement','acceptance')]
                    for q in p['questions'] if q.get('answer')}
                body = dict(expected_revision=p['revision'], idempotency_key='synthetic-table-confirmation-'+str(p['revision']), review_confirmed=True,
                    context=dict(values=values, field_states={k:dict(status='known') for k,v in values.items() if v.strip()},
                                 scope_profile=dict(scenario='new_module',dependencies='independent'), scope_ids=[i['id'] for i in roots]),
                    rows=rows, answers=answers,answer_implementations=witnesses)
                write(session/'table-displayed.json', body)
                confirmed = await post('/refinement-confirmation', body)
                write(session/'table-confirmation.json', confirmed)
                assert confirmed['confirmed'], confirmed.get('issues')
                outcome['stages'].append(dict(action='test_operator_table_confirmation',status='passed',calls=0))
                p = store.get(pid)
            if not p.get('documents', {}).get('prd'):
                p = await action('document', '按已核对需求生成完整 PRD 评审稿，保留只读权限、空数据、加载失败重试、无权限及切换告警分支，不新增业务规则。')
            write(session/'final-project.json', p)
            assert p.get('documents',{}).get('prd'), 'PRD was not generated'
            downloaded = await client.get(root+'/documents/prd/zip?mode=review')
            assert downloaded.status_code == 200, downloaded.text[:900]
            (session/'prd-review.zip').write_bytes(downloaded.content)
            with zipfile.ZipFile(io.BytesIO(downloaded.content)) as z:
                assert z.testzip() is None
                outcome['export_files'] = z.namelist()
            outcome['stages'].append(dict(action='prd_review_export',status='passed',calls=0))
            outcome['status'] = 'passed'
            outcome.pop('error', None)
            outcome.pop('traceback', None)
    except Exception as error:
        import traceback
        outcome.update(status='failed', error=str(error), traceback=traceback.format_exc())
    finally:
        outcome['provider_requests'] = sum(r.get('calls',0) for r in store.records(pid,'run'))
        outcome['daily_data_unchanged'] = daily_fingerprint() == before
        write(session/'outcome.json', outcome)
        print(json.dumps({k:v for k,v in outcome.items() if k!='traceback'}, ensure_ascii=False), flush=True)
    assert outcome['status']=='passed' and outcome['daily_data_unchanged']


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-real-model',action='store_true')
    parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--resume',action='store_true')
    asyncio.run(run(parser.parse_args()))
