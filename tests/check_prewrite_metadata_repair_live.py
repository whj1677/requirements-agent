"""Replay an unchanged captured failed review; only its metadata repair is live."""
import argparse,asyncio,copy,json,os,shutil,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('RA_DATA_DIR',str(ROOT/'evidence/conflict-closure-20261027/repair-import'))
from app.store import Store
from app.provider import Provider,DEFAULT,origin
from app.workflow import Workflow
from app.actions import Actions
from scripts.check_shared_draft_live import daily_fingerprint


class CapturedThenLive(Provider):
    business_draft_protocol=None
    def __init__(self,captured):
        super().__init__(ROOT/'.env',credential_dir=ROOT/'data/.credentials')
        self.captured=captured;self.real_calls=0;self.replays=0
    async def request(self,config,messages,evidence=None):
        if not self.replays:
            self.replays+=1
            evidence.value.update(final_output=json.dumps(self.captured,ensure_ascii=False),
                response_received=True,finish_reason='stop',response_model='CAPTURE_REPLAY')
            evidence.save()
            return copy.deepcopy(self.captured),dict(response_model='CAPTURE_REPLAY',finish_reason='stop')
        self.real_calls+=1
        return await super().request(config,messages,evidence=evidence)


async def run(args):
    assert args.execute_real_model
    source=args.resume_data.resolve();out=args.output.resolve()
    assert source.is_relative_to(ROOT/'evidence') and out.is_relative_to(ROOT/'evidence') and not out.exists()
    out.mkdir(parents=True);before=daily_fingerprint();store=Store(out/'data')
    with sqlite3.connect((source/'requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        p=json.loads(db.execute('select payload from projects').fetchone()[0]);assert '合成' in p['name']
        with sqlite3.connect(out/'data/requirements.sqlite3') as target:db.backup(target)
    for name in ('sources','evidence'):
        if (source/name).is_dir():shutil.copytree(source/name,out/'data'/name)
    capture=json.loads((source/'evidence/model-calls'/args.call).read_text('utf8'))
    provider=CapturedThenLive(json.loads(capture['final_output']))
    with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        config=dict(DEFAULT,**json.loads(db.execute("select payload from settings where id='model'").fetchone()[0]))
    assert origin(config)=='https://api.deepseek.com' and provider.key(config)
    store.setting('model',dict(config,max_calls=None));workflow=Workflow(store,provider);actions=Actions(store,workflow)
    body=dict(expected_revision=p['revision'],action='prewrite',message='',document_type='prd',option_id=None)
    plan=actions.plan(p['id'],body);assert not plan['missing']
    task=actions.start(p['id'],body,plan['plan_hash'],'captured-review-metadata-repair',True)
    await actions.jobs[task['id']];task=store.get_record(p['id'],task['id'],'user_task')
    current=store.get(p['id'])
    report=dict(status=task['status'],message=task.get('message'),actual_repair_calls=provider.real_calls,
        captured_review_replays=provider.replays,task_id=task['id'],daily_data_unchanged=daily_fingerprint()==before,
        requirements_unchanged=current['items']==p['items'],documents_unchanged=current['documents']==p['documents'])
    (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf8');print(json.dumps(report,ensure_ascii=False),flush=True)
    assert report['status'] in ('succeeded','awaiting_user') and provider.real_calls==1 and report['daily_data_unchanged']


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--resume-data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--call',required=True);p.add_argument('--execute-real-model',action='store_true');asyncio.run(run(p.parse_args()))
