"""Freeze the actual tariff project, then run one explicitly bounded real task.

Preparation is read-only against daily data. Execution and later human-review
recording use separate clones; no listener is created and no result is adopted.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PID = 'P-d4b61a83e6f14612'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def isolated_session(path):
    path = path.resolve()
    boundary = (ROOT / 'evidence/tariff-entry-20261010').resolve()
    assert path.is_relative_to(boundary) and path != boundary, 'Choose a new session below the tariff evidence directory'
    return path


def copy_database(source, destination):
    destination.mkdir(parents=True)
    with sqlite3.connect((source/'requirements.sqlite3').as_uri()+'?mode=ro', uri=True) as original:
        with sqlite3.connect(destination/'requirements.sqlite3') as cloned:
            original.backup(cloned)
    for path in source.iterdir():
        if path.name.startswith('requirements.sqlite3'):
            continue
        if path.is_dir():
            shutil.copytree(path, destination/path.name)
        else:
            shutil.copy2(path, destination/path.name)


def provider_metadata(config, endpoint, key_present):
    return dict(**{key: config.get(key) for key in (
        'name', 'model', 'base_url', 'timeout', 'max_tokens', 'thinking_disabled',
        'input_price', 'output_price')}, origin=endpoint, key_present=key_present)


def configured_provider(Provider):
    # ProtectedKeyStore.load and ProjectEnvironment only read original secrets.
    # Credentials are never copied into the snapshot or written to reports.
    return Provider(ROOT/'.env', credential_dir=ROOT/'data/.credentials')


async def prepare(args, session):
    assert not session.exists(), 'Never overwrite an earlier snapshot or evidence'
    session.mkdir(parents=True)
    os.environ['RA_DATA_DIR'] = str((session/'import-data').resolve())
    from app.main import create_app, ActionPlan
    from app.provider import Provider, DEFAULT, origin
    from app.store import Store
    from app.core import digest
    from scripts.check_shared_draft_live import daily_fingerprint

    daily_before = daily_fingerprint()
    snapshot = session/'prepared'
    snapshot.mkdir()
    target = Store(snapshot/'data')
    with sqlite3.connect((ROOT/'data/requirements.sqlite3').as_uri()+'?mode=ro', uri=True) as daily:
        daily.row_factory = sqlite3.Row
        daily.execute('BEGIN')
        row = daily.execute('SELECT * FROM projects WHERE id=?', (PID,)).fetchone()
        assert row, 'Requested actual project does not exist'
        project = json.loads(row['payload'])
        assert project['name'] == '电价管理', 'Project identity does not match the requested tariff project'
        if args.expected_revision is not None:
            assert project['revision'] == args.expected_revision, 'Daily project changed; inspect a new snapshot before a paid task'
        revisions = daily.execute('SELECT * FROM revisions WHERE project_id=? ORDER BY revision', (PID,)).fetchall()
        records = daily.execute('SELECT * FROM records WHERE project_id=? ORDER BY rowid', (PID,)).fetchall()
        active = [record['id'] for record in records if record['kind'] in ('run', 'user_task')
                  and json.loads(record['payload']).get('status') in ('queued', 'running')]
        assert not active, 'Requested project has an active task; do not clone it mid-execution'
        settings = daily.execute("SELECT * FROM settings WHERE id IN ('model','vision')").fetchall()
        with target.connect() as cloned:
            cloned.execute('INSERT INTO projects VALUES(?,?,?)', tuple(row))
            cloned.executemany('INSERT INTO revisions VALUES(?,?,?,?,?)', [tuple(item) for item in revisions])
            cloned.executemany('INSERT INTO records VALUES(?,?,?,?,?)', [tuple(item) for item in records])
            cloned.executemany('INSERT INTO settings VALUES(?,?)', [tuple(item) for item in settings])
    assert target.get(PID) == project
    rawdir = target.folder/'sources'
    rawdir.mkdir()
    copied, missing = [], []
    for source in project['sources']:
        original = (ROOT/'data/sources'/source['id']).resolve()
        assert original.parent == (ROOT/'data/sources').resolve()
        if original.is_file():
            data = original.read_bytes()
            assert hashlib.sha256(data).hexdigest() == source['sha256'], 'Raw source identity mismatch: '+source['id']
            (rawdir/source['id']).write_bytes(data)
            copied.append(source['id'])
        else:
            missing.append(source['id'])
    requirements = [item for item in project['items'] if item['kind'] == 'requirement'
                    and item.get('applies_to') == 'to_be' and not item.get('target_item_id')]
    requirement = next((item for item in requirements if '模板库' in item.get('title','')
                        and '列表' in item.get('title','')), None)
    assert requirement, 'No original tariff template-library list requirement; inspect actual titles'
    provider = configured_provider(Provider)
    app = create_app(target.folder, provider=provider)
    config_stage = 'ingest' if args.action == 'organize' else 'clarify'
    config = app.state.workflow.config(config_stage)
    key_present = bool(provider.key(config))
    body = ActionPlan(expected_revision=project['revision'], action=args.action, message='',
        document_type='prd', target=dict(kind='item', id=requirement['id']) if args.action == 'clarify' else None,
        max_calls=args.max_calls).model_dump()
    plan = app.state.actions.plan(PID, body)
    assert target.get(PID) == project, 'Preparation plan mutated the project'
    write(snapshot/'project-before.json', project)
    write(snapshot/'action-body.json', body)
    write(snapshot/'plan.json', plan)
    (snapshot/'project-id.txt').write_text(PID, encoding='utf-8')
    write(snapshot/'provider.json', provider_metadata(config, origin(config), key_present))
    manifest = dict(project_id=PID, project_name=project['name'], revision=project['revision'], config_stage=config_stage,
        project_hash=digest(project), config_hash=digest(config), source_ids=[s['id'] for s in project['sources']],
        copied_source_ids=copied, missing_raw_source_ids=missing, requirement_id=requirement['id'],
        requirement_title=requirement['title'], original_requirement_count=len(requirements),
        selected_requirement_count=sum(i.get('selection_status') == 'selected' for i in requirements),
        unspecified_change_count=sum(i.get('change_type') == 'unspecified' for i in requirements),
        acceptance_count=sum(i['kind'] == 'acceptance' for i in project['items']),
        model_origin=origin(config), model=config['model'], key_present=key_present,
        max_real_requests=args.max_calls, cost=None, plan_missing=plan['missing'],
        source_snapshot_only=True, external_material_added=False, provider_requests=0)
    write(snapshot/'manifest.json', manifest)
    write(snapshot/'oracle.json', dict(
        scope='Actual tariff project snapshot; requirements remain candidates until human review in isolated copy',
        expected=['Only the target template-library-list requirement and directly related acceptance can receive suggestions',
                  'Every generated clause retains actual existing source references',
                  'Existing original requirements and their selection states remain unchanged',
                  'At least one directly linked verifiable acceptance candidate is available for review, or a truthful limitation is reported',
                  'Unknown change type and business choices are not guessed into confirmed facts',
                  'One explicit UI confirmation can include displayed requirement, change type and acceptance without entering legacy editing'],
        human_business_confirmation='NOT_RUN', formal_delivery='NOT_RUN'))
    after = daily_fingerprint()
    write(snapshot/'daily-before.json', daily_before)
    write(snapshot/'daily-after.json', after)
    manifest['daily_unchanged'] = daily_before == after
    write(snapshot/'manifest.json', manifest)
    assert manifest['daily_unchanged'], 'Daily data changed during snapshot preparation; inspect before proceeding'
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


async def execute(args, session):
    prepared = session/'prepared'
    assert (prepared/'manifest.json').is_file(), 'Run --prepare first'
    output = session/'live'
    assert not output.exists(), 'Real-call phase already exists: never automatically replay a charged or unknown request'
    output.mkdir()
    os.environ['RA_DATA_DIR'] = str((session/'live-import-data').resolve())
    import httpx
    from app.main import create_app
    from app.provider import Provider, origin
    from app.core import Problem, digest
    from scripts.check_shared_draft_live import daily_fingerprint, safe_run_metrics
    manifest = json.loads((prepared/'manifest.json').read_text('utf-8'))
    body = json.loads((prepared/'action-body.json').read_text('utf-8'))
    copy_database(prepared/'data', output/'data')
    daily_before = daily_fingerprint()
    write(output/'daily-before.json', daily_before)

    class OnceProvider(Provider):
        requests = 0

        async def request(self, config, messages, evidence=None):
            if self.requests >= manifest['max_real_requests']:
                raise Problem('REAL_CALL_LIMIT', 'This isolated acceptance reached its explicit provider request limit; evidence retained')
            assert origin(config) == manifest['model_origin'] and config['model'] == manifest['model']
            self.requests += 1
            write(output/'provider-requests.json', dict(requests=self.requests, status='REQUEST_STARTED_NO_REPLAY'))
            value, meta = await super().request(config, messages, evidence=evidence)
            write(output/f'provider-response-{self.requests}.json', dict(value=value, meta=meta))
            write(output/'provider-response.json', dict(value=value, meta=meta))
            return value, meta

    provider = configured_provider(OnceProvider)
    app = create_app(output/'data', provider=provider)
    store = app.state.store
    frozen = store.get(PID)
    assert digest(frozen) == manifest['project_hash'], 'Prepared snapshot changed'
    assert digest(app.state.workflow.config(manifest.get('config_stage', 'clarify'))) == manifest['config_hash'], 'Prepared provider configuration changed'
    assert provider.key(app.state.workflow.config(manifest.get('config_stage', 'clarify'))), 'Configured key unavailable; no request sent'
    write(output/'project-before.json', frozen)
    (output/'project-id.txt').write_text(PID, encoding='utf-8')
    outcome = dict(project_id=PID, requirement_id=manifest['requirement_id'],
        scope='actual_tariff_project_isolated_'+body['action'], provider_requests=0,
        cost=None, paid_request_status='NOT_STARTED', model_output_adoption='NOT_RUN',
        human_product_acceptance='NOT_RUN', ui_recording='NOT_RUN', formal_delivery='NOT_RUN')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            csrf = (await client.get('/api/session')).json()['csrf']
            headers = dict(origin='http://testserver', **{'x-csrf-token':csrf})
            plan_reply = await client.post(f'/api/projects/{PID}/actions/plan', json=body, headers=headers)
            assert plan_reply.status_code == 200, plan_reply.text
            plan = plan_reply.json()
            write(output/'plan.json', plan)
            assert not plan['missing'], '; '.join(plan['missing'])
            if body['action'] == 'clarify':
                assert plan['requirement_recommendation_scope']['requirement_id'] == manifest['requirement_id']
            existing_sources = set(manifest['source_ids'])
            assert all(set(recipient['source_ids']) <= existing_sources for recipient in plan['recipients'])
            assert all(recipient['origin'] == manifest['model_origin'] for recipient in plan['recipients'])
            started = time.monotonic()
            task_reply = await client.post(f'/api/projects/{PID}/actions', headers=headers, json=dict(body,
                plan_hash=plan['plan_hash'], idempotency_key='tariff-live-'+uuid.uuid4().hex, authorize=True))
            assert task_reply.status_code == 200, task_reply.text
            task = task_reply.json()
            write(output/'task-initial.json', task)
            outcome.update(task_id=task['id'], paid_request_status='TASK_STARTED')
            previous = None
            while task['status'] in ('queued', 'running'):
                await asyncio.sleep(.5)
                task = store.get_record(PID, task['id'], 'user_task')
                marker = (task['status'], task.get('calls'))
                if marker != previous:
                    print(json.dumps(dict(status=task['status'], calls=task.get('calls')), ensure_ascii=False), flush=True)
                    previous = marker
            write(output/'task-final.json', task)
            current = store.get(PID)
            original_ids = {item['id'] for item in frozen['items']}
            candidates = [item for item in current['items'] if item['id'] not in original_ids]
            runs = [run for run in store.records(PID, 'run') if run['id'] in task['run_ids']]
            write(output/'runs.json', runs)
            write(output/'metrics.json', safe_run_metrics(runs))
            write(output/'candidate-details.json', candidates)
            rid = manifest['requirement_id']
            outcome.update(paid_request_status='TASK_TERMINAL_NO_REPLAY', task_status=task['status'],
                task_calls=task.get('calls'), provider_requests=provider.requests, cost=task.get('cost'),
                elapsed_seconds=round(time.monotonic()-started,3),
                requirement_candidate_ids=[i['id'] for i in candidates if i['kind']=='requirement' and i.get('target_item_id')==rid],
                acceptance_candidate_ids=[i['id'] for i in candidates if i['kind']=='acceptance' and rid in i.get('related_refs',[])],
                all_generated_items_unadopted=all(i.get('selection_status')=='candidate' for i in candidates),
                original_items_unchanged=all(next(i for i in current['items'] if i['id']==old['id'])==old for old in frozen['items']),
                original_sources_unchanged=current['sources']==frozen['sources'])
    except Exception as error:
        outcome.update(status='failed_or_blocked', error_code=getattr(error,'code',type(error).__name__),
                       error_message=str(error), provider_requests=provider.requests)
    finally:
        write(output/'project-after.json', store.get(PID))
        daily_after = daily_fingerprint()
        write(output/'daily-after.json', daily_after)
        outcome['daily_unchanged'] = daily_before == daily_after
        outcome['provider_requests'] = provider.requests
        write(output/'outcome.json', outcome)
        print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--execute-real-model', action='store_true')
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--action', choices=('clarify', 'organize'), default='clarify')
    parser.add_argument('--max-calls', type=int, choices=(1, 2), default=1,
                        help='Explicit request cap for a new preparation; 2 permits at most one existing guarded repair')
    args = parser.parse_args()
    session = isolated_session(args.session)
    asyncio.run(prepare(args, session) if args.prepare else execute(args, session))
