import copy
import hashlib
import re
import jsonschema
from referencing import Registry, Resource
from .core import KIT, brief_hash, digest, read_json, require

API_CAPABILITIES = tuple(sorted(('actions', 'artifacts', 'confirmations', 'documents', 'exports', 'models',
                                 'options-direction', 'options-items', 'projects', 'runs', 'session', 'sources')))

SCHEMA = read_json(KIT / 'examples/runtime_response.schema.json')
WIRE = read_json(KIT / 'examples/wireframe.schema.json')
REGISTRY = Registry().with_resource('wireframe.schema.json', Resource.from_contents(WIRE))
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA, registry=REGISTRY)
try:
    PROFILES = read_json(KIT / 'references/content_profiles.json')
except (OSError, ValueError):
    PROFILES = {'profiles':[], 'sources':[]}


def profile(kind, fallback=False):
    if fallback:
        return dict(id='builtin-'+kind,version='1.1',document_type=kind,notice='用户明确选择内置参考；未按原 Word 参考生成',sections=[dict(id=kind.upper()+'-BUILTIN-'+str(n),reference_heading=title,content_dimension=title,mapping_required=True,repeat_per_function=False) for n,title in enumerate(['背景与目标','本期范围与非目标','功能 规则与验收','重要未知与风险'],1)])
    p = next((x for x in PROFILES['profiles'] if x['document_type'] == kind),None)
    require(p is not None,'REFERENCE_UNAVAILABLE','章节参考 profile 读取失败；可在文档区明确选择内置回退后继续')
    src = next(x for x in PROFILES['sources'] if x['reference_id'] == p['source_reference_id'])
    path = KIT / src['file']
    require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == src['sha256'],
            'REFERENCE_UNAVAILABLE', '原始章节参考不可用；其它阶段可继续，不能声称按该模板成文')
    return p


def nodes(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from nodes(v)
    elif isinstance(value, list):
        for v in value:
            yield from nodes(v)


def validate_response(value, stage, project, excerpts, document_type='prd'):
    errors = sorted(VALIDATOR.iter_errors(value), key=lambda e: str(e.path))
    require(not errors, 'SCHEMA_INVALID', '模型响应不匹配 Schema：' + (str(list(errors[0].path)) if errors else ''))
    require(value['stage'] == stage, 'SCHEMA_INVALID', '模型返回了错误阶段')
    item_ids = {i['id'] for i in project['items']}
    question_ids = {q['id'] for q in project['questions']}
    temps = [i['temp_id'] for i in value['proposals']] + [q['temp_id'] for q in value['questions']]
    require(len(set(temps)) == len(temps), 'REFERENCE_INVALID', '临时 ID 重复')
    allowed = item_ids | question_ids | set(temps)
    if project.get('ui'):
        allowed |= {p['page_id'] for p in project['ui']['spec']['pages']}
    pairs = {(x['source_id'], x['id']) for x in excerpts}
    for node in nodes(value):
        if 'source_id' in node and 'excerpt_id' in node:
            require((node['source_id'], node['excerpt_id']) in pairs, 'REFERENCE_INVALID', '来源不在本轮实际输入范围')
        for key in ('related_refs', 'ref_ids', 'canonical_refs', 'requirement_refs', 'proposed_item_refs', 'reviewed_refs', 'impacted_refs', 'unimpacted_refs', 'rule_ids', 'ac_ids'):
            if key in node:
                require(set(node[key]) <= allowed, 'REFERENCE_INVALID', '引用不存在或跨项目：' + key)
        for key in ('target_ref', 'target_item_id', 'requirement_id', 'item_id', 'scope_ref'):
            if node.get(key) is not None:
                require(node[key] in allowed, 'REFERENCE_INVALID', '未知关联：' + key)
    for proposal in value['proposals']:
        require((proposal['action'] == 'add' and proposal['target_item_id'] is None) or
                (proposal['action'] == 'revise' and proposal['target_item_id'] in item_ids),
                'REFERENCE_INVALID', '修订目标必须存在；新增不能覆盖旧条目')
    if stage == 'ui':
        validate_ui(value['result']['spec'], project)
    if stage == 'prd':
        validate_document(value['result'], project, document_type)
    if stage in ('ui', 'prd', 'review', 'handoff'):
        require(not value['proposals'] and not value['questions'], 'SCHEMA_INVALID', '派生阶段发现新需求时请返回澄清阶段，不能同时改写基准')
    return value


def validate_ui(spec, p):
    jsonschema.Draft202012Validator(WIRE).validate(spec)
    forbidden=re.compile(r'https?://|javascript:|data:|<\s*(script|iframe|img|svg)\b|\bon(?:error|load|click)\s*=',re.I)
    for node in nodes(spec):
        if isinstance(node, dict):
            require(not any(forbidden.search(value) for value in node.values() if isinstance(value,str)),
                    'SCHEMA_INVALID','原型 spec 不接受脚本、HTML 标签或网络地址')
    require(spec['draft_revision'] == p['revision'], 'STALE_REVISION', '线框版本过期')
    components = [c for page in spec['pages'] for r in page['regions'] for c in r['components']]
    ids = [c['component_id'] for c in components]
    require(len(ids) == len(set(ids)) and len(ids) <= 150, 'SCHEMA_INVALID', '组件 ID 重复或超限')
    pages = [x['page_id'] for x in spec['pages']]
    require(len(pages) == len(set(pages)), 'SCHEMA_INVALID', '页面 ID 重复')
    by_id = {c['component_id']: c for c in components}
    datasets={c['simulation']['dataset_id']:c for c in components if c['type'] in ('table','list') and c.get('simulation')}
    for c in components:
        require(c['ref_ids'] or c['type'] in ('text', 'heading', 'notice'), 'REFERENCE_INVALID', '功能性组件缺少需求关联')
        require(not c['interaction']['target_id'] or c['interaction']['target_id'] in ids + pages, 'REFERENCE_INVALID', '交互目标不存在')
        require(all(len(row) == len(c['columns']) for row in c['rows']), 'SCHEMA_INVALID', '表格行列不一致')
        action = c['interaction']['action']
        target = by_id.get(c['interaction']['target_id'])
        if action in ('new', 'edit', 'filter', 'save', 'cancel', 'switch_page') or c['type'] == 'drawer' or c.get('simulation') or any(f['type'] == 'time' for f in c['fields']):
            require(spec['schema_version'] == '1.1', 'SCHEMA_INVALID', '新模拟能力需要 wireframe 1.1')
        if action in ('new', 'edit'):
            require(c['type'] in ('button', 'table', 'list') and target and target['type'] in ('form', 'panel', 'dialog', 'drawer'), 'REFERENCE_INVALID', '新增或编辑必须指向表单容器')
        if action in ('save', 'cancel'):
            require(c['type'] == 'button' and target and target['type'] in ('form', 'panel', 'dialog', 'drawer'), 'REFERENCE_INVALID', '保存或取消必须指向表单容器')
        if action == 'filter':
            require(c['type'] == 'button' and target and target['type'] in ('table', 'list'), 'REFERENCE_INVALID', '筛选必须指向列表')
        if action == 'switch_page':
            require(c['interaction']['target_id'] in pages, 'REFERENCE_INVALID', '导航目标必须是页面')
        if c.get('simulation'):
            sim = c['simulation']
            fields = {f['name'] for f in c['fields']}
            if c['type'] in ('form','panel','dialog','drawer'):
                require(sim['dataset_id'] in datasets, 'REFERENCE_INVALID', '表单模拟数据集不存在')
            for rule in sim['rules']:
                require(set(rule['field_names']) <= fields, 'REFERENCE_INVALID', '模拟校验字段不存在')
                require(set(rule['ref_ids']) <= {i['id'] for i in p['items']}, 'REFERENCE_INVALID', '模拟校验依据不存在')
                require((rule['kind'] in ('required', 'non_negative') and len(rule['field_names']) == 1) or
                        (rule['kind'] in ('start_before_end', 'no_overlap') and len(rule['field_names']) == 2),
                        'SCHEMA_INVALID', '模拟校验字段数量不匹配')
                if rule['kind']=='no_overlap':
                    require(set(rule['field_names']) <= {col['key'] for col in datasets[sim['dataset_id']]['columns']},
                            'REFERENCE_INVALID', '区间字段未绑定模拟列表')
    for page in spec['pages']:
        for c in (c for region in page['regions'] for c in region['components']):
            if c['interaction']['action']=='switch_state':
                require(c['interaction']['target_state'] in page['states'], 'REFERENCE_INVALID', '目标状态不适用于此页面')


def validate_document(doc, p, kind):
    prof = profile(kind,p.get('reference_mode')=='builtin')
    require(doc['document_type'] == kind and doc['content_profile_id'] == prof['id'] and doc['content_profile_version'] == prof['version'], 'REFERENCE_INVALID', '文档类型或参考 profile 不匹配')
    sections = {s['section_id']: s for s in doc['sections']}
    require(len(sections) == len(doc['sections']), 'REFERENCE_INVALID', '章节 ID 重复')
    seen = set()
    for s in doc['sections']:
        sid, parent = s['section_id'], s['parent_section_id']
        require(sid.startswith(kind.upper() + '-'), 'REFERENCE_INVALID', '章节前缀错误')
        require((parent is None and s['level'] == 1) or (parent in seen and s['level'] == sections[parent]['level'] + 1), 'REFERENCE_INVALID', '章节父子关系、顺序或层级错误')
        seen.add(sid)
    dimensions = {x['id']: x for x in prof['sections']}
    for m in doc['reference_mapping']:
        require(m['profile_section_id'] in dimensions and set(m['output_section_ids']) <= seen, 'REFERENCE_INVALID', '章节参考悬空')
    items = {i['id']: i for i in p['items']}
    for c in doc['coverage']:
        require(set(c['section_ids']) <= seen, 'REFERENCE_INVALID', '覆盖章节不存在')
        require(all(any(c['item_id'] in b['ref_ids'] for b in sections[s]['blocks']) for s in c['section_ids']), 'REFERENCE_INVALID', '虚假覆盖关系')
    for s in doc['sections']:
        for b in s['blocks']:
            if b['kind'] in ('requirement', 'rule', 'acceptance'):
                require(all(r in items and items[r]['selection_status'] == 'selected' and items[r]['applies_to'] == 'to_be' for r in b['ref_ids']), 'SEMANTIC_BLOCKED', '规范内容只能引用本期已选目标条目')
                require(all(items[r]['kind'] == b['kind'] for r in b['ref_ids']), 'REFERENCE_INVALID', '规范段落与条目类型不一致')


def document_gaps(doc, p):
    prof = profile(doc['document_type'],doc['content_profile_id'].startswith('builtin-'))
    functions = [i['id'] for i in p['items'] if i['kind'] == 'requirement' and i['selection_status'] == 'selected' and i['applies_to'] == 'to_be']
    actual = {(m['profile_section_id'], m['scope_ref']) for m in doc['reference_mapping']}
    gaps = []
    for d in prof['sections']:
        if d['mapping_required']:
            for scope in (functions or [None]) if d['repeat_per_function'] else [None]:
                if (d['id'], scope) not in actual:
                    gaps.append('参考维度未处置：' + d['id'] + (' / ' + scope if scope else ''))
    used = {r for s in doc['sections'] for b in s['blocks'] if b['kind'] in ('requirement', 'rule', 'acceptance') for r in b['ref_ids']}
    for i in p['items']:
        if i['selection_status'] == 'selected' and i['applies_to'] == 'to_be' and i['kind'] in ('requirement', 'rule', 'acceptance') and i['id'] not in used:
            gaps.append('未成文：' + i['id'])
    return gaps


def review_target(p):
    return digest({'brief': brief_hash(p), 'documents': p['documents'], 'ui': p['ui']})


def gate(p):
    issues = []
    if 'prd' not in p['documents']:
        return ['缺少 PRD 内容对象；MRD 不能代替 PRD 确认']
    for doc in p['documents'].values():
        if doc['brief_hash'] != brief_hash(p):
            issues.append('文档与当前底稿不一致，请重新生成')
        issues.extend(document_gaps(doc['content'], p))
    if p.get('document_update_needed'):
        issues.append('页面变化后文档需要更新：'+'、'.join(p.get('stale_document_kinds',[])))
    if p['ui'] and p['ui']['brief_hash'] != brief_hash(p):
        issues.append('线框对应旧底稿，请重新生成')
    issues.extend(q['question'] for q in p['questions'] if q['blocking'] and q['status'] != 'answered')
    review = p.get('review')
    if not review or review['target_hash'] != review_target(p):
        issues.append('需要审查当前底稿、文档和线框')
    elif review['response']['result']['assessment'] != 'ready_for_human_review' or any(f['severity'] == 'blocker' for f in review['response']['findings']):
        issues.append('语义审查仍有阻塞项')
    if not any(i['selection_status'] == 'selected' and i['applies_to']=='to_be' and i['kind'] == 'requirement' for i in p['items']):
        issues.append('本期没有已选需求')
    if not any(i['selection_status'] == 'selected' and i['applies_to']=='to_be' and i['kind'] == 'acceptance' for i in p['items']):
        issues.append('缺少已选验收条件')
    selected=[i for i in p['items'] if i['selection_status']=='selected' and i['applies_to']=='to_be']
    for req in (i for i in selected if i['kind']=='requirement'):
        if not any(ac['kind']=='acceptance' and (req['id'] in ac['related_refs'] or ac['id'] in req['related_refs']) for ac in selected):
            issues.append('需求缺少关联验收条件：'+req['id'])
    return list(dict.fromkeys(issues))
