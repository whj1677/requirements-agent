"""Exercise one authorized whole-table draft on the actual tariff workbench.

This script talks only to the sole live localhost workbench. It never imports
an isolated result, starts a listener, reads a credential, adopts a candidate,
or confirms a requirement. Run only after the reviewed build is deployed.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import traceback
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / 'data/requirements.sqlite3'
OUTPUT = ROOT / 'evidence/tariff-live-recovery-20261011/actual-session'
ORIGIN = 'http://127.0.0.1:8765'
PID = 'P-d4b61a83e6f14612'
RECEIVER = 'https://api.deepseek.com'
MODEL = 'deepseek-flash'
EXPECTED_REVISION = 19
TERMINAL = {'succeeded', 'partial', 'failed', 'cancelled', 'paused_budget'}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        projects = {pid: json.loads(payload) for pid, payload in db.execute('SELECT id,payload FROM projects')}
        records = [(rid, pid, kind, json.loads(payload)) for rid, pid, kind, payload in
                   db.execute('SELECT id,project_id,kind,payload FROM records ORDER BY rowid')]
        other = {}
        for table, condition in [('projects', 'id<>?'), ('revisions', 'project_id<>?'), ('records', 'project_id<>?')]:
            rows = db.execute(f'SELECT * FROM {table} WHERE {condition} ORDER BY rowid', (PID,)).fetchall()
            other[table] = dict(count=len(rows), sha256=digest(rows))
        for table in ('settings', 'migrations'):
            rows = db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            other[table] = dict(count=len(rows), sha256=digest(rows))
    return dict(project=projects[PID], other_projects={key:digest(value) for key,value in projects.items() if key!=PID},
                protected_tables=other, records=records)


def active(item):
    return (item.get('applies_to')=='to_be' and item.get('selection_status') in ('candidate','selected')
            and not item.get('superseded_by'))


def source_files(project):
    result = []
    base = (ROOT / 'data/sources').resolve()
    for source in project['sources']:
        file = (base / source['id']).resolve()
        assert file.parent == base and file.is_file(), 'Missing or unsafe original source: '+source['id']
        data = file.read_bytes()
        assert sha(data)==source['sha256'], 'Original source hash mismatch: '+source['id']
        result.append(dict(id=source['id'],sha256=sha(data),bytes=len(data),
            excerpts_sha256=digest(source.get('excerpts',[]))))
    return result


def coverage(project, roots):
    rows=[]
    for root in roots:
        # The original ID remains authoritative. A proposed revision does not
        # become a new requirement and cannot imply human acceptance.
        versions=[root]+[item for item in project['items'] if item['kind']=='requirement'
                        and item.get('target_item_id')==root['id'] and active(item)]
        version_ids={item['id'] for item in versions}
        related={ref for item in versions for ref in item.get('related_refs',[])}
        acceptance=[item for item in project['items'] if item['kind']=='acceptance'
            and not item.get('target_item_id') and active(item)
            and (version_ids.intersection(item.get('related_refs',[])) or item['id'] in related)
            and str(item.get('statement') or '').strip()]
        rows.append(dict(requirement_id=root['id'],title=root['title'],acceptance_ids=[item['id'] for item in acceptance]))
    return rows


def protections(before, after):
    old, current = before['project'], after['project']
    current_items={item['id']:item for item in current['items']}
    original_ids={item['id'] for item in old['items']}
    old_questions={item['id']:item for item in old.get('questions',[])}
    current_questions={item['id']:item for item in current.get('questions',[])}
    protected=('product_context','context_field_states','scope_profile','active_baseline_id','baselines',
               'stage_checks','documents','requirement_confirmation_mode')
    return dict(original_items_unchanged=all(current_items.get(item['id'])==item for item in old['items']),
        original_questions_unchanged=all(current_questions.get(key)==value for key,value in old_questions.items()),
        original_sources_unchanged=old['sources']==current['sources'],
        confirmation_fields_unchanged=all(old.get(key)==current.get(key) for key in protected),
        changed_confirmation_fields=[key for key in protected if old.get(key)!=current.get(key)],
        all_new_items_candidate=all(item.get('selection_status')=='candidate' for item in current['items'] if item['id'] not in original_ids),
        other_projects_unchanged=before['other_projects']==after['other_projects'],
        other_project_history_and_settings_unchanged=before['protected_tables']==after['protected_tables'])


def validate_plan(plan, before):
    ids={source['id'] for source in before['sources']}
    assert plan['action']=='organize' and plan['expected_revision']==EXPECTED_REVISION
    assert plan['stages']==['ingest'], 'Unexpected stage/vision plan; do not expand the authorized scope'
    assert not plan.get('benchmark') and not plan.get('generation_target') and not plan.get('requirement_recommendation_scope')
    assert not plan.get('target_label') and not plan.get('pending_text') and not plan['missing']
    assert len(plan['sources'])==18 and {source['id'] for source in plan['sources']}==ids
    assert len(plan['recipients'])==1
    recipient=plan['recipients'][0]
    assert (recipient['origin'],recipient['model'])==(RECEIVER,MODEL)
    assert set(recipient['source_ids'])==ids
    grant=before.get('grants',{}).get(RECEIVER,{})
    assert ids <= set(grant.get('source_ids',[])), 'Prior source authorization no longer covers these 18 sources'


def validate_input(body):
    assert body.get('expected_revision')==EXPECTED_REVISION and body.get('action')=='organize'
    assert not body.get('message') and body.get('document_type')=='prd'
    assert not any(body.get(key) for key in ('target','benchmark','generation_target','attachment_source_ids','option_id'))


async def run(args):
    assert args.execute_authorized_current_project, 'Explicit execution flag required; preparing this script is not execution'
    assert not OUTPUT.exists(), 'Never overwrite or rerun a previous paid session; inspect its evidence first'
    before=snapshot(DATABASE)
    project=before['project']
    assert (project['id'],project['name'],project['revision'])==(PID,'电价管理',EXPECTED_REVISION), 'Project is no longer tariff v19; stop before sending'
    assert len(project['sources'])==18 and all(not source.get('excluded') for source in project['sources'])
    assert not any(kind in ('run','user_task') and value.get('status') in ('queued','running')
                   for _,_,kind,value in before['records']), 'Active work exists; stop before taking a recovery backup'
    roots=[item for item in project['items'] if item['kind']=='requirement' and not item.get('target_item_id') and active(item)]
    assert len(roots)==10, 'Expected the ten existing tariff requirements'
    assert not any(source.get('business_context') for source in project['sources']), 'Excerpt selection requires separate review'
    originals=source_files(project)
    OUTPUT.mkdir(parents=True)
    backup=OUTPUT/'backup'; backup.mkdir()
    with closing(sqlite3.connect(DATABASE.resolve().as_uri()+'?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(backup/'requirements.sqlite3')) as target:
            source.backup(target)
            assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    frozen=snapshot(backup/'requirements.sqlite3')
    assert frozen==before and snapshot(DATABASE)==before, 'Live database changed during backup; no request sent'
    write(OUTPUT/'project-before.json',project)
    write(OUTPUT/'backup-manifest.json',dict(database_sha256=sha((backup/'requirements.sqlite3').read_bytes()),
        project_id=PID,revision=EXPECTED_REVISION,source_files=originals,
        other_projects=before['other_projects'],protected_tables=before['protected_tables']))
    report=dict(status='running',project_id=PID,input_revision=EXPECTED_REVISION,live_origin=ORIGIN,
        real_model_execution=True,organize_posts=0,listener_started=False,video_recorded=False,
        human_confirmation='NOT_RUN',formal_delivery='NOT_RUN',business_correctness='NOT_RUN',
        api_requests=[],blocked_requests=[],page_errors=[],checks=[],row_checks=[],plan=None,task_id=None)
    plan_ready=asyncio.Event(); task_started=asyncio.Event(); response_jobs=set()
    started=time.monotonic()
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        context=await browser.new_context(viewport=dict(width=1440,height=1000))
        page=await context.new_page(); page.set_default_timeout(15000)

        async def get(path):
            assert path.startswith('/api/')
            response=await context.request.get(ORIGIN+path,timeout=15000)
            assert response.ok, f'Live GET failed {path}: {response.status}'
            return await response.json()

        async def guard(route):
            request=route.request; url=urlsplit(request.url)
            record=dict(method=request.method,path=url.path)
            try:
                assert url.scheme=='http' and url.netloc=='127.0.0.1:8765', 'Browser may only contact the sole live workbench'
                if request.method not in ('GET','HEAD'):
                    assert request.method=='POST' and url.path in (f'/api/projects/{PID}/actions/plan',f'/api/projects/{PID}/actions'), 'Forbidden write interface'
                    body=request.post_data_json
                    validate_input(body)
                    if url.path.endswith('/actions'):
                        await asyncio.wait_for(plan_ready.wait(),10)
                        assert report.get('plan') and not report.get('plan_error'), 'Task plan failed scope verification'
                        assert body.get('plan_hash')==report['plan']['plan_hash']
                        assert report['organize_posts']==0, 'Only one organize POST is authorized'
                        assert body.get('idempotency_key')
                        report['organize_posts']+=1
                        report['idempotency_key']=body['idempotency_key']
                    record['body']=body
                if url.path.startswith('/api/'): report['api_requests'].append(record)
                await route.continue_()
            except Exception as error:
                record['reason']=str(error)
                report['blocked_requests'].append(record)
                await route.abort('blockedbyclient')

        async def observe(response):
            request=response.request; url=urlsplit(response.url)
            if request.method!='POST':return
            if url.path==f'/api/projects/{PID}/actions/plan':
                try:
                    assert response.ok, f'Plan returned HTTP {response.status}'
                    value=await response.json(); validate_plan(value,project)
                    report['plan']=value; write(OUTPUT/'plan.json',value)
                except Exception as error:
                    report['plan_error']=str(error)
                finally: plan_ready.set()
            elif url.path==f'/api/projects/{PID}/actions':
                report['action_http_status']=response.status
                try:
                    value=await response.json(); write(OUTPUT/'action-response.json',value)
                    if response.ok:report['task_id']=value['id']
                    else:report['action_error']=value
                except Exception as error:
                    report['action_error']=str(error)
                finally:task_started.set()

        def queue_response(response):
            job=asyncio.create_task(observe(response)); response_jobs.add(job)
            job.add_done_callback(response_jobs.discard)

        await context.route('**/*',guard)
        page.on('response',queue_response)
        page.on('pageerror',lambda error:report['page_errors'].append(str(error)))
        try:
            runtime=await get('/api/session')
            report['runtime']=runtime.get('backend')
            assert runtime.get('backend',{}).get('runtime_id')==args.expected_runtime_id, 'Runtime differs from the reviewed deployment'
            live_project=await get(f'/api/projects/{PID}')
            assert all(live_project.get(key)==project.get(key) for key in ('id','name','revision','items')), 'Live project changed after backup; do not send'
            assert snapshot(DATABASE)==before, 'Live canonical data changed after backup; do not send'
            prior_task_ids={rid for rid,pid,kind,_ in before['records'] if pid==PID and kind=='user_task'}
            preparation=await get(f'/api/projects/{PID}/refinement-preparation')
            write(OUTPUT/'preparation-before.json',preparation)
            assert preparation['draft_schema']==2 and preparation['status']=='needs_organize' and preparation['auto_allowed']
            assert preparation['revision']==EXPECTED_REVISION
            await context.add_init_script("sessionStorage.setItem('ra-connected-step-"+PID+"','refine');sessionStorage.removeItem('ra-refinement-entry-"+PID+"');")
            await page.goto(ORIGIN+'/',wait_until='networkidle')
            await page.locator('aside.sidebar button[title="电价管理"]').filter(has_text='f14612').click()
            await expect(page.get_by_role('heading',name='电价管理',exact=True)).to_be_visible()
            # A restored refine tab must now go through the ordinary coordinator.
            # Only click an authorization UI if its real plan has passed all guards.
            while not task_started.is_set():
                assert time.monotonic()-started<60, 'Restore did not start its one organize task'
                assert not report['blocked_requests'] and not report.get('plan_error'), report
                dialog=page.get_by_role('dialog',name='核对本次任务',exact=True)
                if await dialog.is_visible():
                    await asyncio.wait_for(plan_ready.wait(),10)
                    validate_plan(report['plan'],project)
                    await expect(dialog).to_contain_text(RECEIVER)
                    await expect(dialog).to_contain_text(MODEL)
                    authorize=dialog.get_by_role('button',name='授权本次范围并运行',exact=True)
                    assert await authorize.count()==1, 'Unexpected execution UI; no speculative clicking'
                    await authorize.click()
                await asyncio.sleep(.25)
            assert report['task_id'] and not report.get('action_error'), report
            await page.screenshot(path=str(OUTPUT/'01-real-task-progress.png'))
            task=None
            while time.monotonic()-started<args.timeout_seconds:
                tasks=await get(f'/api/projects/{PID}/actions')
                created=[item for item in tasks if item['id'] not in prior_task_ids]
                assert len(created)==1 and created[0]['id']==report['task_id'], 'Unexpected additional live task'
                task=created[0]
                if task['status'] in TERMINAL:break
                await asyncio.sleep(2)
            assert task and task['status'] in TERMINAL, 'Task remains active; inspect existing task, never rerun automatically'
            write(OUTPUT/'task-final.json',task)
            report['task_status']=task['status']; report['actual_model_calls']=task.get('calls')
            assert task['status'] in ('succeeded','partial'), 'Actual model task did not produce usable results'
            assert task.get('preparation_draft_schema')==2 and task.get('preparation_result',{}).get('draft_schema')==2
            visible_after=await get(f'/api/projects/{PID}')
            after_project=snapshot(DATABASE)['project']
            assert visible_after['revision']==after_project['revision'] and visible_after['items']==after_project['items']
            write(OUTPUT/'project-after.json',after_project)
            runs=[run for run in await get(f'/api/projects/{PID}/runs') if run.get('user_task_id')==task['id']]
            write(OUTPUT/'runs.json',runs)
            expected_excerpts={(source['id'],excerpt['id']): (source,excerpt)
                for source in project['sources'] for excerpt in source.get('excerpts',[])}
            assert runs and all(run['stage']=='ingest' for run in runs)
            for run in runs:
                assert not run.get('omitted_excerpt_ids'), 'Source excerpts were omitted'
                sent=(run.get('source_snapshot') or {}).get('references',[])
                assert {(ref['source_id'],ref['excerpt_id']) for ref in sent}==set(expected_excerpts)
                for ref in sent:
                    source,excerpt=expected_excerpts[(ref['source_id'],ref['excerpt_id'])]
                    assert ref['source_sha256']==source['sha256']
                    assert ref['excerpt_sha256']==sha(excerpt.get('text','').encode('utf-8'))
                assert set(run.get('sent_excerpt_ids',[]))=={key[1] for key in expected_excerpts}
            rows=coverage(after_project,roots)
            write(OUTPUT/'coverage-after.json',dict(requirement_count=len(rows),rows=rows))
            assert len(rows)==10 and all(row['acceptance_ids'] for row in rows), 'Some original requirements still have no acceptance draft'
            items={item['id']:item for item in after_project['items']}
            confirmation=await get(f'/api/projects/{PID}/requirement-confirmation/state')
            confirmed={row['requirement_id']:row['current'] for row in confirmation['rows']}
            table=page.get_by_role('table',name='需求确认表',exact=True)
            await expect(table).to_be_visible(timeout=30000)
            await expect(page.get_by_role('navigation',name='需求工作流程').get_by_role('button',name=re.compile('完善需求'))).to_have_attribute('aria-current','step')
            for index,row in enumerate(rows):
                rid,title=row['requirement_id'],row['title']
                cell=table.locator(f'tr[data-cell-key="requirement-{rid}-acceptance"]')
                await expect(cell.locator('textarea')).to_have_count(len(row['acceptance_ids']))
                for number,aid in enumerate(row['acceptance_ids'],1):
                    field=cell.get_by_role('textbox',name=f'{title} 验收建议 {number}',exact=True)
                    await field.scroll_into_view_if_needed()
                    await expect(field).to_have_value(items[aid]['statement'])
                    await expect(field).to_be_in_viewport()
                    if items[aid]['selection_status']=='candidate':
                        await expect(field.locator('..').get_by_text('AI 建议 · 随本次确认',exact=True)).to_be_visible()
                if index in (0,len(rows)-1):
                    await page.screenshot(path=str(OUTPUT/f'02-row-{index+1}-acceptance.png'))
                root=items[rid]
                revisions=[item for item in after_project['items'] if item['kind']=='requirement'
                           and item.get('target_item_id')==rid and active(item)
                           and item.get('selection_status')=='candidate']
                offered=root if confirmed.get(rid) or not revisions else revisions[-1]
                ref=offered['source_refs'][0]
                source=next(source for source in after_project['sources'] if source['id']==ref['source_id'])
                excerpt=next(excerpt for excerpt in source['excerpts'] if excerpt['id']==ref['excerpt_id'])
                await table.locator(f'tr[data-requirement-id="{rid}"]').get_by_role('button',name='查看来源',exact=True).click()
                preview=page.get_by_role('dialog',name='附件内容',exact=True)
                await expect(preview.get_by_role('heading',name=source['title'],exact=True)).to_be_visible()
                await expect(preview.locator('[id="excerpt-'+ref['excerpt_id']+'"]')).to_have_text(excerpt['text'])
                await preview.get_by_role('button',name='关闭附件',exact=True).click()
                await page.get_by_role('dialog',name='项目资料',exact=True).get_by_role('button',name='关闭项目资料',exact=True).click()
                report['row_checks'].append(dict(row,all_textareas_match=True,visible_source=ref))
            report['checks'].append('真实restore完善需求经正常准备入口只发起一次整表任务，并自动显示10条原需求的验收草稿')
            report['checks'].append('所有发送摘录逐项hash匹配原始18份资料，无截断或静默遗漏')
            preparation=await get(f'/api/projects/{PID}/refinement-preparation')
            write(OUTPUT/'preparation-after.json',preparation)
            assert preparation['status'] in ('ready','ready_with_limits') and not preparation['needs_organize']
            assert preparation['draft_coverage']['complete']
            assert report['organize_posts']==1 and not report['blocked_requests'] and not report['page_errors']
            report['displayed_acceptance_count']=sum(len(row['acceptance_ids']) for row in rows)
            report['status']='passed'
        except Exception as error:
            report.update(status='failed',failure=str(error),traceback=traceback.format_exc())
            await page.screenshot(path=str(OUTPUT/'failure.png'))
        finally:
            if response_jobs:await asyncio.gather(*response_jobs,return_exceptions=True)
            current=snapshot(DATABASE)
            write(OUTPUT/'project-after.json',current['project'])
            report['protections']=protections(before,current)
            try:
                report['protections']['original_source_files_unchanged']=source_files(current['project'])==originals
            except Exception as error:
                report['protections']['original_source_files_unchanged']=False
                report['source_file_error']=str(error)
            checks=report['protections']
            if not all(value for key,value in checks.items() if key!='changed_confirmation_fields'):
                report['status']='failed';report['protection_failure']='Original data, confirmation fields, or other projects changed; inspect before continuing'
            report['output_revision']=current['project']['revision']
            report['elapsed_seconds']=round(time.monotonic()-started,1)
            write(OUTPUT/'result.json',report)
            await context.close();await browser.close()
    print(json.dumps({key:report.get(key) for key in ('status','task_id','task_status','actual_model_calls',
        'displayed_acceptance_count','output_revision','failure','protection_failure')},ensure_ascii=False))
    if report['status']!='passed':raise SystemExit(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-authorized-current-project',action='store_true')
    parser.add_argument('--expected-runtime-id',required=True)
    parser.add_argument('--timeout-seconds',type=int,default=600)
    asyncio.run(run(parser.parse_args()))
