"""Read an allowlisted frozen Word original with the existing local Office path.

No application database, network, model call or source write is performed. The
trusted host supplies a separate temporary root; the model supplies only a path.
"""
import hashlib
import json
from pathlib import Path

from .core import Problem, require
from .office import extract_office
from .sources import MAX_BYTES, validate_extraction

MAX_OUTPUT_CHARS = 2_000_000


def _inside(workspace, value):
    require(isinstance(value, str) and value.strip(), 'DOCUMENT_PATH_INVALID', '缺少资料路径')
    path = Path(value)
    path = (path if path.is_absolute() else workspace / path).resolve()
    require(path.is_relative_to(workspace) and path != workspace,
            'DOCUMENT_PATH_DENIED', '只能读取当前资料工作区内的白名单文件')
    return path


def _read_checked(workspace, source):
    path = _inside(workspace, source['original_file'])
    require(path.is_file(), 'DOCUMENT_MISSING', '白名单原件不存在')
    require(0 < path.stat().st_size <= MAX_BYTES, 'DOCUMENT_LIMIT', '原件为空或超过读取上限')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == source.get('sha256'),
            'DOCUMENT_HASH_MISMATCH', '原件哈希与冻结来源不一致；不读取或重新绑定')
    return path, raw


def _verified_locations(workspace, source_map, source):
    """Use explicit frozen text, never interpret matching ordinal numbers as proof."""
    locations = []
    for location in source_map.get('locators', []):
        if location.get('source_id') != source['source_id']:
            continue
        require(location.get('source') == source['source'] and
                location.get('source_sha256') == source['sha256'] and
                location.get('original_file') == source['original_file'],
                'DOCUMENT_MAP_INVALID', '片段身份与冻结来源不一致')
        text = location.get('text')
        require(isinstance(text, str) and isinstance(location.get('original_locator'), str),
                'DOCUMENT_MAP_INVALID', '来源映射缺少精确片段正文或位置；请重新准备资料包')
        material = _inside(workspace, location['material_file'])
        require(material.is_file() and material.stat().st_size <= 12 * 1024 * 1024,
                'DOCUMENT_MAP_INVALID', '冻结提取记录不存在或超过上限')
        with material.open(encoding='utf-8', newline='') as handle:
            content = handle.read()
        record = ('## ' + location['source'] + '/' + location['locator'] + '\n'
                  'excerpt_id: ' + location['excerpt_id'] + '\n'
                  '原始位置: ' + location['original_locator'] + '\n'
                  '提取方式: ' + str(location.get('method', '未记录')) + '\n\n' + text + '\n')
        require(record in content, 'DOCUMENT_MAP_INVALID', '片段正文与冻结提取记录不一致')
        locations.append(location)
    return locations


def _ref(location):
    return {key: location[key] for key in ('source', 'locator', 'source_id', 'excerpt_id')}


def _frozen_images(workspace, source_map, parent):
    pictures = []
    for source in source_map['sources']:
        if not source.get('image') or source.get('container_source_id') != parent['source_id']:
            continue
        path, _ = _read_checked(workspace, source)
        locations = _verified_locations(workspace, source_map, source)
        pictures.append(dict(path=str(path), workspace_path=source['original_file'],
            source=source['source'], source_id=source['source_id'], sha256=source['sha256'],
            source_refs=[_ref(location) for location in locations],
            container_locator=source.get('container_locator'),
            provenance='prior_local_office_export',
            notice='冻结资料中先前 Office 导出的图片；不是本次重新提取图的身份或内容核验结果。'))
    return pictures


def read_document(workspace, file_path, *, temp_root):
    """Return full current extraction; temp_root is a trusted host argument only."""
    workspace = Path(workspace).resolve()
    require(workspace.is_dir(), 'DOCUMENT_PATH_INVALID', '资料工作区不存在')
    requested = _inside(workspace, str(file_path))
    map_path = _inside(workspace, 'source-map.json')
    require(map_path.is_file() and map_path.stat().st_size <= 12 * 1024 * 1024,
            'DOCUMENT_MAP_INVALID', '资料工作区缺少有效来源白名单')
    source_map = json.loads(map_path.read_text(encoding='utf-8'))
    require(isinstance(source_map.get('sources'), list), 'DOCUMENT_MAP_INVALID', '来源白名单格式无效')
    matches = [source for source in source_map['sources']
               if _inside(workspace, source['original_file']) == requested]
    require(len(matches) == 1, 'DOCUMENT_PATH_DENIED', '此路径不是唯一白名单原件')
    source = matches[0]
    require(not source.get('image') and Path(source.get('title', '')).suffix.lower() == '.docx',
            'DOCUMENT_FORMAT_UNSUPPORTED', '本工具仅支持白名单 Word docx 资料')
    require(source.get('reading', {}).get('method') == 'local-office',
            'DOCUMENT_READER_UNVERIFIED', '本工具仅重用此资料已验证的本机 Office 读取路径')
    original_path, raw = _read_checked(workspace, source)
    locations = _verified_locations(workspace, source_map, source)
    pictures = _frozen_images(workspace, source_map, source)
    temporary = Path(temp_root).resolve()
    require(temporary != workspace and not temporary.is_relative_to(workspace) and
            not workspace.is_relative_to(temporary), 'DOCUMENT_TEMP_INVALID',
            'Office 临时目录必须与只读资料工作区分离')
    temporary.mkdir(parents=True, exist_ok=True)
    # extract_office owns only its new Office instance and its working copy.
    # It disables macros/link updates, opens read-only and never saves originals.
    result = extract_office(source['title'], raw, temporary)
    require(original_path.read_bytes() == raw, 'DOCUMENT_SOURCE_CHANGED', '读取期间原件变化；结果不绑定')
    require(isinstance(result, dict), 'DOCUMENT_READ_FAILED', 'Office 未返回有效读取结果')
    require(result.get('status') in ('read', 'partial'),
            result.get('code') or 'DOCUMENT_READ_FAILED',
            result.get('reason') or 'Office 未取得可用正文或表格')
    rows = result.get('rows', [])
    validate_extraction(rows, result['status'])
    by_location = {}
    for location in locations:
        by_location.setdefault((location['original_locator'], location['text']), []).append(location)
    segments = []
    for original_locator, text in rows:
        for offset in range(0, len(text), 5000):
            piece = text[offset:offset + 5000]
            locator = original_locator + ' / 字符 ' + str(offset)
            found = by_location.get((locator, piece), [])
            entry = dict(original_locator=locator, text=piece,
                         kind='table' if original_locator.startswith('表格 ') else 'text',
                         binding='locator_and_exact_text' if len(found) == 1 else 'unbound',
                         source_ref=_ref(found[0]) if len(found) == 1 else None)
            if len(found) != 1:
                entry['reason'] = '同一位置和正文存在多个引用' if found else '本次位置或正文未与冻结片段精确匹配'
            segments.append(entry)
    unbound = sum(entry['binding'] == 'unbound' for entry in segments)
    limitations = [result.get('reason', ''),
        '只报告本次 Office 返回的正文与表格；页面渲染、浮动对象、页眉页脚、批注和修订不据此视为已核验。',
        '图像列表沿用本冻结 Word 的既有 Office 派生图；本次新提取图片不输出、不按编号映射到旧图。']
    if source.get('reading_limit'):
        limitations.append('冻结提取时的限制（历史记录）：' + source['reading_limit'])
    if unbound:
        limitations.append(f'{unbound} 个本次片段未绑定旧引用；不得猜别名或凭相近文字引用。')
    report = dict(status='partial', method='local-office-readonly',
        source=dict(source=source['source'], source_id=source['source_id'], title=source['title'],
                    raw_sha256=source['sha256'], file_path=str(original_path)),
        text=dict(status=result['status'], complete_returned_rows=True, truncated=False,
                  row_count=len(rows), segment_count=len(segments),
                  char_count=sum(len(text) for _, text in rows),
                  table_row_count=sum(locator.startswith('表格 ') for locator, _ in rows),
                  bound_count=len(segments)-unbound, unbound_count=unbound, rows=segments),
        images=dict(status='existing_frozen_derivatives', items=pictures,
                    new_extraction_count=len(result.get('images', [])),
                    new_images_matched_to_existing=False, new_images_returned=False),
        limitations=[text for text in limitations if text])
    require(len(json.dumps(report, ensure_ascii=False)) <= MAX_OUTPUT_CHARS,
            'DOCUMENT_PAGINATION_REQUIRED', '完整读取结果超出单次工具上限，需要分页工具；本次未截断输出')
    return report
