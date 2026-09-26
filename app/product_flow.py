"""Human checkpoints. Legacy step 4 stays addressable, but is no longer mandatory."""
from .core import digest, require, now, brief_hash, hashes
from .requirements import requirement_ref, TRACKED

TITLES = ('提供现状与诉求','核对现状、价值与改动范围','澄清流程与规则','核对功能草图','评审 MRD 与 PRD','确认与交接')
ACTIVE_STEPS = (1, 2, 3, 5, 6)
INTAKE = {'product':'现有产品','module':'相关页面或模块','intent':'本次改动意图'}
SCOPE = {'current_state':'当前页面与流程','users':'目标使用者','value':'问题与改动价值',
         'change_scope':'本期新增或修改','preserve_scope':'保持不变','out_of_scope':'暂不涉及','priority':'优先级与依据'}
BEHAVIOR = {'actor':'谁操作','entry':'入口及前置条件','flow':'主要流程','data':'业务数据',
            'permissions':'权限约束','result':'操作结果','exceptions':'边界和异常'}


def selected_requirements(p):
    scope=p.get('product_context',{}).get('scope_ids')
    return [i for i in p['items'] if i['kind']=='requirement' and i['selection_status']=='selected'
            and i['applies_to']=='to_be' and (scope is None or i['id'] in scope)]


def question_stage(q):
    return q.get('blocking_stage',3)


def question_open(q):
    return q['status']!='answered' and not q.get('out_of_scope_reason') and not q.get('superseded_by')


def clarification_focus(p):
    """Expose candidate gaps without treating a proposal as an adopted requirement."""
    scope = p.get('product_context', {}).get('scope_ids')
    reqs = [i for i in p['items'] if i['kind'] == 'requirement' and i['applies_to'] == 'to_be'
            and i['selection_status'] in ('candidate', 'selected') and not i.get('target_item_id')
            and (scope is None or i['id'] in scope)]
    rows, gaps, adoption = [], [], []
    if not reqs:
        gaps.append('本期尚无可核对的独立需求候选；根据材料表达实际改动，缺少范围依据时保留未知。')
    for req in reqs:
        rid = req['id']
        absent = [k for k in BEHAVIOR if not str(req.get('behavior', {}).get(k, '')).strip()]
        acceptance = [i for i in p['items'] if i['kind'] == 'acceptance'
                      and i['selection_status'] in ('candidate', 'selected') and i['applies_to'] == 'to_be'
                      and (rid in i.get('related_refs', []) or i['id'] in req.get('related_refs', []))]
        revisions = [i['id'] for i in p['items'] if i.get('target_item_id') == rid
                     and i['selection_status'] == 'candidate']
        rows.append(dict(requirement_id=rid, selection_status=req['selection_status'],
                         missing_behavior_fields=absent, acceptance_candidate_ids=[i['id'] for i in acceptance],
                         pending_revision_ids=revisions))
        if absent:
            gaps.append(rid + ' 尚缺：' + '、'.join(BEHAVIOR[k] for k in absent)
                        + '；先核对来源及待采纳修订，真正未知才提问，确实不适用需有依据。')
        if not acceptance:
            gaps.append(rid + ' 缺少关联验收候选；仅用已明确行为和来源提出可核对的操作及预期结果。')
        if req['selection_status'] != 'selected':
            adoption.append(rid + ' 尚待人工采纳；模型不能改变采纳状态。')
        if acceptance and not any(i['selection_status'] == 'selected' for i in acceptance):
            adoption.append(rid + ' 的关联验收候选尚待人工采纳。')
    return dict(requirements=rows, content_gaps=gaps, adoption_gaps=adoption,
                answer_revision_targets=[dict(question_id=q['id'], requirement_ids=[r['id'] for r in reqs
                                              if r['id'] in q.get('related_refs', [])])
                                         for q in p['questions'] if q['status'] == 'answered'
                                         and q.get('understanding_status') != 'applied'],
                unresolved_questions=[q['id'] for q in p['questions'] if question_open(q)],
                answer_updates_pending=[q['id'] for q in p['questions']
                                        if q.get('understanding_status') in ('pending', 'candidate_ready')],
                deferred_questions=[dict(id=q['id'],reason=q['out_of_scope_reason'])
                                    for q in p['questions'] if q.get('out_of_scope_reason')])


def fingerprint(p, step):
    context=p.get('product_context',{})
    scope=[i for i in p['items'] if i['kind']=='requirement' and i['id'] in context.get('scope_ids',[])]
    if step==1:
        value=p['intake'] if 'intake' in p else {k:context.get(k) for k in INTAKE}
    elif step==2:
        value=dict(context={k:context.get(k) for k in SCOPE},scope_ids=context.get('scope_ids',[]),scope=[{k:i.get(k) for k in ('id','title','applies_to','change_type')} for i in scope])
    elif step==3:
        ids={i['id'] for i in scope}
        relevant=[i for i in p['items'] if i['selection_status']=='selected' and (i['id'] in ids or set(i.get('related_refs',[])) & ids)]
        value=dict(items=relevant,questions=p['questions'])
    elif step==4:
        value=dict(review=p.get('sketch_review'),ui=p['ui']['spec'] if p.get('ui') else None)
    else:
        value=dict(documents={k:dict(id=d['id'],content_hash=digest(d['content'])) for k,d in p['documents'].items()},
                   delivery=p.get('delivery_scope',{'documents':['mrd','prd']}))
    return digest(value)


def missing(p, step):
    ctx=p.get('product_context',{});issues=[]
    if step==1:
        if 'intake' not in p:
            issues += ['请说明'+label for key,label in INTAKE.items() if not str(ctx.get(key,'')).strip()]
        if not any(not s['excluded'] and s.get('excerpts') for s in p['sources']):
            issues.append('请添加至少一份可读取的现状或诉求材料。')
    elif step==2:
        issues += ['请核对'+label for key,label in SCOPE.items() if not str(ctx.get(key,'')).strip()]
        scope=ctx.get('scope_ids',[])
        actual={i['id'] for i in p['items'] if i['kind']=='requirement' and i['applies_to']=='to_be'}
        if not scope or not set(scope)<=actual:
            issues.append('尚未形成带编号的独立本期需求；背景或目标条目不能代替功能需求。')
        issues += [i['id']+' 改动性质无法确定，请核对依据' for i in p['items'] if i['id'] in scope and i.get('change_type')=='unspecified']
    elif step==3:
        reqs=selected_requirements(p)
        if not reqs:issues.append('请明确采纳至少一条本期功能或可独立验证的非功能需求。')
        unselected=set(ctx.get('scope_ids',[]))-{i['id'] for i in reqs}
        if unselected:issues.append('本期范围内仍有需求未明确采纳：'+'、'.join(sorted(unselected)))
        issues += [q['id']+' 已保存回答，关联需求修订仍待核对采纳' for q in p['questions']
                   if q.get('understanding_status') in ('pending','candidate_ready') and q.get('blocking')
                   and set(q.get('related_refs',[])) & {i['id'] for i in reqs}]
        for req in reqs:
            absent=[label for key,label in BEHAVIOR.items() if not str(req.get('behavior',{}).get(key,'')).strip()]
            if absent:issues.append(req['id']+' 尚缺：'+'、'.join(absent)+'；确实不适用的维度应说明原因。')
            if not any(i['kind']=='acceptance' and i['selection_status']=='selected' and
                       (req['id'] in i['related_refs'] or i['id'] in req['related_refs']) for i in p['items']):
                issues.append(req['id']+' 缺少已采纳的关联验收条件。')
    elif step==4:
        review=p.get('sketch_review',{})
        if review.get('applicable') is False:
            if not review.get('reason','').strip():issues.append('无界面变化时，请说明草图不适用的业务原因。')
        else:
            if not p.get('ui'):issues.append('请生成本次受影响功能的需求草图；静态线框即可。')
            elif p['ui']['brief_hash']!=brief_hash(p):issues.append('草图对应旧需求，请重新核对或更新。')
            for key,label in (('changes','新增或修改区域'),('preserved','原有保持部分'),('behavior','入口、操作及结果')):
                if not review.get(key,'').strip():issues.append('请核对草图的'+label+'。')
    elif step==5:
        kinds=p.get('delivery_scope',{}).get('documents',['mrd','prd'])
        for kind in kinds:
            doc=p['documents'].get(kind)
            if not doc:issues.append('请生成 '+kind.upper()+' 评审稿。');continue
            if doc['brief_hash']!=brief_hash(p) or kind in p.get('stale_document_kinds',[]):
                issues.append(kind.upper()+' 基于旧内容，请更新后核对。')
            covered={r for s in doc['content']['sections'] for b in s['blocks'] if b['kind'] in TRACKED for r in b['ref_ids']}
            for req in selected_requirements(p):
                if req['id'] not in covered:issues.append(kind.upper()+' 尚未成文：'+req['id'])
            # Honor an unchanged legacy stage check, without inventing per-document reviews.
            legacy=p.get('stage_checks',{}).get('5',{})
            legacy_valid=legacy and not legacy.get('contract_version') and legacy.get('content_hash')==fingerprint(p,5)
            if not legacy_valid and not document_review_current(p,kind):
                issues.append('请评审并核对当前 '+kind.upper()+' 文档。')
    if 2<=step<=5:
        issues += [q['question'] for q in p['questions'] if q.get('blocking') and question_open(q) and question_stage(q)<=step]
    return list(dict.fromkeys(issues))


def status(p):
    result=[];prior_valid=True
    for step,title in enumerate(TITLES,1):
        own_missing=missing(p,step) if step<6 else []
        recorded=p.get('stage_checks',{}).get(str(step))
        token=fingerprint(p,step)
        valid=bool(step<6 and prior_valid and not own_missing and recorded and recorded['content_hash']==token)
        if step==6:valid=bool(prior_valid and p.get('active_baseline_id') and p.get('confirmed_hashes')==hashes(p))
        result.append(dict(step=step,title=title,content_hash=token,complete=valid,
            available=prior_valid,missing=own_missing,needs_recheck=bool(recorded and not valid),
            retired=step not in ACTIVE_STEPS,
            display_step=ACTIVE_STEPS.index(step)+1 if step in ACTIVE_STEPS else None))
        # Do not invent a sketch approval or renumber historical stage-check keys.
        if step in ACTIVE_STEPS:prior_valid=valid
    return result


def checkpoint(p, step, expected_hash):
    require(1<=step<=5,'STAGE_INVALID','正式确认请使用确认与交接接口')
    state=status(p)[step-1]
    require(state['content_hash']==expected_hash,'STALE_REVISION','核对内容已变化，请重新查看，不会自动确认',409)
    require(state['available'],'STAGE_BLOCKED','请先完成前一步的内容核对',409)
    require(not state['missing'],'STAGE_BLOCKED','；'.join(state['missing']),409)
    p.setdefault('stage_checks',{})[str(step)]=dict(content_hash=expected_hash,checked_at=now(),
        actor='authenticated_local_user',draft_revision=p['revision'],
        requirements=[requirement_ref(p,i) for i in selected_requirements(p)],
        meaning='功能表达核对，非最终UI批准' if step==4 else '阶段内容核对，非正式业务批准')
    if step==5:p['stage_checks']['5']['contract_version']='document-review-1'


def document_review_current(p, kind):
    doc=p['documents'].get(kind)
    record=p.get('document_reviews',{}).get(kind,{})
    return bool(doc and record.get('document_id')==doc['id'] and
                record.get('content_hash')==digest(doc['content']) and
                record.get('brief_hash')==brief_hash(p) and
                kind not in p.get('stale_document_kinds',[]))


def review_document(p, kind, document_id):
    doc=p['documents'].get(kind)
    require(doc and doc['id']==document_id,'STALE_DOCUMENT','文档版本已变化，请重新核对',409)
    require(status(p)[4]['available'],'STAGE_BLOCKED','请先完成范围及业务规则核对',409)
    require(doc['brief_hash']==brief_hash(p) and kind not in p.get('stale_document_kinds',[]),
            'STALE_DOCUMENT','文档基于旧内容，请更新后评审',409)
    p.setdefault('document_reviews',{})[kind]=dict(document_id=document_id,content_hash=digest(doc['content']),
        brief_hash=brief_hash(p),reviewed_at=now(),actor='authenticated_local_user',meaning='文档评审核对，非正式业务批准')


def execution_issues(p, stage, kind='prd'):
    if stage=='ui':return ['业务草图功能已取消，请通过需求条目、业务规则及文档表达改动。']
    # Intake remains possible with missing information. Later execution shares these checks.
    required={'brainstorm':1,'clarify':2,'change':2,'ui':3,'prd':3,'review':3}.get(stage,0)
    if not required:return []
    steps=status(p)
    failures=[s for s in steps[:required] if not s['retired'] and not s['complete']]
    if not failures:return []
    first=failures[0]
    return [f'先完成第{first["display_step"]}步「{first["title"]}」']+first['missing']
