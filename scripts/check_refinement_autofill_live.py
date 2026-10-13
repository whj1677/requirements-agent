"""Freeze the actual tariff project and run one bounded whole-table organize task.

Preparation reads the daily SQLite database and model configuration only. The
explicit execution phase uses an isolated ASGI application: no listener, human
confirmation, source replacement, paid-task replay, or automatic rerun.
"""
import argparse
import asyncio
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
BOUNDARY = ROOT / 'evidence/refinement-autofill-20261011'
FROZEN = BOUNDARY / 'project-before.json'
ORIGIN = 'https://api.deepseek.com'
MODEL = 'deepseek-flash'
BEHAVIOR_FIELDS = ('actor', 'entry', 'flow', 'data', 'permissions', 'exceptions', 'result')


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def value_hash(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), allow_nan=False).encode('utf-8'))


def valid_session(path):
    path = path.resolve()
    assert path.is_relative_to(BOUNDARY.resolve()) and path != BOUNDARY.resolve(), 'Use a new session below the refinement-autofill evidence directory'
    assert not path.is_relative_to((ROOT / 'data').resolve())
    return path


def daily_fingerprint():
    """One consistent read-only transaction; no checkpoint or recovery write."""
    result = {}
    with sqlite3.connect((ROOT / 'data/requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        for table in ('projects', 'revisions', 'records', 'settings', 'migrations'):
            rows = db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            result[table] = dict(count=len(rows), sha256=value_hash(rows))
    return result


def active(item):
    return (item.get('applies_to') == 'to_be' and not item.get('superseded_by')
            and item.get('selection_status') in ('candidate', 'selected'))


def original_roots(project):
    return [item for item in project.get('items', []) if item.get('kind') == 'requirement'
            and not item.get('target_item_id') and active(item)]


def acceptance_coverage(project, original_ids=None, roots=None):
    """Availability only; additional alternatives never inflate row coverage."""
    original_ids = set(original_ids or ())
    roots = roots if roots is not None else original_roots(project)
    items = project.get('items', [])
    current = {item['id']: item for item in items}
    results = []
    for frozen_root in roots:
        root = current.get(frozen_root['id'], frozen_root)
        versions = [root] + [item for item in items if item.get('kind') == 'requirement'
                            and item.get('target_item_id') == root['id'] and active(item)]
        version_ids = {item['id'] for item in versions}
        related = {ref for item in versions for ref in item.get('related_refs', [])}
        acceptance = [item for item in items if item.get('kind') == 'acceptance'
                      and not item.get('target_item_id') and active(item)
                      and (version_ids.intersection(item.get('related_refs', [])) or item['id'] in related)]
        acceptance_ids = {item['id'] for item in acceptance}
        revisions = [item for item in items if item.get('kind') == 'acceptance' and active(item)
                     and item.get('target_item_id') in acceptance_ids]
        available = [item for item in acceptance + revisions if str(item.get('statement') or '').strip()]
        results.append(dict(requirement_id=root['id'], title=root.get('title'),
            acceptance_count=len(acceptance), acceptance_ids=[item['id'] for item in acceptance],
            previously_existing_acceptance_ids=[item['id'] for item in acceptance if item['id'] in original_ids],
            new_acceptance_ids=[item['id'] for item in acceptance if item['id'] not in original_ids],
            acceptance_revision_candidate_ids=[item['id'] for item in revisions],
            requirement_revision_candidate_ids=[item['id'] for item in versions[1:]],
            has_reviewable_acceptance=bool(available),
            behavior_present_fields=[field for field in BEHAVIOR_FIELDS if any(
                str((item.get('behavior') or {}).get(field) or '').strip() for item in versions)]))
    covered = sum(row['has_reviewable_acceptance'] for row in results)
    return dict(measure='draft_availability_not_business_correctness_or_human_approval',
        requirement_count=len(results), requirements_with_acceptance=covered,
        coverage_ratio=covered / len(results) if results else None,
        missing_acceptance_requirement_ids=[row['requirement_id'] for row in results if not row['has_reviewable_acceptance']],
        rows=results)


def invariants(before, after):
    old = {item['id']: item for item in before['items']}
    current = {item['id']: item for item in after['items']}
    created = [item for item in after['items'] if item['id'] not in old]
    changed = [key for key, item in old.items() if current.get(key) != item]
    confirmation_keys = ('selection_status', 'requirement_confirmation', 'behavior_review',
                         'behavior_review_required', 'adoption_review', 'confirmed_at')
    confirmation_changes = [key for key, item in old.items() if any(
        current.get(key, {}).get(field) != item.get(field) for field in confirmation_keys)]
    valid_refs = {(source['id'], excerpt['id']) for source in after['sources'] for excerpt in source.get('excerpts', [])}
    invalid_refs = [dict(item_id=item['id'], source_ref=ref) for item in created
                    for ref in item.get('source_refs', [])
                    if (ref.get('source_id'), ref.get('excerpt_id')) not in valid_refs]
    protected = ('active_baseline_id', 'baselines', 'stage_checks', 'documents', 'requirement_confirmation_mode')
    return dict(original_items_unchanged=not changed, changed_original_item_ids=changed,
        original_confirmation_states_unchanged=not confirmation_changes,
        changed_original_confirmation_ids=confirmation_changes,
        original_sources_unchanged=before['sources'] == after['sources'],
        protected_project_fields_unchanged=all(before.get(key) == after.get(key) for key in protected),
        changed_protected_project_fields=[key for key in protected if before.get(key) != after.get(key)],
        all_new_items_candidate=all(item.get('selection_status') == 'candidate' for item in created),
        new_item_count=len(created), new_item_ids=[item['id'] for item in created],
        generated_source_bindings_valid=not invalid_refs, invalid_generated_source_refs=invalid_refs,
        new_items_without_source_refs=[item['id'] for item in created if not item.get('source_refs')])


def provider_metadata(config):
    return {key: config.get(key) for key in ('name', 'model', 'base_url', 'max_tokens',
        'timeout', 'thinking_disabled', 'input_price', 'output_price', 'business_draft_protocol')}


def copy_prepared_data(source, destination):
    destination.mkdir(parents=True)
    with sqlite3.connect((source / 'requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as original:
        with sqlite3.connect(destination / 'requirements.sqlite3') as cloned:
            original.backup(cloned)
    # Preparation stores only this project's opaque source objects, no secrets.
    shutil.copytree(source / 'sources', destination / 'sources')


async def prepare(args, session):
    assert not session.exists(), 'Never overwrite a prior preparation or paid-call evidence'
    frozen = read(FROZEN)
    assert (frozen['id'], frozen['name'], frozen['revision']) == (PID, '电价管理', 19)
    session.mkdir(parents=True)
    os.environ['RA_DATA_DIR'] = str((session / 'import-data').resolve())
    import httpx
    from app.main import create_app, ActionPlan
    from app.provider import Provider, origin
    from app.store import Store
    from app.refinement_preparation import view

    daily_before = daily_fingerprint()
    prepared = session / 'prepared'
    prepared.mkdir()
    store = Store(prepared / 'data')
    with sqlite3.connect((ROOT / 'data/requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as daily:
        daily.row_factory = sqlite3.Row
        daily.execute('BEGIN')
        row = daily.execute('SELECT * FROM projects WHERE id=?', (PID,)).fetchone()
        assert row is not None, 'Requested tariff project does not exist'
        project = json.loads(row['payload'])
        assert project == frozen, 'Daily project differs from the root-frozen v19 snapshot; stop before sending'
        revisions = daily.execute('SELECT * FROM revisions WHERE project_id=? ORDER BY revision', (PID,)).fetchall()
        records = daily.execute('SELECT * FROM records WHERE project_id=? ORDER BY rowid', (PID,)).fetchall()
        assert not any(record['kind'] in ('run', 'user_task') and json.loads(record['payload']).get('status') in ('queued', 'running') for record in records), 'Project has active work; do not copy it mid-execution'
        settings = daily.execute("SELECT * FROM settings WHERE id IN ('model','vision')").fetchall()
        with store.connect() as copied:
            copied.execute('INSERT INTO projects VALUES(?,?,?)', tuple(row))
            copied.executemany('INSERT INTO revisions VALUES(?,?,?,?,?)', [tuple(value) for value in revisions])
            copied.executemany('INSERT INTO records VALUES(?,?,?,?,?)', [tuple(value) for value in records])
            copied.executemany('INSERT INTO settings VALUES(?,?)', [tuple(value) for value in settings])
    source_dir = store.folder / 'sources'
    source_dir.mkdir()
    source_identity = []
    for source in project['sources']:
        source_path = (ROOT / 'data/sources' / source['id']).resolve()
        assert source_path.parent == (ROOT / 'data/sources').resolve(), 'Invalid opaque source identity'
        data = source_path.read_bytes()
        assert sha(data) == source['sha256'], 'Original source hash mismatch: ' + source['id']
        destination = source_dir / source['id']
        destination.write_bytes(data)
        assert sha(destination.read_bytes()) == source['sha256']
        source_identity.append(dict(source_id=source['id'], sha256=source['sha256'], bytes=len(data)))

    # Verify the same visible configuration as the daily UI without copying a
    # proxy placeholder or fetching credentials through that API.
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        reply = await client.get('http://127.0.0.1:8765/api/models')
        reply.raise_for_status()
        visible = reply.json()['model']
    provider = Provider(ROOT / '.env', credential_dir=ROOT / 'data/.credentials')
    app = create_app(store.folder, provider=provider)
    config = app.state.workflow.config('ingest')
    assert origin(config) == ORIGIN and config['model'] == MODEL, 'Configured receiver differs from the authorized official DeepSeek model'
    assert origin(visible) == ORIGIN and visible['model'] == MODEL
    for key in ('base_url', 'model', 'max_tokens', 'timeout', 'thinking_disabled'):
        assert visible.get(key) == config.get(key), 'Visible and snapshot model configuration differ: ' + key
    assert provider.key(config), 'Configured credential is unavailable; no model request sent'
    # Empty input deliberately adds no task-input source; the production
    # organize contract must itself draft the complete current table.
    body = ActionPlan(expected_revision=project['revision'], action='organize', message='',
                      document_type='prd', max_calls=args.max_calls).model_dump()
    plan = app.state.actions.plan(PID, body)
    assert plan['pending_image_count'] == 0, 'Pending image analysis found; report the full plan before execution'
    assert plan['stages'] == ['ingest'], 'Unexpected whole-table pipeline; inspect before a real task'
    assert store.get(PID) == project, 'Read-only planning changed the isolated project'
    write(prepared / 'project-before.json', project)
    write(prepared / 'action-body.json', body)
    write(prepared / 'plan.json', plan)
    write(prepared / 'preparation-state-before.json', view(store, PID))
    write(prepared / 'coverage-before.json', acceptance_coverage(project, {item['id'] for item in project['items']}))
    write(prepared / 'source-identity.json', source_identity)
    write(prepared / 'provider.json', dict(provider_metadata(config), origin=origin(config), key_present=True,
        visible_api='http://127.0.0.1:8765/api/models', proxy_configured=bool(config.get('proxy'))))
    manifest = dict(project_id=PID, project_name=project['name'], revision=19,
        project_hash=value_hash(project), frozen_file_sha256=sha(FROZEN.read_bytes()),
        config_hash=value_hash(config), source_ids=[source['id'] for source in project['sources']],
        source_count=len(project['sources']), original_requirement_count=len(original_roots(project)),
        model_origin=ORIGIN, model=MODEL, max_real_requests=args.max_calls,
        provider_requests=0, task_limit=1, plan_missing=plan['missing'],
        task_input_message='', materials_truncated=False, external_material_added=False,
        model_settings_overridden=False, cost=None)
    daily_after = daily_fingerprint()
    write(prepared / 'daily-before.json', daily_before)
    write(prepared / 'daily-after.json', daily_after)
    manifest['daily_unchanged'] = daily_before == daily_after
    write(prepared / 'manifest.json', manifest)
    assert manifest['daily_unchanged'], 'Daily data changed during read-only preparation; inspect concurrent activity'
    print(json.dumps(manifest, ensure_ascii=False), flush=True)


async def execute(args, session):
    prepared = session / 'prepared'
    manifest = read(prepared / 'manifest.json')
    body = read(prepared / 'action-body.json')
    assert manifest['daily_unchanged'] and not manifest['plan_missing'], 'Prepared snapshot has unresolved preconditions'
    assert sha(FROZEN.read_bytes()) == manifest['frozen_file_sha256'], 'Root-frozen evidence changed'
    live = session / 'live'
    assert not live.exists(), 'A prior or uncertain real attempt exists; never automatically replay it'
    live.mkdir()
    os.environ['RA_DATA_DIR'] = str((session / 'live-import-data').resolve())
    import httpx
    from app.main import create_app
    from app.core import Problem
    from app.provider import Provider, origin
    from app.refinement_preparation import view
    from scripts.check_shared_draft_live import safe_run_metrics

    copy_prepared_data(prepared / 'data', live / 'data')
    daily_before = daily_fingerprint()
    write(live / 'daily-before.json', daily_before)

    class CountedProvider(Provider):
        requests = 0

        async def request(self, config, messages, evidence=None):
            if self.requests >= manifest['max_real_requests']:
                raise Problem('REAL_CALL_LIMIT', 'This one-task real verification reached its authorized request cap; no replay')
            assert origin(config) == manifest['model_origin'] and config['model'] == manifest['model']
            self.requests += 1
            number = self.requests
            write(live / 'provider-requests.json', dict(requests=number, status='REQUEST_STARTED_NO_REPLAY'))
            value, meta = await super().request(config, messages, evidence=evidence)
            write(live / f'provider-response-{number}.json', dict(value=value, meta=meta))
            write(live / 'provider-response.json', dict(value=value, meta=meta))
            return value, meta

    provider = CountedProvider(ROOT / '.env', credential_dir=ROOT / 'data/.credentials')
    app = create_app(live / 'data', provider=provider)
    store = app.state.store
    before = store.get(PID)
    assert value_hash(before) == manifest['project_hash'], 'Prepared project changed'
    assert value_hash(app.state.workflow.config('ingest')) == manifest['config_hash'], 'Prepared provider configuration changed'
    assert provider.key(app.state.workflow.config('ingest')), 'Configured credential unavailable; no model request sent'
    for source in before['sources']:
        assert sha((store.folder / 'sources' / source['id']).read_bytes()) == source['sha256']
    write(live / 'project-before.json', before)
    write(live / 'preparation-state-before.json', view(store, PID))
    original_ids = {item['id'] for item in before['items']}
    roots = original_roots(before)
    write(live / 'coverage-before.json', acceptance_coverage(before, original_ids, roots))
    outcome = dict(project_id=PID, input_revision=before['revision'], scope='whole_table_organize_isolated',
        provider_requests=0, cost=None, cost_status='unknown', paid_request_status='NOT_STARTED',
        human_business_acceptance='NOT_RUN', ui_recording='NOT_RUN', formal_delivery='NOT_RUN',
        model_output_confirmation='NOT_RUN', model_output_adoption='NOT_RUN')
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            csrf = (await client.get('/api/session')).json()['csrf']
            headers = dict(origin='http://testserver', **{'x-csrf-token': csrf})
            response = await client.post(f'/api/projects/{PID}/actions/plan', json=body, headers=headers)
            response.raise_for_status()
            plan = response.json()
            write(live / 'plan.json', plan)
            assert not plan['missing'], '; '.join(plan['missing'])
            assert plan['pending_image_count'] == 0 and plan['stages'] == ['ingest']
            assert all(recipient['origin'] == ORIGIN and recipient['model'] == MODEL
                       and set(recipient['source_ids']) <= set(manifest['source_ids']) for recipient in plan['recipients'])
            response = await client.post(f'/api/projects/{PID}/actions', headers=headers, json=dict(body,
                plan_hash=plan['plan_hash'], idempotency_key='refinement-autofill-' + uuid.uuid4().hex, authorize=True))
            response.raise_for_status()
            task = response.json()
            write(live / 'task-initial.json', task)
            outcome.update(task_id=task['id'], paid_request_status='TASK_STARTED')
            previous = None
            while task['status'] in ('queued', 'running'):
                await asyncio.sleep(.5)
                task = store.get_record(PID, task['id'], 'user_task')
                marker = (task['status'], task.get('calls'))
                if marker != previous:
                    print(json.dumps(dict(status=task['status'], calls=task.get('calls')), ensure_ascii=False), flush=True)
                    previous = marker
            write(live / 'task-final.json', task)
            runs = [run for run in store.records(PID, 'run') if run['id'] in task['run_ids']]
            write(live / 'runs.json', runs)
            write(live / 'metrics.json', safe_run_metrics(runs))
            outcome.update(paid_request_status='TASK_TERMINAL_NO_REPLAY', task_status=task['status'],
                task_error=task.get('error'), task_message=task.get('message'), task_calls=task.get('calls'),
                cost=task.get('cost'), cost_status='known' if task.get('cost') is not None else 'unknown',
                result_applied=any(run.get('result_applied') is True for run in runs),
                omitted_excerpt_ids=[entry for run in runs for entry in run.get('omitted_excerpt_ids', [])])
    except Exception as error:
        outcome.update(status='failed_or_blocked', error_code=getattr(error, 'code', type(error).__name__),
                       error_message=str(error))
    finally:
        after = store.get(PID)
        write(live / 'project-after.json', after)
        write(live / 'candidate-details.json', [item for item in after['items'] if item['id'] not in original_ids])
        coverage = acceptance_coverage(after, original_ids, roots)
        write(live / 'coverage-after.json', coverage)
        write(live / 'all-current-row-coverage.json', acceptance_coverage(after, original_ids))
        write(live / 'preparation-state-after.json', view(store, PID))
        checks = invariants(before, after)
        checks['original_raw_source_files_unchanged'] = all(
            (ROOT / 'data/sources' / source['id']).is_file()
            and sha((ROOT / 'data/sources' / source['id']).read_bytes()) == source['sha256']
            for source in before['sources'])
        write(live / 'invariants.json', checks)
        daily_after = daily_fingerprint()
        write(live / 'daily-after.json', daily_after)
        outcome.update(provider_requests=provider.requests, elapsed_seconds=round(time.monotonic() - started, 3),
            daily_unchanged=daily_before == daily_after, invariants=checks,
            original_requirement_count=len(roots), requirements_with_acceptance=coverage['requirements_with_acceptance'],
            acceptance_draft_coverage_ratio=coverage['coverage_ratio'],
            missing_acceptance_requirement_ids=coverage['missing_acceptance_requirement_ids'],
            coverage_does_not_prove_business_quality=True)
        write(live / 'outcome.json', outcome)
        print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--execute-real-model', action='store_true')
    parser.add_argument('--session', required=True, type=Path)
    parser.add_argument('--max-calls', type=int, choices=(3,), default=3,
                        help='One task, at most three actual requests; preserve the normal guarded review flow')
    args = parser.parse_args()
    session = valid_session(args.session)
    asyncio.run(prepare(args, session) if args.prepare else execute(args, session))
