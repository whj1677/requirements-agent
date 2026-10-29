import copy
import json
import re
import jsonschema
from .core import brief_hash, digest, require
from .requirements import delivery_items
from .document_schema import SCHEMA, WIRE, REGISTRY, VALIDATOR, PROFILES, profile

API_CAPABILITIES = tuple(sorted(('actions', 'artifacts', 'confirmations', 'documents', 'exports', 'models',
                                 'options-direction', 'options-items', 'projects', 'runs', 'session', 'sources', 'product-flow', 'five-step-workflow', 'requirements-exchange', 'business-context', 'conversational-intake', 'shared-draft', 'decision-review', 'domain-context','prewrite-review')))

def nodes(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from nodes(v)
    elif isinstance(value, list):
        for v in value:
            yield from nodes(v)


def nodes_with_paths(value, path=()):
    """Yield each mapping with its JSON path for actionable reference diagnostics."""
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from nodes_with_paths(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from nodes_with_paths(child, (*path, index))


def json_path(path):
    result = '$'
    for part in path:
        if isinstance(part, int):
            result += f'[{part}]'
        elif re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', part):
            result += '.' + part
        else:
            result += '[' + json.dumps(part, ensure_ascii=False) + ']'
    return result


def describe_schema_error(errors):
    flat = []
    stack = list(errors)
    while stack:
        e = stack.pop()
        flat.append(e)
        stack.extend(getattr(e, 'context', None) or [])
    best = max(flat, key=lambda e: (len(list(e.absolute_path)), len(list(e.absolute_schema_path))))
    message = (f'{best.validator}={best.validator_value}，实际长度={len(best.instance)}'
               if best.validator in ('maxItems','minItems','maxLength','minLength') else best.message)
    if len(message) > 160:
        message = message[:160] + '…'
    return str(list(best.absolute_path)) + ' ' + message


def validate_response(value, stage, project, excerpts, document_type='prd', *, trusted_business_draft=False):
    if stage=='interview':
        from .interview import validate
        return validate(value,excerpts)
    validator = VALIDATOR
    if trusted_business_draft:
        require(stage in ('ingest', 'clarify'), 'SCHEMA_INVALID', '业务草稿验证仅适用于整理和本行完善')
        from .business_draft import SCHEMA as BUSINESS_SCHEMA
        # This switch comes only from the server-owned request protocol. The
        # model cannot enable it with a response field. All other gates below
        # remain identical to the canonical protocol.
        spec = copy.deepcopy(SCHEMA)
        rows = BUSINESS_SCHEMA['properties']['requirements']
        spec['properties']['questions']['maxItems'] = rows['maxItems'] * rows['items']['properties']['questions']['maxItems']
        if stage == 'ingest':
            # Preserve ingest.validate's additional mandatory context contract.
            spec['required'] = [*spec['required'], 'product_context_proposal']
            context = spec['properties']['product_context_proposal']
            context['required'] = list(context['properties'])
        validator = jsonschema.Draft202012Validator(spec, registry=REGISTRY)
    errors = sorted(validator.iter_errors(value), key=lambda e: str(e.path))
    require(not errors, 'SCHEMA_INVALID', '模型响应不匹配 Schema：' + (describe_schema_error(errors) if errors else ''))
    require(value['stage'] == stage, 'SCHEMA_INVALID', '模型返回了错误阶段')
    require('benchmark_analysis' not in value or stage=='ingest', 'SCHEMA_INVALID', '对标分析只属于材料整理阶段')
    benchmark_sources=[s['id'] for s in project.get('sources',[]) if s.get('benchmark_reference') or s.get('origin')=='benchmark_intake']
    if benchmark_sources:
        from .benchmark import validate_proposal_roles
        validate_proposal_roles(value,benchmark_sources)
    if stage=='review':
        require(not value['proposals'] and not value['questions'] and 'product_context_proposal' not in value,
                'SEMANTIC_BLOCKED','语义审查只报告发现，不能创建候选、问题或替换产品上下文；请在澄清阶段处理修订')
    item_ids = {i['id'] for i in project['items']}
    detail_ref_kinds = {i['id']:i['kind'] for i in project['items']}
    detail_ref_kinds.update({i['temp_id']:i['kind'] for i in value['proposals']})
    question_ids = {q['id'] for q in project['questions']}
    temps = [i['temp_id'] for i in value['proposals']] + [q['temp_id'] for q in value['questions']] + [q['temp_id'] for parent in value['questions'] for q in parent.get('decision_points',[])]
    require(len(set(temps)) == len(temps), 'REFERENCE_INVALID', '临时 ID 重复')
    allowed = item_ids | question_ids | set(temps)
    if project.get('ui'):
        allowed |= {p['page_id'] for p in project['ui']['spec']['pages']}
    pairs = {(x['source_id'], x['id']) for x in excerpts}
    sources_by_excerpt = {}
    for source_id, excerpt_id in pairs:
        sources_by_excerpt.setdefault(excerpt_id, set()).add(source_id)
    invalid_source_refs = []
    for path, node in nodes_with_paths(value):
        if 'source_id' in node and 'excerpt_id' in node:
            pair = (node['source_id'], node['excerpt_id'])
            if pair not in pairs:
                diagnostic = (f'{json_path(path)}：source_id={node["source_id"]!r}，'
                              f'excerpt_id={node["excerpt_id"]!r}，不在本轮实际输入范围')
                candidates = sources_by_excerpt.get(node['excerpt_id'], set())
                if len(candidates) == 1:
                    source_id = next(iter(candidates))
                    diagnostic += (f'；此 excerpt_id 唯一对应本轮 source_id={source_id!r}、'
                                   f'excerpt_id={node["excerpt_id"]!r}，仅供修复参考')
                invalid_source_refs.append(diagnostic)
        for key in ('related_refs', 'ref_ids', 'canonical_refs', 'requirement_refs', 'proposed_item_refs', 'reviewed_refs', 'impacted_refs', 'unimpacted_refs', 'rule_ids', 'ac_ids'):
            if key in node:
                bad = sorted(set(node[key]) - allowed)
                require(not bad, 'REFERENCE_INVALID', '引用不存在或跨项目：' + key + '（' + '、'.join(bad[:5]) + (' 等' if len(bad) > 5 else '') + '）')
        for key in ('target_ref', 'target_item_id', 'requirement_id', 'item_id', 'scope_ref'):
            if node.get(key) is not None:
                require(node[key] in allowed, 'REFERENCE_INVALID', '未知关联：' + key + '（' + str(node[key]) + '）')
    if invalid_source_refs:
        limit = 5
        message = '来源引用不在本轮实际输入范围（最多列出 '+str(limit)+' 项）：' + '；'.join(invalid_source_refs[:limit])
        if len(invalid_source_refs) > limit:
            message += f'；另有 {len(invalid_source_refs)-limit} 项未展开'
        message += '。上述提示仅供修复参考，程序不会自动替换引用'
        require(False, 'REFERENCE_INVALID', message)
    for proposal in value['proposals']:
        if proposal.get('detail_contract') is not None:
            from .requirement_details import validate_detail
            from .detail_draft import validate_candidate_links
            require(proposal['kind']=='requirement','SCHEMA_INVALID','详细定义仅适用于需求条目。')
            validate_detail(proposal['detail_contract'],project)
            validate_candidate_links(proposal['detail_contract'],detail_ref_kinds,
                                     proposal.get('target_item_id') or proposal['temp_id'])
        require((proposal['action'] == 'add' and proposal['target_item_id'] is None) or
                (proposal['action'] == 'revise' and proposal['target_item_id'] in item_ids),
                'REFERENCE_INVALID', '修订目标必须存在；新增不能覆盖旧条目')
        if proposal['action']=='revise':
            target=next(i for i in project['items'] if i['id']==proposal['target_item_id'])
            require(proposal['kind']==target['kind'],'REFERENCE_INVALID',
                    proposal['temp_id']+' 修订目标 '+target['id']+' 实际 kind='+target['kind']+'；必须保持同 kind，不得改变目标、原文或新增替代修订')
    from .understanding import validate as validate_understanding
    validate_understanding(value,project,excerpts)
    from .semantic_quality import validate_analysis, validate_review
    validate_analysis(value,project,excerpts)
    from .result_conditions import validate_response_conditions
    validate_response_conditions(value,project,excerpts)
    from .source_fidelity import validate_response as validate_source_fidelity
    validate_source_fidelity(value,project,excerpts)
    if stage in ('ingest','clarify','change','prd'):
        from .semantic_quality import validate_current_workflow
        context=project.get('product_context',{})
        used={(r['source_id'],r['excerpt_id']) for r in value['used_source_refs']}
        existing='\n'.join([context.get('current_state','')]+[
            e['text'] for e in excerpts if (e['source_id'],e['id']) in used])
        validate_current_workflow(value['summary'],existing,'summary')
    if stage=='review':validate_review(value,project)
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
        for field in c['fields']:
            if 'filter_all_option' in field:
                require(spec['schema_version']=='1.1' and c['type']=='filters' and field['type']=='select'
                    and field['filter_all_option'] in field['options'], 'REFERENCE_INVALID',
                    f"组件 {c['component_id']} 字段 {field['name']}：filter_all_option 仅用于 1.1 filters/select 且必须是 options 中的值")
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
            require(c['type'] == 'button' and target and target['type'] in ('table', 'list'), 'REFERENCE_INVALID',
                    f"组件 {c['component_id']}：filter 只能放在 button，当前类型 {c['type']}；target_id={c['interaction']['target_id']} 必须指向 table/list。filters 只保留输入且 action=none，另设筛选 button。")
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
            if b['kind']=='detail':
                from .detail_rendering import detail_hash
                ref=b['ref_ids'][0]; item=items.get(ref)
                require(kind=='prd' and item is not None and item['kind']=='requirement'
                        and ref in {i['id'] for i in delivery_items(p)} and item.get('detail_contract'),
                        'REFERENCE_INVALID','详细定义只能绑定本期已选需求。')
                require(b['detail_hash']==detail_hash(item),'STALE_REVISION','详细定义与文档绑定版本不一致：'+ref)
            if b['kind']=='explanation':
                from .prd import verified_context, verified_business_context, plan_contract, normative_evidence, CLOUD_PRD_SECTIONS
                basis=b.get('evidence_refs',{})
                require(any(basis.values()),'REFERENCE_INVALID','解释性正文必须有已核对依据')
                require(set(basis.get('context_refs',[]))<=set(verified_context(p)),
                        'REFERENCE_INVALID','解释性正文引用未核对的产品上下文')
                require(set(basis.get('normative_refs',[]))<=set(plan_contract(p,kind)['normative_item_ids']),
                        'SEMANTIC_BLOCKED','解释性正文只能依赖本期已选规范')
                require(set(basis.get('business_claim_refs',[]))<=set(verified_business_context(p)),
                        'REFERENCE_INVALID','解释性正文引用未核对或未选中的业务背景')
                require(set(b['ref_ids'])==set(basis.get('normative_refs',[])),
                        'REFERENCE_INVALID','解释性正文的规范引用与依据不一致')
                from .semantic_quality import validate_explanation
                context=verified_context(p);business=verified_business_context(p)
                detail=kind=='prd' and s['title'] in CLOUD_PRD_SECTIONS.values()
                evidence='\n'.join([context[r] for r in basis.get('context_refs',[])]+[normative_evidence(items[r],include_behavior=detail) for r in basis.get('normative_refs',[])]+[business[r]['text'] for r in basis.get('business_claim_refs',[])])
                existing='\n'.join([context[r] for r in basis.get('context_refs',[]) if r=='current_state']+[business[r]['text'] for r in basis.get('business_claim_refs',[])])
                scenario='\n'.join([context[r] for r in basis.get('context_refs',[]) if r!='current_state']+[normative_evidence(items[r],include_behavior=detail) for r in basis.get('normative_refs',[])])
                validate_explanation(b['text'],evidence,existing,s['section_id'],scenario_evidence=scenario)
            if b['kind']=='narrative' and not s['title'].startswith(('历史分析提示','附录：条目身份与历史讨论','附录：未采纳与历史讨论')):
                from .prd import verified_context, verified_business_context
                from .semantic_quality import validate_current_workflow
                existing='\n'.join([verified_context(p).get('current_state','')]+[c['text'] for c in verified_business_context(p).values()])
                validate_current_workflow(b['text'],existing,s['section_id'])
            if b['kind'] in ('requirement', 'rule', 'acceptance'):
                unselected = sorted(r for r in b['ref_ids'] if r not in {i['id'] for i in delivery_items(p)})
                require(not unselected, 'SEMANTIC_BLOCKED', '规范内容只能引用本期已选目标条目（' + '、'.join(unselected[:5]) + (' 等' if len(unselected) > 5 else '') + ' 未已选）')
                mismatched = sorted(r for r in b['ref_ids'] if r in items and items[r]['kind'] != b['kind'])
                require(not mismatched, 'REFERENCE_INVALID', '规范段落与条目类型不一致（' + '、'.join(mismatched[:5]) + (' 等' if len(mismatched) > 5 else '') + '）')


def document_gaps(doc, p):
    from .prd import cloud_prd_gaps
    prof = profile(doc['document_type'],doc['content_profile_id'].startswith('builtin-'))
    functions = [i['id'] for i in delivery_items(p) if i['kind'] == 'requirement']
    actual = {(m['profile_section_id'], m['scope_ref']) for m in doc['reference_mapping']}
    gaps = cloud_prd_gaps(doc,p)
    if doc['document_type']=='prd':
        from .requirement_details import detail_issues
        from .detail_rendering import detail_hash, GROUP_TITLES
        for item in delivery_items(p):
            if item['kind']!='requirement':
                continue
            gaps.extend(issue['message'] for issue in detail_issues(item,p,require_confirmed=True))
            if not item.get('detail_contract'):
                continue
            groups={b['detail_group'] for s in doc['sections'] for b in s['blocks']
                    if b['kind']=='detail' and b['ref_ids']==[item['id']]
                    and b.get('detail_hash')==detail_hash(item)}
            for group in GROUP_TITLES.keys()-groups:
                gaps.append(item['id']+' 的'+GROUP_TITLES[group]+'未绑定当前详细定义，请重新生成文档。')
    for d in prof['sections']:
        if d['mapping_required']:
            for scope in (functions or [None]) if d['repeat_per_function'] else [None]:
                if (d['id'], scope) not in actual:
                    gaps.append('参考维度未处置：' + d['id'] + (' / ' + scope if scope else ''))
    used = {r for s in doc['sections'] for b in s['blocks'] if b['kind'] in ('requirement', 'rule', 'acceptance') for r in b['ref_ids']}
    for i in delivery_items(p):
        if i['selection_status'] == 'selected' and i['applies_to'] == 'to_be' and i['kind'] in ('requirement', 'rule', 'acceptance') and i['id'] not in used:
            gaps.append('未成文：' + i['id'])
    return gaps


def review_target(p):
    from .semantic_quality import VERSION
    target={'brief': brief_hash(p), 'documents': p['documents'], 'ui': p['ui'], 'semantic_quality_version':VERSION}
    if p.get('requirement_relations'):target['requirement_relations']=p['requirement_relations']
    return digest(target)


def gate_blockers(p):
    from .product_flow import status, question_open
    from .source_fidelity import project_issues, DIMENSIONS
    rows = []
    def add(code, title, message, actions, detail=None):
        row = dict(key=code+':'+str(len(rows)), code=code, title=title,
                   message=message, actions=actions)
        if detail is not None: row['detail'] = detail
        rows.append(row)
    def action(kind, label, **target):
        return dict(kind=kind, label=label, **target)
    from .prewrite import formal_issues
    for issue in formal_issues(p):
        add('prewrite_required','成文一致性检查',issue['message'],
            [action('stage','处理成文检查',step=3)])
    for finding in project_issues(p):
        message = finding['location']+': '+DIMENSIONS[finding['dimension']]+' / '+finding['quote']
        add('source_fidelity', '核对来源忠实度', message, [action('source', '核对参考资料')])
    from .requirement_details import detail_issues
    for item in delivery_items(p):
        for issue in detail_issues(item,p,require_confirmed=True):
            add('detail_contract', '核对详细需求', issue['message'],
                [action('requirement','完善详细需求',item_id=item['id'])],
                detail=issue['path']+'：'+issue['message'])
    steps=status(p)
    first=next((s for s in steps[:5] if not s['retired'] and not s['complete']),None)
    if first:
        add('stage_incomplete', first['title'], '请完成第'+str(first['display_step'])+'步「'+first['title']+'」核对',
            [action('stage', '前往本步骤核对', step=first['step'])])
        by_message = {row['message']: row for row in first.get('blockers', [])}
        for message in first['missing']:
            existing = by_message.get(message)
            if existing:
                rows.append(copy.deepcopy(existing))
            else:
                add('stage_missing', first['title'], message,
                    [action('stage', '前往本步骤核对', step=first['step'])])
    for kind in p.get('delivery_scope',{}).get('documents',['mrd','prd']):
        if kind not in p['documents']:
            add('document_missing', kind.upper()+' 文档', '缺少 '+kind.upper()+' 评审稿',
                [action('document', '生成 '+kind.upper()+' 评审稿', document=kind)])
    if 'prd' not in p['documents']:
        add('prd_content_missing', 'PRD 内容对象', '缺少 PRD 内容对象；MRD 不能代替 PRD 确认',
            [action('document', '生成 PRD 评审稿', document='prd')])
        return rows
    for kind, doc in p['documents'].items():
        if doc['brief_hash'] != brief_hash(p):
            add('document_stale', kind.upper()+' 文档', '文档与当前底稿不一致，请重新生成',
                [action('document', '更新 '+kind.upper()+' 评审稿', document=kind)])
        for message in document_gaps(doc['content'], p):
            add('document_gap', kind.upper()+' 文档', message,
                [action('document', '核对 '+kind.upper()+' 评审稿', document=kind)])
    if p.get('document_update_needed'):
        stale=p.get('stale_document_kinds',[])
        add('document_update_needed', '文档更新', '页面变化后文档需要更新：'+'、'.join(stale),
            [action('document', '更新 '+kind.upper()+' 评审稿', document=kind) for kind in stale] or
            [action('document', '核对 PRD 评审稿', document='prd')])
    for q in p['questions']:
        if q['blocking'] and question_open(q):
            add('question_open', '待回答的问题', q['question'],
                [action('question', '回答问题', question_id=q['id'])])
    review = p.get('review')
    if not review or review['target_hash'] != review_target(p):
        add('review_missing', '当前版本内容审查', '需要审查当前底稿和文档',
            [action('review', '查看当前版本审查')])
    else:
        result=review['response']['result']
        if result['assessment'] != 'ready_for_human_review' or any(f['severity'] == 'blocker' for f in review['response']['findings']):
            add('review_blocked', '当前版本内容审查', '语义审查仍有阻塞项',
                [action('review', '查看审查意见')])
        if result.get('required_decisions'):
            add('review_decisions', '当前版本内容审查', '语义审查仍有必要决定待产品处理：'+'；'.join(result['required_decisions']),
                [action('review', '查看审查意见')])
        reviewed=set(result.get('reviewed_refs',[]))
        uncovered=[i['id'] for i in delivery_items(p) if i['kind'] in ('requirement','rule','acceptance') and i['id'] not in reviewed]
        if uncovered or not result.get('perspectives'):
            add('review_coverage', '当前版本内容审查', '语义审查覆盖不足：'+('未审查规范条目 '+ '、'.join(uncovered) if uncovered else '缺少审查视角'),
                [action('review', '查看审查意见')])
    if not any(i['selection_status'] == 'selected' and i['applies_to']=='to_be' and i['kind'] == 'requirement' for i in p['items']):
        add('scope_empty', '本期需求范围', '本期没有已选需求', [action('scope', '核对本期需求')])
    if not any(i['selection_status'] == 'selected' and i['applies_to']=='to_be' and i['kind'] == 'acceptance' for i in p['items']):
        add('acceptance_empty', '本期验收条件', '缺少已选验收条件',
            [action('add_acceptance', '补充关联验收条件')])
    selected=delivery_items(p)
    for req in (i for i in selected if i['kind']=='requirement'):
        if not any(ac['kind']=='acceptance' and (req['id'] in ac['related_refs'] or ac['id'] in req['related_refs']) for ac in selected):
            candidates=[i for i in p['items'] if i['kind']=='acceptance' and i['selection_status']=='candidate'
                        and i['applies_to']=='to_be' and
                        (req['id'] in i['related_refs'] or i['id'] in req['related_refs'])]
            kind='acceptance' if candidates else 'add_acceptance'
            label='核对关联验收候选' if candidates else '补充关联验收条件'
            add('requirement_acceptance_missing', req.get('title') or req['id'],
                '需求缺少关联验收条件：'+req['id'], [action(kind, label, item_id=req['id'])])
    unique=[];seen={}
    for row in rows:
        if row['message'] not in seen:
            unique.append(row);seen[row['message']]=row
        else:
            for action in row['actions']:
                if action not in seen[row['message']]['actions']:
                    seen[row['message']]['actions'].append(action)
    return unique


def gate(p):
    return [row['message'] for row in gate_blockers(p)]
