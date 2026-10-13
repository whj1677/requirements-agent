"""Material export tests only: no database, DSH runtime, keys or model request."""
import copy
import hashlib
import json
import os
from pathlib import Path

os.environ['RA_DATA_DIR'] = str(Path(__file__).resolve().parents[1] / 'evidence/dsh-pilot-20261010/test-runtime')

import pytest
import jsonschema

from scripts.prepare_dsh_requirement_pilot import build_bundle, sha256


def fixture(tmp_path):
    raw = tmp_path / 'frozen-source-pool'; raw.mkdir()
    text = '云平台电价时段维护。\n表格行1：时段开始和结束。\n' + '完整材料末尾。' * 250
    image = b'fixture-image-bytes-unchanged'
    sources = []
    for sid, data, mime, title, content in (
        ('SRC-text', text.encode(), None, '已授权需求.txt', text),
        ('SRC-image', image, 'image/png', '界面.png', '图片像素已提取，尚未视觉分析')):
        (raw / sid).write_bytes(data)
        sources.append(dict(id=sid, sha256=sha256(data), title=title, image_mime=mime,
            parse_status='partial' if mime else 'read', excluded=False,
            excerpts=[dict(id='EX-' + sid, source_id=sid, text=content, locator='页1 / 表格1' if not mime else '内嵌图2')]))
    source_ref = dict(source_id='SRC-text', excerpt_id='EX-SRC-text')
    p = dict(id='P-pilot', name='储能云合成资料', revision=13, sources=sources,
        items=[dict(id='REQ-a', kind='requirement', title='电价时段', statement='维护电价时段。',
            applies_to='to_be', selection_status='candidate', source_refs=[source_ref], related_refs=[])],
        questions=[dict(id='Q-a', question='相接时段是否允许？', why='文档未明确', status='open',
            answer=None, blocking=True, related_refs=['REQ-a'], source_refs=[source_ref],
            answer_review_proposal={'internal_oracle':'PRIVATE-REVIEW-ANSWER'})],
        product_context={'intent':'维护电价', 'scope_ids':['REQ-a'], 'secret':'PRIVATE-PROJECT-SECRET'},
        context_field_states={'intent':{'state':'known', 'reason':'用户原话', 'audit':'PRIVATE-AUDIT'}},
        scope_profile={'kind':'module', 'raw_metadata':'PRIVATE-SCOPE-METADATA'},
        grants={'api-secret':'PRIVATE-KEY'}, model={'api_key':'PRIVATE-KEY'},
        messages=[dict(role='user', text='请按这份文档整理。', response={'internal':'PRIVATE-USER-METADATA'}),
            dict(role='assistant', stage='vision', text='PRIVATE-OLD-MODEL-ANSWER',
                 response={'oracle':'PRIVATE-OLD-MODEL-ANSWER'})],
        oracle={'expected':'PRIVATE-GROUND-TRUTH'})
    snapshot = tmp_path / 'frozen-project.json'; snapshot.write_text(json.dumps(p, ensure_ascii=False), encoding='utf-8')
    (raw / '.env').write_text('PRIVATE-KEY', encoding='utf-8')
    (raw / 'requirements.sqlite3').write_bytes(b'PRIVATE-DATABASE')
    (raw / 'SRC-other-project').write_bytes(b'PRIVATE-OTHER-PROJECT')
    return snapshot, raw, p


def test_bundle_has_full_traceable_text_original_images_and_only_business_projection(tmp_path):
    snapshot, raw, p = fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in raw.iterdir()}
    output = tmp_path / 'pilot' / 'workspace'
    report = build_bundle(snapshot, raw, output, expected_project_id='P-pilot', expected_revision=13)
    assert report['source_count'] == 2 and report['image_count'] == 1 and report['excerpt_count'] == 2
    assert report['model_requests'] == 0
    index = json.loads((output / 'input-index.json').read_text('utf-8'))
    source_map = json.loads((output / 'source-map.json').read_text('utf-8'))
    materials = (output / index['files']['material_text']).read_text('utf-8')
    assert p['sources'][0]['excerpts'][0]['text'] in materials
    assert 'SRC-text' in materials and 'EX-SRC-text' in materials and '页1 / 表格1' in materials
    for source in source_map['sources']:
        original = output / source['original_file']
        assert original.read_bytes() == before[source['source_id']]
        assert sha256(original.read_bytes()) == source['sha256']
    assert {(loc['source'], loc['locator']) for loc in source_map['locators']} == {('S1', 'L1'), ('S2', 'L1')}
    current = json.loads((output / 'current-workspace.json').read_text('utf-8'))
    assert current['user_messages'] == [dict(role='user', text='请按这份文档整理。')]
    assert current['current_rows'][0]['row_key'] == 'R1'
    assert current['current_questions'][0]['text'] == '相接时段是否允许？'
    assert current['current_questions'][0]['saved_answer'] is None
    assert current['context_field_states']['intent'] == {'state':'known', 'reason':'用户原话'}
    assert all(b'PRIVATE-' not in path.read_bytes() for path in output.rglob('*') if path.is_file())
    assert not list(output.rglob('*.sqlite3')) and not list(output.rglob('.env'))
    assert {path.name: path.read_bytes() for path in raw.iterdir()} == before
    assert json.loads(snapshot.read_text('utf-8')) == p
    schema = json.loads((output / 'business-draft.schema.json').read_text('utf-8'))
    jsonschema.Draft202012Validator.check_schema(schema)
    assert set(schema['properties']) == {'summary', 'summary_fields', 'requirements', 'limitations'}
    assert Path(os.environ['RA_DATA_DIR']).is_absolute()
    assert Path(os.environ['RA_DATA_DIR']) != Path(__file__).resolve().parents[1] / 'data'


@pytest.mark.parametrize('problem', ['hash', 'missing', 'traversal', 'duplicate', 'unsupported_image'])
def test_bad_source_preflight_never_creates_partial_workspace(tmp_path, problem):
    snapshot, raw, p = fixture(tmp_path)
    if problem == 'hash': p['sources'][0]['sha256'] = 'wrong'
    elif problem == 'missing': (raw / 'SRC-text').unlink()
    elif problem == 'traversal': p['sources'][0]['id'] = '../outside'
    elif problem == 'duplicate': p['sources'].append(copy.deepcopy(p['sources'][0]))
    else: p['sources'][1]['image_mime'] = 'image/unsupported'
    snapshot.write_text(json.dumps(p), encoding='utf-8')
    output = tmp_path / 'bad-workspace'
    with pytest.raises((ValueError, FileNotFoundError)):
        build_bundle(snapshot, raw, output)
    assert not output.exists()


def test_existing_evidence_is_not_overwritten_and_wrong_project_stops(tmp_path):
    snapshot, raw, _ = fixture(tmp_path)
    output = tmp_path / 'workspace'
    with pytest.raises(ValueError, match='identity'):
        build_bundle(snapshot, raw, output, expected_project_id='P-other')
    assert not output.exists()
    with pytest.raises(ValueError, match='revision'):
        build_bundle(snapshot, raw, output, expected_revision=99)
    assert not output.exists()
    build_bundle(snapshot, raw, output)
    hashes = {p.relative_to(output).as_posix(): sha256(p.read_bytes()) for p in output.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='already exists'):
        build_bundle(snapshot, raw, output)
    assert hashes == {p.relative_to(output).as_posix(): sha256(p.read_bytes()) for p in output.rglob('*') if p.is_file()}


def test_unselected_source_and_its_original_are_not_sent(tmp_path):
    snapshot, raw, p = fixture(tmp_path)
    p['sources'][1]['excluded'] = True
    snapshot.write_text(json.dumps(p), encoding='utf-8')
    output = tmp_path / 'workspace'
    report = build_bundle(snapshot, raw, output)
    assert report['source_count'] == 1 and report['image_count'] == 0
    assert list((output / 'images').iterdir()) == []
    assert 'SRC-image' not in (output / 'source-map.json').read_text('utf-8')


def test_opaque_originals_keep_upload_bytes_and_include_exact_reference_text(tmp_path):
    snapshot, raw, project = fixture(tmp_path)
    project['sources'][0]['title'] = '受保护资料.docx'
    project['sources'][0]['reading'] = {'method': 'local-office'}
    snapshot.write_text(json.dumps(project), encoding='utf-8')
    output = tmp_path / 'opaque-workspace'
    build_bundle(snapshot, raw, output, opaque_originals=True)
    source_map = json.loads((output / 'source-map.json').read_text('utf-8'))
    original = source_map['sources'][0]
    assert original['original_file'] == 'originals/SRC-text'
    assert original['reading']['method'] == 'local-office'
    assert (output / original['original_file']).read_bytes() == (raw / 'SRC-text').read_bytes()
    assert source_map['locators'][0]['text'] == project['sources'][0]['excerpts'][0]['text']
    assert not (output / original['original_file']).suffix


def test_written_hash_mismatch_is_not_reported_as_prepared(tmp_path, monkeypatch):
    snapshot, raw, _ = fixture(tmp_path)
    write_bytes = Path.write_bytes
    def changed_bytes(path, data):
        return write_bytes(path, data + b'changed' if path.parent.name == 'originals' else data)
    monkeypatch.setattr(Path, 'write_bytes', changed_bytes)
    output = tmp_path / 'changed-workspace'
    with pytest.raises(ValueError, match='Written source hash'):
        build_bundle(snapshot, raw, output, opaque_originals=True)
    assert not (output / 'input-index.json').exists()
