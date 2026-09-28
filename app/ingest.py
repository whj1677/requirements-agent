"""Stage-local intake contract and content-preserving format repairs."""
import copy
import json
import jsonschema

from .contracts import SCHEMA, VALIDATOR
from .core import digest, dumps, require


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


def complete_object(output):
    """Only a fully decoded JSON object can anchor a non-ingest format repair."""
    try:
        value=json.loads(output,parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError,TypeError):
        return None
    return value if isinstance(value,dict) else None


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


def response_anchor(value, excerpts, stage, project=None):
    """Conservatively retain valid business fields in a malformed non-ingest reply."""
    if not isinstance(value, dict):
        return {}
    definitions=SCHEMA['$defs']
    pairs={(e['source_id'],e['id']) for e in excerpts}
    locked={}

    def rule_valid(rule, field):
        return jsonschema.Draft202012Validator({'$defs':definitions,**rule}).is_valid(field)

    def fields(node, properties, prefix):
        if not isinstance(node,dict):return
        for key,rule in properties.items():
            if key not in node:continue
            field=node[key]
            if key in ('source_refs','used_source_refs') and isinstance(field,list):
                valid=[(r['source_id'],r['excerpt_id']) for r in field if isinstance(r,dict)
                       and isinstance(r.get('source_id'),str) and isinstance(r.get('excerpt_id'),str)
                       and (r['source_id'],r['excerpt_id']) in pairs]
                locked[(*prefix,key)]=valid
            elif key=='decision_points' and isinstance(field,list):
                ids=[r.get('temp_id') for r in field if isinstance(r,dict)]
                if len(ids)==len(field) and all(isinstance(i,str) for i in ids) and len(ids)==len(set(ids)):
                    locked[(*prefix,key,'@ids')]=ids
                    child_properties=rule['items']['properties']
                    for child in field:fields(child,child_properties,(*prefix,key,child['temp_id']))
            elif isinstance(field, list) and rule.get('type') == 'array' and isinstance(rule.get('items'), dict):
                item_rule = rule['items']
                if '$ref' in item_rule:
                    item_rule = definitions[item_rule['$ref'].rsplit('/',1)[1]]
                if item_rule.get('type') == 'object':
                    locked[(*prefix,key,'@count')] = len(field)
                    for index, child in enumerate(field):
                        fields(child, item_rule.get('properties', {}), (*prefix,key,index))
                elif rule_valid(rule,field):
                    locked[(*prefix,key)] = copy.deepcopy(field)
            elif rule_valid(rule,field):
                locked[(*prefix,key)]=copy.deepcopy(field)
            elif isinstance(field,dict) and '$ref' in rule:
                definition=definitions[rule['$ref'].rsplit('/',1)[1]]
                fields(field,definition.get('properties',{}),(*prefix,key))

    for name,definition in (('proposals','Proposal'),('questions','Question')):
        rows=value.get(name)
        if not isinstance(rows,list):continue
        ids=[r.get('temp_id') for r in rows if isinstance(r,dict)]
        if len(ids)!=len(rows) or any(not isinstance(i,str) for i in ids) or len(ids)!=len(set(ids)):continue
        locked[(name,'@ids')]=ids
        for row in rows:
            fields(row,definitions[definition]['properties'],(name,row['temp_id']))
            if name == 'proposals' and project is not None:
                from .understanding import answer_target
                # Preserve actual bindings, not invalid references that validation
                # already rejects. This only builds the anchor; never edits output.
                refs = row.get('answer_refs', [])
                questions = {q['id']: q for q in project['questions'] if q['status'] == 'answered'}
                locked[(name, row['temp_id'], 'answer_refs')] = [
                    ref for ref in refs if isinstance(ref, str) and ref in questions
                    and answer_target(project, questions[ref], row) is not None
                ] if isinstance(refs, list) else []
    findings=value.get('findings')
    if isinstance(findings,list):
        locked[('findings','@count')]=len(findings)
        for index,row in enumerate(findings):fields(row,definitions['Finding']['properties'],('findings',index))
    result_rule=next((branch['properties']['result'] for branch in SCHEMA['oneOf']
                      if branch['properties']['stage']['const']==stage),None)
    if result_rule:
        result_def=definitions[result_rule['$ref'].rsplit('/',1)[1]]
        fields(value.get('result'),result_def.get('properties',{}),('result',))
    fields(value,{k:SCHEMA['properties'][k] for k in ('summary','limitations','used_source_refs','product_context_proposal')},())
    return locked


TEMPORARY_REFERENCE_FIELDS = frozenset(('related_refs','ref_ids','canonical_refs',
    'proposed_item_refs','reviewed_refs','impacted_refs','unimpacted_refs',
    'requirement_refs','rule_ids','ac_ids'))
STABLE_REFERENCE_FIELDS = frozenset(('target_item_id','target_question_id','source_id',
    'excerpt_id','requirement_id','item_id','scope_ref','target_ref','id'))


def temporary_id_repair(original, value, stage):
    """Return an alpha-renamed original only for a pure temporary-ID syntax fix.

    Stable IDs, all business text and non-reference values remain byte-for-byte
    equal as JSON values. The original response and call evidence are untouched.
    Anything outside this narrow proof falls back to the usual repair guard.
    """
    if not isinstance(original,dict) or not isinstance(value,dict):return None
    if original.get('stage')!=stage or value.get('stage')!=stage:return None
    if not VALIDATOR.is_valid(value):return None

    def declarations(response):
        rows={}
        for key,definition in (('proposals','Proposal'),('questions','Question')):
            for index,row in enumerate(response.get(key,[])):
                if not isinstance(row,dict):return None
                properties=SCHEMA['$defs'][definition]['properties']
                rows[(key,index,'temp_id')]=(row.get('temp_id'),properties['temp_id'])
                if key=='questions':
                    for child_index,child in enumerate(row.get('decision_points',[])):
                        if not isinstance(child,dict):return None
                        rule=properties['decision_points']['items']['properties']['temp_id']
                        rows[(key,index,'decision_points',child_index,'temp_id')]=(child.get('temp_id'),rule)
        return rows

    # Before traversing malformed arrays, exclude every non-ID schema defect.
    errors=list(VALIDATOR.iter_errors(original))
    if not errors or any(e.validator not in ('pattern','maxLength')
                         or not e.absolute_path or e.absolute_path[-1]!='temp_id' for e in errors):
        return None
    before=declarations(original);after=declarations(value)
    if before is None or after is None or before.keys()!=after.keys():return None
    if any(tuple(e.absolute_path) not in before for e in errors):return None
    old_ids=[row[0] for row in before.values()]
    new_ids=[row[0] for row in after.values()]
    if any(not isinstance(i,str) for i in old_ids+new_ids):return None
    if len(old_ids)!=len(set(old_ids)) or len(new_ids)!=len(set(new_ids)):return None
    mapping={}
    for path,(old,rule) in before.items():
        new=after[path][0]
        if old==new:continue
        validator=jsonschema.Draft202012Validator(rule)
        prefix='TMP-' if path[0]=='proposals' else 'QTMP-'
        if not old.startswith(prefix) or validator.is_valid(old) or not validator.is_valid(new):return None
        mapping[old]=new
    if not mapping:return None

    def original_references(node):
        if isinstance(node,list):
            for child in node:yield from original_references(child)
        elif isinstance(node,dict):
            for key,child in node.items():
                if key in TEMPORARY_REFERENCE_FIELDS or key=='answer_refs':
                    if isinstance(child,list):yield from (ref for ref in child if isinstance(ref,str))
                elif key in STABLE_REFERENCE_FIELDS and isinstance(child,str):yield child
                yield from original_references(child)

    # A newly named temporary ID must not capture an existing stable reference.
    existing=set(original_references(original)) | set(old_ids)
    if set(mapping.values()) & (existing-set(mapping)):return None
    normalized=copy.deepcopy(original)
    for path in before:
        node=normalized
        for segment in path[:-1]:node=node[segment]
        node['temp_id']=mapping.get(node['temp_id'],node['temp_id'])

    def rewrite_references(node):
        if isinstance(node,list):
            for child in node:rewrite_references(child)
        elif isinstance(node,dict):
            for key,child in node.items():
                if key in TEMPORARY_REFERENCE_FIELDS and isinstance(child,list):
                    node[key]=[mapping.get(ref,ref) if isinstance(ref,str) else ref for ref in child]
                else:rewrite_references(child)

    rewrite_references(normalized)
    return normalized if dumps(normalized)==dumps(value) else None


def reference_repair_anchor(original, value, excerpts, stage, project):
    """Align only invalid reference slots for comparison; never amend model output.

    A source-pair correction must keep its excerpt and have a unique actual
    source. Unknown review IDs may be dropped, never replaced to expand coverage.
    Existing valid references cannot be removed, moved, or replaced.
    """
    normalized = copy.deepcopy(original)
    pairs = {(e['source_id'], e['id']) for e in excerpts}
    by_excerpt = {}
    for source, excerpt in pairs:
        by_excerpt.setdefault(excerpt, set()).add(source)
    known = ({i['id'] for i in project['items']} | {q['id'] for q in project['questions']}) if project else set()
    if project and project.get('ui'):
        known.update(page['page_id'] for page in project['ui']['spec']['pages'])

    def pair(ref):
        if isinstance(ref, dict) and isinstance(ref.get('source_id'), str) and isinstance(ref.get('excerpt_id'), str):
            return ref['source_id'], ref['excerpt_id']
        return None, None

    def align(before, after, valid, replacement):
        if not isinstance(before, list) or not isinstance(after, list):
            return False
        # Try both deletion and correction: a wrong pair before an identical
        # valid pair must not consume the valid pair's slot greedily.
        reachable = {0}
        for old in before:
            following = set()
            for index in reachable:
                if valid(old):
                    if index < len(after) and after[index] == old:
                        following.add(index + 1)
                else:
                    following.add(index)
                    if index < len(after) and replacement(old, after[index]):
                        following.add(index + 1)
            reachable = following
            if not reachable:
                return False
        return len(after) in reachable

    def walk(before, after, path=()):
        if isinstance(before, dict) and isinstance(after, dict):
            for key in before.keys() & after.keys():
                old, new = before[key], after[key]
                if key in ('source_refs', 'used_source_refs'):
                    def valid(ref):
                        return pair(ref) in pairs
                    def replacement(a, b):
                        source, excerpt = pair(b)
                        return (valid(b) and excerpt == pair(a)[1]
                                and by_excerpt.get(excerpt) == {source})
                    if align(old, new, valid, replacement):
                        before[key] = copy.deepcopy(new)
                elif key == 'source_ref' and isinstance(old, dict) and isinstance(new, dict):
                    old_pair, new_pair = pair(old), pair(new)
                    source, excerpt = new_pair
                    if (old_pair not in pairs and new_pair in pairs
                            and old_pair[1] == excerpt
                            and by_excerpt.get(excerpt) == {source}):
                        before[key] = copy.deepcopy(new)
                    else:
                        walk(old, new, (*path, key))
                elif stage == 'review' and project is not None and (*path, key) == ('result', 'reviewed_refs'):
                    if align(old, new, lambda ref: isinstance(ref, str) and ref in known,
                             lambda a, b: False):
                        before[key] = copy.deepcopy(new)
                else:
                    walk(old, new, (*path, key))
        elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
            for index, (old, new) in enumerate(zip(before, after)):
                walk(old, new, (*path, index))

    walk(normalized, value)
    return normalized


def preserve_response(anchor, value, excerpts, stage, original=None, project=None):
    if original is not None and anchor==response_anchor(original,excerpts,stage,project):
        normalized=reference_repair_anchor(original,value,excerpts,stage,project)
        renamed=temporary_id_repair(normalized,value,stage)
        anchor=response_anchor(renamed if renamed is not None else normalized,excerpts,stage,project)
    current=response_anchor(value,excerpts,stage,project)
    changed=[list(path) for path,original in anchor.items() if current.get(path)!=original]
    require(not changed,'SEMANTIC_BLOCKED',
            '格式修复改变了已有问题、规则、决定或引用，结果未采纳；请单独核对：'+str(changed))
