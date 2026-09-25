"""Stage-local intake contract and content-preserving format repairs."""
import copy
import json
import jsonschema

from .contracts import SCHEMA
from .core import digest, require


def schema():
    value = copy.deepcopy(SCHEMA)
    value.pop('allOf', None)
    value.pop('oneOf', None)
    value['properties']['stage'] = {'const': 'ingest'}
    value['properties']['result'] = {'$ref': '#/$defs/ingestResult'}
    value['required'] = [*SCHEMA['required'], 'product_context_proposal']
    context = value['properties']['product_context_proposal']
    context['required'] = list(context['properties'])
    # Keep only transitively referenced definitions, with exactly the old constraints.
    definitions = value.pop('$defs')
    needed = {}

    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            ref = node.get('$ref', '')
            if ref.startswith('#/$defs/'):
                name = ref.rsplit('/', 1)[1]
                if name not in needed:
                    needed[name] = definitions[name]
                    visit(needed[name])
            for child in node.values():
                visit(child)

    visit(value)
    value['$defs'] = needed
    return value


def contract():
    spec = schema()
    example = dict(schema_version='1.1', stage='ingest', summary='', proposals=[],
                   questions=[], findings=[], used_source_refs=[], limitations=[],
                   result=dict(understanding='', material_limits=[]),
                   product_context_proposal={k: '' for k in spec['properties']['product_context_proposal']['properties']})
    jsonschema.Draft202012Validator(spec).validate(example)
    return dict(version='ingest-contract-2', example=example,
                instruction='示例仅说明结构，不是业务答案或失败回退。必须保留根节点 result，'
                '含 understanding 字符串和 material_limits 数组。禁止 *_note、placeholder 或任何未知字段。'
                '未知正文填空字符串；来源只使用实际 excerpts 中 source_id/id 配对，不能生成来源ID。')


def issues(value, excerpts):
    errors = [str(list(e.absolute_path)) + ' ' + e.message
              for e in jsonschema.Draft202012Validator(schema()).iter_errors(value)]
    pairs = {(e['source_id'], e['id']) for e in excerpts}

    def visit(node, path=()):
        if isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, (*path, index))
        elif isinstance(node, dict):
            if isinstance(node.get('source_id'), str) and isinstance(node.get('excerpt_id'), str):
                if (node['source_id'], node['excerpt_id']) not in pairs:
                    errors.append(f'{list(path)} 来源不在本次实际输入：{node["source_id"]}/{node["excerpt_id"]}')
            for key, child in node.items():
                visit(child, (*path, key))

    visit(value)
    return errors


def validate(value, excerpts):
    errors = issues(value, excerpts)
    structural = list(jsonschema.Draft202012Validator(schema()).iter_errors(value))
    require(not errors, 'SCHEMA_INVALID' if structural else 'REFERENCE_INVALID',
            '材料分析契约不匹配：\n' + '\n'.join(errors))


def diagnostic_object(output):
    """A parseable first object is only an anchor, never a cleaned accepted response."""
    try:
        value, _ = json.JSONDecoder().raw_decode(output.lstrip())
        return value if isinstance(value, dict) else None
    except (ValueError, AttributeError):
        return None


def business_anchor(value, excerpts=()):
    """Lock valid business fields; missing/invalid fields may still be repaired."""
    if not isinstance(value, dict):
        return {}
    spec = schema()
    locked = {}
    pairs = {(e['source_id'], e['id']) for e in excerpts}

    def lock_fields(node, properties, prefix):
        if not isinstance(node, dict):
            return
        for key, rule in properties.items():
            if key in ('source_refs', 'related_refs', 'target_item_id') or key not in node:
                continue
            validator = jsonschema.Draft202012Validator({'$defs': spec['$defs'], **rule})
            if validator.is_valid(node[key]):
                locked[(*prefix, key)] = copy.deepcopy(node[key])
        if isinstance(node.get('source_refs'), list):
            refs = [r for r in node['source_refs'] if isinstance(r, dict)
                    and isinstance(r.get('source_id'), str) and isinstance(r.get('excerpt_id'), str)
                    and (r['source_id'], r['excerpt_id']) in pairs]
            locked[(*prefix, '@sources')] = [(r['source_id'], r['excerpt_id']) for r in refs]

    for key, definition in [('proposals', 'Proposal'), ('questions', 'Question')]:
        rows = value.get(key)
        if not isinstance(rows, list):
            continue
        ids = [r.get('temp_id') for r in rows if isinstance(r, dict)]
        if len(ids) != len(rows) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            continue
        locked[(key, '@ids')] = ids
        for row in rows:
            lock_fields(row, spec['$defs'][definition]['properties'], (key, row['temp_id']))
    for key in ('product_context_proposal', 'result'):
        properties = (spec['properties'][key]['properties'] if key != 'result'
                      else spec['$defs']['ingestResult']['properties'])
        lock_fields(value.get(key), properties, (key,))
    lock_fields(value, {k:spec['properties'][k] for k in ('summary','limitations')}, ())
    return locked


def preserve(anchor, value, excerpts=()):
    current = business_anchor(value, excerpts)
    changed = [list(path) for path, original in anchor.items()
               if (not set(original) <= set(current.get(path, [])) if path[-1]=='@sources'
                   else current.get(path) != original)]
    require(not changed, 'SEMANTIC_BLOCKED',
            '格式修复改变了已有业务内容或未决问题，结果未采纳；请单独核对：' + str(changed))


def anchor_hash(anchor):
    return digest([[list(k), v] for k, v in anchor.items()])
