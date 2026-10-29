"""Opt-in: injected HTTP faults followed by genuine DeepSeek recovery calls.

Synthetic fault responses are counted separately from actual provider traffic.
Uses the production decoder/workflow; never replaces a model result with a stub.
"""
import argparse,asyncio,copy,json,os,shutil,sqlite3,sys,uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('RA_DATA_DIR',str(ROOT/'evidence/full-acceptance-20261027/fault-import'))
import httpx
from app.main import create_app
from app.provider import Provider,DEFAULT,origin
from app.workflow import Workflow
from scripts.check_shared_draft_live import daily_fingerprint
from tests.provider_transport import reply


class FaultTransport(httpx.AsyncBaseTransport):
    def __init__(self,state):self.state=state;self.live=httpx.AsyncHTTPTransport()
    async def handle_async_request(self,request):
        self.state['attempts']+=1
        if self.state['attempts']==1:
            self.state['injected']+=1
            if self.state['fault']=='timeout':raise httpx.ReadTimeout('Synthetic timeout',request=request)
            return reply(request,self.state['malformed'],model='SYNTHETIC_FORMAT_FAULT')
        assert request.url.host=='api.deepseek.com'
        self.state['real_model_calls']+=1
        return await self.live.handle_async_request(request)
    async def aclose(self):await self.live.aclose()


async def run(args):
    assert args.execute_real_model
    out=args.output.resolve();assert out.is_relative_to(ROOT/'evidence') and not out.exists();out.mkdir(parents=True)
    before=daily_fingerprint();provider=Provider(ROOT/'.env',credential_dir=ROOT/'data/.credentials')
    with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        config=dict(DEFAULT,**json.loads(db.execute("select payload from settings where id='model'").fetchone()[0]))
    assert origin(config)=='https://api.deepseek.com' and config['model']=='deepseek-flash' and provider.key(config)
    config['max_calls']=None
    report=dict(status='running',scenarios=[])
    original=httpx.AsyncClient
    try:
        from tests.test_requirement_recommendation import project
        from tests.test_prd01 import plan
        from app.prd import plan_contract
        store,p=project(out/'syntax-data');store.setting('model',config);workflow=Workflow(store,provider)
        value=plan(plan_contract(p,'mrd')['normative_item_ids']);raw=json.dumps(value,ensure_ascii=False)
        malformed=raw.replace(', "sections"',' "sections"',1);assert malformed!=raw
        state=dict(fault='comma',malformed=malformed,attempts=0,injected=0,real_model_calls=0)
        def factory(*a,**kw):
            if kw.get('transport') is None:kw['transport']=FaultTransport(state)
            return original(*a,**kw)
        with patch('app.provider.httpx.AsyncClient',factory):
            r=workflow.start(p['id'],p['revision'],'prd','仅验证合成 PRD 编排的格式恢复',kind='mrd')
            await workflow.tasks[r['id']]
        result=store.get_record(p['id'],r['id'],'run')
        report['scenarios'].append(dict(name='malformed_json_real_repair',**state,status=result['status'],result_applied=result.get('result_applied'),run_id=r['id']))
        assert state['injected']==1 and state['real_model_calls']==1 and result.get('result_applied'),result.get('error')
        assert result['attempts'][1]['repair_of']==result['attempts'][0]['call_id']
        assert store.get(p['id'])['items']==p['items']
        source=args.resume_data.resolve();assert source.is_relative_to(ROOT/'evidence')
        app=create_app(out/'timeout-data',provider=provider);store=app.state.store
        with sqlite3.connect((source/'requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
            p=json.loads(db.execute('select payload from projects').fetchone()[0]);assert '合成' in p['name']
            assert all(json.loads(r[0])['status'] not in ('queued','running') for r in db.execute("select payload from records where kind in ('run','user_task')"))
            with sqlite3.connect(out/'timeout-data/requirements.sqlite3') as target:db.backup(target)
        for name in ('sources','evidence'):
            if (source/name).is_dir():shutil.copytree(source/name,out/'timeout-data'/name)
        store.setting('model',config);pid=p['id'];protected={k:copy.deepcopy(p.get(k)) for k in ('items','questions','documents','active_baseline_id')}
        state=dict(fault='timeout',attempts=0,injected=0,real_model_calls=0)
        async with original(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            csrf=(await client.get('/api/session')).json()['csrf'];headers={'origin':'http://testserver','x-csrf-token':csrf}
            async def action():
                body=dict(expected_revision=store.get(pid)['revision'],action='prewrite',message='按冻结合成资料检查当前需求、交互与验收，真实冲突需定位并给可核对方案。',document_type='prd',option_id=None)
                endpoint='/api/projects/'+pid
                plan_response=await client.post(endpoint+'/actions/plan',headers=headers,json=body);assert plan_response.status_code==200,plan_response.text
                planned=plan_response.json();assert not planned['missing'],planned['missing']
                response=await client.post(endpoint+'/actions',headers=headers,json=dict(body,authorize=True,plan_hash=planned['plan_hash'],idempotency_key=str(uuid.uuid4())))
                assert response.status_code==200,response.text
                task=response.json();job=app.state.actions.jobs.get(task['id'])
                if job:await job
                return store.get_record(pid,task['id'],'user_task')
            with patch('app.provider.httpx.AsyncClient',factory):
                failed=await action()
                assert failed['status']=='failed' and state['attempts']==1 and state['real_model_calls']==0,failed
                assert {k:store.get(pid).get(k) for k in protected}==protected
                report['scenarios'].append(dict(name='prewrite_timeout_preserves_content',status='passed',task_id=failed['id'],automatic_resends=0))
                recovered=await action()
                assert recovered['status'] in ('succeeded','partial','awaiting_user'),recovered
                assert state['real_model_calls']>=1
                assert {k:store.get(pid).get(k) for k in protected}==protected
                report['scenarios'].append(dict(name='explicit_prewrite_retry_real_model',status='passed',task_status=recovered['status'],real_model_calls=state['real_model_calls'],task_id=recovered['id']))
        report['status']='passed'
    except Exception as e:
        import traceback
        report.update(status='failed',error=str(e)[:2000],traceback=traceback.format_exc())
    report['daily_data_unchanged']=daily_fingerprint()==before
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='traceback'},ensure_ascii=False),flush=True)
    assert report['status']=='passed' and report['daily_data_unchanged']


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--resume-data',type=Path,required=True);p.add_argument('--execute-real-model',action='store_true');asyncio.run(run(p.parse_args()))
