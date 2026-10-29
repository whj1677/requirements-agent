"""Opt-in real supplement clicks on synthetic projects, without a listener.

Only the existing official DeepSeek credential/configuration is reused. Browser
requests are intercepted into isolated ASGI; daily projects are never copied.
Each scenario sends at most one request, and preserves failures without replay.
"""
import argparse
import asyncio
import copy
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import sqlite3
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


async def run(args):
    assert bool(args.execute_real_model) != bool(args.replay_from), 'Choose an explicit live run or offline replay'
    output, build = args.output.resolve(), args.static_build.resolve()
    assert output.is_relative_to(ROOT/'evidence') and not output.exists()
    assert (build/'index.html').is_file()
    output.mkdir(parents=True)
    os.environ['RA_DATA_DIR'] = str(output/'import-data')
    import httpx
    from playwright.async_api import async_playwright, expect
    from app.main import create_app
    from app.provider import DEFAULT, Provider, origin
    from scripts.check_refinement_autofill_live import daily_fingerprint
    from tests.check_requirement_entry_browser import Journey, RID, OTHER, TITLE, QUESTION, ORIGIN

    with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        saved = db.execute('SELECT payload FROM settings WHERE id=?', ('model',)).fetchone()
    config = {**DEFAULT, **(json.loads(saved[0]) if saved else {})}
    assert origin(config) == 'https://api.deepseek.com' and config['model'] == 'deepseek-flash'
    before_daily = daily_fingerprint()
    write(output/'daily-before.json', before_daily)
    report = dict(status='running', scenarios=[], provider_requests=0, cost=None,
        synthetic_only=True, listener_started=False, human_business_acceptance='NOT_RUN',
        mode='offline_replay_of_real_response' if args.replay_from else 'real_model_ui')

    class CountedProvider(Provider):
        requests = 0
        scenario = None
        scenario_calls = 0
        responses = 0
        kind = None

        async def request(self, settings, messages, evidence=None):
            assert self.scenario_calls == 0, 'Never replay a real request to force success'
            assert self.responses < 2 and origin(settings) == origin(config)
            assert settings['model'] == config['model']
            self.responses += 1
            self.scenario_calls += 1
            write(self.scenario/'request.json', messages)
            if args.replay_from:
                original = args.replay_from.resolve()/self.kind
                frozen = json.loads((original/'response.json').read_text('utf-8'))
                old_messages = json.loads((original/'request.json').read_text('utf-8'))
                ctx = json.loads(messages[1]['content'][0]['text'])
                prior = json.loads(old_messages[1]['content'][0]['text'])
                assert ctx['excerpts']==prior['excerpts'] and ctx['user_message']==prior['user_message']
                assert ctx['current_rows']==prior['current_rows']
                if evidence:
                    evidence.value['offline_replay'] = True
                    evidence.before_send(settings)
                    evidence.response(json.dumps(frozen['value'],ensure_ascii=False),http_status=200,finish_reason='stop',usage={})
                return copy.deepcopy(frozen['value']),dict(frozen['meta'],offline_replay=True)
            self.requests += 1
            write(output/'requests.json', dict(requests=self.requests, status='started_no_replay'))
            value, meta = await super().request(settings, messages, evidence=evidence)
            write(self.scenario/'response.json', dict(value=value, meta=meta))
            return value, meta

    provider = CountedProvider(output/'absent.env' if args.replay_from else ROOT/'.env',
        credential_dir=None if args.replay_from else ROOT/'data/.credentials')
    if args.replay_from:
        provider.keys[origin(config)]='OFFLINE_REPLAY_ONLY'
    assert provider.key(config), 'Configured credential unavailable; no model request sent'
    write(output/'provider.json', {**{k:config.get(k) for k in
        ('name','base_url','model','max_tokens','timeout','input_price','output_price')}, 'key_present':True})
    try:
        async with AsyncExitStack() as clients:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                for kind in (('whole','single') if args.scenario=='both' else (args.scenario,)):
                    folder = output/kind
                    folder.mkdir()
                    # Stable fixture IDs must belong to separate databases.
                    app = create_app(folder/'data', provider=provider)
                    store = app.state.store
                    store.setting('model', config)
                    client = await clients.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30))
                    provider.scenario, provider.scenario_calls = folder, 0
                    provider.kind = kind
                    result = dict(kind=kind, status='running', checks=[], api_requests=[], page_errors=[])
                    report['scenarios'].append(result)
                    journey = Journey(args, 'entry', app, provider, client, folder, result)
                    pid = journey.pid
                    with store.edit(pid, journey.project()['revision'], '合成测试：增加尚未回答的地区筛选问题') as (p, _):
                        p['name'] += ' · '+kind
                        if args.missing_context:
                            p['product_context']['intent']=''
                            p['context_field_states']['intent']={'status':'unknown'}
                        p['questions'] = [dict(id='Q-SHARED', topic_key='shared-filter', question=QUESTION,
                            why='合成的待决定项', related_refs=[RID,OTHER], source_refs=p['items'][0]['source_refs'],
                            options=[], blocking=True, blocking_stage=3, status='open', answer=None)]
                    initial = copy.deepcopy(journey.project())
                    write(folder/'project-before.json', initial)
                    context = await browser.new_context(viewport=dict(width=1366, height=768))
                    await context.route('**/*', journey.dispatch)
                    page = await context.new_page()
                    page.set_default_timeout(15000)
                    journey.page = page
                    page.on('pageerror', lambda e:result['page_errors'].append(str(e)))
                    try:
                        await page.goto(ORIGIN, wait_until='networkidle')
                        await page.locator('aside.sidebar button[title="'+initial['name']+'"]').click()
                        work = journey.work()
                        await work.get_by_role('button', name='不使用 AI，直接完善', exact=True).click()
                        table = work.get_by_role('table', name='需求确认表', exact=True)
                        await expect(table).to_be_visible()
                        if args.replay_from:
                            await expect(table.locator('[data-cell-key="requirement-'+RID+'-acceptance"]')).to_have_class('sheet-invalid')
                            await expect(table.locator('[data-cell-key="requirement-'+RID+'-change_type"]')).to_have_class('sheet-invalid')
                        users = table.locator('[data-cell-key="context-users"] textarea')
                        manual = '电价管理员；人工补充：本轮只核对已保存模板'
                        await users.fill(manual)
                        answer = table.locator('[data-cell-key="question-Q-SHARED"] textarea').first
                        await answer.fill('两个模块分别筛选，不联动筛选条件。')
                        button = work.get_by_role('button', name='AI 补充建议', exact=True) if kind=='whole' else table.locator('[data-cell-key="requirement-'+RID+'-acceptance"]').get_by_role('button', name='请 AI 完善此需求', exact=True)
                        await button.click()
                        dialog = page.get_by_role('dialog', name='核对本次任务', exact=True)
                        await expect(dialog).to_be_visible()
                        plan = next(r for r in reversed(result['api_requests']) if r['path'].endswith('/actions/plan'))
                        assert plan['body']['action'] == ('organize' if kind=='whole' else 'clarify')
                        assert '人工补充' in plan['body']['message'] and '不联动' in plan['body']['message']
                        assert store.get(pid) == initial and provider.scenario_calls == 0
                        await page.screenshot(path=str(folder/'01-plan.png'))
                        await dialog.get_by_role('button', name='授权本次范围并运行', exact=True).click()
                        # Wait for the actual terminal task. No repeat request is started.
                        for _ in range(600):
                            tasks = store.records(pid, 'user_task')
                            if tasks and tasks[-1]['status'] not in ('queued','running'):
                                break
                            await asyncio.sleep(.5)
                        else:
                            raise AssertionError('Real task did not finish within 300 seconds')
                        task = tasks[-1]
                        write(folder/'task.json', task)
                        write(folder/'runs.json', store.records(pid, 'run'))
                        after = store.get(pid)
                        write(folder/'project-after.json', after)
                        result.update(task_status=task['status'], provider_requests=0 if args.replay_from else provider.scenario_calls,
                            cost=task.get('cost'), task_message=task.get('message'))
                        assert task['status'] in ('succeeded','partial'), task.get('message')
                        # Browser polling must merge the actual response, without reloading away local edits.
                        await expect(users).to_have_value(manual, timeout=30000)
                        await expect(answer).to_have_value('两个模块分别筛选，不联动筛选条件。')
                        if args.missing_context:
                            intent = table.locator('[data-cell-key="context-intent"] textarea')
                            await expect(intent).not_to_have_value('',timeout=30000)
                            await expect(table.locator('[data-cell-key="context-intent"]')).not_to_have_class('sheet-invalid')
                            result['visible_intent']=await intent.input_value()
                        acceptance = table.get_by_role('textbox', name=TITLE+' 验收建议 1', exact=True)
                        await expect(acceptance).not_to_have_value('', timeout=30000)
                        if args.replay_from:
                            await expect(table.locator('[data-cell-key="requirement-'+RID+'-acceptance"]')).not_to_have_class('sheet-invalid')
                            await expect(table.locator('[data-cell-key="requirement-'+RID+'-change_type"]')).to_have_class('sheet-invalid')
                        if kind=='whole':
                            other = table.get_by_role('textbox',name='电价历史版本查询 验收建议 2',exact=True)
                            # The original history criterion is retained, and new actual suggestions are appended.
                            await expect(other).not_to_have_value('',timeout=30000)
                            await expect(table.get_by_role('combobox',name='电价历史版本查询 本次改动',exact=True)).to_have_value('new')
                        await acceptance.scroll_into_view_if_needed()
                        result['visible_acceptance'] = await acceptance.input_value()
                        await page.screenshot(path=str(folder/'02-supplement-returned.png'))
                        assert all(next(i for i in after['items'] if i['id']==old['id'])==old for old in initial['items'])
                        assert after['questions'][0].get('answer') is None
                        assert not after['questions'][0].get('applied_item_ids')
                        new = [i for i in after['items'] if i['id'] not in {x['id'] for x in initial['items']}]
                        assert new and all(i['selection_status']=='candidate' for i in new)
                        if kind=='single':
                            assert not any(i.get('target_item_id')==OTHER for i in new)
                        result['checks'] += ['真实界面计划携带本地人工摘要与回答', '真实模型验收建议自动回填；人工内容保留', '原条款未改写，建议仍为候选，问题未自动回答或落实']
                        result['status'] = 'passed'
                    except Exception as exc:
                        result.update(status='failed', failure=str(exc), traceback=traceback.format_exc())
                        await page.screenshot(path=str(folder/'failure.png'), full_page=True)
                    finally:
                        write(folder/'result.json', result)
                        await context.close()
                    print(json.dumps({k:result.get(k) for k in ('kind','status','task_status','provider_requests','failure')}, ensure_ascii=False), flush=True)
                await browser.close()
        report['status'] = 'passed' if all(s['status']=='passed' for s in report['scenarios']) else 'failed'
    finally:
        after_daily = daily_fingerprint()
        report.update(provider_requests=provider.requests, daily_data_unchanged=before_daily==after_daily)
        write(output/'daily-after.json', after_daily)
        write(output/'result.json', report)
        assert report['daily_data_unchanged'], 'Daily data changed; inspect concurrent activity'
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute-real-model', action='store_true')
    parser.add_argument('--scenario', choices=('both','whole','single'), default='both')
    parser.add_argument('--replay-from',type=Path)
    parser.add_argument('--missing-context',action='store_true')
    asyncio.run(run(parser.parse_args()))
