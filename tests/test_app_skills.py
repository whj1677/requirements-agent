"""Packaged skills are methods, not sources, facts, or extra model calls."""
import copy
import hashlib
import json
import shutil

import pytest

from app import skill_guidance as skills
from app.core import Problem, dumps, digest
from app.provider import DEFAULT, assemble
from app.store import Store
from tests.helpers import prepared


def header(messages):
    return json.loads(messages[0]['content'].split('可信任务头：', 1)[1])


@pytest.fixture
def resources(tmp_path, monkeypatch):
    root = tmp_path / 'skills'
    shutil.copytree(skills.ROOT, root)
    monkeypatch.setattr(skills, 'ROOT', root)
    return root


def test_common_and_business_assembly_use_upstream_methods_without_changing_sources(tmp_path):
    from app import business_draft
    p = prepared(Store(tmp_path))
    before = copy.deepcopy(p)
    for builder, stage in [(assemble, 'review'), (business_draft.assemble, 'ingest')]:
        messages, excerpts, omitted = builder(p, stage, '核对已保存资料', DEFAULT, tmp_path)
        h = header(messages)
        selection = h['builtin_skills']['selected']
        assert selection and all(s['upstream_commit'] == skills.PINNED[s['id']] for s in selection)
        expected = 'pm-strategy-red-team' if stage == 'review' else 'anthropic-write-spec'
        assert selection[0]['id'] == expected
        assert all('vendor/' not in str(e) for e in excerpts)
        assert '技能片段只提供写作与检查方法' in messages[0]['content']
        assert 'source_ref' in messages[0]['content']
    assert p == before


def test_prd_selects_both_writing_skills_and_exact_original_slices(tmp_path):
    p = prepared(Store(tmp_path))
    guidance = skills.load(p, 'prd')
    ids = {s['id'] for s in guidance['manifest']['selected']}
    assert {'anthropic-write-spec', 'pm-create-prd'} <= ids
    for entry in guidance['manifest']['selected']:
        for section in entry['sections']:
            original = (skills.ROOT / section['path']).read_bytes().decode('utf-8')
            excerpt = ''.join(original.splitlines(keepends=True)[section['start_line']-1:section['end_line']])
            assert excerpt in guidance['text']
            assert hashlib.sha256(excerpt.encode()).hexdigest() == section['selected_sha256']
    assert skills.load(p, 'vision') is None
    assert skills.load(p, 'ui') is None
    interview = skills.load(p, 'interview')
    assert {s['heading'] for s in interview['manifest']['selected'][0]['sections']} == {
        '### Problem Statement', '### Open Questions'}


def test_flow_selection_respects_explicit_non_applicability_and_single_row():
    def row(oid, applicable):
        return dict(id=oid, kind='requirement', selection_status='selected', applies_to='to_be',
                    behavior=dict(flow='已保存流程'), detail_contract=dict(
                        applicability=dict(flow=dict(applicable=applicable)), flow=dict(nodes=[])))
    p = dict(items=[row('R1', False), row('R2', True)])
    assert not skills.flow_applicable(p, 'ingest', dict(requirement_id='R1'), True)
    assert skills.flow_applicable(p, 'ingest', dict(requirement_id='R2'), True)
    first = skills.load(p, 'ingest', dict(requirement_id='R1'), True)
    second = skills.load(p, 'ingest', dict(requirement_id='R2'), True)
    assert 'mermaid-diagrams' not in {s['id'] for s in first['manifest']['selected']}
    assert 'mermaid-diagrams' in {s['id'] for s in second['manifest']['selected']}
    p['items'][1]['selection_status'] = 'deferred'
    assert not skills.flow_applicable(p, 'review')


@pytest.mark.parametrize('fault', ['missing', 'utf8', 'hash', 'oversize', 'path', 'heading'])
def test_broken_selected_resources_fail_diagnostically(resources, fault):
    manifest_path = resources / 'upstream.json'
    manifest = json.loads(manifest_path.read_text('utf-8'))
    row = next(s for s in manifest['skills'] if s['id'] == 'anthropic-write-spec')
    path = resources / row['path']
    if fault == 'missing': path.unlink()
    elif fault == 'utf8': path.write_bytes(b'\xff')
    elif fault == 'hash': path.write_bytes(path.read_bytes() + b'changed')
    elif fault == 'oversize': path.write_bytes(b'x' * (skills.MAX_FILE_BYTES + 1))
    elif fault == 'path': row['selection']['writing']['sections'][0]['path'] = '../outside.md'
    elif fault == 'heading': row['selection']['writing']['sections'][0]['heading'] = '## Wrong heading'
    if fault in ('path', 'heading'):
        manifest_path.write_text(json.dumps(manifest), 'utf-8')
    with pytest.raises(Problem) as error:
        skills.load({}, 'ingest')
    assert error.value.code == 'SKILL_RESOURCE_INVALID'
    assert not error.value.model_repair_allowed


def test_missing_skill_fails_before_provider_and_preserves_project(resources, tmp_path):
    from tests.test_runtime import execute_run
    (resources / 'application-policy.md').unlink()
    run, project, provider = execute_run(tmp_path / 'isolated', [])
    assert run['error'] == 'SKILL_RESOURCE_INVALID' and run['calls'] == 0
    assert not provider.received and not project['items'] and not project['messages']
    from app.local_prd import preview
    store = Store(tmp_path / 'local-preview')
    p = prepared(store)
    before = copy.deepcopy(store.get(p['id']))
    preview(store, p['id'], dict(expected_revision=p['revision']))
    assert store.get(p['id']) == before


def test_nested_flow_reference_integrity_is_checked(resources):
    path = resources / 'vendor/mermaid-diagrams/references/flowcharts.md'
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(Problem, match='hash'):
        skills.load({}, 'ingest', detail_requested=True)


def test_policy_change_changes_call_identity(resources):
    first = skills.load({}, 'ingest')
    path = resources / 'application-policy.md'
    path.write_bytes(path.read_bytes() + '\n追加合成方法约束'.encode('utf-8'))
    second = skills.load({}, 'ingest')
    assert first['manifest']['policy']['sha256'] != second['manifest']['policy']['sha256']
    assert first['manifest']['text_sha256'] != second['manifest']['text_sha256']


def test_repair_uses_frozen_text_and_manifest_after_package_changes(resources):
    guidance = skills.load({}, 'ingest')
    h = dict(stage='ingest', schema={})
    original = skills.apply(h, 'Original schema rules', guidance) + '\n可信任务头：' + dumps(h)
    frozen = skills.freeze(original)
    (resources / 'application-policy.md').unlink()
    messages = [dict(role='system', content='Repair only\n可信任务头：' + dumps(dict(stage='repair', schema={}))),
                dict(role='user', content='Frozen invalid output')]
    result = skills.preserve(messages, frozen)
    assert header(result)['builtin_skills'] == h['builtin_skills']
    assert guidance['text'] in result[0]['content']
    assert result[1] == messages[1] and 'builtin_skills' not in messages[0]['content']
    assert skills.preserve(result, frozen) == result


def test_actual_call_evidence_has_manifest_and_matching_prompt_hash(tmp_path):
    from tests.test_runtime import execute_run, empty_ingest
    run, project, provider = execute_run(tmp_path, [empty_ingest()])
    assert run['status'] == 'succeeded' and run['calls'] == 1
    evidence = json.loads(next((tmp_path / 'evidence/model-calls').glob('*.json')).read_text('utf-8'))
    messages = json.loads(evidence['request_input'])
    manifest = header(messages)['builtin_skills']
    assert manifest == evidence['authorization']['builtin_skills'] == run['authorization']['builtin_skills']
    assert evidence['authorization']['prompt_hash'] == digest(messages[0]['content'].split('可信任务头：', 1)[0])


def test_workflow_repair_does_not_reload_changed_skill_package(resources, tmp_path, monkeypatch):
    from tests.test_runtime import execute_run, empty_ingest, FakeProvider
    original_request = FakeProvider.request

    async def request(self, config, messages, evidence=None):
        result = await original_request(self, config, messages, evidence)
        if len(self.received) == 1:
            (resources / 'application-policy.md').unlink()
        return result

    monkeypatch.setattr(FakeProvider, 'request', request)
    valid = empty_ingest()
    invalid = dict(valid, unexpected_format_field='remove only this field')
    run, _, provider = execute_run(tmp_path / 'repair', [invalid, valid])
    assert run['status'] == 'succeeded' and run['calls'] == 2
    assert skills.freeze(provider.received[0][0]['content']) == skills.freeze(provider.received[1][0]['content'])
    evidence = [json.loads(p.read_text('utf-8')) for p in (tmp_path / 'repair/evidence/model-calls').glob('*.json')]
    assert len(evidence) == 2
    for call in evidence:
        messages = json.loads(call['request_input'])
        assert header(messages)['builtin_skills'] == call['authorization']['builtin_skills']
        assert call['authorization']['prompt_hash'] == digest(messages[0]['content'].split('可信任务头：', 1)[0])
