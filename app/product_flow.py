"""Human checkpoints. Legacy step 4 stays addressable, but is no longer mandatory."""
from .core import digest, require, now, brief_hash, hashes
from .requirements import requirement_ref, TRACKED
from .scope_context import scope_field_issues

TITLES = ('提供现状与诉求','核对现状、价值与改动范围','澄清流程与规则','核对功能草图','评审 MRD 与 PRD','确认与交接')
ACTIVE_STEPS = (1, 2, 3, 5, 6)
INTAKE = {'product':'现有产品','module':'相关页面或模块','intent':'本次改动意图'}
SCOPE = {'current_state':'当前页面与流程','users':'目标使用者','value':'问题与改动价值',
         'change_scope':'本期新增或修改','preserve_scope':'保持不变','out_of_scope':'暂不涉及','priority':'优先级与依据'}
BEHAVIOR = {'actor':'谁操作','entry':'入口及前置条件','flow':'主要流程','data':'业务数据',
            'permissions':'权限约束','result':'操作结果','exceptions':'边界和异常'}


def expanded_context_contract(p):
    # New interview summaries are candidates and require the same explicit
    # intent checkpoint as the field editor. Unmarked legacy projects keep
    # their historical gate and fingerprint contract.
    return any(key in p for key in ('scope_profile', 'context_field_states', 'interview_context_review'))


def selected_requirements(p):
    scope=p.get('product_context',{}).get('scope_ids')
    return [i for i in p['items'] if i['kind']=='requirement' and i['selection_status']=='selected'
            and i['applies_to']=='to_be' and (scope is None or i['id'] in scope)]


def question_stage(q):
    return q.get('blocking_stage',3)


def question_open(q):
    from .understanding import coverage_complete
    return (q['status']!='answered' or not coverage_complete(q)) and not q.get('out_of_scope_reason') and not q.get('superseded_by')


def clarification_focus(p):
    """Expose candidate gaps without treating a proposal as an adopted requirement."""
    from .understanding import answer_targets
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
                                              if r['id'] in q.get('related_refs', [])],
                                              item_ids=[i['id'] for i in answer_targets(p,q)])
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
        # Untouched projects retain their historical checkpoint identity.
        legacy=not expanded_context_contract(p)
        value=dict(context={k:context.get(k) for k in (SCOPE if legacy else {**INTAKE, **SCOPE})},
                   scope_ids=context.get('scope_ids',[]),scope=[{k:i.get(k) for k in ('id','title','applies_to','change_type')} for i in scope])
        if not legacy:
            value['scope_profile']=p.get('scope_profile')
            value['context_field_states']=p.get('context_field_states')
    elif step==3:
        ids={i['id'] for i in scope}
        relevant=[i for i in p['items'] if i['selection_status']=='selected' and (i['id'] in ids or set(i.get('related_refs',[])) & ids)]
        value=dict(items=relevant,questions=p['questions'])
        if expanded_context_contract(p):
            value['scope_context_hash']=fingerprint(p,2)
    elif step==4:
        value=dict(review=p.get('sketch_review'),ui=p['ui']['spec'] if p.get('ui') else None)
    else:
        value=dict(documents={k:dict(id=d['id'],content_hash=digest(d['content'])) for k,d in p['documents'].items()},
                   delivery=p.get('delivery_scope',{'documents':['mrd','prd']}))
    if step in (2,3):
        from .business_context import selection_identity
        business=selection_identity(p)
        if business:value=dict(content=value,business_context=business)
    return digest(value)


def blockers(p, step):
    """Read-only navigation for the same blockers used by checkpoint gates."""
    ctx=p.get('product_context',{});rows=[]
    def add(code, title, message, actions, detail=None):
        target=actions[0] if actions else {}
        identity=next((str(target[k]) for k in ('field','item_id','question_id','document') if k in target),message)
        row=dict(key=code+':'+identity,code=code,title=title,message=message,actions=actions)
        if detail:row['detail']=detail
        rows.append(row)
    def act(kind,label,**target):
        return dict(kind=kind,label=label,**target)
    def req_title(rid):
        return next((i.get('title') or rid for i in p['items'] if i['id']==rid),rid)
    def fields(labels,legacy=False):
        for field,label in labels.items():
            messages=(['请说明'+label] if legacy and not str(ctx.get(field,'')).strip() else
                      scope_field_issues(p,{field:label}) if not legacy else [])
            for message in messages:
                add('context_field',label,message,[act('context','核对'+label,field=field)])
    if step==1:
        if 'intake' not in p:
            if expanded_context_contract(p):
                fields(INTAKE)
            else:
                fields(INTAKE,legacy=True)
        if not any(not s['excluded'] and s.get('excerpts') for s in p['sources']):
            add('source_missing','现状或诉求材料','请添加至少一份可读取的现状或诉求材料。',
                [act('source','管理参考资料')])
    elif step==2:
        # The new UI opts into the expanded contract when it saves metadata.
        labels={'intent':INTAKE['intent'], **SCOPE} if expanded_context_contract(p) else SCOPE
        fields(labels)
        scope=ctx.get('scope_ids',[])
        actual={i['id'] for i in p['items'] if i['kind']=='requirement' and i['applies_to']=='to_be'}
        if not actual:
            add('requirement_candidate_missing','本期需求候选','尚无独立本期需求候选，请先整理或补充本次实际改动。',
                [act('candidate','整理需求候选')])
        elif not scope:
            add('scope_empty','本期需求范围','请选择至少一条本期需求。',[act('scope','选择本期需求')])
        elif not set(scope)<=actual:
            add('scope_unavailable','本期需求范围','已选范围中有需求不再可用，请重新选择本期需求。',
                [act('scope','重新选择本期需求')])
        elif scope:
            # Adoption belongs to the scope page. Do not send users forward only
            # to ask them to return; behavior detail and acceptance remain step 3.
            unselected=set(scope)-{i['id'] for i in selected_requirements(p)}
            if unselected:
                add('scope_unadopted','本期需求采纳','请先采纳已纳入本期的需求，或将暂不采用的需求移出本期。',
                    [act('requirement','核对'+req_title(rid),item_id=rid) for rid in sorted(unselected)])
        for i in p['items']:
            if i['id'] in scope and i.get('change_type')=='unspecified':
                add('change_type_unknown',i.get('title') or i['id'],i['id']+' 改动性质无法确定，请核对依据',
                    [act('requirement','核对需求条款',item_id=i['id'])])
    elif step==3:
        reqs=selected_requirements(p)
        if not reqs:
            add('selected_requirement_missing','本期需求','请明确采纳至少一条本期功能或可独立验证的非功能需求。',
                [act('scope','核对本期范围')])
        unselected=set(ctx.get('scope_ids',[]))-{i['id'] for i in reqs}
        if unselected:
            add('scope_unadopted','本期需求采纳','本期范围内仍有需求未明确采纳：'+'、'.join(sorted(unselected)),
                [act('requirement','核对'+req_title(rid),item_id=rid) if any(i['id']==rid and i['kind']=='requirement'
                                                                 and i['applies_to']=='to_be' for i in p['items'])
                 else act('scope','重新选择本期需求') for rid in sorted(unselected)])
        from .requirements import delivery_items
        clause_ids={i['id'] for i in delivery_items(p) if i['kind'] in TRACKED}
        for q in p['questions']:
            if q.get('blocking') and q.get('decision_pending_group_ids') and not q.get('superseded_by') and not q.get('out_of_scope_reason'):
                add('decision_changes_pending',q.get('question') or '关联修改尚未落实',
                    '回答关联的修改仍有未接受的组，请在原处核对或明确修订。',
                    [act('answer','核对关联修改',question_id=q['id'])])
            if q.get('understanding_status') in ('pending','candidate_ready') and q.get('blocking') \
                    and set(q.get('related_refs',[])) & clause_ids:
                add('answer_review_pending',q.get('question') or '已保存回答',
                    q['id']+' 已保存回答，关联条款修订仍待核对采纳',
                    [act('answer','核对已保存回答',question_id=q['id'])])
        for req in reqs:
            from .requirements import behavior_review_current
            from .requirement_confirmation import confirmation_current
            whole_requirement_reviewed = confirmation_current(p, req)
            if (req.get('requirement_confirmation') or p.get('requirement_confirmation_mode') == 'table') and not whole_requirement_reviewed:
                add('requirement_confirmation_stale', req.get('title') or req['id'],
                    ('这条需求或关联验收有修改，请核对本行变化后重新确认。'
                     if req.get('requirement_confirmation') else '请核对这条需求与关联验收，并明确确认本行内容。'),
                    [act('requirement', '核对需求与验收', item_id=req['id'])])
            if not whole_requirement_reviewed and not behavior_review_current(req):
                add('behavior_review_pending',req.get('title') or req['id'],
                    req['id']+' 的模型行为说明仍待核对；请检查无来源的时点、流程与默认规则。',
                    [act('behavior','核对业务行为',item_id=req['id'],fields=list(BEHAVIOR))],
                    '模型行为说明仍待核对；请检查无来源的时点、流程与默认规则。')
            absent=[key for key in BEHAVIOR if not str(req.get('behavior',{}).get(key,'')).strip()]
            if absent and not whole_requirement_reviewed:
                detail='尚缺：'+'、'.join(BEHAVIOR[key] for key in absent)+'；确实不适用的维度应说明原因。'
                add('behavior_fields_missing',req.get('title') or req['id'],req['id']+' '+detail,
                    [act('behavior','补全业务行为',item_id=req['id'],fields=absent)],detail)
            if not any(i['kind']=='acceptance' and i['selection_status']=='selected' and
                       (req['id'] in i['related_refs'] or i['id'] in req['related_refs']) for i in p['items']):
                candidates=[i for i in p['items'] if i['kind']=='acceptance' and
                            i['selection_status']=='candidate' and i['applies_to']=='to_be' and
                            (req['id'] in i['related_refs'] or i['id'] in req['related_refs'])]
                action=(act('acceptance','核对关联验收候选',item_id=req['id']) if candidates else
                        act('add_acceptance','补充关联验收条件',item_id=req['id']))
                add('acceptance_missing',req.get('title') or req['id'],
                    req['id']+' 缺少已采纳的关联验收条件。',[action],'缺少已采纳的关联验收条件。')
    elif step==4:
        review=p.get('sketch_review',{})
        if review.get('applicable') is False:
            if not review.get('reason','').strip():
                add('sketch_reason_missing','功能草图','无界面变化时，请说明草图不适用的业务原因。',[act('sketch','核对功能草图')])
        else:
            if not p.get('ui'):
                add('sketch_missing','功能草图','请生成本次受影响功能的需求草图；静态线框即可。',[act('sketch','核对功能草图')])
            elif p['ui']['brief_hash']!=brief_hash(p):
                add('sketch_stale','功能草图','草图对应旧需求，请重新核对或更新。',[act('sketch','核对功能草图')])
            for key,label in (('changes','新增或修改区域'),('preserved','原有保持部分'),('behavior','入口、操作及结果')):
                if not review.get(key,'').strip():
                    add('sketch_review_missing','功能草图','请核对草图的'+label+'。',[act('sketch','核对功能草图')])
    elif step==5:
        kinds=p.get('delivery_scope',{}).get('documents',['mrd','prd'])
        for kind in kinds:
            doc=p['documents'].get(kind)
            if not doc:
                add('document_missing',kind.upper()+' 文档','请生成 '+kind.upper()+' 评审稿。',
                    [act('document','生成 '+kind.upper()+' 评审稿',document=kind)]);continue
            if doc['brief_hash']!=brief_hash(p) or kind in p.get('stale_document_kinds',[]):
                add('document_stale',kind.upper()+' 文档',kind.upper()+' 基于旧内容，请更新后核对。',
                    [act('document','更新 '+kind.upper()+' 文档',document=kind)])
            covered={r for s in doc['content']['sections'] for b in s['blocks'] if b['kind'] in TRACKED for r in b['ref_ids']}
            for req in selected_requirements(p):
                if req['id'] not in covered:
                    add('document_requirement_missing',req.get('title') or req['id'],
                        kind.upper()+' 尚未成文：'+req['id'],
                        [act('document','核对 '+kind.upper()+' 文档',document=kind,item_id=req['id'])])
            # Honor an unchanged legacy stage check, without inventing per-document reviews.
            legacy=p.get('stage_checks',{}).get('5',{})
            legacy_valid=legacy and not legacy.get('contract_version') and legacy.get('content_hash')==fingerprint(p,5)
            if not legacy_valid and not document_review_current(p,kind):
                add('document_review_pending',kind.upper()+' 文档',
                    '请评审并核对当前 '+kind.upper()+' 文档。',
                    [act('document','评审 '+kind.upper()+' 文档',document=kind)])
    if 2<=step<=5:
        for q in p['questions']:
            if q.get('blocking') and question_open(q) and question_stage(q)<=step:
                add('question_open','待回答的问题',q['question'],[act('question','回答问题',question_id=q['id'])])
    unique={}
    for row in rows:
        message=row['message']
        if message in unique:
            existing=unique[message]
            for action in row['actions']:
                if action not in existing['actions']:existing['actions'].append(action)
        else:
            unique[message]=row
    return list(unique.values())


def missing(p, step):
    return [row['message'] for row in blockers(p, step)]


def status(p):
    result=[];prior_valid=True
    for step,title in enumerate(TITLES,1):
        own_blockers=blockers(p,step) if step<6 else []
        own_missing=[row['message'] for row in own_blockers]
        recorded=p.get('stage_checks',{}).get(str(step))
        token=fingerprint(p,step)
        valid=bool(step<6 and prior_valid and not own_missing and recorded and recorded['content_hash']==token)
        if step==6:valid=bool(prior_valid and p.get('active_baseline_id') and p.get('confirmed_hashes')==hashes(p))
        result.append(dict(step=step,title=title,content_hash=token,complete=valid,
            available=prior_valid,missing=own_missing,blockers=own_blockers,needs_recheck=bool(recorded and not valid),
            retired=step not in ACTIVE_STEPS,
            display_step=ACTIVE_STEPS.index(step)+1 if step in ACTIVE_STEPS else None))
        # Do not invent a sketch approval or renumber historical stage-check keys.
        if step in ACTIVE_STEPS:prior_valid=valid
    return result


def current_stage(p):
    """Return the authoritative business stage, independent of the viewed UI tab."""
    states = status(p)
    # Saving intake confirms only that the user's raw input was stored. Until
    # analysis has produced a substantive result, keep the business stage at 1.
    analyzed = (any(m.get('role') == 'assistant' and m.get('stage') == 'ingest'
                    for m in p.get('messages', [])) or bool(p.get('items')) or bool(p.get('questions')))
    if states[5]['complete']:
        return states[5]
    if not analyzed and p.get('intake', {}).get('mode') != 'direct':
        return states[0]
    return next((s for s in states if not s['retired'] and not s['complete']), states[5])


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


def execution_issues(p, stage, kind='prd', discussion_target=None):
    if stage=='ui':return ['业务草图功能已取消，请通过需求条目、业务规则及文档表达改动。']
    # Shared drafts are editable work, not a stage-check or formal approval.
    # Content/source validation still applies in generation and final gates.
    if p.get('workflow_mode') == 'shared-draft' and stage in ('brainstorm','clarify','change','prd','review'):
        return []
    # Intake remains possible with missing information. Later execution shares these checks.
    required={'brainstorm':1,'clarify':2,'change':2,'ui':3,'prd':3,'review':3}.get(stage,0)
    # Local help may prepare an unconfirmed requirement. It never approves the
    # scope or relaxes document-generation/delivery gates.
    if stage == 'clarify' and discussion_target:
        target = discussion_target
        valid = (target.get('kind') == 'item' and any(i['id'] == target.get('id') and
                 i['kind'] == 'requirement' and i['applies_to'] == 'to_be' for i in p['items']))
        valid = valid or (target.get('kind') == 'question' and any(q['id'] == target.get('id') for q in p['questions']))
        if valid:
            required = 1
    if not required:return []
    steps=status(p)
    failures=[s for s in steps[:required] if not s['retired'] and not s['complete']]
    if not failures:return []
    first=failures[0]
    return [f'先完成第{first["display_step"]}步「{first["title"]}」']+first['missing']
