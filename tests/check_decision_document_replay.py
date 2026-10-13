"""Private frozen-case replay and synthetic storage-cloud checks; zero model calls.

The genuine snapshots stay in ignored evidence. Only isolated application
factories are mutated; this script never reads or writes daily data.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ORIGIN = 'http://127.0.0.1:8765'
FIELDS = ('actor', 'entry', 'flow', 'data', 'permissions', 'result', 'exceptions')
REQ, RULE, AC = 'REQ-STATION-ALARM', 'RULE-STATION-ACCESS', 'AC-STATION-ALARM'
Q_PERMISSION, Q_TIME = 'Q-STATION-PERMISSION', 'Q-STATION-UPDATE-TIME'
PERMISSION = '只允许查看当前账号有权限的站点告警。'
TIMING = '用户点击刷新时更新告警列表。'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def isolated_app(folder):
    from app.main import create_app
    from app.provider import Provider
    class NoModelProvider(Provider):
        def __init__(self):
            super().__init__(env_path=folder / 'absent.env', credential_dir=folder / 'credentials')
            self.calls = 0
        async def request(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError('Real model calls forbidden')
    provider = NoModelProvider()
    return create_app(folder / 'isolated-data', provider=provider), provider


def make_payload(project, *, reference=None, answers=None, implementations=None, key='replay-table-confirmation-0001'):
    from app.requirement_confirmation import linked_acceptance
    reference = reference or project
    scope = reference['product_context']['scope_ids']
    rows = []
    for iid in scope:
        req = next(item for item in reference['items'] if item['id'] == iid)
        rows.append(dict(requirement_id=iid, statement=req['statement'], change_type=req.get('change_type', 'new'),
            behavior={field: req.get('behavior', {}).get(field, '') for field in FIELDS},
            acceptance_items=[dict(id=ac['id'], statement=ac['statement']) for ac in linked_acceptance(reference, req)
                              if ac.get('selection_status') in ('selected', 'candidate')], confirmed_question_ids=[]))
    linked = [dict(item_id=item['id'], statement=item['statement'],
                  behavior={field: item.get('behavior', {}).get(field, '') for field in FIELDS})
              for item in reference['items'] if item['kind'] in ('rule', 'constraint', 'goal')
              and any(item['id'] in q.get('related_refs', []) for q in reference['questions']
                      if any(answer['question_id'] == q['id'] and answer['answer'] for answer in answers or []))]
    return dict(expected_revision=project['revision'], idempotency_key=key, review_confirmed=True,
        context=dict(values={k:v for k,v in reference['product_context'].items() if k != 'scope_ids'},
                     field_states=reference.get('context_field_states', {}),
                     scope_profile=reference.get('scope_profile', dict(scenario='new_product', dependencies='unknown')),
                     scope_ids=scope), rows=rows, answers=answers or [], linked_clauses=linked,
        answer_implementations=implementations or {})


def seed_storage_case(store, *, suffix=''):
    from app.core import digest, now
    from app.product_flow import checkpoint, fingerprint
    project = store.create('合成大型储能云：站点告警权限与刷新时点')
    with store.edit(project['id'], project['revision'], '冻结合成告警权限与时点前置') as (p, _):
        source_text = ('合成测试资料，不是实际业务规则。' + PERMISSION +
                       '无权限站点的告警不展示，详情入口也拒绝。告警更新时点尚待明确。')
        refs = [dict(source_id='SRC-STORAGE-CLOSURE', excerpt_id='EX-STORAGE-CLOSURE')]
        p.update(workflow_mode='shared-draft', scope_profile=dict(scenario='new_module', dependencies='independent'),
            workspace_draft=dict(text=source_text, entry_mode='direct', saved_at=now()),
            intake=dict(mode='direct', text=source_text, saved_at=now()),
            product_context=dict(product='大型储能集成商云平台', module='站点告警查看', users='站点运维人员',
                intent='运维人员查看有权限站点的告警。', change_scope='新增站点告警查看模块。', scope_ids=[REQ]),
            context_field_states={key:dict(status='known') for key in ('users', 'intent', 'change_scope')})
        p['sources'] = [dict(id=refs[0]['source_id'], title='合成告警权限资料', purpose='goal', uri=None,
            sha256=digest(source_text), created=now(), version=1, excluded=False, parse_status='read', failure_reason='',
            excerpts=[dict(id=refs[0]['excerpt_id'], source_id=refs[0]['source_id'], locator='合成第1段', text=source_text, method='synthetic-fixture')])]
        def item(iid, kind, title, statement, related):
            return dict(id=iid, kind=kind, title=title, statement=statement, applies_to='to_be', epistemic_status='reported',
                source_refs=copy.deepcopy(refs), related_refs=related, selection_status='selected', revision=1, content_version=1, change_type='new')
        req = item(REQ, 'requirement', '站点告警查看', PERMISSION, [AC])
        req.update(behavior_review_required=True, behavior=dict(actor='站点运维人员', entry='从站点告警列表进入',
            flow='用户打开告警列表查询；自动更新的时点资料未说明。', data='站点与告警名称',
            permissions=PERMISSION, result='展示当前账号有权限站点的告警', exceptions='无告警时显示暂无告警'))
        rule = item(RULE, 'rule', '站点权限边界', '无权限站点的告警不展示，详情入口也拒绝。', [REQ])
        rule['behavior'] = dict(permissions=PERMISSION)
        p['items'] = [req, rule, item(AC, 'acceptance', '站点告警权限验收', '打开列表仅显示有权限站点的告警；无权限详情访问被拒绝。', [REQ])]
        p['questions'] = [dict(id=Q_PERMISSION, question='告警列表和详情是否都限定当前账号有权限站点？', why='合成权限与详情访问必须一致',
            options=[PERMISSION], blocking=False, related_refs=[REQ, RULE], source_refs=copy.deepcopy(refs), status='open', answer=None),
            dict(id=Q_TIME, question='告警列表在什么时点更新？', why='更新触发决定尚未落实', options=[TIMING], blocking=False,
                related_refs=[REQ], source_refs=copy.deepcopy(refs), status='open', answer=None,
                unresolved_locations=[dict(item_id=REQ, field='behavior.flow', quote='自动更新的时点资料未说明。')])]
        if suffix:
            aliases={item['id']:item['id']+suffix for item in p['items']}
            for item in p['items']:
                item['id']=aliases[item['id']]
                item['related_refs']=[aliases.get(iid,iid) for iid in item.get('related_refs',[])]
            p['product_context']['scope_ids']=[aliases[iid] for iid in p['product_context']['scope_ids']]
            for q in p['questions']:
                q['related_refs']=[aliases.get(iid,iid) for iid in q['related_refs']]
                for anchor in q.get('unresolved_locations',[]):anchor['item_id']=aliases[anchor['item_id']]
        checkpoint(p, 1, fingerprint(p, 1))
    return store.get(project['id'])


def run(args):
    output, fixtures = args.output.resolve(), args.fixtures.resolve()
    assert output.is_relative_to(ROOT / 'evidence') and not output.exists()
    output.mkdir(parents=True)
    manifest = json.loads((fixtures / 'manifest.json').read_text('utf-8'))
    snapshots = {}
    for entry in manifest['files']:
        raw = (fixtures / entry['file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry['payload_sha256']
        snapshots[entry['file']] = json.loads(raw)
    v24, v25, artifact = (snapshots[name] for name in ('v24.json', 'v25.json', 'artifact.json'))
    with tempfile.TemporaryDirectory(prefix='ra-decision-replay-') as scratch:
        os.environ['RA_DATA_DIR'] = str(Path(scratch) / 'import-data')
        from fastapi.testclient import TestClient
        from app.core import dumps, now
        from app.document_reader import reader_document
        from app.understanding import applied_targets
        app, provider = isolated_app(Path(scratch)); store = app.state.store
        pid = v24['id']
        with store.connect() as db:
            db.execute('INSERT INTO projects VALUES(?,?,?)', (pid, 24, dumps(v24)))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)', (pid, 24, dumps(v24), '真实案例v24冻结重放输入，不是业务确认', now()))
        history = copy.deepcopy(artifact)
        store.record(pid, 'document_artifact', history, rid='HISTORICAL-FROZEN-PRD')
        answers = [dict(question_id=q['id'], answer=q['answer']) for q in v25['questions'] if q.get('status') == 'answered']
        assert len(answers) == 4
        with TestClient(app, base_url=ORIGIN) as client:
            csrf = client.get('/api/session').json()['csrf']
            client.headers.update({'Origin':ORIGIN, 'X-CSRF-Token':csrf})
            body = make_payload(v24, reference=v25, answers=answers)
            response = client.post('/api/projects/' + pid + '/refinement-confirmation', json=body)
            assert response.status_code == 200, response.text
            result = response.json(); assert result['confirmed'], result
            assert result['destination'] == 'refinement'
            assert set(result['implementation_pending_ids']) == {row['question_id'] for row in answers}
            pending = store.get(pid)
            assert all(q.get('understanding_status') == 'pending' for q in pending['questions'] if q['id'] in result['implementation_pending_ids'])
            projection = copy.deepcopy(artifact)
            projection.update(item_snapshot=copy.deepcopy(pending['items']), question_snapshot=copy.deepcopy(pending['questions']), draft_revision=pending['revision'])
            reader = reader_document(projection)
            unresolved = [b['text'] for section in reader['sections'] for b in section['blocks'] if b['text'].startswith('待决事项：')]
            assert len(unresolved) == 5, unresolved
            assert sum('已回答，正文及关联验收仍待落实' in text for text in unresolved) == 4
            assert any('本期范围外' in text for text in unresolved)
            write(output / 'pending-reader.json', reader)
            implemented = copy.deepcopy(pending)
            refs = {}
            for q in implemented['questions']:
                if q['id'] not in result['implementation_pending_ids']: continue
                target = next(item for item in implemented['items'] if item['id'] == q['related_refs'][0])
                text = q['answer'].removeprefix('建议：').removeprefix('建议')
                target['behavior']['flow'] = text
                refs[q['id']] = [dict(item_id=target['id'], field='behavior.flow', quote=text)]
            resolved_body = make_payload(pending, reference=implemented, answers=answers, implementations=refs, key='replay-final-witness-confirmation-0002')
            final_response = client.post('/api/projects/' + pid + '/refinement-confirmation', json=resolved_body)
            assert final_response.status_code == 200, final_response.text
            final_result = final_response.json(); assert final_result['confirmed'] and final_result['destination'] == 'delivery', final_result
            final = store.get(pid)
            assert all(applied_targets(final, q) for q in final['questions'] if q['id'] in refs)
            projection.update(item_snapshot=copy.deepcopy(final['items']), question_snapshot=copy.deepcopy(final['questions']), draft_revision=final['revision'])
            final_reader = reader_document(projection)
            remaining = [b['text'] for section in final_reader['sections'] for b in section['blocks'] if b['text'].startswith('待决事项：')]
            assert len(remaining) == 1 and '本期范围外' in remaining[0]
            assert store.get_record(pid, 'HISTORICAL-FROZEN-PRD', 'document_artifact') == dict(history, id='HISTORICAL-FROZEN-PRD')
            synthetic = seed_storage_case(store)
            synthetic_body = make_payload(synthetic, answers=[dict(question_id=Q_PERMISSION, answer=PERMISSION),dict(question_id=Q_TIME, answer=TIMING)],
                implementations={Q_PERMISSION:[dict(item_id=iid, field='statement', quote=next(i['statement'] for i in synthetic['items'] if i['id']==iid)) for iid in (REQ,RULE)]}, key='storage-witness-and-unknown-0003')
            synthetic_response = client.post('/api/projects/' + synthetic['id'] + '/refinement-confirmation', json=synthetic_body)
            synthetic_result = synthetic_response.json(); assert synthetic_result['confirmed'], synthetic_result
            assert synthetic_result['implementation_pending_ids'] == [Q_TIME]
            saved = store.get(synthetic['id'])
            assert next(q for q in saved['questions'] if q['id']==Q_PERMISSION)['understanding_status']=='applied'
            assert next(q for q in saved['questions'] if q['id']==Q_TIME)['understanding_status']=='pending'
            assert next(i for i in saved['items'] if i['id']==REQ)['behavior']['flow']==next(i for i in synthetic['items'] if i['id']==REQ)['behavior']['flow']
            for item in saved['items']:
                old=next(i for i in synthetic['items'] if i['id']==item['id'])
                assert item['statement']==old['statement'] and item.get('behavior')==old.get('behavior')
        assert provider.calls == 0
        for entry in manifest['files']:
            assert hashlib.sha256((fixtures / entry['file']).read_bytes()).hexdigest() == entry['payload_sha256']
    report = dict(status='passed', frozen_case_draft_revision=24, historical_document_revision=25,
        old_four_answers_pending_without_witness=True, pending_reader_questions=5,
        resolved_reader_questions=1, historical_artifact_unchanged=True, fixture_hashes_unchanged=True,
        synthetic_two_target_original_reuse=True, synthetic_other_unknown_retained=True,
        model_calls=0, listener_started=False, daily_data_access=False, business_acceptance='NOT_RUN')
    write(output / 'result.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=ROOT/'evidence/decision-document-closure-20261012/fixtures')
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
