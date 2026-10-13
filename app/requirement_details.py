"""Optional, versioned business definitions; no automatic approval or migration.

Structure validation permits incomplete drafts. Completeness is checked at the
human confirmation and delivery boundaries, using stable IDs rather than indexes.
"""
import re

import jsonschema

from .core import Problem

SECTIONS = ('fields', 'interactions', 'flow', 'state_transitions')
FIELD_PROPERTIES = ('name', 'meaning', 'data_type', 'source', 'required', 'default_value',
                    'constraints', 'visibility', 'editability')
INTERACTION_PROPERTIES = ('name', 'actor', 'entry', 'preconditions', 'trigger', 'inputs',
                          'validation', 'pending', 'repeat', 'cancel', 'success', 'failure', 'effects')
TRANSITION_PROPERTIES = ('from_state', 'to_state', 'trigger', 'condition', 'prohibited_actions')
DATA_TYPES = ('text', 'string', 'id', 'number', 'integer', 'boolean', 'enum', 'date', 'datetime', 'object', 'array')
ID_SCHEMA = {'type': 'string', 'pattern': r'^[A-Za-z0-9_-]{1,80}$'}
TEXT_SCHEMA = {'type': 'string', 'maxLength': 6000}
REF_SCHEMA = {'type': 'array', 'items': ID_SCHEMA, 'maxItems': 300, 'uniqueItems': True}
SOURCE_REF_SCHEMA = {'type': 'object', 'required': ['source_id', 'excerpt_id'],
    'properties': {'source_id': ID_SCHEMA, 'excerpt_id': ID_SCHEMA}, 'additionalProperties': False}
EVIDENCE_SCHEMA = {'type': 'object', 'maxProperties': 60, 'additionalProperties': {
    'type': 'object', 'required': ['status'], 'properties': {
        'status': {'enum': ['source', 'proposal', 'unknown']}, 'reason': TEXT_SCHEMA,
        'source_refs': {'type': 'array', 'items': SOURCE_REF_SCHEMA, 'maxItems': 30}},
    'additionalProperties': False}}


def _object_schema(properties):
    return {'type': 'object', 'required': ['id'], 'properties': dict(
        id=ID_SCHEMA, evidence=EVIDENCE_SCHEMA, field_refs=REF_SCHEMA,
        rule_refs=REF_SCHEMA, acceptance_refs=REF_SCHEMA, **properties), 'additionalProperties': False}


def _rows(properties, maximum=150):
    return {'type': 'array', 'maxItems': maximum, 'items': _object_schema(properties)}


DETAIL_SCHEMA = {'type': 'object', 'required': ['schema_version', 'applicability', *SECTIONS],
    'properties': {
        'schema_version': {'const': 1},
        'applicability': {'type': 'object', 'required': list(SECTIONS), 'properties': {
            name: {'type': 'object', 'required': ['applicable', 'reason'], 'properties': {
                'applicable': {'type': 'boolean'}, 'reason': TEXT_SCHEMA}, 'additionalProperties': False}
            for name in SECTIONS}, 'additionalProperties': False},
        'fields': _rows({**{name: TEXT_SCHEMA for name in (*FIELD_PROPERTIES, 'permissions',
            'unit', 'precision', 'time_basis', 'formula', 'empty_value', 'uniqueness', 'parent_id')},
            'data_type': {'enum': ['', *DATA_TYPES]}, 'enum_values': {'type': 'array',
                'items': TEXT_SCHEMA, 'maxItems': 300, 'uniqueItems': True}}),
        'interactions': _rows({name: TEXT_SCHEMA for name in INTERACTION_PROPERTIES}),
        'flow': {'type': 'object', 'required': ['nodes', 'edges'], 'properties': {
            'nodes': _rows({'label': TEXT_SCHEMA, 'kind': {'enum': ['', 'start', 'action', 'decision', 'wait', 'end']},
                            'actor': TEXT_SCHEMA, 'interaction_id': TEXT_SCHEMA}, 120),
            'edges': _rows({'from': TEXT_SCHEMA, 'to': TEXT_SCHEMA, 'condition': TEXT_SCHEMA}, 240)
        }, 'additionalProperties': False},
        'state_transitions': _rows({name: TEXT_SCHEMA for name in TRANSITION_PROPERTIES}),
        'depends_on': REF_SCHEMA}, 'additionalProperties': False}


class DetailProblem(Problem):
    def __init__(self, issues, code='DETAIL_INCOMPLETE'):
        self.issues = issues
        super().__init__(code, '；'.join(row['message'] for row in issues[:8]), 409)


def _objects(detail):
    for section in ('fields', 'interactions', 'state_transitions'):
        for row in detail.get(section, []):
            yield section, row
    for section in ('nodes', 'edges'):
        for row in detail.get('flow', {}).get(section, []):
            yield 'flow.' + section, row


def _stable_path(detail, path):
    node, parts = detail, ['detail_contract']
    for part in path:
        if isinstance(part, int) and isinstance(node, list) and part < len(node):
            node = node[part]
            parts.append(str(node.get('id', part)) if isinstance(node, dict) else str(part))
        else:
            parts.append(str(part))
            node = node.get(part) if isinstance(node, dict) else None
    return '.'.join(parts)


def _shape_issues(detail, item_id=''):
    return [dict(code='DETAIL_STRUCTURE', path=_stable_path(detail, error.absolute_path),
                 message='详细需求结构无效：' + error.message[:250], item_id=item_id, object_id='')
            for error in jsonschema.Draft202012Validator(DETAIL_SCHEMA).iter_errors(detail)]


def validate_detail(detail, project=None):
    issues = _shape_issues(detail)
    if issues:
        raise DetailProblem(issues, 'DETAIL_INVALID')
    return detail


def detail_issues(item, project=None, require_confirmed=False):
    """Return stable, actionable gaps. Absent contracts preserve legacy behavior."""
    if 'detail_contract' not in item:
        return ([dict(code='DETAIL_REQUIRED', path='detail_contract', message='本条需求须整理结构化字段、交互和流程后再确认',
                      item_id=item.get('id', ''), object_id='')] if item.get('detail_required') else [])
    detail, iid = item['detail_contract'], item.get('id', '')
    issues = _shape_issues(detail, iid)
    if issues:
        return issues

    def add(code, path, message, oid=''):
        issues.append(dict(code=code, path='detail_contract.' + path, message=message,
                           item_id=iid, object_id=oid))

    unknown = re.compile(r'^(?:待(?:确认|决定|补充|核对|确定)|未知|未明确|资料未说明|TBD|TODO)(?:[：:，,；;。.!！\s]|$)', re.I)

    def required(row, path, properties):
        for prop in properties:
            value = row.get(prop)
            if not isinstance(value, str) or not value.strip():
                add('DETAIL_PROPERTY_MISSING', path + '.' + prop, f'{row["id"]} 尚未定义 {prop}', row['id'])
            elif unknown.match(value.strip()):
                add('DETAIL_PROPERTY_UNKNOWN', path + '.' + prop, f'{row["id"]} 的 {prop} 仍待决定', row['id'])
            elif value.strip() in ('不适用', 'N/A', 'NA'):
                add('DETAIL_NA_REASON', path + '.' + prop, f'{row["id"]} 的 {prop} 不适用须说明原因', row['id'])

    for section in SECTIONS:
        applicability = detail['applicability'][section]
        rows = detail[section]['nodes'] if section == 'flow' else detail[section]
        if not applicability['applicable']:
            if not applicability['reason'].strip():
                add('DETAIL_NA_REASON', 'applicability.' + section + '.reason', section + ' 不适用须说明业务原因')
            if rows or section == 'flow' and detail['flow']['edges']:
                add('DETAIL_APPLICABILITY_CONFLICT', section, section + ' 标记不适用但仍有定义，请核对')
        elif not rows:
            add('DETAIL_SECTION_EMPTY', section, section + ' 适用但尚未定义')

    objects = list(_objects(detail))
    seen = set()
    fields = {row['id']: row for row in detail['fields']}
    interactions = {row['id']: row for row in detail['interactions']}
    sources = {(source['id'], excerpt['id']) for source in (project or {}).get('sources', [])
               if not source.get('excluded') and not source.get('superseded_by')
               for excerpt in source.get('excerpts', [])}
    items = {row['id']: row for row in (project or {}).get('items', [])}
    for section, row in objects:
        path, oid = section + '.' + row['id'], row['id']
        if oid in seen:
            add('DETAIL_DUPLICATE_ID', path, '详细定义编号重复：' + oid, oid)
        seen.add(oid)
        for prop, evidence in row.get('evidence', {}).items():
            if prop not in row or prop in ('id', 'evidence'):
                add('DETAIL_EVIDENCE_PATH', path + '.evidence.' + prop, '属性依据指向不存在的业务属性：' + prop, oid)
            if evidence['status'] == 'unknown':
                add('DETAIL_PROPERTY_UNKNOWN', path + '.' + prop, oid + ' 的 ' + prop + ' 仍待确认', oid)
            if evidence['status'] == 'source' and not evidence.get('source_refs'):
                add('DETAIL_SOURCE_MISSING', path + '.' + prop, oid + ' 的 ' + prop + ' 缺少所声明的资料依据', oid)
            if project is not None and any((ref['source_id'], ref['excerpt_id']) not in sources
                                          for ref in evidence.get('source_refs', [])):
                add('DETAIL_SOURCE_INVALID', path + '.' + prop, oid + ' 的 ' + prop + ' 引用来源无效或已停用', oid)
        for ref in row.get('field_refs', []):
            if ref not in fields:
                add('DETAIL_FIELD_REFERENCE', path + '.field_refs', oid + ' 使用的字段尚未定义：' + ref, oid)
        for kind, prop in (('rule', 'rule_refs'), ('acceptance', 'acceptance_refs')):
            if project is not None:
                for ref in row.get(prop, []):
                    target = items.get(ref, {})
                    if target.get('kind') != kind or target.get('applies_to') != 'to_be' or target.get('selection_status') != 'selected':
                        add('DETAIL_ITEM_REFERENCE', path + '.' + prop, oid + ' 引用的已采纳' + kind + '无效：' + ref, oid)
        if section == 'fields':
            required(row, path, FIELD_PROPERTIES)
            if row.get('data_type') == 'enum' and (not row.get('enum_values') or any(not v.strip() for v in row['enum_values'])):
                add('DETAIL_ENUM_MISSING', path + '.enum_values', oid + ' 尚未定义完整枚举选项', oid)
            if row.get('data_type') in ('number', 'integer'):
                required(row, path, ('unit', 'precision'))
            if row.get('data_type') in ('date', 'datetime'):
                required(row, path, ('time_basis',))
            if row.get('source') == 'calculated':
                required(row, path, ('formula',))
            if row.get('parent_id') and (row['parent_id'] not in fields or row['parent_id'] == oid):
                add('DETAIL_FIELD_REFERENCE', path + '.parent_id', oid + ' 的父字段不存在或指向自身', oid)
            parents, parent = {oid}, row.get('parent_id')
            while parent and parent in fields:
                if parent in parents:
                    add('DETAIL_FIELD_CYCLE', path + '.parent_id', oid + ' 的父字段关系形成循环', oid)
                    break
                parents.add(parent)
                parent = fields[parent].get('parent_id')
        elif section == 'interactions':
            required(row, path, INTERACTION_PROPERTIES)
        elif section == 'state_transitions':
            required(row, path, TRANSITION_PROPERTIES)
        elif section == 'flow.nodes':
            required(row, path, ('label', 'kind') + (() if row.get('kind') in ('start', 'end') else ('actor',)))
            if row.get('interaction_id') and row['interaction_id'] not in interactions:
                add('DETAIL_INTERACTION_REFERENCE', path + '.interaction_id', oid + ' 引用的交互不存在', oid)

    if detail['applicability']['flow']['applicable'] and detail['flow']['nodes']:
        nodes, edges = {row['id']: row for row in detail['flow']['nodes']}, detail['flow']['edges']
        outgoing = {nid: [] for nid in nodes}
        incoming = {nid: [] for nid in nodes}
        for edge in edges:
            path = 'flow.edges.' + edge['id']
            if edge.get('from') not in nodes or edge.get('to') not in nodes:
                add('DETAIL_FLOW_REFERENCE', path, edge['id'] + ' 的起点或终点不存在', edge['id'])
                continue
            outgoing[edge['from']].append(edge)
            incoming[edge['to']].append(edge)
        starts = [nid for nid, row in nodes.items() if row.get('kind') == 'start']
        terminals = [nid for nid, row in nodes.items() if row.get('kind') in ('end', 'wait')]
        if not starts:
            add('DETAIL_FLOW_START', 'flow.nodes', '业务流程缺少开始节点')
        if not terminals:
            add('DETAIL_FLOW_END', 'flow.nodes', '业务流程缺少结束或等待节点')
        for nid, row in nodes.items():
            path = 'flow.nodes.' + nid
            if row.get('kind') == 'decision':
                if len(outgoing[nid]) < 2:
                    add('DETAIL_FLOW_BRANCH', path, nid + ' 判断节点至少需要两个条件分支', nid)
                conditions = [edge.get('condition', '').strip() for edge in outgoing[nid]]
                if not all(conditions) or len(set(conditions)) != len(conditions):
                    add('DETAIL_FLOW_CONDITION', path, nid + ' 的分支条件为空或重复', nid)
                for edge in outgoing[nid]:
                    required(edge, 'flow.edges.' + edge['id'], ('condition',))
            if row.get('kind') == 'end' and outgoing[nid]:
                add('DETAIL_FLOW_TERMINAL', path, nid + ' 结束节点不能继续连出', nid)
            if row.get('kind') == 'start' and incoming[nid]:
                add('DETAIL_FLOW_START', path, nid + ' 开始节点不能有入边', nid)
            if row.get('kind') not in ('end', 'wait') and not outgoing[nid]:
                add('DETAIL_FLOW_DEAD_END', path, nid + ' 缺少后续处理去向', nid)
            if row.get('kind') == 'wait' and (not outgoing[nid] or any(not edge.get('condition', '').strip() for edge in outgoing[nid])):
                add('DETAIL_FLOW_WAIT', path, nid + ' 等待节点须定义继续条件及去向', nid)
        def reachable(seeds, graph, key):
            visited, todo = set(), list(seeds)
            while todo:
                nid = todo.pop()
                if nid in visited:
                    continue
                visited.add(nid)
                todo.extend(edge[key] for edge in graph[nid] if edge[key] not in visited)
            return visited
        reached = reachable(starts, outgoing, 'to')
        finishes = reachable(terminals, incoming, 'from')
        for nid in nodes:
            if nid not in reached:
                add('DETAIL_FLOW_UNREACHABLE', 'flow.nodes.' + nid, nid + ' 无法从开始节点到达', nid)
            elif nid not in finishes:
                add('DETAIL_FLOW_NO_RESULT', 'flow.nodes.' + nid, nid + ' 无法到达结束或等待状态', nid)

    if project is not None:
        scope = (project.get('product_context') or {}).get('scope_ids')
        for dependency in detail.get('depends_on', []):
            target = items.get(dependency, {})
            if (dependency == iid or target.get('kind') != 'requirement' or target.get('applies_to') != 'to_be'
                    or target.get('selection_status') != 'selected' or scope is not None and dependency not in scope):
                add('DETAIL_DEPENDENCY_INVALID', 'depends_on', '依赖需求不存在、不在本期或指向自身：' + dependency)
        todo, visited = list(detail.get('depends_on', [])), set()
        while todo:
            dependency = todo.pop()
            if dependency == iid:
                add('DETAIL_DEPENDENCY_CYCLE', 'depends_on', '需求依赖形成循环，请核对依赖方向')
                break
            if dependency in visited:
                continue
            visited.add(dependency)
            dependency_detail = items.get(dependency, {}).get('detail_contract')
            if isinstance(dependency_detail, dict):
                todo.extend(dependency_detail.get('depends_on', []))
        if require_confirmed:
            from .requirement_confirmation import confirmation_current
            if not confirmation_current(project, item):
                add('DETAIL_CONFIRMATION_REQUIRED', '', '详细需求未确认或确认后内容/依赖已变化')
    return issues


def require_complete(item, project=None):
    issues = detail_issues(item, project)
    if issues:
        raise DetailProblem(issues)


def dependency_snapshot(project, item):
    """Transitive dependency contents, without recursive confirmation hashing."""
    from .requirements import item_content
    detail = item.get('detail_contract')
    if not isinstance(detail, dict) or not detail.get('depends_on'):
        return None
    items = {row['id']: row for row in project['items']}
    seen, todo, result = {item['id']}, list(detail['depends_on']), []
    while todo:
        iid = todo.pop()
        if iid in seen:
            continue
        seen.add(iid)
        target = items.get(iid)
        clauses = [row for row in project['items'] if target and row.get('kind') in ('rule', 'acceptance')
                   and row.get('applies_to') == 'to_be' and row.get('selection_status') == 'selected'
                   and not row.get('target_item_id')
                   and (iid in row.get('related_refs', []) or row['id'] in target.get('related_refs', []))]
        scope = (project.get('product_context') or {}).get('scope_ids')
        result.append(dict(id=iid, content=item_content(target) if target else None,
                           selection_status=target.get('selection_status') if target else None,
                           in_scope=scope is None or iid in scope,
                           linked_clauses=[dict(id=row['id'], content=item_content(row)) for row in sorted(clauses, key=lambda row:row['id'])]))
        if target and isinstance(target.get('detail_contract'), dict):
            todo.extend(target['detail_contract'].get('depends_on', []))
    return sorted(result, key=lambda row: row['id'])


def detail_source_refs(item):
    detail = item.get('detail_contract')
    if not isinstance(detail, dict) or _shape_issues(detail):
        return []
    return [ref for _, row in _objects(detail) for evidence in row.get('evidence', {}).values()
            for ref in evidence.get('source_refs', [])]


def detail_property_paths(item):
    detail = item.get('detail_contract')
    if not isinstance(detail, dict) or _shape_issues(detail):
        return []
    return ['detail_contract.' + section + '.' + row['id'] + '.' + prop
            for section, row in _objects(detail) for prop, value in row.items()
            if prop not in ('id', 'evidence') and (isinstance(value, str) or
                isinstance(value, list) and all(isinstance(v, str) for v in value))]


def detail_property_text(item, path):
    if path not in detail_property_paths(item):
        return None
    suffix = path[len('detail_contract.'):]
    for section, row in _objects(item['detail_contract']):
        prefix = section + '.' + row['id'] + '.'
        if suffix.startswith(prefix):
            value = row[suffix[len(prefix):]]
            return '、'.join(value) if isinstance(value, list) else value
    return None
