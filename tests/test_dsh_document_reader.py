"""Isolated Word-reader boundaries; Office is stubbed in these offline tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket

os.environ['RA_DATA_DIR'] = str(Path(__file__).resolve().parents[1] / 'evidence/dsh-pilot-20261010/reader-tests-runtime')

import pytest

from app.core import Problem
from app import dsh_document_reader as reader
from scripts.prepare_dsh_requirement_pilot import build_bundle


def bundle(tmp_path):
    source_dir = tmp_path / 'pool'; source_dir.mkdir()
    data = b'opaque-protected-word-original'
    picture = b'existing-frozen-office-image'
    text = '维护云平台时段。'
    table = '时段开始\r时段结束'
    digest = lambda value: hashlib.sha256(value).hexdigest()
    sources = [dict(id='SRC-word', title='需求.docx', sha256=digest(data), excluded=False,
        parse_status='partial', image_mime=None, reading={'method':'local-office'},
        failure_reason='1 个对象导出失败；页眉、页脚未提取',
        excerpts=[dict(id='EX-para', source_id='SRC-word', source_hash=digest(data),
            locator='正文段落 1 / 字符 0', method='local-office', text=text),
            dict(id='EX-table', source_id='SRC-word', source_hash=digest(data),
            locator='表格 1 / 单元格 1 / 行 1 列 1 / 字符 0', method='local-office', text=table)]),
        dict(id='SRC-image', title='需求.docx · 内嵌图.png', sha256=digest(picture), excluded=False,
            parse_status='partial', image_mime='image/png', container_source_id='SRC-word',
            container_locator='内嵌对象 2 / 页 3 / 字符 70',
            excerpts=[dict(id='EX-image', source_id='SRC-image', locator='整图', method='image',
                           text='图片原始像素；尚未视觉分析')])]
    (source_dir/'SRC-word').write_bytes(data); (source_dir/'SRC-image').write_bytes(picture)
    project = dict(id='P-pilot', name='储能云', revision=1, sources=sources, items=[], questions=[], messages=[])
    snapshot = tmp_path/'snapshot.json'; snapshot.write_text(json.dumps(project), encoding='utf-8')
    workspace = tmp_path/'workspace'
    build_bundle(snapshot, source_dir, workspace, opaque_originals=True)
    result = dict(status='partial', code='OFFICE_EXTRACTED', rows=[
        ['正文段落 1', text], ['表格 1 / 单元格 1 / 行 1 列 1', table]],
        images=[dict(locator='different-object', data_base64='DO-NOT-RETURN-IMAGE-DATA')],
        reason='正文表格已提取，页面未核验')
    return workspace, result


def read(workspace):
    return reader.read_document(workspace, 'originals/SRC-word', temp_root=workspace.parent/'office-temp')


def no_office(*args):
    pytest.fail('invalid source must be refused before Office starts')


def test_reads_original_bytes_binds_exact_locations_and_reports_prior_images(tmp_path, monkeypatch):
    workspace, extracted = bundle(tmp_path)
    before = {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    calls = []
    def office(title, data, temporary):
        calls.append((title, data, temporary))
        return extracted
    monkeypatch.setattr(reader, 'extract_office', office)
    result = read(workspace)
    assert calls == [('需求.docx', b'opaque-protected-word-original', workspace.parent/'office-temp')]
    assert result['text']['bound_count'] == 2 and result['text']['unbound_count'] == 0
    assert [r['text'] for r in result['text']['rows']] == [r[1] for r in extracted['rows']]
    assert result['text']['rows'][1]['source_ref']['excerpt_id'] == 'EX-table'
    assert result['text']['complete_returned_rows'] and not result['text']['truncated']
    assert result['images']['new_extraction_count'] == 1
    assert not result['images']['new_images_matched_to_existing']
    assert result['images']['items'][0]['provenance'] == 'prior_local_office_export'
    assert Path(result['images']['items'][0]['path']).read_bytes() == b'existing-frozen-office-image'
    assert 'DO-NOT-RETURN' not in json.dumps(result)
    assert any('导出失败' in reason for reason in result['limitations'])
    assert before == {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}


@pytest.mark.parametrize('path', ['../secret.docx', 'https://example.com/source.docx', 'materials/S1.md', 'not-listed.docx'])
def test_non_allowlisted_paths_stop_before_reading_or_office(tmp_path, monkeypatch, path):
    workspace, _ = bundle(tmp_path)
    monkeypatch.setattr(reader, 'extract_office', no_office)
    with pytest.raises(Problem, match='白名单|工作区'):
        reader.read_document(workspace, path, temp_root=workspace.parent/'office-temp')


@pytest.mark.parametrize('target', ['originals/SRC-word', 'images/S2-SRC-image.png'])
def test_changed_original_or_frozen_image_hash_stops_before_office(tmp_path, monkeypatch, target):
    workspace, _ = bundle(tmp_path)
    (workspace/target).write_bytes(b'changed')
    monkeypatch.setattr(reader, 'extract_office', no_office)
    with pytest.raises(Problem) as raised:
        read(workspace)
    assert raised.value.code == 'DOCUMENT_HASH_MISMATCH'


def test_unmatched_or_ambiguous_text_is_returned_unbound_not_guessed(tmp_path, monkeypatch):
    workspace, extracted = bundle(tmp_path)
    extracted['rows'][0][1] += '内容已变化'
    extracted['rows'].append(['正文段落 99', '维护云平台时段。'])
    monkeypatch.setattr(reader, 'extract_office', lambda *args: extracted)
    result = read(workspace)
    assert result['text']['unbound_count'] == 2
    assert result['text']['rows'][0]['source_ref'] is None
    assert result['text']['rows'][-1]['source_ref'] is None
    assert result['text']['rows'][1]['source_ref']['excerpt_id'] == 'EX-table'
    source_map_path = workspace/'source-map.json'
    source_map = json.loads(source_map_path.read_text('utf-8'))
    source_map['locators'].append(copy.deepcopy(source_map['locators'][1]))
    source_map_path.write_text(json.dumps(source_map), encoding='utf-8')
    result = read(workspace)
    assert result['text']['unbound_count'] == 3


def test_map_and_frozen_material_mismatch_is_not_bound(tmp_path, monkeypatch):
    workspace, _ = bundle(tmp_path)
    material=workspace/'materials/S1.md'
    material.write_text(material.read_text('utf-8').replace('维护云平台时段。', '另一个场景。'), encoding='utf-8')
    monkeypatch.setattr(reader, 'extract_office', no_office)
    with pytest.raises(Problem) as raised:
        read(workspace)
    assert raised.value.code == 'DOCUMENT_MAP_INVALID'


def test_external_link_is_not_fetched_and_office_failure_is_not_cached_success(tmp_path, monkeypatch):
    workspace, extracted = bundle(tmp_path)
    extracted['rows'].append(['正文段落 3', '参考链接：https://external.invalid/private'])
    def no_network(*args, **kwargs):
        pytest.fail('document reader must not fetch external links')
    monkeypatch.setattr(socket, 'create_connection', no_network)
    monkeypatch.setattr(reader, 'extract_office', lambda *args: extracted)
    result = read(workspace)
    assert result['text']['rows'][-1]['text'].endswith('https://external.invalid/private')
    assert result['text']['rows'][-1]['binding'] == 'unbound'
    monkeypatch.setattr(reader, 'extract_office', lambda *args: dict(
        status='permission_denied', code='OFFICE_PERMISSION_DENIED', reason='企业策略拒绝本次读取', rows=[]))
    with pytest.raises(Problem) as raised:
        read(workspace)
    assert raised.value.code == 'OFFICE_PERMISSION_DENIED'


def test_garbled_output_and_overlarge_result_never_masquerade_as_complete(tmp_path, monkeypatch):
    workspace, extracted = bundle(tmp_path)
    monkeypatch.setattr(reader, 'extract_office', lambda *args: extracted)
    extracted['rows'][0][1] += '\ufffd'
    with pytest.raises(Problem) as raised:
        read(workspace)
    assert raised.value.code == 'SOURCE_FAILED'
    extracted['rows'][0][1] = '维护云平台时段。'
    monkeypatch.setattr(reader, 'MAX_OUTPUT_CHARS', 10)
    with pytest.raises(Problem) as raised:
        read(workspace)
    assert raised.value.code == 'DOCUMENT_PAGINATION_REQUIRED'


def test_temp_path_inside_workspace_is_refused(tmp_path, monkeypatch):
    workspace, _ = bundle(tmp_path)
    monkeypatch.setattr(reader, 'extract_office', no_office)
    with pytest.raises(Problem) as raised:
        reader.read_document(workspace, 'originals/SRC-word', temp_root=workspace/'office-temp')
    assert raised.value.code == 'DOCUMENT_TEMP_INVALID'
