"""Resolve the current BMS conflicts in an isolated copy with a stateful oracle.

The frozen model proposes the displayed recommendations, never sends BMS data.
The review oracle only passes after exact targeted edits and answer application.
"""
import argparse,asyncio,copy,json,mimetypes,re,shutil,sqlite3,tempfile,time
from pathlib import Path
from urllib.parse import urlsplit
from tests.check_prewrite_batch_browser import ROOT,ORIGIN,Provider,DEFAULT,PROTOCOL,origin,create_app,daily_fingerprint,httpx,async_playwright,expect,EXAMPLES
from app.core import Problem
from app.business_draft import _roots,_item_aliases,_aliases
from app.detail_draft import input_detail
from app.requirements import delivery_items

PID='P-e9e927a868c249d8'
AC_REPLACEMENTS={'AC-fbaad6a5b4ea4c17':'AC-0194b3d874774e92','AC-a6ab0a8b778f47cb':'AC-77b3bc41243645bc',
    'AC-2d5bd66f75b34f37':'AC-85b48764a6244a97','AC-2e08c16d0d9b45d9':'AC-affe5e16f9d940f8',
    'AC-c48cd793ebcf4ef2':'AC-0194b3d874774e92','AC-70e22ecca1444b90':'AC-affe5e16f9d940f8',
    'AC-b1113595e9014232':'AC-c335c7cb1b7f432a','AC-d264d40b3ffc48ea':'AC-d961f7868d054c6e'}

def final_text(text):
    # Frozen choices in this acceptance case, not a production text-repair rule.
    replacements={
        '（该范围为建议，待确认为准）':'', '，该范围为建议待人工核对':'',
        '调试工具界面与内容按统型基线建议纳入，待确认':'调试工具界面与内容按统型基线纳入本期供应商交付要求',
        '建议纳入供应商侧交付要求，待确认':'纳入本期供应商侧交付要求',
        'AFE采集驱动及建议的Modbus':'AFE采集驱动及Modbus',
        '调试工具（建议含界面与内容按统型基线）':'调试工具（含界面与内容按统型基线）',
        '建议增加出厂测试':'本期纳入出厂测试','作为供应商侧交付要求，待人工确认':'作为供应商侧交付要求',
        '（若确认纳入本期供应商交付要求）':'',
    }
    for old,new in replacements.items():text=text.replace(old,new)
    return text

class Oracle(Provider):
    business_draft_protocol=PROTOCOL
    def __init__(self,folder,case):super().__init__(folder/'absent.env');self.case=case;self.requests=[];self.initial=None;self.project=None;self.failed=False;self.drafts=0
    def key(self,config):return 'OFFLINE_ONLY'
    async def request(self,config,messages,evidence=None):
        try:return await self.response(config,messages,evidence)
        except AssertionError as error:raise Problem('FIXTURE_ASSERTION',str(error)) from error
        except Exception as error:
            import jsonschema
            if isinstance(error,jsonschema.ValidationError):raise Problem('FIXTURE_SCHEMA','/'.join(map(str,error.absolute_path))+': '+error.message) from error
            raise
    async def response(self,config,messages,evidence=None):
        header=json.loads(messages[0]['content'].split('可信任务头：')[1]);context=json.loads(messages[1]['content'][0]['text']);p=self.project()
        self.requests.append(dict(stage=header['stage'],draft_request=header.get('refinement_completion')))
        evidence.before_send(config)
        if self.case=='retry' and header['stage']=='ingest' and not self.failed:
            self.failed=True;raise Problem('PROVIDER_UNAVAILABLE','合成故障：本轮模型暂时不可用，原稿保留。')
        if header['stage']=='review':
            items={i['id']:i for i in delivery_items(p)}
            for old,new in AC_REPLACEMENTS.items():assert items[old]['statement'].removeprefix('建议：')==next(i for i in self.initial['items'] if i['id']==new)['statement'].removeprefix('建议：'),old+' old acceptance was left behind'
            for iid in ('REQ-fa968adde1534a9c','REQ-8c61dd431b0e4fd9'):
                assert not re.search('该范围为建议|待确认为准|待人工核对|待人工确认|建议纳入，待确认|交付要求，待确认',json.dumps(items[iid],ensure_ascii=False).split('"confirmed_revisions"')[0]),iid
            for f in self.initial['prewrite_review']['findings']:
                if f['status']=='open' and f.get('question_id'):
                    q=next(q for q in p['questions'] if q['id']==f['question_id']);assert q.get('understanding_status')=='applied' and q.get('answer')
            assert {i['id'] for i in delivery_items(p)}=={i['id'] for i in delivery_items(self.initial)},'revision must replace existing acceptance instead of appending duplicates'
            value=copy.deepcopy(EXAMPLES['review']);value.update(proposals=[],questions=[],findings=[])
            value['result'].update(assessment='ready_for_human_review',required_decisions=[],reviewed_refs=list(items))
        elif header['stage']=='prd':
            value=dict(plan_version='4',sections=[dict(section_key='function',context_refs=[],normative_refs=[i['id'] for i in delivery_items(p) if i['kind'] in ('requirement','rule','acceptance')],discussion_refs=[],explanations=[])])
        else:
            self.drafts+=1
            assert header['refinement_completion']['mode']=='prewrite'
            choices={f['question_id']:f for f in self.initial['prewrite_review']['findings'] if f['status']=='open' and f.get('question_id')}
            # Displayed proposals must reach the drafter, even when the user
            # accepted their default text without typing an edit into the box.
            points={f['question_id']:f for f in context['prewrite_confirmation_points'] if f.get('question_id')}
            assert all(points[qid].get('recommended_answer')==f['recommended_answer'] for qid,f in choices.items()),'displayed recommendations were not sent to the drafter'
            refs=[{k:context['excerpts'][0][k] for k in ('source','locator')}]
            field=lambda text,basis='recommendation':dict(text=text,basis=basis,sources=refs)
            byid={i['id']:i for i in p['items']};item_aliases=_item_aliases(p)
            aliases=_aliases([dict(e,source_id=s['id']) for s in p['sources'] if not s.get('excluded') for e in s['excerpts']])[0]
            rows=[]
            for key,root in _roots(p).items():
                acs=[]
                for ac in delivery_items(p):
                    if ac['kind']!='acceptance' or root['id'] not in ac.get('related_refs',[]):continue
                    text=final_text(byid[AC_REPLACEMENTS.get(ac['id'],ac['id'])]['statement'])
                    changed=text!=ac['statement'];acs.append(dict(field(text,'recommendation' if changed else 'fact'),**({'target_ref':item_aliases[ac['id']]} if changed else {})))
                detail=input_detail(root['detail_contract'],aliases,item_aliases)
                detail=json.loads(final_text(json.dumps(detail,ensure_ascii=False)))
                for node in detail['flow']['nodes']:node.setdefault('actor','系统')
                for edge in detail['flow']['edges']:edge.setdefault('condition','')
                for group in (detail['fields'],detail['interactions'],detail['flow']['nodes'],detail['flow']['edges'],detail['state_transitions']):
                    for obj in group:obj['evidence']={k:dict(status='proposal',sources=refs) for k in obj if k not in ('id','evidence')}
                rows.append(dict(row_key=key,title=root['title'],statement=field(final_text(root['statement'])),
                    behavior={k:field(final_text(v),'fact' if final_text(v)==v else 'recommendation') for k,v in root['behavior'].items()},
                    acceptance=acs,questions=[],detail_contract=detail))
            answers={}
            pending_bytext={q['question']:q for q in p['questions'] if q['id'] in choices}
            for q in context['completion_questions']:
                proposal=choices[pending_bytext[q['text']]['id']]['recommended_answer']
                proposal=re.sub(r'此为建议，待确认[。.]?$','',proposal).removeprefix('推荐：')
                answers[q['question_key']]=field('本期确认：'+proposal)
            value=dict(summary='已按显示的推荐方案修订原条款，等待整批人工核对。',summary_fields={},requirements=rows,limitations=[],completion=dict(answers=answers,profile=p['scope_profile'],change_types={k:r['change_type'] for k,r in _roots(p).items()},scope_rows=list(_roots(p))))
            if self.case=='partial' and self.drafts==1:value['requirements']=rows[:-1]
            import jsonschema
            jsonschema.validate(value,header['schema'])
        usage=dict(prompt_tokens=1,completion_tokens=1,total_tokens=2)
        evidence.response(json.dumps(value,ensure_ascii=False),http_status=200,finish_reason='stop',usage=usage)
        return value,dict(usage=usage,finish_reason='stop',origin=origin(config))

async def run(args):
    args.output.mkdir(parents=True);daily=daily_fingerprint();report=dict(status='running',case=args.case,model_calls=0,page_errors=[],api_events=[])
    with tempfile.TemporaryDirectory(prefix='ra-bms-resolve-') as temporary:
        data=Path(temporary)/'data';data.mkdir();source=sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True);target=sqlite3.connect(data/'requirements.sqlite3');source.backup(target);target.close();source.close()
        shutil.copytree(ROOT/'data/sources',data/'sources')
        provider=Oracle(Path(temporary),args.case);app=create_app(data,provider=provider);store=app.state.store
        store.setting('model',dict(DEFAULT,business_draft_protocol=PROTOCOL,budget_mode='unlimited'))
        initial=store.get(PID);provider.initial=copy.deepcopy(initial);provider.project=lambda:store.get(PID);report['input_revision']=initial['revision']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client,async_playwright() as pw:
            browser=await pw.chromium.launch();page=await browser.new_page(viewport=dict(width=args.width,height=args.height));page.on('pageerror',lambda e:report['page_errors'].append(str(e)));report['viewport']=[args.width,args.height]
            await page.add_init_script("window.ops={clicks:[],inputEvents:0};document.addEventListener('click',e=>{const b=e.target.closest('button');if(b)window.ops.clicks.push(b.textContent.trim())});document.addEventListener('input',()=>window.ops.inputEvents++)")
            async def route(r):
                req=r.request;url=urlsplit(req.url);assert url.netloc=='127.0.0.1:8765'
                if url.path.startswith('/api/'):
                    response=await client.request(req.method,url.path+('?' +url.query if url.query else ''),content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k.lower() not in ('host','content-length','connection','accept-encoding')})
                    report['api_events'].append(dict(method=req.method,path=url.path,status=response.status_code))
                    await r.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','content-encoding','transfer-encoding')})
                else:
                    file=args.static_build/(url.path.lstrip('/') or 'index.html');await r.fulfill(content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream',body=file.read_bytes())
            await page.route('**/*',route)
            async def execute():
                started=time.monotonic()
                dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible(timeout=60000)
                report.setdefault('plan_wait_seconds',[]).append(round(time.monotonic()-started,2))
                button=dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$'));bounds=await button.bounding_box()
                assert bounds and bounds['y']>=0 and bounds['y']+bounds['height']<=args.height,'authorization action must be visible without scrolling through source identifiers'
                await button.click()
            async def wait_task(action,ids):
                for _ in range(800):
                    tasks=[t for t in store.records(PID,'user_task') if t['id'] not in ids and t['action']==action]
                    if tasks and tasks[-1]['status'] not in ('queued','running'):return tasks[-1]
                    await asyncio.sleep(.05)
                raise AssertionError('task timeout: '+action)
            try:
                await page.goto(ORIGIN,wait_until='networkidle');await page.locator('aside.sidebar button[title="'+initial['name']+'"]').click()
                work=page.locator('.connected-workspace:visible');await work.get_by_role('button',name='继续核对已有草稿',exact=True).click()
                batch=work.get_by_role('region',name='成文检查确认表',exact=True);await expect(batch).to_be_visible();assert store.get(PID)==initial
                await page.screenshot(path=str(args.output/'01-problems.png'))
                if args.case=='cancel':
                    await batch.get_by_role('button',name='AI 批量起草全部修改',exact=True).click()
                    dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible(timeout=20000)
                    await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
                    assert not provider.requests and store.get(PID)==initial
                for attempt in range(2 if args.case in ('retry','partial') else 1):
                    ids={t['id'] for t in store.records(PID,'user_task')};await expect(batch.get_by_role('button',name='AI 批量起草全部修改',exact=True)).to_be_enabled(timeout=5000);await batch.get_by_role('button',name='AI 批量起草全部修改',exact=True).click();await execute();task=await wait_task('organize',ids)
                    if args.case=='retry' and attempt==0:
                        assert task['status']=='failed';assert store.get(PID)['documents']==initial['documents'] and store.get(PID)['items']==initial['items']
                        await expect(batch.get_by_role('alert').filter(has_text='本次 AI 起草未完成')).to_be_visible(timeout=10000)
                        await expect(page.get_by_role('complementary',name='AI 任务进度',exact=True)).to_have_count(0)
                        await page.screenshot(path=str(args.output/'02-ai-unavailable.png'));continue
                    if args.case=='partial' and attempt==0:
                        assert task['status']=='partial'
                        before=store.get(PID);assert before.get('refinement_completion',{}).get('complete') is False
                        for original in delivery_items(initial):
                            actual=next(i for i in before['items'] if i['id']==original['id'])
                            for field in ('statement','behavior','detail_contract'):assert actual.get(field)==original.get(field)
                        await batch.get_by_role('button',name='确认全部修改并复检',exact=True).click()
                        await expect(batch.get_by_role('alert').filter(has_text='检查点缺少可确认的修改')).to_be_visible()
                        assert not [e for e in report['api_events'] if e['method']=='POST' and e['path'].endswith('/refinement-confirmation')]
                        await page.screenshot(path=str(args.output/'02-partial-draft.png'));continue
                    assert task['status'] in ('succeeded','partial'),{k:task.get(k) for k in ('status','error','message')}
                    assert store.get(PID).get('refinement_completion',{}).get('complete') is True
                assert store.get(PID)['documents']==initial['documents']
                await expect(batch.get_by_role('button',name='确认全部修改并复检',exact=True)).to_be_enabled(timeout=15000)
                await expect(page.get_by_role('complementary',name='AI 任务进度',exact=True)).to_have_count(0)
                await page.screenshot(path=str(args.output/'03-draft.png'))
                await batch.get_by_role('button',name='确认全部修改并复检',exact=True).click();ids={t['id'] for t in store.records(PID,'user_task')};await execute();task=await wait_task('prewrite',ids);assert task['status'] in ('succeeded','partial'),{k:task.get(k) for k in ('status','error','message')}
                await expect(work.get_by_role('button',name='返回并继续生成 PRD',exact=True)).to_be_enabled(timeout=15000);assert store.get(PID)['prewrite_review']['blocking_count']==0
                await work.get_by_role('button',name='返回并继续生成 PRD',exact=True).click();ids={t['id'] for t in store.records(PID,'user_task')};await execute();task=await wait_task('document',ids);assert task['status'] in ('succeeded','partial'),{k:task.get(k) for k in ('status','error','message')}
                reader=page.get_by_role('article',name='PRD 正文',exact=True);await expect(reader.locator('.document-detail-table table').first).to_be_visible(timeout=15000)
                current=store.get(PID);assert current['documents']['prd']['base_document_id']==initial['documents']['prd']['id']
                assert current['documents']['prd']['prewrite_snapshot']['blocking_count']==0
                async with page.expect_download() as event:await reader.get_by_role('button',name='下载评审稿 MD',exact=True).click()
                await (await event.value).save_as(str(args.output/'resolved-prd.md'))
                assert not report['page_errors'];report.update(status='passed',requests=provider.requests,operations=await page.evaluate('window.ops'),pending=0,document_updated=True)
                assert report['operations']['inputEvents']==0 and len(report['operations']['clicks'])<=12
                await page.screenshot(path=str(args.output/'04-updated-prd.png'))
            except Exception as error:
                import traceback
                report.update(status='failed',error=str(error),traceback=traceback.format_exc(),requests=provider.requests,operations=await page.evaluate('window.ops'));await page.screenshot(path=str(args.output/'failure.png'),full_page=True)
                report['button_debug']=await page.evaluate("()=>{const e=document.querySelector('.prewrite-batch-heading button');if(!e)return [];let f=e[Object.keys(e).find(k=>k.startsWith('__reactFiber'))],out=[];for(;f;f=f.return){const p=f.memoizedProps||{};if(p.project&&('onDraftAll'in p||'onGeneratePrd'in p)){let s=f.memoizedState,states=[];for(let i=0;s&&i<7;i++,s=s.next)states.push(s.memoizedState);out.push({name:f.type?.name,busy:p.busy,running:p.running,revision:p.project.revision,states:states.map(v=>typeof v==='object'?'object':v)})}}return out}")
                (args.output/'project.json').write_text(json.dumps(store.get(PID),ensure_ascii=False,indent=2),'utf-8')
                (args.output/'tasks.json').write_text(json.dumps(store.records(PID,'user_task'),ensure_ascii=False,indent=2),'utf-8')
            finally:await browser.close()
    report['daily_data_unchanged']=daily_fingerprint()==daily
    (args.output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8');print(json.dumps({k:v for k,v in report.items() if k not in ('api_events','traceback','error')},ensure_ascii=False));assert report['status']=='passed' and report['daily_data_unchanged']

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--static-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--case',choices=['complete','retry','partial','cancel'],default='complete');parser.add_argument('--width',type=int,default=1280);parser.add_argument('--height',type=int,default=720);asyncio.run(run(parser.parse_args()))
