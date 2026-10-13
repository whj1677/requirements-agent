"""Replay one preserved real response through actions, with zero network calls.

This checks parser/workflow recovery only. It is not a new model generation or
business acceptance, and never edits the original response or daily workspace.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


async def replay(session, output):
    boundary = (ROOT / 'evidence/tariff-entry-20261010').resolve()
    session, output = session.resolve(), output.resolve()
    assert session.is_relative_to(boundary) and output.is_relative_to(session)
    assert output not in (session, session / 'live', session / 'prepared') and not output.exists()
    original = session / 'live'
    response_path = original / 'provider-response.json'
    response_bytes = response_path.read_bytes()
    response_hash = hashlib.sha256(response_bytes).hexdigest()
    raw = json.loads(response_bytes)['value']
    expected = json.loads((original / 'project-before.json').read_text('utf-8'))
    body = json.loads((session / 'prepared/action-body.json').read_text('utf-8'))
    assert body['action'] == 'organize' and body['max_calls'] == 1
    output.mkdir(parents=True)
    os.environ['RA_DATA_DIR'] = str(output / 'import-data')

    from app.actions import Actions
    from app.business_draft import PROTOCOL
    from app.core import dumps
    from app.provider import Provider, origin
    from app.store import Store
    from app.workflow import Workflow

    data = output / 'data'
    data.mkdir()
    source_data = session / 'prepared/data'
    with sqlite3.connect((source_data / 'requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as source:
        with sqlite3.connect(data / 'requirements.sqlite3') as destination:
            source.backup(destination)
    if (source_data / 'sources').is_dir():
        shutil.copytree(source_data / 'sources', data / 'sources')

    class FrozenProvider(Provider):
        business_draft_protocol = PROTOCOL
        responses = 0

        def __init__(self):
            super().__init__(env_path=output / 'nonexistent-offline.env')

        def key(self, config):
            return 'offline-frozen-response-only'

        async def request(self, config, messages, evidence=None):
            assert self.responses == 0, 'Frozen replay must not append a repair or second response'
            header = json.loads(messages[0]['content'].split('可信任务头：', 1)[1])
            assert header['response_protocol'] == PROTOCOL and header['stage'] == 'ingest'
            self.responses += 1
            if evidence:
                # Satisfy the isolated workflow's receipt accounting only;
                # this provider never instantiates a network client.
                evidence.value['offline_replay'] = True
                evidence.value['original_response_sha256'] = response_hash
                evidence.before_send(config)
                evidence.response(dumps(raw), http_status=200, finish_reason='stop', usage={})
            return copy.deepcopy(raw), dict(request_model=config['model'],
                response_model='OFFLINE_REPLAY_OF_PRESERVED_RESPONSE', origin=origin(config),
                finish_reason='stop', usage={}, offline_replay=True)

    provider = FrozenProvider()
    store = Store(data)
    project = store.get(expected['id'])
    assert project == expected, 'Prepared project no longer equals the original real-call input'
    workflow = Workflow(store, provider)
    actions = Actions(store, workflow)
    plan = actions.plan(project['id'], body)
    assert plan['stages'] == ['ingest'] and not plan['missing'], plan
    write(output / 'plan.json', plan)
    write(output / 'project-before.json', project)
    (output / 'project-id.txt').write_text(project['id'], encoding='utf-8')
    started = time.monotonic()
    task = actions.start(project['id'], body, plan['plan_hash'], 'offline-replay-' + response_hash[:24], True)
    await actions.jobs[task['id']]
    finished = store.get_record(project['id'], task['id'], 'user_task')
    after = store.get(project['id'])
    original_ids = {item['id'] for item in project['items']}
    candidates = [item for item in after['items'] if item['id'] not in original_ids]
    reports = [r for r in after.get('business_draft_reviews', [])
               if r.get('run_id') in finished.get('run_ids', [])]
    runs = [run for run in store.records(project['id'], 'run') if run['id'] in finished.get('run_ids', [])]
    result = dict(mode='offline_workflow_replay', provider_requests=0, frozen_model_responses=provider.responses,
        original_real_session=str(original), original_response_sha256=response_hash,
        project_id=project['id'], input_revision=project['revision'], task_status=finished['status'],
        task_id=finished['id'], replay_internal_calls=finished.get('calls'),
        elapsed_seconds=round(time.monotonic() - started, 3),
        accepted_rows=len(reports[-1]['accepted']) if reports else 0,
        rejected_rows=len(reports[-1]['rejected']) if reports else 0,
        candidate_count=len(candidates),
        original_items_unchanged=all(next(i for i in after['items'] if i['id'] == old['id']) == old
                                     for old in project['items']),
        original_sources_unchanged=after['sources'] == project['sources'],
        original_questions_unchanged=all(next(q for q in after['questions'] if q['id'] == old['id']) == old
                                        for old in project['questions']),
        all_generated_items_unadopted=all(item['selection_status'] == 'candidate' for item in candidates),
        human_product_acceptance='NOT_RUN', formal_delivery='NOT_RUN', new_model_cost=0,
        limitation='Parser/workflow recovery of preserved output; not a new generation or business acceptance.')
    write(output / 'task-final.json', finished)
    write(output / 'runs.json', runs)
    write(output / 'candidate-details.json', candidates)
    write(output / 'project-after.json', after)
    write(output / 'business-draft-report.json', reports[-1] if reports else None)
    write(output / 'outcome.json', result)
    assert hashlib.sha256(response_path.read_bytes()).hexdigest() == response_hash
    assert finished['status'] == 'partial' and provider.responses == 1, finished
    assert all(result[key] for key in ('original_items_unchanged', 'original_sources_unchanged',
               'original_questions_unchanged', 'all_generated_items_unadopted')), result
    assert reports[-1]['structure_rejected'], 'Protocol extras must remain inspectable'
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(replay(args.session, args.output))
