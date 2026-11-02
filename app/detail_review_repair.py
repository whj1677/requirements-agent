"""Finite review-entry replacements. Diagnostics never constitute acceptance.

Only proven protocol defects authorize a slot. The unchanged complete semantic
validator remains mandatory after assembly; no candidate or verdict is repaired
by this module.
"""
import copy
import json
import jsonschema
from .core import digest, dumps, require
from . import detail_semantics as semantics

PROTOCOL='detail-review-entry-patch-1'
GROUPS=('checks','source_checks','state_checks')


def identity(group,check):
    return ({'scope':check['scope'],'dimension':check['dimension']} if group=='checks' else
            {'key':check['key']} if group=='source_checks' else {'transition_id':check['transition_id']})


def prepare(plan,response):
    """Return a frozen repair packet, or None when identity/shape is ambiguous.

    The diagnostic predicates below mirror strict technical rejection conditions.
    A missed condition cannot approve anything: apply() invokes full validate().
    """
    if plan.get('version')!=semantics.POOL_VERSION:return None
    unknown_scope=semantics.checked_unknown_scope(plan)
    if (plan.get('quote_pool')!=semantics.quote_pool(plan) or
        plan.get('quote_pool_hash')!=digest(plan['quote_pool'])):return None
    if not isinstance(response,dict) or set(response)!={'rows','quote_pool_hash'} or response['quote_pool_hash']!=plan['quote_pool_hash']:return None
    if not isinstance(response['rows'],list) or len(response['rows'])!=len(plan['rows']):return None
    origins={r['row_key']:r for r in plan['rows']}
    if any(not isinstance(r,dict) or not isinstance(r.get('row_key'),str) for r in response['rows']):return None
    if len({r['row_key'] for r in response['rows']})!=len(origins) or {r['row_key'] for r in response['rows']}!=set(origins):return None
    pool={q['key']:q for q in plan['quote_pool']};slots=[]
    for ri,row in enumerate(response['rows']):
        origin=origins[row['row_key']]
        if set(row)!={'row_key','candidate_hash',*GROUPS} or row['candidate_hash']!=origin['candidate_hash'] or origin['candidate_hash']!=digest(origin['detail_contract']):return None
        expected={'checks':{(c['scope'],c['dimension']) for c in origin['required_checks']},
                  'source_checks':{c['key'] for c in origin['source_claims']},
                  'state_checks':{c['id'] for c in origin['detail_contract']['state_transitions']}}
        row_schema=semantics.review_schema(dict(plan,rows=[origin]))['properties']['rows']['items']
        for group in GROUPS:
            if not isinstance(row[group],list):return None
            try:
                keys=[tuple(identity(group,c).values()) if group=='checks' else next(iter(identity(group,c).values())) for c in row[group]]
                if len(keys)!=len(expected[group]) or set(keys)!=expected[group]:return None
            except (KeyError,TypeError,AttributeError):return None
        detail=origin['detail_contract'];refs=origin['property_refs'];claims={c['key']:c for c in origin['source_claims']}
        edges={e['id']:e for e in detail['flow']['edges']};nodes={n['id']:n for n in detail['flow']['nodes']}
        from .detail_recovery import facts,trace_witness
        entries={i['id']:semantics.entry_nodes(detail,i['id']) for i in detail['interactions']}
        structure=facts(detail,entries);by_slot={};boundaries={};coverage=[]
        def report(group,ci,code,message):
            slot=by_slot.setdefault((group,ci),dict(row_index=ri,row_key=row['row_key'],candidate_hash=row['candidate_hash'],
                group=group,index=ci,identity=identity(group,row[group][ci]),original_hash=digest(row[group][ci]),errors=[]))
            item=dict(code=code,message=message)
            if item not in slot['errors']:slot['errors'].append(item)
        for group in GROUPS:
            for ci,check in enumerate(row[group]):
                add=lambda code,message:report(group,ci,code,message)
                schema=row_schema['properties'][group]['items']
                errors=list(jsonschema.Draft202012Validator(schema).iter_errors(check))
                if errors:
                    for error in errors:add('schema',dumps(list(error.absolute_path))+': '+error.message)
                    continue
                if group=='checks' and plan.get('default_scope_review'):
                    from .detail_business_scope import scope_errors
                    for message in scope_errors(plan,origin,check):add('default_scope_binding',message)
                if group=='checks':
                    missing=semantics.unknown_scope_missing(unknown_scope,row['row_key'],check)
                    if missing:add('unknown_scope_coverage','全局field_conditions遗漏冻结unknown属性：'+dumps(missing))
                keys=check.get('candidate_keys',[check.get('key')]);affected=check['affected_keys']
                verdict=check.get('verdict') or next(v for v in ('contradicted','unsupported','unclear','supported') if v in check['verdicts'].values())
                scope=check.get('scope','source:'+check['key'] if group=='source_checks' else 'state:'+check.get('transition_id',''))
                dimension=check.get('dimension','source_support' if group=='source_checks' else 'state_identity')
                selected=[];human=[]
                for field,kind in (('source_quote_keys','source'),('human_quote_keys','human')):
                    for key in check.get(field,[]):
                        q=pool.get(key)
                        if not q or q['kind']!=kind:add('quote_reference',field+' 引文不存在或材料类型不匹配：'+key);continue
                        if kind=='human' and q['material']['row_key'] not in (None,row['row_key']):add('human_owner','人工作答引用不属于当前需求行：'+key)
                        (selected if kind=='source' else human).append(q)
                if (verdict=='supported' and affected) or (verdict!='supported' and not affected):add('affected_keys','通过项不得授权改写，不通过项须绑定纠正位置。')
                paths=[refs[k]['path'] for k in keys]
                if group=='source_checks':
                    if not set(affected)<={check['key']}:add('affected_keys','来源属性检查不能授权改写另一属性。')
                    own={(r['source_id'],r['excerpt_id']) for r in claims[check['key']]['source_refs']}
                    if verdict=='supported' and not any((q['material']['source_id'],q['material']['excerpt_id']) in own for q in selected):
                        add('source_binding','来源属性通过结论未引用自身绑定来源：'+check['key']+' '+refs[check['key']]['path'])
                if group=='state_checks':
                    prefix='detail_contract.state_transitions.'+check['transition_id']+'.'
                    if not any(p.startswith(prefix) for p in paths):add('state_owner','状态检查未引用指定转换的真实属性。')
                    if any(refs[k]['path'].startswith('detail_contract.state_transitions.') and not refs[k]['path'].startswith(prefix) for k in affected):add('state_owner','状态检查不能授权改写另一转换。')
                if group=='checks' and scope!='detail':
                    bound={n for n in nodes if nodes[n].get('interaction_id')==scope}
                    bound_edges={e for e in edges if edges[e]['from'] in bound or edges[e]['to'] in bound}
                    if not any(p.startswith('detail_contract.interactions.'+scope+'.') or any(p.startswith('detail_contract.flow.nodes.'+n+'.') for n in bound) or any(p.startswith('detail_contract.flow.edges.'+e+'.') for e in bound_edges) or dimension=='state_identity' and p.startswith('detail_contract.state_transitions.') for p in paths):add('operation_owner','逐操作检查须引用本操作实际属性。')
                if dimension=='coverage' and not {'detail_contract.applicability.'+s+'.applicable' for s in detail['applicability']}<=set(paths):add('coverage_properties','覆盖核对缺少实际适用性声明。')
                if verdict in ('supported','contradicted') and not (selected or human):add('evidence','支持或冲突结论必须附来源或当前人工作答引文。')
                traces=check.get('traces',[])
                if dimension in semantics.RECOVERY_DIMENSIONS and verdict=='supported' and not any(t['edge_ids'] or t['kind']=='not_applicable' and t['explanation'].strip() for t in traces):add('recovery_trace','恢复结果通过项缺少路径或有依据的不适用说明。')
                for ti,trace in enumerate(traces):
                    ids=trace['edge_ids'];label='trace_index='+str(ti)+' '
                    missing=[e for e in ids if e not in edges]
                    if missing:add('trace_missing',label+'路径引用不存在的边：'+dumps(missing));continue
                    disconnected=[(a,b) for a,b in zip(ids,ids[1:]) if edges[a]['to']!=edges[b]['from']]
                    if disconnected:add('trace_disconnected',label+'不连续边对：'+dumps(disconnected));continue
                    missing_path=(not ids and trace['kind'] in ('exception','unknown') and verdict!='supported' and bool(selected or human) and bool(affected) and bool(trace['explanation'].strip()))
                    if not ids and trace['kind']!='not_applicable' and not missing_path:add('trace_empty',label+'适用路径缺少连线或来源绑定的缺失路径反例。')
                    recovery=False
                    if ids and verdict=='supported':
                        visited={n for e in ids for n in (edges[e]['from'],edges[e]['to'])}
                        scoped_entries=entries.get(scope,set())
                        proof=trace_witness(detail,entries,structure,scope,edges[ids[0]]['from'],edges[ids[-1]]['to'],dimension in semantics.RECOVERY_DIMENSIONS)
                        recovery=bool(proof.get('recovery_premise'))
                        entry_ok=(bool(visited&scoped_entries) if scoped_entries else nodes[edges[ids[0]]['from']].get('kind')=='start') or recovery
                        if not entry_ok:add('trace_entry',label+'通过路径缺少真实入口或本操作等待前提。')
                        terminal=nodes[edges[ids[-1]]['to']].get('kind') in ('end','wait') or bool(proof.get('interaction_boundary'))
                        if not terminal:
                            if scope=='detail' and dimension=='coverage':coverage.append((group,ci,tuple(ids),label))
                            else:add('trace_terminal',label+'通过路径未到结果终点或本操作边界。')
                        if proof.get('interaction_boundary') and dimension in ('operation_results','actor_paths'):boundaries[tuple(ids)]=True
                    if ids and scope!='detail' and trace['kind']=='normal':
                        associated={n for n in nodes if nodes[n].get('interaction_id')==scope}
                        if associated and not recovery and not associated&{n for e in ids for n in (edges[e]['from'],edges[e]['to'])}:add('normal_owner',label+'正常路径未经过本操作绑定节点。')
        for group,ci,ids,label in coverage:
            if ids not in boundaries:report(group,ci,'coverage_boundary',label+'全局覆盖路径缺少操作检查已验证的相同边界路径。')
        if detail['applicability']['flow']['applicable']:
            for interaction in detail['interactions']:
                scoped=[(ci,c) for ci,c in enumerate(row['checks']) if c['scope']==interaction['id'] and c['dimension'] in ('operation_results','actor_paths')]
                # Skip malformed peers: those already have exact schema errors.
                if scoped and all(not list(jsonschema.Draft202012Validator(row_schema['properties']['checks']['items']).iter_errors(c)) for _,c in scoped):
                    if all(c['verdict']=='supported' for _,c in scoped) and not any(t['kind']=='normal' for _,c in scoped for t in c['traces']):
                        for ci,_ in scoped:report('checks',ci,'normal_coverage','同一操作的结果与角色检查均通过，却未提供正常路径；联合核对这两条，不借用另一操作。')
        slots.extend(by_slot.values())
    if not slots:return None
    return dict(protocol=PROTOCOL,plan_hash=digest(plan),review_hash=digest(response),quote_pool_hash=plan['quote_pool_hash'],
        original=copy.deepcopy(response),slots=slots)


def schema(plan,packet,*,wire=False):
    variants=[]
    for slot in packet['slots']:
        origin=next(r for r in plan['rows'] if r['row_key']==slot['row_key'])
        item=copy.deepcopy(semantics.review_schema(dict(plan,rows=[origin]))['properties']['rows']['items']['properties'][slot['group']]['items'])
        if wire:item=semantics.wire_item(plan,origin,slot['group'],item)
        for key,value in slot['identity'].items():item['properties'][key]={'const':value}
        variants.append(semantics.obj(dict(slot_id={'const':slot_id(slot)},original_hash={'const':slot['original_hash']},replacement=item)))
    return semantics.obj(dict(protocol={'const':PROTOCOL},plan_hash={'const':packet['plan_hash']},review_hash={'const':packet['review_hash']},
        replacements=dict(type='array',items={'oneOf':variants},minItems=len(variants),maxItems=len(variants),uniqueItems=True)))


def slot_id(slot):
    return str(slot['row_index'])+':'+slot['group']+':'+str(slot['index'])


def messages(plan,packet,*,transport_protocol=None):
    base=semantics.messages(plan)
    instruction=('仅修复审查协议错误，候选、来源与人工上下文冻结。仅返回授权slot的完整replacement，保留条目身份。'
        '依据全部冻结材料独立核对错误；不得为了过关强制supported。若候选自身引错来源，可判unsupported并给精确纠正位置，留给后续业务纠正。'
        '不能改候选、补造来源、拼边或将互斥出边串成一路；路径/结论均须由你重新核对。其他合法审查条目由程序保持。'
        '无递归修复，合并后仍执行原完整严格校验。只返回Schema JSON。\n可信任务头：')
    header=dict(stage='review',response_protocol=PROTOCOL,schema=schema(plan,packet,wire=True))
    if transport_protocol is not None:
        require(transport_protocol=='responses-json-schema-v1','DETAIL_REVIEW_TRANSPORT_INVALID','不支持的详细审查传输协议。')
        header.update(transport_protocol=transport_protocol,strict_json_protocol='detail-review-strict-json-1')
    return [dict(role='system',content=instruction+dumps(header)),
        dict(role='user',content=[dict(type='text',text=dumps(dict(frozen_review_context=json.loads(base[1]['content'][0]['text']),
            original_review=packet['original'],repair_packet={k:v for k,v in packet.items() if k!='original'},
            review_rules=base[0]['content'].split('可信任务头：',1)[0])))])]


def apply(plan,packet,patch,*,assembly=None):
    require(packet['plan_hash']==digest(plan) and packet['review_hash']==digest(packet['original']),
        'DETAIL_REVIEW_INVALID','审查修复冻结输入已变化。')
    require(packet==prepare(plan,packet['original']),'DETAIL_REVIEW_INVALID','审查修复授权条目或诊断已变化。')
    if semantics.checked_encoding(plan) is not None:
        original=copy.deepcopy(patch);changes=[]
        require(isinstance(patch,dict) and patch.get('protocol')==PROTOCOL and patch.get('plan_hash')==packet['plan_hash']
            and patch.get('review_hash')==packet['review_hash'] and isinstance(patch.get('replacements'),list),
            'DETAIL_REVIEW_INVALID','编码补丁冻结身份不匹配。')
        slots={slot_id(s):s for s in packet['slots']};patch=copy.deepcopy(patch)
        require(len(patch['replacements'])==len(slots),'DETAIL_REVIEW_INVALID','编码补丁条目数不匹配。')
        seen=set()
        for index,replacement in enumerate(patch['replacements']):
            require(isinstance(replacement,dict) and isinstance(replacement.get('slot_id'),str)
                and replacement['slot_id'] in slots and replacement['slot_id'] not in seen,'DETAIL_REVIEW_INVALID','编码补丁slot无效。')
            slot=slots[replacement['slot_id']];seen.add(replacement['slot_id'])
            require(replacement.get('original_hash')==slot['original_hash'] and isinstance(replacement.get('replacement'),dict)
                and all(replacement['replacement'].get(k)==v for k,v in slot['identity'].items()),'DETAIL_REVIEW_INVALID','编码补丁条目身份不匹配。')
            replacement['replacement']=semantics.decode_property_entry(plan,slot['row_key'],replacement['replacement'],
                ['replacements',index,'replacement'],changes)
        if assembly is not None:assembly.update(property_reference_decoding=dict(protocol=semantics.REFERENCE_ENCODING,
            encoding_hash=plan['reference_encoding_hash'],input_hash=digest(original),canonical_hash=digest(patch),changes=changes),
            canonical_detail_review_patch=copy.deepcopy(patch))
    require(not list(jsonschema.Draft202012Validator(schema(plan,packet)).iter_errors(patch)),
        'DETAIL_REVIEW_INVALID','审查条目修复结构、身份或冻结哈希不匹配。')
    replacements={item['slot_id']:item for item in patch['replacements']}
    require(len(replacements)==len(packet['slots']) and set(replacements)=={slot_id(s) for s in packet['slots']},
        'DETAIL_REVIEW_INVALID','审查修复重复或遗漏授权条目。')
    merged=copy.deepcopy(packet['original']);changed=set();audit=[]
    for slot in packet['slots']:
        current=merged['rows'][slot['row_index']][slot['group']][slot['index']]
        require(digest(current)==slot['original_hash'],'DETAIL_REVIEW_INVALID','待替换审查条目已变化。')
        replacement=replacements[slot_id(slot)]['replacement']
        merged['rows'][slot['row_index']][slot['group']][slot['index']]=copy.deepcopy(replacement)
        changed.add((slot['row_index'],slot['group'],slot['index']))
        audit.append(dict(slot_id=slot_id(slot),original_hash=slot['original_hash'],replacement_hash=digest(replacement)))
    preserved=[]
    for ri,row in enumerate(packet['original']['rows']):
        for group in GROUPS:
            for ci,check in enumerate(row[group]):
                if (ri,group,ci) not in changed:
                    require(check==merged['rows'][ri][group][ci],'DETAIL_REVIEW_INVALID','未授权的合法审查条目发生变化。')
                    preserved.append(dict(slot_id=f'{ri}:{group}:{ci}',hash=digest(check)))
    audit=dict(protocol=PROTOCOL,plan_hash=packet['plan_hash'],original_review_hash=packet['review_hash'],
        merged_review_hash=digest(merged),replacements=audit,preserved_slots=preserved,full_strict_validation='pending')
    if assembly is not None:assembly.update(merged_detail_review=copy.deepcopy(merged),review_entry_repair=audit)
    result=semantics.validate(plan,merged)
    audit['full_strict_validation']='passed'
    return merged,result,audit
