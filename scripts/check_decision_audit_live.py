"""One explicit diagnostic call against preserved synthetic draft evidence.

This tests the repaired semantic contract without regenerating the main draft.
It never edits the original session, retries, or writes daily project data.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from scripts.check_shared_draft_live import daily_fingerprint, write


async def main(args):
    assert args.execute_real_model
    session=ROOT/'evidence/shared-draft-live-20261008-02'
    output=ROOT/'evidence/shared-draft-audit-recheck-20261008'
    assert not output.exists(), 'Preserve previous evidence; never replay automatically'
    output.mkdir()
    from app.core import digest, ident
    from app.decision_semantics import build_request,apply_audit
    from app.domain_context import active_source_ids
    from app.model_evidence import ModelCallEvidence
    from app.provider import DEFAULT,Provider,origin
    from app.budgets import stage_config,check_context
    original=json.loads((session/'clarify/project-before.json').read_text('utf-8'))
    staged=json.loads((session/'clarify/project-after.json').read_text('utf-8'))
    for q in staged['questions']:
        q.pop('decision_review_proposal',None)
    runs=json.loads((session/'clarify/runs.json').read_text('utf-8'))
    run=next(r for r in runs if r['stage']=='clarify')
    sent=set(run['sent_excerpt_ids'])
    excerpts=[e for s in staged['sources'] for e in s['excerpts'] if e['id'] in sent]
    request=build_request(staged,original,excerpts)
    assert request and request['targets']
    with urllib.request.urlopen('http://127.0.0.1:8765/api/models',timeout=5) as response:
        settings=json.load(response)['model']
    config=stage_config({**DEFAULT,**settings,'budget_mode':'unlimited','max_calls':None},'clarify')
    assert origin(config)=='https://api.deepseek.com'
    assert active_source_ids(staged)<=set(staged['grants'][origin(config)]['source_ids'])
    provider=Provider(ROOT/'.env',credential_dir=ROOT/'data/.credentials')
    assert provider.key(config)
    check_context(request['messages'],config)
    before=daily_fingerprint()
    write(output/'input.json',request)
    evidence=ModelCallEvidence(output,ident('CALL'),ident('DIAG'),'decision_semantics',
        config['model'],origin(config),None,provider.key(config))
    evidence.request_context(config,request['messages'],dict(scope='one_synthetic_diagnostic_call',
        schema_hash=digest(request['schema']),source_run_id=run['id']),dict(target_count=len(request['targets'])))
    started=time.monotonic()
    outcome=dict(scope='targeted_semantic_contract_recheck_not_full_workflow',calls=1,cost=None)
    try:
        value,meta=await asyncio.wait_for(provider.request(config,request['messages'],evidence=evidence),config['timeout'])
        write(output/'response.json',value)
        write(output/'metadata.json',meta)
        summary=apply_audit(staged,value,request['targets'])
        outcome.update(status='structurally_valid_proposal_pending_independent_semantic_review',result=summary)
        evidence.finish('accepted',time.monotonic()-started)
        write(output/'staged-after.json',staged)
    except Exception as error:
        code=getattr(error,'code',type(error).__name__)
        outcome.update(status='not_applied',error=code,message=str(error))
        evidence.finish(code,time.monotonic()-started)
    finally:
        outcome.update(elapsed_seconds=round(time.monotonic()-started,3),daily_unchanged=daily_fingerprint()==before)
        write(output/'outcome.json',outcome)
    print(json.dumps(outcome,ensure_ascii=False))
    assert outcome['daily_unchanged']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-real-model',action='store_true')
    asyncio.run(main(parser.parse_args()))
