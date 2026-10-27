"""One table, one draft task, one confirmation, one whole-project recheck.

Browser traffic is intercepted into a temporary ASGI application at the sole
workbench origin. Local BMS snapshots never go to an external model.
"""
import argparse,asyncio,contextlib,copy,json,mimetypes,os,re,shutil,sqlite3,sys,tempfile
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
IMPORT=tempfile.TemporaryDirectory(prefix='ra-batch-import-');os.environ['RA_DATA_DIR']=str(Path(IMPORT.name)/'data')
import httpx,jsonschema
from playwright.async_api import async_playwright,expect
from app.main import create_app
from app.core import dumps
from app.provider import Provider,DEFAULT,origin
from app.business_draft import PROTOCOL,BEHAVIOR,_roots
from app.prewrite import input_hash
from app.prewrite_correction import plan as saved_plan
from app.decision_review import save_raw_answer
from tests.helpers import EXAMPLES
from tests.check_requirement_entry_browser import seed,RID,OTHER
from scripts.check_shared_draft_live import daily_fingerprint
ORIGIN='http://127.0.0.1:8765'


def detail():
    row=dict(id='F1',name='已保存内容',meaning='本期只读展示数据',data_type='text',source='已保存数据',required='只读展示',default_value='无默认值',constraints='按已保存内容展示',visibility='本期页面可见',editability='只读')
    row['evidence']={key:dict(status='proposal',reason='离线合成界面夹具，待用户核对') for key in row if key!='id'}
    return dict(schema_version=1,applicability={key:dict(applicable=key=='fields',reason='本期只读展示字段' if key=='fields' else '本期不包含该类变化') for key in ('fields','interactions','flow','state_transitions')},fields=[row],interactions=[],flow=dict(nodes=[],edges=[]),state_transitions=[])


class BatchProvider(Provider):
    business_draft_protocol=PROTOCOL
    def __init__(self,folder):super().__init__(folder/'absent.env');self.project=lambda:None;self.requests=[]
    def key(self,config):return 'OFFLINE_ONLY'
    async def request(self,config,messages,evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：')[1]);context=json.loads(messages[1]['content'][0]['text'])
        self.requests.append(dict(stage=header['stage'],draft_request=header.get('refinement_completion')))
        p=self.project()
        if header['stage']=='review':
            value=copy.deepcopy(EXAMPLES['review']);value.update(proposals=[],questions=[],findings=[])
            value['result'].update(assessment='ready_for_human_review',required_decisions=[],reviewed_refs=[i['id'] for i in p['items'] if i['selection_status']=='selected' and i['applies_to']=='to_be' and i['kind'] in ('requirement','rule','acceptance')])
        else:
            assert header['refinement_completion']['mode']=='prewrite' and context['prewrite_confirmation_points']
            refs=[dict(source=context['excerpts'][0]['source'],locator=context['excerpts'][0]['locator'])]
            field=lambda text:dict(text=text,basis='fact',sources=refs)
            offered={}
            for f in (p.get('prewrite_review') or {}).get('findings',[]):
                proposal=saved_plan(p,f)
                if proposal:
                    for cid in proposal['candidate_ids']:
                        candidate=next(i for i in p['items'] if i['id']==cid);offered[candidate['target_item_id']]=candidate
            roots=_roots(p);rows=[]
            for key,root in roots.items():
                after=offered.get(root['id'],root);behavior={key:(after.get('behavior') or {}).get(key) or '按本期已保存资料核对。' for key in BEHAVIOR}
                for q in p['questions']:
                    if root['id'] in q.get('related_refs',[]) and q.get('answer') and '只读' in q['answer']:
                        behavior['permissions']=q['answer']
                acs=[i for i in p['items'] if i['kind']=='acceptance' and not i.get('target_item_id') and (root['id'] in i.get('related_refs',[]) or i['id'] in root.get('related_refs',[]))]
                acceptance=[field(offered.get(i['id'],i)['statement']) for i in acs] or [field(root['title']+'按本期已保存内容显示。')]
                row=dict(row_key=key,title=root['title'],statement=field(after['statement']),behavior={k:field(v) for k,v in behavior.items()},acceptance=acceptance,questions=[])
                if header.get('detail_required'):row['detail_contract']=detail()
                rows.append(row)
            value=dict(summary='离线整批修改夹具，等待一次人工确认。',summary_fields={},requirements=rows,limitations=[],completion=dict(
                answers={q['question_key']:field(next((v['answer'] for v in p['questions'] if v.get('answer')),'本期仅只读查看，不新增写操作。')) for q in context['completion_questions']},
                profile={k:(p.get('scope_profile') or {}).get(k,default) for k,default in [('scenario','new_module'),('dependencies','independent')]},
                change_types={key:root.get('change_type') if root.get('change_type') in ('new','modified','preserved') else 'new' for key,root in roots.items()},scope_rows=list(roots)))
            jsonschema.validate(value,header['schema'])
        evidence.before_send(config);usage=dict(prompt_tokens=20,completion_tokens=20,total_tokens=40)
        evidence.response(json.dumps(value,ensure_ascii=False),http_status=200,finish_reason='stop',usage=usage)
        return value,dict(usage=usage,finish_reason='stop',origin=origin(config))


class LiveProvider(Provider):
    def __init__(self,replay=None):
        super().__init__(ROOT/'.env',credential_dir=ROOT/'data/.credentials');self.requests=[];self.replay=replay
    async def request(self,config,messages,evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：')[1])
        self.requests.append(dict(stage=header['stage'],draft_request=header.get('refinement_completion'),call_id=evidence.value['call_id'],replayed=bool(self.replay and header['stage']=='ingest')))
        if self.replay and header['stage']=='ingest':
            raw=json.loads(self.replay.read_text('utf-8'))['final_output']
            evidence.before_send(config);evidence.response(raw,http_status=200,finish_reason='stop',usage={})
            return json.loads(raw),dict(usage={},finish_reason='stop',origin='OFFLINE_REPLAY')
        return await super().request(config,messages,evidence=evidence)


def synthetic(store):
    pid=seed(store,'entry')
    with store.edit(pid,store.get(pid)['revision'],'合成成文检查待确认点') as (p,_):
        p['name']='合成储能云：全部检查点一次确认'
        for item in p['items']:
            if item['kind']=='requirement':item['change_type']='new'
        p['questions']=[];findings=[]
        for n,ids in enumerate(([RID,OTHER],[RID]),1):
            q=dict(id='Q-BATCH-'+str(n),question='检查点 '+str(n)+'：本期查看权限如何统一，是否包含写操作？',why='合成权限口径',related_refs=ids,source_refs=p['items'][0]['source_refs'],options=[],blocking=True,blocking_stage=3,status='open',answer=None,prewrite_origin='prewrite-1')
            p['questions'].append(q)
            if n==1:save_raw_answer(p,q['id'],'管理员与只读查看人员均只能查看；本期不新增维护、参数下发或控制操作。',store)
            findings.append(dict(id='PW-BATCH-'+str(n),title='本期权限口径待落实',message='两个确认点共享同一条需求，权限必须统一。',suggested_resolution='将已确认的角色权限同步到正文、权限说明和验收。',severity='blocker',related_refs=ids,question_id=q['id'],quotes=[dict(object_id=i,path='behavior.permissions',text='权限待确认') for i in ids],source_refs=[],status='open'))
        p['prewrite_review']=dict(id='PWR-BATCH',version='prewrite-1',input_revision=p['revision'],input_hash=input_hash(p),status='needs_confirmation',findings=findings,blocking_count=len(findings),resolved_count=0,message='合成检查发现权限口径待落实。')
    return store.get(pid)


async def run(args):
    args.output.mkdir(parents=True)
    daily=daily_fingerprint();report=dict(status='running',page_errors=[],requests=[],real_model_calls=0,api_events=[])
    real=getattr(args,'execute_real_model',False)
    assert not (real and args.local_bms_snapshot),'Local BMS data cannot be sent to a provider by this test'
    if real:
        live_folder=args.output/'live-session';live_folder.mkdir()
    with (contextlib.nullcontext(str(live_folder)) if real else tempfile.TemporaryDirectory(prefix='ra-batch-ui-')) as folder:
        provider=LiveProvider(args.replay_draft_from) if real else BatchProvider(Path(folder));app=create_app(Path(folder)/'data',provider=provider);store=app.state.store
        if real:
            with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:config=dict(DEFAULT,**json.loads(db.execute("select payload from settings where id='model'").fetchone()[0]))
            assert origin(config)=='https://api.deepseek.com' and config['model']=='deepseek-flash' and provider.key(config)
            store.setting('model',config)
        if args.local_bms_snapshot:
            with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
                p=json.loads(db.execute('select payload from projects where id=?',('P-e9e927a868c249d8',)).fetchone()[0])
                config=json.loads(db.execute("select payload from settings where id='model'").fetchone()[0]);store.setting('model',config)
            pid=store.create('隔离副本')['id'];p.update(id=pid,name='本机隔离副本：BMS 整批确认')
            with store.connect() as db:db.execute('update projects set revision=?,payload=? where id=?',(p['revision'],dumps(p),pid))
            (store.folder/'sources').mkdir(exist_ok=True)
            for source in p['sources']:
                src=(ROOT/'data/sources'/source['id']).resolve();assert src.is_relative_to((ROOT/'data/sources').resolve())
                if src.is_file():shutil.copyfile(src,store.folder/'sources'/source['id'])
        else:p=synthetic(store);pid=p['id']
        if getattr(args,'review_recommendations',False):
            assert not real and not args.local_bms_snapshot
            with store.edit(pid,p['revision'],'合成检查自带推荐回答',bump=False) as (p,_):
                p['prewrite_review']['findings'][1]['recommended_answer']='建议本期所有角色均只读查看，不新增参数下发或控制操作。'
            p=store.get(pid)
        provider.project=lambda:store.get(pid)
        initial=copy.deepcopy(store.get(pid));report['check_count']=len([f for f in p['prewrite_review']['findings'] if f['status']!='resolved' and (f['severity']=='blocker' or f.get('blocks_document'))])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch();context=await browser.new_context(viewport=dict(width=1366,height=768))
                async def route(r):
                    req=r.request;url=urlsplit(req.url);assert url.netloc=='127.0.0.1:8765'
                    if url.path.startswith('/api/'):
                        headers={k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')}
                        response=await client.request(req.method,url.path,headers=headers,content=req.post_data_buffer)
                        report['api_events'].append(dict(method=req.method,path=url.path,status=response.status_code))
                        await r.fulfill(status=response.status_code,headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')},body=response.content)
                    else:
                        build=args.static_build.resolve();path=(build/(url.path.lstrip('/') or 'index.html')).resolve();assert path.is_relative_to(build)
                        await r.fulfill(content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',body=path.read_bytes())
                await context.route('**/*',route);page=await context.new_page();page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
                try:
                    await page.goto(ORIGIN,wait_until='networkidle');await page.locator('aside.sidebar button[title="'+p['name']+'"]').click()
                    work=page.locator('.connected-workspace:visible');await work.get_by_role('button',name='不使用 AI，直接完善',exact=True).click()
                    await expect(work.get_by_role('button',name='保存草稿',exact=True)).to_be_enabled();await expect(page.locator('.request-feedback .feedback-spinner')).to_have_count(0)
                    await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
                    batch=work.get_by_role('region',name='成文检查确认表',exact=True)
                    await expect(batch.get_by_role('table',name='全部成文检查确认点')).to_be_visible()
                    assert await batch.locator('tr[data-check-id]').count()==report['check_count']
                    assert not provider.requests
                    if getattr(args,'review_recommendations',False):
                        answer=batch.get_by_role('textbox',name='检查点 2 的回答',exact=True)
                        await expect(answer).to_have_value('建议本期所有角色均只读查看，不新增参数下发或控制操作。')
                        assert store.get(pid)['questions'][1]['answer'] is None
                        await expect(batch.get_by_text('AI 推荐回答已填入，核对后可整表确认；不满意可改。',exact=True)).to_be_visible()
                        await answer.fill('建议只读查看，沿用人工正在修改的完整方案。')
                        await work.locator('.refinement-view-switch').get_by_role('button',name='需求列表',exact=True).click()
                        guard=page.get_by_role('dialog',name='未保存的修改',exact=True)
                        await expect(guard).to_be_visible()
                        await guard.get_by_role('button',name='保存后离开',exact=True).click()
                        await expect(work.get_by_role('button',name='保存草稿',exact=True)).to_be_visible()
                        await work.locator('.refinement-view-switch').get_by_role('button',name=re.compile('^成文检查')).click()
                        await expect(answer).to_have_value('建议只读查看，沿用人工正在修改的完整方案。')
                        assert not provider.requests
                    elif not real:
                        empty=next((f for f in p['prewrite_review']['findings'] if f.get('question_id') and not next((q for q in p['questions'] if q['id']==f['question_id']),{}).get('answer')),None)
                        if empty:
                            row=batch.locator('tr[data-check-id="'+empty['id']+'"]')
                            await expect(row.get_by_text('本轮检查尚未生成回答草稿',exact=True)).to_be_visible()
                            assert not await row.get_by_text('AI 推荐草稿，确认后才保存为你的回答。',exact=True).count()
                    evidence=work.locator('.prewrite-optional-details');await evidence.locator('summary').first.click()
                    assert await evidence.get_by_role('textbox').count()==0
                    assert await evidence.get_by_role('button',name=re.compile('整理|确认|生成')).count()==0
                    await evidence.locator('summary').first.click();assert not provider.requests
                    await page.screenshot(path=str(args.output/'01-all-checks.png'))
                    await batch.get_by_role('button',name='AI 批量起草全部修改',exact=True).click()
                    dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible()
                    await dialog.get_by_role('button',name='返回保留输入',exact=True).click();assert not provider.requests
                    await batch.get_by_role('button',name='AI 批量起草全部修改',exact=True).click();await expect(dialog).to_be_visible()
                    await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
                    if getattr(args,'review_recommendations',False):
                        keep=batch.get_by_role('button',name='保留全部手工修改，继续核对',exact=True)
                        await expect(keep).to_be_visible(timeout=20000)
                        await expect(batch.get_by_role('textbox',name='检查点 2 的回答',exact=True)).to_have_value('建议只读查看，沿用人工正在修改的完整方案。')
                        await keep.click()
                    confirm=batch.get_by_role('button',name='确认全部修改并复检',exact=True);await expect(confirm).to_be_enabled(timeout=180000 if real else 20000)
                    draft_calls=len(provider.requests)
                    assert (1<=draft_calls<=2 if real else draft_calls==1) and provider.requests[0]['draft_request']['mode']=='prewrite'
                    drafted=store.get(pid);assert drafted['questions']==initial['questions'] and drafted['documents']==initial['documents']
                    assert drafted['items'][:len(initial['items'])]==initial['items']
                    for width,height in ((1366,768),(1280,720),(1093,614)):
                        await page.set_viewport_size(dict(width=width,height=height))
                        bounds=await confirm.bounding_box();assert bounds and bounds['y']>=0 and bounds['y']+bounds['height']<=height,(width,height,bounds)
                        assert await batch.evaluate('e=>e.scrollWidth<=e.clientWidth+2')
                        scroll_height=await batch.locator('.prewrite-batch-scroll').evaluate('e=>e.clientHeight')
                        assert scroll_height>=160,(width,height,scroll_height)
                        assert await confirm.evaluate('e=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2))}')
                    await page.screenshot(path=str(args.output/'02-ready-on-laptop.png'))
                    await confirm.click();await expect(dialog).to_be_visible(timeout=15000)
                    assert len(provider.requests)==draft_calls,'Saving the whole table cannot call the provider'
                    assert len(store.records(pid,'refinement_confirmation_receipt'))==1
                    accepted=store.get(pid);assert all(q.get('understanding_status')=='applied' for q in accepted['questions'] if not q.get('superseded_by'))
                    assert accepted['documents']==initial['documents']
                    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                    await batch.get_by_role('button',name='继续整批复检',exact=True).click();await expect(dialog).to_be_visible()
                    assert len(provider.requests)==draft_calls and len(store.records(pid,'refinement_confirmation_receipt'))==1
                    prior_tasks={t['id'] for t in store.records(pid,'user_task')}
                    await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
                    if real:
                        review_task=None
                        for _ in range(1800):
                            tasks=[t for t in store.records(pid,'user_task') if t['id'] not in prior_tasks and t['action']=='prewrite']
                            review_task=tasks[-1] if tasks else None
                            if review_task and review_task['status'] not in ('queued','running'):break
                            await asyncio.sleep(.1)
                        assert review_task and review_task['status'] in ('succeeded','partial','awaiting_user'),review_task
                        report['real_model_calls']=len(provider.requests);report['real_prewrite_status']=store.get(pid)['prewrite_review']['status']
                        pending=[f for f in store.get(pid)['prewrite_review']['findings'] if f.get('kind')=='semantic' and f.get('status')!='resolved' and (f['severity']=='blocker' or f.get('blocks_document'))]
                        report['review_pending_count']=len(pending);report['review_recommendation_count']=sum(bool(f.get('recommended_answer')) for f in pending)
                        assert report['real_prewrite_status'] in ('passed','needs_confirmation')
                    else:
                        await expect(work.get_by_text('可以继续生成 PRD。',exact=True)).to_be_visible(timeout=20000)
                        assert len(provider.requests)==2 and provider.requests[1]['stage']=='review'
                    report.update(status='passed',model_requests=len(provider.requests),model_requests_by_stage=provider.requests,confirmation_requests=len([r for r in report['api_events'] if r['method']=='POST' and r['path'].endswith('/refinement-confirmation')]),checks=['所有检查点默认同表显示','打开与取消零调用','一次批量起草','旧内容与回答在确认前不变','共享条款合并','一次确认全部回答与条款','仅整批复检一次','笔记本底部按钮可见'])
                    await page.screenshot(path=str(args.output/'03-passed.png'))
                except Exception as error:
                    import traceback
                    report.update(status='failed',error=str(error),traceback=traceback.format_exc());await page.screenshot(path=str(args.output/'failure.png'),full_page=True)
                    (args.output/'project.json').write_text(json.dumps(store.get(pid),ensure_ascii=False,indent=2),'utf-8')
                    (args.output/'tasks.json').write_text(json.dumps(store.records(pid,'user_task'),ensure_ascii=False,indent=2),'utf-8')
                finally:
                    if real:report['real_model_calls']=len([r for r in provider.requests if not r.get('replayed')]);report['model_requests_by_stage']=provider.requests
                    await browser.close()
    report['daily_data_unchanged']=daily_fingerprint()==daily
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('api_events','traceback')},ensure_ascii=False));assert report['status']=='passed' and report['daily_data_unchanged']

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--static-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--local-bms-snapshot',action='store_true');parser.add_argument('--execute-real-model',action='store_true');parser.add_argument('--replay-draft-from',type=Path);parser.add_argument('--review-recommendations',action='store_true')
    asyncio.run(run(parser.parse_args()))
