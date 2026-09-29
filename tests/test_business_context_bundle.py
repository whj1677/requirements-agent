import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.business_context import (build_source, model_context, parse_bundle,
                                  selected_excerpts, selection_identity, selection_update)
from app.core import Problem
from app.provider import DEFAULT, assemble
from app.store import Store


def bundle():
    sha = hashlib.sha256(b'one\ntwo\n').hexdigest()
    return {
        'schema_version': '1.0', 'bundle_id': 'sample-1', 'generated_at': '2026-09-29T00:00:00Z',
        'project': {'id': 'sample', 'name': '样本'},
        'source_snapshot': {'repositories': [{'id': 'repo', 'revision': 'unknown', 'dirty': None}],
                            'deployment': 'unknown'},
        'overview': '示例产品概览',
        'modules': [
            {'id': 'ui', 'name': '页面', 'summary': '操作入口', 'claim_ids': ['c-ui'], 'depends_on': ['service']},
            {'id': 'service', 'name': '服务', 'summary': '业务处理', 'claim_ids': ['c-service'], 'depends_on': ['data']},
            {'id': 'data', 'name': '数据', 'summary': '状态保存', 'claim_ids': ['c-data'], 'depends_on': []},
            {'id': 'other', 'name': '其它', 'summary': '未选', 'claim_ids': ['c-other'], 'depends_on': []},
        ],
        'claims': [
            {'id': f'c-{mid}', 'module_ids': [mid], 'dimension': '现状', 'text': f'{mid} 原文',
             'origin': 'code_observation', 'evidence_ids': ['e1']}
            for mid in ('ui', 'service', 'data', 'other')],
        'evidence': [{'id': 'e1', 'repository_id': 'repo', 'path': 'sample.txt',
                      'symbol': 'sample', 'line_start': 1, 'line_end': 2, 'sha256': sha,
                      'kind': 'implementation'}],
        'unknowns': [{'id': 'u1', 'question': '需确认哪些角色可操作？', 'module_ids': ['ui']},
                     {'id': 'u2', 'question': '其它模块待确认', 'module_ids': ['other']}],
        'conflicts': [{'id': 'x1', 'description': '两个观察冲突', 'claim_ids': ['c-ui', 'c-service']}],
        'coverage': {'inspected_modules': ['ui', 'service'], 'indexed_modules': ['data', 'other'],
                     'excluded': ['build'], 'limitations': ['仅静态核对']},
        'technical_summary': '接口与业务处理关联。',
    }


def raw(value):
    return json.dumps(value, ensure_ascii=False).encode()


def test_parse_rejects_extra_approval_duplicate_and_unsafe_paths():
    valid = bundle()
    assert parse_bundle(raw(valid)) == valid
    for mutation in (
        lambda b: b.update(approved=True),
        lambda b: b['evidence'][0].update(path='../secret'),
        lambda b: b['evidence'][0].update(path='C:/secret'),
        lambda b: b['evidence'][0].update(path='folder\\secret'),
        lambda b: b['claims'][0].update(evidence_ids=['missing']),
        lambda b: b['modules'][0].update(claim_ids=['c-service']),
    ):
        bad = copy.deepcopy(valid)
        mutation(bad)
        with pytest.raises(Problem):
            parse_bundle(raw(bad))
    with pytest.raises(Problem):
        parse_bundle(b'{"bundle_id":"a","bundle_id":"b"}')
    with pytest.raises(Problem):
        parse_bundle(b' ' * (2 * 1024 * 1024 + 1))


def test_stable_source_selection_and_project_identity(tmp_path):
    class Store:
        folder = tmp_path

    original = raw(bundle())
    source = build_source(Store(), 'business-context.json', original)
    assert (tmp_path / 'sources' / source['id']).read_bytes() == original
    assert source['parse_status'] == 'read'
    assert source['image_mime'] is None
    assert source['business_active'] is False
    assert source['purpose'] == 'business'
    assert len(source['excerpts']) == 4
    assert selected_excerpts(source) == []
    project = {'sources': [source]}
    assert model_context(project) is None
    assert selection_identity(project) is None

    selection = selection_update(source, ['ui'], ['c-data'])
    assert source['business_selection']['module_ids'] == []
    source['business_selection'] = selection
    source['business_active'] = True
    excerpts = selected_excerpts(source)
    assert {e['business_claim_id'] for e in excerpts} == {'c-ui', 'c-service', 'c-data'}
    assert all(e['text'] == next(s['text'] for s in source['excerpts'] if s['id'] == e['id']) for e in excerpts)
    assert next(e for e in excerpts if e['business_claim_id'] == 'c-data')['business_confirmed'] is True
    assert all(e['source_hash'] == source['sha256'] for e in excerpts)
    context = model_context(project)['packages'][0]
    assert {m['id'] for m in context['modules']} == {'ui', 'service', 'data'}
    assert {u['id'] for u in context['unknowns']} == {'u1'}
    assert context['coverage']['limitations'] == ['仅静态核对']
    assert all('text' not in claim and 'locator' not in claim for claim in context['claims'])
    assert 'c-other' not in json.dumps(context)
    assert selection_identity(project)[0]['module_ids'] == ['ui']
    with pytest.raises(Problem):
        selection_update(source, ['ui'], ['c-other'])
    source['business_active'] = False
    assert selected_excerpts(source) == []
    assert model_context(project) is None
    assert selection_identity(project) is None


def test_provider_assemble_receives_selected_excerpts_once(tmp_path):
    store = Store(tmp_path)
    project = store.create('样本')
    source = build_source(store, 'business-context.json', raw(bundle()))
    source['business_selection'] = selection_update(source, ['ui'], [])
    source['business_active'] = True
    project['sources'].append(source)
    messages, selected, omitted = assemble(project, 'ingest', '分析业务场景', DEFAULT, tmp_path)
    assert omitted == []
    assert {entry['business_claim_id'] for entry in selected} == {'c-ui', 'c-service', 'c-data'}
    context = json.loads(messages[1]['content'][0]['text'])
    assert {entry['business_claim_id'] for entry in context['excerpts']} == {'c-ui', 'c-service', 'c-data'}
    assert all('text' not in claim for claim in context['business_context']['packages'][0]['claims'])
    assert 'other 原文' not in messages[1]['content'][0]['text']


def test_partial_conflict_marks_missing_claim_without_sending_unselected_text():
    example = bundle()
    example['conflicts'][0]['claim_ids'] = ['c-ui', 'c-other']
    example['conflicts'][0]['description'] = 'other 原文与 ui 原文冲突'
    source = {'id': 'SRC-1', 'purpose': 'business', 'excluded': False,
              'sha256': 'a' * 64, 'business_context': example,
              'business_selection': {'module_ids': ['ui'], 'confirmed_claim_ids': []},
              'excerpts': [{'id': 'SRC-1:' + c['id'], 'source_id': 'SRC-1',
                            'source_hash': 'a' * 64, 'text': c['text'], 'locator': 'repo:sample.txt:1-2',
                            'business_claim_id': c['id'], 'evidence_ids': c['evidence_ids']}
                           for c in example['claims']]}
    context = model_context({'sources': [source]})['packages'][0]
    assert context['conflicts'][0]['missing_claim_ids'] == ['c-other']
    assert 'description' not in context['conflicts'][0]
    assert 'other 原文' not in json.dumps(context, ensure_ascii=False)


def test_skill_validator_same_contract_and_optional_hash_check(tmp_path):
    product_schema = Path('requirements-agent-codex-kit-v1.1/schemas/business-context.schema.json')
    skill_schema = Path('skills/export-business-context/references/business-context.schema.json')
    assert product_schema.read_bytes() == skill_schema.read_bytes()
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'sample.txt').write_bytes(b'one\ntwo\n')
    path = tmp_path / 'business-context.json'
    path.write_bytes(raw(bundle()))
    script = Path('skills/export-business-context/scripts/validate_bundle.py')
    command = [sys.executable, str(script), str(path), '--repository', f'repo={repo}']
    checked = subprocess.run(command, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    (repo / 'sample.txt').write_bytes(b'changed\n')
    checked = subprocess.run(command, capture_output=True, text=True)
    assert checked.returncode == 1
    assert 'SHA-256 differs' in checked.stderr
