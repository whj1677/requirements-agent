"""Untrusted, versioned business context bundles and explicit project selection."""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath

import jsonschema

from .core import KIT, Problem, ident, now

SCHEMA_PATH = KIT / 'schemas' / 'business-context.schema.json'
MAX_BYTES = 2 * 1024 * 1024
_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
_VALIDATOR = jsonschema.Draft202012Validator(_SCHEMA, format_checker=jsonschema.FormatChecker())


def _fail(message):
    raise Problem('BUSINESS_CONTEXT_INVALID', message)


def _unique(value):
    result = {}
    for key, item in value:
        if key in result:
            _fail(f'JSON 字段重复：{key}')
        result[key] = item
    return result


def _ids(rows, label):
    result = [row['id'] for row in rows]
    if len(result) != len(set(result)):
        _fail(f'{label} ID 重复')
    return set(result)


def _refs(values, allowed, label):
    if not set(values) <= allowed:
        _fail(f'{label} 引用不存在')


def _safe_path(value):
    # Paths in the portable bundle always use forward slashes.
    if ('\\' in value or ':' in value or '\x00' in value or
            value.startswith('/') or any(part in ('', '.', '..') for part in value.split('/'))):
        _fail('证据路径必须是安全的仓库相对路径')
    if PurePosixPath(value).is_absolute():
        _fail('证据路径不能是绝对路径')


def parse_bundle(raw: bytes) -> dict:
    """Validate JSON structure, identity links and path safety; never execute content."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        _fail('业务背景包必须是非空且不超过 2 MiB 的 JSON')
    try:
        bundle = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique,
                            parse_constant=lambda value: _fail('JSON 不允许非有限数字'))
    except (UnicodeError, json.JSONDecodeError) as error:
        _fail('业务背景包不是完整的 UTF-8 JSON 文件，请重新运行导出 Skill 后导入')
    if isinstance(bundle,dict) and bundle.get('schema_version')!='1.0':
        _fail('不支持此业务背景包版本，请使用当前导出 Skill 重新生成')
    error = next(_VALIDATOR.iter_errors(bundle), None)
    if error:
        explanation={'required':'缺少必要信息','additionalProperties':'包含当前版本不支持的字段',
                     'maxItems':'内容条目超出本次格式上限','maxLength':'字段内容过长'}.get(error.validator,'字段格式不正确')
        _fail(f'业务背景包{explanation}（位置 {error.json_path[:160]}），请使用导出 Skill 重新生成')
    repositories = _ids(bundle['source_snapshot']['repositories'], 'repository')
    modules = _ids(bundle['modules'], 'module')
    claims = _ids(bundle['claims'], 'claim')
    evidence = _ids(bundle['evidence'], 'evidence')
    _ids(bundle['unknowns'], 'unknown')
    _ids(bundle['conflicts'], 'conflict')
    claim_by_id = {row['id']: row for row in bundle['claims']}
    for row in bundle['modules']:
        _refs(row['claim_ids'], claims, 'module.claim_ids')
        _refs(row['depends_on'], modules - {row['id']}, 'module.depends_on')
        for claim_id in row['claim_ids']:
            if row['id'] not in claim_by_id[claim_id]['module_ids']:
                _fail('module.claim_ids 与 claim.module_ids 不一致')
    for row in bundle['claims']:
        if not row['module_ids'] or not row['evidence_ids']:
            _fail('每条 claim 必须有模块和证据引用')
        _refs(row['module_ids'], modules, 'claim.module_ids')
        _refs(row['evidence_ids'], evidence, 'claim.evidence_ids')
        for module_id in row['module_ids']:
            module = next(m for m in bundle['modules'] if m['id'] == module_id)
            if row['id'] not in module['claim_ids']:
                _fail('claim.module_ids 与 module.claim_ids 不一致')
    for row in bundle['evidence']:
        _refs([row['repository_id']], repositories, 'evidence.repository_id')
        _safe_path(row['path'])
        if row['line_end'] < row['line_start']:
            _fail('证据结束行不能早于开始行')
    for row in bundle['unknowns']:
        _refs(row['module_ids'], modules, 'unknown.module_ids')
    for row in bundle['conflicts']:
        if len(row['claim_ids']) < 2:
            _fail('冲突须引用至少两条 claim')
        _refs(row['claim_ids'], claims, 'conflict.claim_ids')
    for key in ('inspected_modules', 'indexed_modules'):
        _refs(bundle['coverage'][key], modules, f'coverage.{key}')
    return bundle


def build_source(store, filename: str, raw: bytes) -> dict:
    """Save exact original bytes and return source metadata; caller persists project."""
    bundle = parse_bundle(raw)
    if not isinstance(filename, str) or not filename or len(filename) > 255 or Path(filename).name != filename or '\\' in filename:
        _fail('文件名必须是不含路径的名称')
    sid = ident('SRC')
    sha = hashlib.sha256(raw).hexdigest()
    rawdir = store.folder / 'sources'
    rawdir.mkdir(parents=True, exist_ok=True)
    path = rawdir / sid
    with path.open('xb') as output:
        output.write(raw)
    evidence = {e['id']: e for e in bundle['evidence']}
    excerpts = []
    for claim in bundle['claims']:
        locations = [evidence[eid] for eid in claim['evidence_ids']]
        locator = '；'.join(f"{e['repository_id']}:{e['path']}:{e['line_start']}-{e['line_end']}@{e['sha256']}" for e in locations)
        excerpts.append(dict(id=f'{sid}:{claim["id"]}', source_id=sid, source_hash=sha,
                             locator=locator, text=claim['text'], method='business-context',
                             business_claim_id=claim['id'], evidence_ids=claim['evidence_ids']))
    return dict(id=sid, title=filename, purpose='business', uri=None, sha256=sha,
                created=now(), version=1, excluded=False, parse_status='read',
                failure_reason='', image_mime=None, excerpts=excerpts, business_context=bundle,
                business_selection={'module_ids': [], 'confirmed_claim_ids': []},
                business_active=False)


def selection_update(source: dict, module_ids: list, confirmed_claim_ids: list) -> dict:
    """Return a validated selection without mutating source or adopting a rule."""
    bundle = source.get('business_context')
    if not bundle:
        _fail('资料不是业务背景包')
    available = {m['id'] for m in bundle['modules']}
    if (not isinstance(module_ids, list) or not isinstance(confirmed_claim_ids, list) or
            any(not isinstance(x, str) for x in module_ids + confirmed_claim_ids) or
            len(module_ids) != len(set(module_ids)) or
            len(confirmed_claim_ids) != len(set(confirmed_claim_ids))):
        _fail('选择列表须为不重复的 ID')
    _refs(module_ids, available, 'module_ids')
    selected = _related(bundle, module_ids)
    eligible = {c['id'] for c in bundle['claims'] if selected.intersection(c['module_ids'])}
    _refs(confirmed_claim_ids, eligible, 'confirmed_claim_ids')
    return {'module_ids': module_ids[:], 'confirmed_claim_ids': confirmed_claim_ids[:]}


def _active_sources(project):
    return [source for source in project.get('sources', [])
            if source.get('purpose') == 'business' and not source.get('excluded')
            and source.get('business_context')
            and source.get('business_active', True)
            and (source.get('business_selection') or {}).get('module_ids')]


def _related(bundle, module_ids):
    by_id = {m['id']: m for m in bundle['modules']}
    related = set()
    pending = list(module_ids)
    while pending:
        current = pending.pop()
        if current in related:
            continue
        related.add(current)
        pending.extend(by_id[current]['depends_on'])
    return related


def selected_excerpts(source: dict) -> list[dict]:
    """Stable claim excerpts for selected modules and their direct dependencies."""
    bundle = source.get('business_context')
    if not bundle or source.get('excluded') or not source.get('business_active', True):
        return []
    selection = source.get('business_selection') or {'module_ids': [], 'confirmed_claim_ids': []}
    selection_update(source, selection['module_ids'], selection['confirmed_claim_ids'])
    if not selection['module_ids']:
        return []
    related = _related(bundle, selection['module_ids'])
    confirmed = set(selection['confirmed_claim_ids'])
    claim_by_id = {c['id']: c for c in bundle['claims']}
    excerpts = []
    for stored in source.get('excerpts', []):
        claim = claim_by_id.get(stored.get('business_claim_id'))
        if not claim or not related.intersection(claim['module_ids']):
            continue
        excerpt = copy.deepcopy(stored)
        excerpt['business_origin'] = claim['origin']
        excerpt['business_confirmed'] = claim['id'] in confirmed
        excerpt['business_status'] = ('用户已核对现状；不等于本期规范采纳' if claim['id'] in confirmed
                                      else '未经用户确认的现状线索')
        excerpts.append(excerpt)
    return excerpts


def model_context(project: dict) -> dict | None:
    """Compact context for the current enabled bundle and relevant modules only."""
    sources = _active_sources(project)
    if not sources:
        return None
    packages = []
    for source in sources:
        bundle = source['business_context']
        selection = source['business_selection']
        selection_update(source, selection['module_ids'], selection['confirmed_claim_ids'])
        related = _related(bundle, selection['module_ids'])
        excerpts = selected_excerpts(source)
        selected_claims = {e['business_claim_id'] for e in excerpts}
        claims = [{'id': e['business_claim_id'], 'excerpt_id': e['id'],
                   'source_id': source['id'], 'origin': e['business_origin'],
                   'confirmed': e['business_confirmed'], 'status': e['business_status'],
                   'evidence_ids': e['evidence_ids']} for e in excerpts]
        conflicts = []
        for conflict in bundle['conflicts']:
            present = [cid for cid in conflict['claim_ids'] if cid in selected_claims]
            if not present:
                continue
            missing = [cid for cid in conflict['claim_ids'] if cid not in selected_claims]
            row = {'id': conflict['id'], 'claim_ids': present, 'missing_claim_ids': missing}
            if missing:
                row['notice'] = '冲突涉及未选模块陈述；未发送其正文，需核对后再扩大选择'
            else:
                row['description'] = conflict['description']
            conflicts.append(row)
        packages.append({'source_id': source['id'], 'source_sha256': source['sha256'],
            'bundle_id': bundle['bundle_id'], 'generated_at': bundle['generated_at'],
            'project': bundle['project'], 'source_snapshot': bundle['source_snapshot'],
            'overview': bundle['overview'], 'technical_summary': bundle['technical_summary'],
            'selected_module_ids': selection['module_ids'],
            'modules': [{k: m[k] for k in ('id', 'name', 'summary', 'depends_on')}
                        for m in bundle['modules'] if m['id'] in related],
            'claims': claims,
            'unknowns': [u for u in bundle['unknowns'] if related.intersection(u['module_ids'])],
            'conflicts': conflicts,
            'coverage': {'inspected_modules': [mid for mid in bundle['coverage']['inspected_modules'] if mid in related],
                         'indexed_modules': [mid for mid in bundle['coverage']['indexed_modules'] if mid in related],
                         'excluded': bundle['coverage']['excluded'],
                         'limitations': bundle['coverage']['limitations']}})
    return {'packages': packages, 'confirmation_note': '用户核对现状不等于本期需求或规范采纳'}


def selection_identity(project: dict) -> list[dict] | None:
    """Nonempty version/selection snapshot for brief and execution fingerprints."""
    sources = _active_sources(project)
    if not sources:
        return None
    result = []
    for source in sources:
        bundle = source['business_context']
        selection = source['business_selection']
        selection_update(source, selection['module_ids'], selection['confirmed_claim_ids'])
        result.append({'source_id': source['id'], 'source_sha256': source['sha256'],
                       'bundle_id': bundle['bundle_id'], 'schema_version': bundle['schema_version'],
                       'module_ids': selection['module_ids'],
                       'confirmed_claim_ids': selection['confirmed_claim_ids']})
    return sorted(result, key=lambda row: row['source_id'])
