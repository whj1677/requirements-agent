"""One opt-in real row-recommendation task against synthetic storage-cloud data.

No listener, daily mutation, task replay, or AI suggestion adoption. The result
is preserved so a later browser recording can use a separate snapshot copy.
"""
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MATERIAL = '''合成储能云平台需求材料，仅用于验证，不是实际业务规则。
本期新增告警处理记录协作功能。两名值班人员各用独立账户，可查看双方创建的告警处理记录。
入口是云平台的告警处理记录列表，用户可打开记录详情并修改处理说明；修改后列表及详情展示更新后的说明。
处理记录包含告警编号、处理说明、创建人和最近修改时间。修改权限还需要单独明确。
本材料没有规定失败重试、并发冲突策略、时限或其他异常分支，不应由助手补成既定规则。
另一本期功能是单站告警数量总览：值班人员进入已选站点的告警总览，只读查看该站当前告警总数。
单站告警数量总览不含修改或控制操作，本次局部完善不得改动这项功能。'''
ANSWER = '只能修改本人创建的告警处理记录。'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def seed(store):
    from app.sources import save_source
    from app.core import now
    from app.requirement_confirmation import FIELDS, row_fingerprint
    from app.requirements import behavior_fingerprint
    project = store.create('真实模型验证 · 储能云告警协作表格确认')
    source = save_source(store, '合成储能云告警协作.txt', MATERIAL.encode('utf-8'), 'goal')
    refs = [dict(source_id=source['id'], excerpt_id=e['id']) for e in source['excerpts']]
    with store.edit(project['id'], 0, '冻结合成场景与旁观已确认需求') as (p, _):
        p['sources'] = [source]
        p['workflow_mode'] = 'shared-draft'
        p['workspace_draft'] = dict(text=MATERIAL, entry_mode='direct')
        p['product_context'] = dict(scope_ids=['REQ-ALARM-COLLAB', 'REQ-ALARM-OVERVIEW'],
            product='大型储能云平台', module='告警处理记录', intent='让值班人员协作记录和核对告警处理过程',
            users='两名值班人员', changes='新增告警处理记录协作', current_state='', preserved='',
            non_goals='不包含设备控制、失败重试或并发冲突策略的新增业务决定', priority='')
        p['scope_profile'] = dict(scenario='new_module', dependencies='independent')
        base = dict(applies_to='to_be', epistemic_status='reported', source_refs=refs,
                    revision=1, change_type='new', selection_status='candidate')
        root = dict(base, id='REQ-ALARM-COLLAB', kind='requirement', title='告警处理记录协作',
            statement='两名值班人员在储能云平台协作查看和维护告警处理记录。', related_refs=[],
            behavior={field:'' for field in FIELDS}, behavior_review_required=True)
        other = dict(base, id='REQ-ALARM-OVERVIEW', kind='requirement', title='单站告警数量总览',
            statement='值班人员在已选站点的告警总览，只读查看该站当前告警总数。',
            related_refs=['AC-OVERVIEW'], selection_status='selected', behavior_review_required=True,
            behavior=dict(actor='值班人员', entry='已选站点的告警总览', flow='查看该站当前告警总数',
                          data='当前告警总数', permissions='只读，不含修改或控制操作',
                          exceptions='', result='显示已选站点的当前告警总数'))
        acceptance = dict(base, id='AC-OVERVIEW', kind='acceptance', title='单站告警总览验收',
            statement='值班人员选择站点并打开告警总览，可以查看该站当前告警总数；页面不提供修改或控制操作。',
            related_refs=['REQ-ALARM-OVERVIEW'], selection_status='selected')
        p['items'] = [root, other, acceptance]
        p['questions'] = [dict(id='Q-MODIFY-OWNER', topic_key='alarm-record-modify-owner',
            question='值班人员能修改谁创建的告警处理记录？', why='决定处理记录的修改权限',
            options=[], blocking=True, blocking_stage=3, related_refs=['REQ-ALARM-COLLAB'],
            source_refs=refs, status='open', answer=None)]
        other['behavior_review'] = dict(content_hash=behavior_fingerprint(other),
            actor='authenticated_local_user', reviewed_at=now(), method='synthetic_fixture')
        other['requirement_confirmation'] = dict(schema='requirement-confirmation-1',
            content_hash=row_fingerprint(p, other), actor='authenticated_local_user',
            reviewed_at=now(), fixture_only=True)
        p['requirement_confirmation_mode'] = 'table'
    return store.get(project['id'])


async def run(args):
    assert args.execute_real_model, 'Explicit real-model execution is required'
    session = args.session.resolve()
    assert session.is_relative_to(ROOT / 'evidence') and session != ROOT / 'evidence'
    assert not session.exists(), 'Never overwrite or replay an existing paid task'
    session.mkdir(parents=True)
    os.environ['RA_DATA_DIR'] = str((session / 'import-data').resolve())
    import httpx
    from app.provider import Provider, DEFAULT, origin
    from app.main import create_app
    from app.requirement_confirmation import confirmation_current, FIELDS
    from scripts.check_shared_draft_live import daily_fingerprint, safe_run_metrics

    class CountedProvider(Provider):
        requests = 0

        async def request(self, config, messages, evidence=None):
            self.requests += 1
            write(session / 'provider-requests.json', dict(requests=self.requests, status='request_started'))
            return await super().request(config, messages, evidence=evidence)

    before = daily_fingerprint()
    write(session / 'daily-before.json', before)
    outcome = dict(scope='one_real_row_recommendation_task_synthetic_storage_cloud',
                   provider_requests=0, cost=None, paid_request_status='NOT_STARTED',
                   human_product_acceptance='NOT_RUN', ui_recording='NOT_RUN',
                   model_output_adoption='NOT_RUN', fixture_only=True)
    provider = app = pid = None
    started = None
    try:
        async with httpx.AsyncClient(trust_env=False) as live:
            reply = await live.get('http://127.0.0.1:8765/api/models', timeout=10)
            reply.raise_for_status()
            settings = reply.json()['model']
        config = {key: settings.get(key, default) for key, default in DEFAULT.items()}
        provider = CountedProvider(ROOT / '.env', credential_dir=ROOT / 'data/.credentials')
        assert provider.key(config), 'Configured model key unavailable; no request sent'
        write(session / 'provider.json', {**{key:config.get(key) for key in
            ('name','model','base_url','timeout','max_tokens','json_mode','thinking_disabled','input_price','output_price')},
            'origin':origin(config), 'key_present':True})
        app = create_app(session / 'data', provider=provider)
        store = app.state.store
        store.setting('model', config)
        project = seed(store)
        pid = project['id']
        (session / 'project-id.txt').write_text(pid, encoding='utf-8')
        write(session / 'fixture-project.json', project)
        write(session / 'oracle.json', dict(
            scope='Frozen synthetic expectations, not production business rules',
            expected=['new recommendation for REQ-ALARM-COLLAB, candidate only',
                      'independent accounts, both view, modify only own created record',
                      'material-supported entry, record fields, flow and visible result',
                      'at least one verifiable acceptance candidate directly bound to original requirement',
                      'exceptions/retry/concurrency values remain unknown, no invented business branch',
                      'saved modification permission is not asked again',
                      'other original requirement and acceptance and confirmation remain unchanged',
                      'new confirmation preview can display the real candidates without adoption']))
        outcome['project_id'] = pid
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            csrf = (await client.get('/api/session')).json()['csrf']
            headers = {'origin':'http://testserver', 'x-csrf-token':csrf}

            async def post(path, body):
                reply = await client.post(f'/api/projects/{pid}' + path, headers=headers, json=body)
                assert reply.status_code == 200, f'{path}: HTTP {reply.status_code}: {reply.text[:900]}'
                return reply.json()

            await post('/decision-review/answer', dict(expected_revision=project['revision'],
                       question_id='Q-MODIFY-OWNER', answer=ANSWER))
            frozen = store.get(pid)
            write(session / 'project-before.json', frozen)
            body = dict(expected_revision=frozen['revision'], action='clarify',
                message='请根据已保存原资料和本条权限回答，完善这一行需求的正文和适用业务说明，并起草可验证的验收候选。'
                        '已有资料和回答已经明确的内容不重复提问。未规定的异常处理、重试和并发策略保持未知，不补成规则。'
                        '保留另一条已确认的单站告警数量总览；所有生成内容仅供人工核对。',
                document_type='prd', target=dict(kind='item',id='REQ-ALARM-COLLAB'))
            plan = await post('/actions/plan', body)
            write(session / 'plan.json', plan)
            assert not plan['missing'], '; '.join(plan['missing'])
            assert plan['requirement_recommendation_scope']['requirement_id'] == 'REQ-ALARM-COLLAB'
            started = time.monotonic()
            task = await post('/actions', dict(body, plan_hash=plan['plan_hash'],
                             idempotency_key='row-live-'+str(uuid.uuid4()), authorize=True))
            write(session / 'task-initial.json', task)
            outcome.update(task_id=task['id'], paid_request_status='TASK_STARTED')
            marker = None
            while True:
                rows = (await client.get(f'/api/projects/{pid}/actions')).json()
                task = next(row for row in rows if row['id'] == task['id'])
                latest = (task['status'], task.get('calls'), task.get('message'))
                if latest != marker:
                    print(json.dumps(dict(status=task['status'], calls=task.get('calls'),
                                          message=task.get('message')), ensure_ascii=False), flush=True)
                    marker = latest
                if task['status'] not in ('queued','running'):
                    break
                await asyncio.sleep(.5)
            elapsed = round(time.monotonic()-started,3)
            write(session / 'task-final.json', task)
            runs = (await client.get(f'/api/projects/{pid}/runs')).json()
            write(session / 'runs.json', runs)
            write(session / 'metrics.json', safe_run_metrics(runs))
            shown = (await client.get(f'/api/projects/{pid}')).json()
            write(session / 'project-view.json', shown)
            current = store.get(pid)
            candidates = [i for i in current['items'] if i['id'] not in {row['id'] for row in frozen['items']}]
            requirements = [i for i in candidates if i.get('target_item_id') == 'REQ-ALARM-COLLAB' and i['kind'] == 'requirement']
            acceptance = [i for i in candidates if i['kind'] == 'acceptance' and not i.get('target_item_id')
                          and 'REQ-ALARM-COLLAB' in i.get('related_refs',[])]
            other = next(i for i in current['items'] if i['id'] == 'REQ-ALARM-OVERVIEW')
            original_items_unchanged = all(next(i for i in current['items'] if i['id'] == old['id']) == old for old in frozen['items'])
            outcome.update(task_status=task['status'], task_calls=task.get('calls'),
                provider_requests=provider.requests, cost=task.get('cost'), elapsed_seconds=elapsed,
                paid_request_status='TASK_TERMINAL_NO_REPLAY',
                candidate_count=len(candidates), requirement_candidate_ids=[i['id'] for i in requirements],
                acceptance_candidate_ids=[i['id'] for i in acceptance],
                all_generated_items_unadopted=all(i['selection_status']=='candidate' for i in candidates),
                original_items_unchanged=original_items_unchanged,
                other_row_confirmation_current=confirmation_current(current,other),
                new_questions=[dict(id=q['id'],question=q['question'],blocking=q['blocking'])
                    for q in current['questions'] if q['id'] not in {old['id'] for old in frozen['questions']}])
            write(session / 'candidate-details.json', candidates)
            if requirements and acceptance:
                candidate = requirements[-1]
                view = await post('/requirement-confirmation/preview', dict(
                    expected_revision=current['revision'], requirement_id='REQ-ALARM-COLLAB',
                    candidate_id=candidate['id'], statement=candidate['statement'],
                    behavior={field:candidate.get('behavior',{}).get(field,'') for field in FIELDS},
                    acceptance_items=[dict(id=i['id'],statement=i['statement']) for i in acceptance],
                    confirmed_question_ids=['Q-MODIFY-OWNER']))
                write(session / 'confirmation-preview.json', view)
                outcome.update(confirmation_preview_ready=True,
                    preview_did_not_mutate_project=store.get(pid)==current,
                    preview_permission_answer_can_confirm=any(q['question_id']=='Q-MODIFY-OWNER' and q['can_confirm']
                        for q in view['question_results']))
            else:
                outcome.update(confirmation_preview_ready=False, limitation='No complete requirement plus acceptance candidate package')
    except Exception as error:
        outcome.update(status='failed_or_blocked', error_code=getattr(error,'code',type(error).__name__),
                       error_message=str(error), provider_requests=provider.requests if provider else 0)
    finally:
        if app and pid:
            write(session / 'project-after.json', app.state.store.get(pid))
        after = daily_fingerprint()
        write(session / 'daily-after.json', after)
        outcome['daily_unchanged'] = before == after
        write(session / 'provider-requests.json', dict(requests=provider.requests if provider else 0,
                                                     status=outcome['paid_request_status']))
        write(session / 'outcome.json', outcome)
        print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-real-model', action='store_true')
    parser.add_argument('--session', required=True, type=Path)
    asyncio.run(run(parser.parse_args()))
