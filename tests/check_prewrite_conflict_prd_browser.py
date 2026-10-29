"""Synthetic conflict-to-PRD user acceptance, frozen provider only.
The browser uses the deployed UI contract and isolated ASGI, never daily writes.
"""
import argparse,asyncio,contextlib,copy,json,mimetypes,re,sqlite3,tempfile
from pathlib import Path
from urllib.parse import urlsplit
from tests.check_prewrite_batch_browser import ROOT,ORIGIN,Provider,PROTOCOL,DEFAULT,origin,create_app,daily_fingerprint,httpx,async_playwright,expect
from scripts.run_prewrite_agent_validation import seed as base_seed,CONTRADICTION,DECISION
from app.prewrite import apply_result,input_hash
from app.decision_review import save_raw_answer
from app.business_draft import _roots,_item_aliases,_aliases
from app.detail_draft import input_detail
from app.requirements import delivery_items
from tests.helpers import EXAMPLES

PERMISSION_BAD='仅进入页面时核对权限，响应返回时不再核对权限。'
PERMISSION_GOOD='查询响应返回时必须重新核对站点查看权限；权限失效则隐藏详情并提示无权限。'
MANUAL='派单结果仍未知且具有本站查看权限时，才显示并启用查询按钮。'
ALTERNATIVE='本期确认：只有结果未知可查询；查询超时调整为20秒；成功与失败均是终态，不再查询。'


def review_value(p,kind=None):
    response=copy.deepcopy(EXAMPLES['review']);response.update(summary='合成工程验收：核对查询状态、权限与已确认口径。',proposals=[],questions=[],findings=[])
    response['result'].update(assessment='needs_changes' if kind else 'ready_for_human_review',required_decisions=[],reviewed_refs=[i['id'] for i in delivery_items(p) if i['kind'] in ('requirement','rule','acceptance')])
    if kind:
        req=next(i for i in p['items'] if i['id']=='REQ-QUERY');prop='success' if kind=='permission' else 'preconditions'
        question='查询响应返回时是否要再次核对站点查看权限？' if kind=='permission' else '哪些派单结果状态允许手工查询？'
        answer=PERMISSION_GOOD if kind=='permission' else DECISION
        response['findings']=[dict(type='contradiction',severity='blocker',topic_key='acceptance-'+kind,
            message='查询响应的权限处理与已确认规则冲突。' if kind=='permission' else '正文只允许未知状态查询，但交互仍允许成功或失败时查询。',
            suggested_resolution='同步修改关联正文、交互说明与验收，按已确认口径统一。',decision_question=question,recommended_answer=answer,
            related_refs=['REQ-QUERY'],source_refs=req['source_refs'],evidence_quotes=[dict(object_id=req['id'],path='detail_contract.interactions.I1.'+prop,quote=req['detail_contract']['interactions'][0][prop])])]
        response['result']['required_decisions']=[question]
    return response


def seed(store,case):
    p=base_seed(store)
    with store.edit(p['id'],p['revision'],'冻结用户验收资料与上下文') as (p,_):
        p['scope_profile']=dict(scenario='new_module',dependencies='independent')
        values=dict(product='储能云平台',module='派单任务详情',intent='仅查询已有派单任务的结果',current_state='已有派单任务与系统记录',users='有当前站点查看权限的运维员',value='核对结果未知的派单任务，避免重复派单',change_scope='本期仅提供手工查询已有派单结果的入口',preserve_scope='不改变原派单记录，不创建派单任务',out_of_scope='不包含首次派单、失败重试、自动轮询或权限管理',priority='本例只验证已明确的查询功能')
        p['product_context'].update(values,scope_ids=['REQ-QUERY'])
        p['context_field_states']={key:dict(status='known') for key in values}
        if case=='remaining':p['items'][0]['detail_contract']['interactions'][0]['success']=PERMISSION_BAD
        if case=='saved':
            p['questions'].append(dict(id='Q-ALREADY',question='哪些派单结果状态允许手工查询？',why='已确认但未落实',related_refs=['REQ-QUERY'],source_refs=p['items'][0]['source_refs'],blocking=True,status='open',answer=None,options=[]))
            save_raw_answer(p,'Q-ALREADY',DECISION,store)
    p=store.get(p['id'])
    with store.edit(p['id'],p['revision'],'冻结已完成的成文检查',bump=False) as (p,_):
        apply_result(p,review_value(p,'status'),'RUN-FROZEN-CONFLICT')
    return store.get(p['id'])


class ConflictProvider(Provider):
    business_draft_protocol=PROTOCOL
    def __init__(self,folder,case):super().__init__(folder/'absent.env');self.case=case;self.requests=[];self.project=lambda:None;self.ingests=0
    def key(self,config):return 'OFFLINE_ONLY'
    async def request(self,config,messages,evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：')[1]);context=json.loads(messages[1]['content'][0]['text']);p=self.project()
        if header['stage']=='prd' and getattr(self,'replay_plan',None):
            capture=json.loads(self.replay_plan.read_text('utf-8'));assert capture['response_received'] and capture['finish_reason']=='stop'
            old=json.loads(json.loads(capture['request_input'])[1]['content'][0]['text'])
            assert old['A_normative']==context['A_normative'],'a plan cannot be replayed against changed normative content'
            self.requests.append(dict(stage='prd',replayed=True,source_call_id=capture['call_id']))
            evidence.before_send(config);evidence.response(capture['final_output'],http_status=200,finish_reason='stop',usage={})
            return json.loads(capture['final_output']),dict(usage={},finish_reason='stop',origin='OFFLINE_REAL_PLAN_REPLAY')
        self.requests.append(dict(stage=header['stage'],draft_request=header.get('refinement_completion')))
        if header['stage']=='prd':
            value=dict(plan_version='4',sections=[dict(section_key='function',context_refs=[],normative_refs=[i['id'] for i in delivery_items(p) if i['kind'] in ('requirement','rule','acceptance')],discussion_refs=[],explanations=[])])
        elif header['stage']=='review':
            req=next(i for i in p['items'] if i['id']=='REQ-QUERY');pre=req['detail_contract']['interactions'][0]['preconditions'];success=req['detail_contract']['interactions'][0]['success']
            if CONTRADICTION in pre:value=review_value(p,'status')
            elif PERMISSION_BAD in success:value=review_value(p,'permission')
            else:
                assert '未知' in pre and ('仅' in pre or '才' in pre)
                if self.case=='manual':assert pre==MANUAL,'manual choice must reach the normative row'
                if self.case=='alternative':
                    assert '20秒' in json.dumps(req,ensure_ascii=False)
                    assert not any('10秒' in i['statement'] for i in delivery_items(p) if i['kind']=='acceptance'),'old acceptance must be revised rather than left beside its replacement'
                value=review_value(p)
        else:
            assert header['refinement_completion']['mode']=='prewrite';self.ingests+=1
            assert self.case!='alternative' or '20秒' in context['user_message'],'unsubmitted human choice must be sent as drafting context'
            source_aliases=_aliases([dict(e,source_id=s['id']) for s in p['sources'] if not s['excluded'] for e in s['excerpts']])[0]
            # Use actual aliases from this request, never guess source numbering.
            ref=context['excerpts'][0];refs=[dict(source=ref['source'],locator=ref['locator'])]
            if self.case=='alternative':
                new=next((e for e in context['excerpts'] if '20秒' in e['text']),None)
                assert new,'human change needs a persisted, authorized input excerpt'
                refs.append(dict(source=new['source'],locator=new['locator']))
            field=lambda text:dict(text=text,basis='recommendation',sources=refs)
            req=next(i for i in p['items'] if i['id']=='REQ-QUERY');detail=copy.deepcopy(req['detail_contract'])
            detail['interactions'][0]['preconditions']='仅结果未知且具有当前站点查看权限时，显示并启用查询按钮。'
            if self.case!='remaining' or self.ingests>1:detail['interactions'][0]['success']=PERMISSION_GOOD+'有权限则显示系统返回结果；仍未知可再次手工查询。'
            if self.case=='alternative':detail=json.loads(json.dumps(detail,ensure_ascii=False).replace('10秒','20秒'))
            aliases={ref['source_id']:ref for ref in req['source_refs']};item_aliases=_item_aliases(p)
            detail=input_detail(detail,source_aliases,item_aliases)
            for node in detail['flow']['nodes']:node.setdefault('actor','系统' if node['kind']!='start' else '有站点查看权限的运维员')
            for edge in detail['flow']['edges']:edge.setdefault('condition','')
            collections=[detail['fields'],detail['interactions'],detail['flow']['nodes'],detail['flow']['edges'],detail['state_transitions']]
            for rows in collections:
                for row in rows:row['evidence']={key:dict(status='proposal',sources=refs) for key in row if key not in ('id','evidence')}
            acceptance=[]
            for ac in delivery_items(p):
                if ac['kind']!='acceptance':continue
                text=ac['statement'].replace('10秒','20秒') if self.case=='alternative' else ac['statement']
                acceptance.append(dict(field(text),**({'target_ref':item_aliases[ac['id']]} if text!=ac['statement'] else {})))
            key=next(iter(_roots(p)))
            behavior=dict(req['behavior']);behavior['permissions']=PERMISSION_GOOD
            if self.case=='alternative':behavior={k:v.replace('10秒','20秒') for k,v in behavior.items()}
            statement='有站点查看权限的运维员仅能查询结果未知的派单任务；成功与失败均不允许再次查询；响应返回时重新核对权限。'
            if self.case=='alternative':statement+='查询超时为20秒，超时后保留原派单结果。'
            value=dict(summary='已对冲突条款提出整批修订，等待人工确认。',summary_fields={},requirements=[dict(row_key=key,title=req['title'],statement=field(statement),behavior={k:field(v) for k,v in behavior.items()},acceptance=acceptance,questions=[],detail_contract=detail)],limitations=[],completion=dict(
                answers={q['question_key']:field(PERMISSION_GOOD if '响应返回' in q['text'] else ALTERNATIVE if self.case=='alternative' else DECISION) for q in context['completion_questions']},profile=p['scope_profile'],change_types={key:'new'},scope_rows=[key]))
            import jsonschema
            try:jsonschema.validate(value,header['schema'])
            except jsonschema.ValidationError as error:raise AssertionError('Fixture schema '+str(list(error.absolute_path))+': '+error.message)
        evidence.before_send(config);usage=dict(prompt_tokens=30,completion_tokens=30,total_tokens=60)
        evidence.response(json.dumps(value,ensure_ascii=False),http_status=200,finish_reason='stop',usage=usage)
        return value,dict(usage=usage,finish_reason='stop',origin=origin(config))


class LiveConflictProvider(Provider):
    business_draft_protocol=PROTOCOL
    def __init__(self,replay=None):super().__init__(ROOT/'.env',credential_dir=ROOT/'data/.credentials');self.requests=[];self.replay=replay
    async def request(self,config,messages,evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：')[1])
        replay=bool(self.replay and header['stage']=='ingest')
        self.requests.append(dict(stage=header['stage'],draft_request=header.get('refinement_completion'),call_id=evidence.value['call_id'],replayed=replay))
        if replay:
            raw=json.loads(self.replay.read_text('utf-8'));assert raw['response_received'] and raw['finish_reason']=='stop' and not raw.get('json_integrity_failure')
            old=json.loads(json.loads(raw['request_input'])[1]['content'][0]['text']);current=json.loads(messages[1]['content'][0]['text'])
            content=lambda c:{(e['source'],e['locator']):e['text'] for e in c['excerpts']}
            assert content(old)==content(current),'a captured reply cannot be replayed against changed source material'
            evidence.before_send(config);evidence.response(raw['final_output'],http_status=200,finish_reason='stop',usage={})
            return json.loads(raw['final_output']),dict(usage={},finish_reason='stop',origin='OFFLINE_REAL_OUTPUT_REPLAY')
        return await super().request(config,messages,evidence=evidence)


async def run(args):
    args.output.mkdir(parents=True);before=daily_fingerprint();report=dict(status='running',case=args.case,page_errors=[],api_events=[],real_model_calls=0)
    real=getattr(args,'execute_real_model',False)
    assert not real or args.case=='saved','Real validation is bounded to the frozen saved-answer case'
    if real:
        live=args.output/'live-session';live.mkdir()
    with (contextlib.nullcontext(str(live)) if real else tempfile.TemporaryDirectory(prefix='ra-conflict-prd-')) as folder:
        provider=LiveConflictProvider(args.replay_draft_from) if real else ConflictProvider(Path(folder),args.case);app=create_app(Path(folder)/'data',provider=provider);store=app.state.store;store.setting('model',dict(DEFAULT,business_draft_protocol=PROTOCOL,budget_mode='unlimited'))
        if real:
            with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:config=json.loads(db.execute("select payload from settings where id='model'").fetchone()[0])
            assert origin(config)=='https://api.deepseek.com' and config['model']=='deepseek-flash' and provider.key(config)
            store.setting('model',config)
        if args.resume_data:
            assert not real and args.resume_data.resolve().is_relative_to((ROOT/'evidence').resolve())
            import shutil
            with sqlite3.connect(args.resume_data.resolve().joinpath('requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as source:
                p=json.loads(source.execute('select payload from projects').fetchone()[0]);assert p['test_provenance']['type']=='synthetic'
                assert all(json.loads(r[0])['status'] not in ('queued','running') for r in source.execute("select payload from records where kind in ('run','user_task')"))
                with sqlite3.connect(store.folder/'requirements.sqlite3') as target:source.backup(target)
                target.close()
            source.close()
            for name in ('sources','evidence'):
                if (args.resume_data/name).is_dir():shutil.copytree(args.resume_data/name,store.folder/name,dirs_exist_ok=True)
            provider.replay_plan=args.replay_plan_from
        else:p=seed(store,args.case)
        pid=p['id'];provider.project=lambda:store.get(pid);initial=copy.deepcopy(p)
        assert p['test_provenance']['type']=='synthetic'
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client,async_playwright() as pw:
            browser=await pw.chromium.launch();context=await browser.new_context(viewport=dict(width=1280,height=720))
            async def route(r):
                request=r.request;url=urlsplit(request.url);assert url.netloc=='127.0.0.1:8765'
                if url.path.startswith('/api/'):
                    response=await client.request(request.method,url.path+('?' +url.query if url.query else ''),content=request.post_data_buffer,headers={k:v for k,v in request.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')})
                    report['api_events'].append(dict(method=request.method,path=url.path,status=response.status_code,body=request.post_data_json if request.post_data else None))
                    await r.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
                else:
                    build=args.static_build.resolve();file=(build/(url.path.lstrip('/') or 'index.html')).resolve();assert file.is_relative_to(build)
                    await r.fulfill(content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream',body=file.read_bytes())
            await context.route('**/*',route);page=await context.new_page();page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
            try:
                await page.goto(ORIGIN,wait_until='networkidle');await page.locator('aside.sidebar button[title="'+p['name']+'"]').click()
                work=page.locator('.connected-workspace:visible');await expect(work.get_by_role('button',name='继续核对已有草稿',exact=True)).to_be_enabled()
                await page.screenshot(path=str(args.output/'00-assistant-entry.png'))
                await work.get_by_role('button',name='继续核对已有草稿',exact=True).click()
                if args.resume_data:
                    await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
                    await expect(work.get_by_text('可以继续生成 PRD。',exact=True)).to_be_visible()
                    await work.get_by_role('button',name='返回并继续生成 PRD',exact=True).click()
                    dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible()
                    ids={t['id'] for t in store.records(pid,'user_task')}
                    await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
                    for _ in range(600):
                        tasks=[t for t in store.records(pid,'user_task') if t['id'] not in ids and t['action']=='document']
                        if tasks and tasks[-1]['status'] not in ('queued','running'):break
                        await asyncio.sleep(.05)
                    assert tasks[-1]['status'] in ('succeeded','partial'),tasks[-1]
                else:
                    batch=work.get_by_role('region',name='成文检查确认表',exact=True);confirm=batch.get_by_role('button',name='确认全部修改并复检',exact=True)
                    await expect(confirm).to_be_enabled();assert not provider.requests and store.get(pid)==initial,'opening the existing draft must not change project data'
                    await page.screenshot(path=str(args.output/'01-conflict.png'))
                    async def execute():
                        dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible()
                        await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
                    async def wait_task(action,ids):
                        for _ in range(6000 if real else 600):
                            tasks=[t for t in store.records(pid,'user_task') if t['id'] not in ids and t['action']==action]
                            if tasks and tasks[-1]['status'] not in ('queued','running'):return tasks[-1]
                            await asyncio.sleep(.05)
                        raise AssertionError('Task did not finish: '+action)
                    if args.case=='review_draft':
                        await batch.get_by_role('button',name='先生成待确认评审稿',exact=True).click()
                        await expect(page.get_by_role('article',name='PRD 正文',exact=True)).to_be_visible(timeout=15000)
                        assert not provider.requests,'saved-content review PRD must not invoke the model'
                        assert store.get(pid)['documents']['prd']['assembly_origin']=='saved_content'
                        assert store.get(pid)['questions']==initial['questions'] and store.get(pid)['items']==initial['items']
                    else:
                        if args.case in ('manual','manual_ai'):
                            detail=batch.locator('tr').filter(has_text='详细定义').first;await detail.locator('summary').first.click()
                            editor=detail.locator('.detail-contract-editor');await editor.locator('[data-detail-section="interactions"]>summary').click()
                            await editor.locator('[data-detail-section="interactions"] .detail-object>summary').first.click()
                            await editor.get_by_role('textbox',name='派单任务结果查询 查询派单结果 前置条件',exact=True).fill(MANUAL)
                        if args.case=='alternative':await batch.get_by_role('textbox',name='检查点 1 的回答',exact=True).fill(ALTERNATIVE)
                        rounds=2 if args.case=='remaining' else 1
                        for round in range(rounds):
                            ids={t['id'] for t in store.records(pid,'user_task')};await batch.get_by_role('button',name='AI 批量起草全部修改',exact=True).click();await execute();task=await wait_task('organize',ids);assert task['status'] in ('succeeded','partial'),task
                            assert store.get(pid)['documents']==initial['documents']
                            if args.case in ('manual','manual_ai','alternative'):
                                await confirm.click();conflict=work.get_by_role('region',name='比较并处理冲突',exact=True);await expect(conflict).to_be_visible()
                                await expect(conflict.get_by_text('我的修改',exact=True).first).to_be_visible();await expect(conflict.get_by_text('AI 新建议',exact=True).first).to_be_visible()
                                await page.screenshot(path=str(args.output/'02-compare-versions.png'))
                                choice='采用 AI 建议' if args.case=='manual_ai' else '保留我的修改'
                                while await conflict.get_by_role('button',name=choice,exact=True).count():await conflict.get_by_role('button',name=choice,exact=True).first.click()
                            await confirm.click();ids={t['id'] for t in store.records(pid,'user_task')};await execute();task=await wait_task('prewrite',ids);assert task['status'] in ('succeeded','partial','awaiting_user'),task
                            current=store.get(pid)
                            if args.case=='remaining' and round==0:
                                assert current['prewrite_review']['status']=='needs_confirmation' and not current['documents'].get('prd')
                                assert all(q.get('answer') for q in current['questions'] if q['question']=='哪些派单结果状态允许手工查询？')
                                await page.screenshot(path=str(args.output/'03-still-pending.png'))
                            else:assert current['prewrite_review']['status']=='passed',current['prewrite_review']
                        await expect(work.get_by_text('可以继续生成 PRD。',exact=True)).to_be_visible()
                        assert all(q.get('understanding_status')=='applied' for q in store.get(pid)['questions'] if q.get('answer') and not q.get('superseded_by'))
                        await work.get_by_role('button',name='返回并继续生成 PRD',exact=True).click()
                        ids={t['id'] for t in store.records(pid,'user_task')};await execute();task=await wait_task('document',ids);assert task['status'] in ('succeeded','partial'),task
                artifact=store.get(pid)['documents'].get('prd');assert artifact,store.records(pid,'user_task')[-1]
                from app.document_reader import reader_document
                content=json.dumps(reader_document(artifact),ensure_ascii=False)
                if args.case!='review_draft':
                    assert CONTRADICTION not in content and PERMISSION_BAD not in content
                    accepted=next(i for i in delivery_items(store.get(pid)) if i['id']=='REQ-QUERY')
                    assert accepted['statement'] in content and accepted['detail_contract']['interactions'][0]['preconditions'] in content,'PRD must render the exact confirmed requirement and query condition'
                    if args.case=='manual':assert MANUAL in content
                    if args.case=='alternative':assert '20秒' in content and '超过10秒' not in content
                else:assert artifact.get('prewrite_snapshot',{}).get('blocking_count',0)>0
                reader=page.get_by_role('article',name='PRD 正文',exact=True);await expect(reader).to_be_visible(timeout=15000)
                await expect(reader.locator('.document-detail-table table').first).to_be_visible(timeout=15000)
                assert await reader.locator('.document-detail-table table').count()>=3
                await reader.locator('.document-detail-diagram img').first.scroll_into_view_if_needed()
                assert await reader.locator('.document-detail-diagram img').first.evaluate('i=>i.complete&&i.naturalWidth>0')
                download=reader.get_by_role('button',name='下载评审稿 MD',exact=True)
                await expect(download).to_be_visible()
                async with page.expect_download() as event:await download.click()
                await (await event.value).save_as(str(args.output/'synthetic-prd.md'))
                text=(args.output/'synthetic-prd.md').read_text('utf-8');assert '非正式交付' in text
                if args.case=='review_draft':
                    response=await client.get('/api/projects/'+pid+'/documents/prd/docx',params={'document_id':artifact['id']});assert response.status_code==409
                assert not report['page_errors'];await page.screenshot(path=str(args.output/'04-prd.png'))
                report.update(status='passed',model_requests=provider.requests,artifact_id=artifact['id'],document_generated=True,download_verified=True,prewrite_status=store.get(pid)['prewrite_review']['status'])
            except Exception as error:
                import traceback
                report.update(status='failed',error=str(error),traceback=traceback.format_exc());await page.screenshot(path=str(args.output/'failure.png'),full_page=True)
                (args.output/'project.json').write_text(json.dumps(store.get(pid),ensure_ascii=False,indent=2),'utf-8')
                (args.output/'tasks.json').write_text(json.dumps(store.records(pid,'user_task'),ensure_ascii=False,indent=2),'utf-8')
            finally:
                report['real_model_calls']=sum(not r.get('replayed') for r in provider.requests) if real else 0
                report['model_requests']=provider.requests
                await browser.close()
    report['daily_data_unchanged']=daily_fingerprint()==before
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('api_events','traceback','error')},ensure_ascii=False));assert report['status']=='passed' and report['daily_data_unchanged']

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--static-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--case',choices=['saved','manual','manual_ai','alternative','remaining','review_draft'],required=True);parser.add_argument('--execute-real-model',action='store_true');parser.add_argument('--replay-draft-from',type=Path);parser.add_argument('--resume-data',type=Path);parser.add_argument('--replay-plan-from',type=Path)
    asyncio.run(run(parser.parse_args()))
