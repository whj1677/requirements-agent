"""One bounded model correction for uniquely located local binding defects.

The unknown-marked probe is local validation only; it is never a candidate,
review plan, model input, or persisted business value.
"""
import copy
import re
import jsonschema
from .core import Problem,digest
from .detail_draft import model_schema
from .requirement_details import _objects,technical_issues


def _accepted_keys(report,canonical):
    """Check object integrity, not unknown business values or PM confirmation.

    adapt checks source/item bindings; this additionally covers local object
    references which ordinary draft acceptance deliberately leaves editable.
    No project is passed: same-response temporary acceptance IDs are legal.
    """
    proposals={r['temp_id']:r for r in canonical.get('proposals',[])}
    accepted=set()
    for row in report.get('accepted',[]):
        candidate=proposals.get(row.get('candidate_temp_id'),{})
        if row.get('field_rejected') or not isinstance(candidate.get('detail_contract'),dict):continue
        if technical_issues(candidate):continue
        accepted.add(row['row_key'])
    return accepted


def prepare(raw, report, project, excerpts, stage, scope=None, *, draft_request=None, graph_kind_protocol=None):
    from .business_draft import adapt,_input,_bind_detail_references,SCHEMA
    if not isinstance(raw,dict) or not isinstance(raw.get('requirements'),list):return None
    keys=[r.get('row_key') for r in raw['requirements'] if isinstance(r,dict)]
    rejected={r.get('row_key'):r.get('code') for r in report.get('rejected',[])
        if r.get('code') in ('BUSINESS_DRAFT_SOURCE','BUSINESS_DRAFT_DETAIL_REFERENCE')}
    graph_targets={}
    if graph_kind_protocol=='detail-local-graph-kind-1':
        for rejection in report.get('rejected',[]):
            issues=rejection.get('issues') or []
            if rejection.get('code')!='BUSINESS_DRAFT_DETAIL_FLOW' or len(issues)!=1:continue
            issue=issues[0]
            if issue.get('code')!='DETAIL_FLOW_BRANCH':continue
            match=re.fullmatch(r'detail_contract\.flow\.nodes\.([A-Za-z0-9_-]{1,80})',issue.get('path',''))
            if not match:continue
            key=rejection.get('row_key')
            if sum(r.get('row_key')==key for r in report.get('rejected',[]))!=1:continue
            if any(r.get('row_key')==key for r in report.get('field_rejected',[])):continue
            rejected[key]='BUSINESS_DRAFT_DETAIL_FLOW';graph_targets[key]=dict(node_id=match[1],issue=copy.deepcopy(issue))
    context=None
    outcomes={};probe=copy.deepcopy(raw)
    for row in probe['requirements']:
        key=row.get('row_key') if isinstance(row,dict) else None
        if key not in rejected or keys.count(key)!=1:continue
        detail=row.get('detail_contract')
        shape_view=detail;shape_normalizations=[]
        if key in graph_targets:
            from .detail_draft import normalize_redundant_metadata
            shape_view,shape_normalizations=normalize_redundant_metadata(detail)
        if list(jsonschema.Draft202012Validator(model_schema()).iter_errors(shape_view)):continue
        objects=list(_objects(detail));ids=[item['id'] for _,item in objects]
        if len(ids)!=len(set(ids)):continue
        findings=[]
        if not isinstance(row.get('acceptance'),list):continue
        if context is None:
            try:
                context=_input(project,excerpts,'',scope)
                _bind_detail_references({'schema':copy.deepcopy(SCHEMA)},context,project)
            except Problem:return None
        # Use the same request-wide dynamic whitelist sent to the model. A
        # whole-table request may expose legitimate shared clauses; a scoped
        # request must not borrow aliases hidden in another row/project area.
        known=[ref for ref in context['detail_reference_options']['acceptance_refs']
            if not re.fullmatch(r'A[1-9][0-9]*',ref)]
        local=['A'+str(n) for n,part in enumerate(row['acceptance'],1) if isinstance(part,dict)
            and part.get('basis') in ('fact','recommendation') and isinstance(part.get('text'),str) and part['text'].strip()]
        allowed=list(dict.fromkeys(known+local))
        reference_scope=dict(allowed_refs=allowed,origin='actual_dynamic_whitelist_and_effective_row_acceptance')
        if rejected[key]=='BUSINESS_DRAFT_DETAIL_FLOW':
            target=graph_targets[key];nodes=detail['flow']['nodes'];edges=detail['flow']['edges']
            node=next((n for n in nodes if n['id']==target['node_id']),None)
            outgoing=[e for e in edges if e.get('from')==target['node_id']]
            node_ids={n['id'] for n in nodes}
            if (not node or node.get('kind')!='decision' or len(outgoing)!=1 or
                outgoing[0].get('to') not in node_ids or
                any(n.get('kind') not in ('start','action','decision','wait','end') for n in nodes)):continue
            if any(ref not in allowed for _,item in objects for ref in item.get('acceptance_refs',[])):continue
            dynamic_refs={name:(allowed if name=='acceptance_refs' else context['detail_reference_options'][name])
                for name in ('depends_on','rule_refs','acceptance_refs')}
            if (any(ref not in dynamic_refs['depends_on'] for ref in detail.get('depends_on',[])) or
                any(ref not in dynamic_refs[name] for _,item in objects for name in ('rule_refs','acceptance_refs') for ref in item.get(name,[]))):continue
            reference_scope['dynamic_reference_options']=copy.deepcopy(dynamic_refs)
            path='detail_contract.flow.nodes.'+node['id']+'.kind'
            frozen=dict(raw_response_hash=digest(raw),candidate_hash=digest(detail),node_id=node['id'],original_kind='decision',kind_path=path,
                incoming_edge_ids=[e['id'] for e in edges if e.get('to')==node['id']],
                outgoing_edge_ids=[e['id'] for e in outgoing],reference_scope_hash=digest(reference_scope))
            finding=dict(scope='detail',dimension='local_graph_kind_single_branch',verdict='unbound',affected_paths=[path],traces=[],
                graph_kind_binding=frozen,graph_kind_binding_hash=digest(frozen),
                explanation='原严格校验仅定位该decision恰好一条出边。仅允许模型核对节点类型表达，不代表业务语义已通过。',
                correction='独立对照冻结来源和原图，只可纠正该节点kind及evidence.kind；其它属性、节点、连线、角色、来源与验收只读。不得为过校验强选类型、补分支或改变业务；若需更大改动保留原值，交由失败诊断。')
            # A private empty-kind diagnostic probe only removes the known
            # decision branch predicate. It does not choose a replacement kind
            # and is never returned, sent to the model, or saved as a candidate.
            node['kind']=''
            outcomes[key]=dict(status='failed',findings=[finding],reference_scope=reference_scope,
                qualification_probe=dict(mode='private_empty_kind_diagnostic',node_id=node['id'],
                    original_candidate_hash=frozen['candidate_hash'],excluded_gate=copy.deepcopy(target['issue']),
                    shape_view_hash=digest(shape_view),existing_shape_normalizations=copy.deepcopy(shape_normalizations),
                    notice='仅在内存以空kind诊断其余守卫；不是原候选通过，也不是模型修复答案。'))
            continue
        if rejected[key]=='BUSINESS_DRAFT_DETAIL_REFERENCE':
            current=next((r for r in context['current_rows'] if r['row_key']==key),None)
            if current is None:continue
            # Prove the entire row's existing acceptance references are in its
            # visible scope, not merely the one property selected for a patch.
            # canonical adaptation permits project-wide same-kind IDs; that is
            # not authority to repair a candidate with an unrelated hidden C#.
            if any(ref not in allowed and not (re.fullmatch(r'A[1-9][0-9]*',ref) and ref not in local)
                   for _,item in objects for ref in item.get('acceptance_refs',[])):continue
            for section,item in objects:
                refs=item.get('acceptance_refs',[])
                invalid=[ref for ref in refs if re.fullmatch(r'A[1-9][0-9]*',ref) and ref not in local]
                if not invalid:continue
                path='detail_contract.'+section+'.'+item['id']+'.acceptance_refs'
                kept=[ref for ref in refs if ref not in invalid]
                findings.append(dict(scope='detail',dimension='local_acceptance_reference_missing',verdict='unbound',
                    affected_paths=[path],traces=[],original_refs=copy.deepcopy(refs),invalid_refs=invalid,
                    allowed_refs=allowed,preserved_refs=kept,
                    explanation='引用的本行A编号没有有效验收实体；只核对技术绑定，不判定业务来源已支持。',
                    correction='保留已有有效引用；依据本轮材料仅移除无实体编号或明确选择合法引用替换，不新增验收或修改正文、对象及属性依据。'))
                item['acceptance_refs']=kept
            if findings:outcomes[key]=dict(status='failed',findings=findings,reference_scope=reference_scope)
            continue
        if any(ref not in allowed for _,item in objects for ref in item.get('acceptance_refs',[])):continue
        for section,item in objects:
            for attribute,proof in item.get('evidence',{}).items():
                if attribute not in item or attribute in ('id','evidence'):continue
                if proof.get('status')!='source' or proof.get('sources') not in (None,[]):continue
                path='detail_contract.'+section+'.'+item['id']+'.'+attribute
                findings.append(dict(scope='detail',dimension='source_binding_missing',verdict='unbound',
                    affected_paths=[path],traces=[],
                    explanation='该属性标为来源事实，但没有绑定来源；这不是业务含义不支持的结论。',
                    correction='依据本轮材料明确修正此属性及其依据；无可靠来源时如实保留建议或未知，不补造来源。'))
                proof['status']='unknown'
        if findings:outcomes[key]=dict(status='failed',findings=findings,reference_scope=reference_scope)
    if not outcomes:return None
    try:canonical,checked=adapt(probe,project,excerpts,stage,scope,draft_request=draft_request)
    except Problem:return None
    eligible=_accepted_keys(checked,canonical)
    outcomes={key:value for key,value in outcomes.items() if key in eligible}
    return outcomes or None


def accepted(report, canonical, outcomes):
    if not set(outcomes)<=_accepted_keys(report,canonical):return False
    rows={row['row_key']:row for row in report.get('accepted',[])}
    proposals={row['temp_id']:row for row in canonical.get('proposals',[])}
    for key,outcome in outcomes.items():
        local=[f for f in outcome.get('findings',[]) if f['dimension']=='local_graph_kind_single_branch']
        if local:
            details=[rows[key].get('raw',{}).get('detail_contract'),
                proposals.get(rows[key].get('candidate_temp_id'),{}).get('detail_contract')]
            for finding in local:
                nid=finding['graph_kind_binding']['node_id']
                for detail in details:
                    if not isinstance(detail,dict):return False
                    matching=[n for n in detail.get('flow',{}).get('nodes',[]) if n.get('id')==nid]
                    if len(matching)!=1 or matching[0].get('kind') not in ('start','action','decision','wait','end'):return False
        scope=outcome.get('reference_scope')
        if scope is None:continue
        detail=rows[key].get('raw',{}).get('detail_contract')
        if not isinstance(detail,dict):return False
        if any(ref not in scope['allowed_refs'] for _,item in _objects(detail) for ref in item.get('acceptance_refs',[])):
            return False
    return True
