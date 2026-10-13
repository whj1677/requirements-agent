"""Explicit, version-bound review of independently answerable decisions.

Coverage is a reviewed proposal, never inferred from a saved sentence. Preview
records are immutable; project changes happen only in commit's Store.edit.
"""
import copy

from .core import Problem, digest, ident, now, require
from .requirements import require_behavior_review
from .understanding import (accept_answer_update, answer_target, answer_targets,
                            application_hash, candidate_answer_binding_current,
                            current_answer, question_fingerprint)

COVERAGE = {'pending', 'partial', 'clear', 'conflict'}


def decision_ids(project, question_id):
    questions = project['questions']
    group = [q for q in questions if q.get('decision_group_id') == question_id]
    if group:
        return [q['id'] for q in group if not q.get('superseded_by')]
    q = next((q for q in questions if q['id'] == question_id), None)
    require(q is not None, 'NOT_FOUND', '问题或决定组不存在', 404)
    require(not q.get('superseded_by'), 'QUESTION_SPLIT', '旧问题已有明确拆分，请核对子决定', 409)
    return [question_id]


def review_question_hash(project, question_id):
    """One stable meaning hash for a flat question or its existing child group."""
    by_id={q['id']:q for q in project['questions']}
    ids=decision_ids(project,question_id)
    return digest([question_fingerprint(by_id[qid]) for qid in ids])


def preview_question_group_hash(project, question_id):
    """Bind a preview to every current leaf and its grouping semantics."""
    by_id={q['id']:q for q in project['questions']}
    return digest([dict(id=qid,question_fingerprint=question_fingerprint(by_id[qid]),
                      decision_group_title=by_id[qid].get('decision_group_title'))
                   for qid in decision_ids(project,question_id)])


def answer_record(project, question_id, answer_id):
    row = next((a for a in project.get('decision_answers', []) if a['id'] == answer_id
                and a['question_id'] == question_id), None)
    require(row is not None, 'NOT_FOUND', '已保存的原话不存在', 404)
    return row


def save_raw_answer(project, question_id, answer, store=None):
    require(isinstance(answer, str) and answer.strip() and len(answer) <= 6000,
            'INPUT_REQUIRED', '回答不能为空或超过6000字符')
    ids = decision_ids(project, question_id)
    row = dict(id=ident('DA'), question_id=question_id, decision_ids=ids,
               text=answer, answer_hash=digest(answer), created=now())
    if store is not None:
        from .sources import save_source
        source=save_source(store,'决定组原话.txt',answer.encode('utf-8'),'goal')
        project['sources'].append(source)
        row['source_refs']=[dict(source_id=source['id'],excerpt_id=e['id'])
                            for e in source['excerpts']]
    else:
        row['source_refs']=[]
    project.setdefault('decision_answers', []).append(row)
    for q in project['questions']:
        if q['id'] in ids and q.get('decision_group_id') == question_id:
            from .understanding import record_answer
            record_answer(q)
            q['decision_coverage'] = 'pending'
            q['decision_answer_id'] = row['id']
            q['answer']=None
            q['status']='open'
            q['answer_source_refs']=[]
            q['understanding_status'] = 'pending'
            q['answer_effect'] = '原话已保存，独立决定覆盖和条款修改待核对'
            q['decision_answer_source_refs']=copy.deepcopy(row['source_refs'])
        elif q['id']==question_id and q['id'] in ids:
            # A flat or historical question keeps its original answer trail and
            # remains visible to the existing clarification context.
            from .understanding import record_answer
            record_answer(q)
            q.update(answer=answer,status='answered',answered_at=now(),
                     answer_source_refs=copy.deepcopy(row['source_refs']),
                     decision_answer_id=row['id'],
                     answer_effect='原话已保存，独立决定覆盖和条款修改待核对')
    return row


def _answer(project, question_id, answer_id):
    if answer_id:
        row=answer_record(project, question_id, answer_id)
        by_id={q['id']:q for q in project['questions']}
        require(set(row['decision_ids'])==set(decision_ids(project,question_id))
                and all(by_id[qid].get('decision_answer_id')==answer_id
                        for qid in row['decision_ids'] if qid in by_id),
                'STALE_ANSWER', '已有更新的原话，请重新核对', 409)
        return row
    q = next((q for q in project['questions'] if q['id'] == question_id), None)
    require(q is not None and isinstance(q.get('answer'), str) and q['answer'].strip(),
            'ANSWER_REQUIRED', '请先保存原话', 409)
    return dict(id=None, question_id=question_id, decision_ids=[question_id],
                text=q['answer'], answer_hash=digest(q['answer']),
                source_refs=copy.deepcopy(q.get('answer_source_refs') or []),
                created=q.get('answered_at'))


def append_model_proposal(project, audit):
    """Attach a version-matched semantic suggestion without approving coverage.

    The caller retains its full model-call audit. A stale or malformed suggestion
    is not converted into project truth or used to split historical questions.
    """
    from .core import Problem
    question_id=audit.get('question_id')
    if not isinstance(question_id,str):return False
    try:
        ids=decision_ids(project,question_id)
        answer=_answer(project,question_id,audit.get('answer_id'))
        if (audit.get('question_hash')!=review_question_hash(project,question_id)
                or audit.get('answer_hash')!=answer['answer_hash']):return False
    except (KeyError, TypeError, Problem):
        return False
    try:
        rows=audit.get('decisions')
        if not isinstance(rows,list) or not rows:return False
        seen=set()
        for row in rows:
            if (not isinstance(row,dict) or not isinstance(row.get('question'),str)
                    or not row['question'].strip()):return False
            identity=row.get('id')
            if not isinstance(identity,str) or identity in seen:return False
            seen.add(identity)
            coverage=row.get('coverage')
            if coverage not in COVERAGE | {'unknown'}:return False
            quote=row.get('answer_quote') or ''
            if not isinstance(quote,str) or quote and quote not in answer['text']:
                return False
            if coverage=='clear' and not quote:return False
        # The model may propose a legacy split, but it cannot mutate identities.
        if len(ids)>1 and seen!={*ids}:return False
    except Problem:
        return False
    proposal=copy.deepcopy(audit)
    for row in proposal['decisions']:
        if row['coverage']=='unknown':row['coverage']='pending'
    proposal['status']='proposal_only'
    proposal['stored_at']=now()
    for q in project['questions']:
        if q['id'] in ids:q['decision_review_proposal']=copy.deepcopy(proposal)
    return True


def _source_snapshot(project, refs):
    sources = {s['id']: s for s in project.get('sources', [])}
    result = []
    for ref in refs:
        source = sources.get(ref.get('source_id'))
        excerpt = next((e for e in source.get('excerpts', [])
                        if e['id'] == ref.get('excerpt_id')), None) if source else None
        require(source is not None and excerpt is not None and not source.get('excluded')
                and not source.get('superseded_by'), 'REFERENCE_INVALID',
                '关联来源不存在或已停用', 409)
        result.append(dict(source_id=source['id'], excerpt_id=excerpt['id'],
                           source_version=source.get('version'),
                           source_hash=source.get('sha256'), excerpt_hash=digest(excerpt)))
    return result


def prepare_implementations(project, preview, supplied=None):
    """Bind visible final words without preventing an unfinished review draft."""
    from .answer_implementation import (derive_changed_witnesses, saved_implementations,
        validate_implementation_map, validate_implementations)
    projected=copy.deepcopy(project)
    items={item['id']:item for item in projected['items']}
    changes={}
    for group in preview['groups']:
        for target in group['targets']:
            iid=target['id']
            if iid not in items:
                continue
            after=items[iid]
            changes.setdefault(iid,copy.deepcopy(after))
            for key in ('title','statement','applies_to','epistemic_status','source_refs','related_refs',
                        'behavior','change_type','scope_evidence','classification_reason',
                        'behavior_review_required','behavior_review'):
                if key in target['after']:
                    after[key]=copy.deepcopy(target['after'][key])
    known={q['id']:q for q in projected['questions']}
    for decision in preview['decisions']:
        if decision['id'] not in known:
            question=dict(decision,status='answered',answer=decision.get('answer_quote'))
            projected['questions'].append(question);known[question['id']]=question
        known[decision['id']]['answer']=decision.get('answer_quote')
    supplied=validate_implementation_map(projected,supplied)
    require(set(supplied)<={d['id'] for d in preview['decisions']},'ANSWER_IMPLEMENTATION_INVALID',
            '只能选择本次独立决定的回答落实位置',409)
    implementations={};issues=[];pending=[]
    for decision in preview['decisions']:
        if decision['coverage']!='clear':
            continue
        question=known[decision['id']]
        targets=answer_targets(projected,question)
        witnesses=supplied.get(question['id'])
        if witnesses is None:
            witnesses=saved_implementations(projected,question)
            represented={row['item_id'] for row in witnesses}
            for item in targets:
                if item['id'] not in represented:
                    witnesses+=derive_changed_witnesses(changes.get(item['id']),item)
        implementations[question['id']]=copy.deepcopy(witnesses)
        try:
            validate_implementations(projected,question,witnesses)
        except Problem as error:
            pending.append(question['id'])
            location=next((row for row in question.get('unresolved_locations') or []
                           if isinstance(row,dict) and row.get('item_id') in items
                           and row.get('field') and row.get('quote')), {})
            issues.append(dict(question_id=question['id'],code=error.code,message=error.message,
                               **{key:location[key] for key in ('item_id','field') if key in location}))
    preview['answer_implementations']=implementations
    preview['implementation_issues']=issues
    preview['implementation_pending_ids']=pending
    return preview


def build_preview(project, payload):
    require(project['revision'] == payload['expected_revision'], 'STALE_REVISION',
            '页面版本已过期，请刷新核对', 409)
    question_id = payload['question_id']
    existing_ids = decision_ids(project, question_id)
    answer = _answer(project, question_id, payload.get('answer_id'))
    legacy = payload.get('legacy_decisions') or []
    original = next((q for q in project['questions'] if q['id'] == question_id), None)
    if legacy:
        require(original is not None and current_answer(original) and len(legacy) >= 2,
                'REVIEW_REQUIRED', '旧已答复合题须显式核对至少两个子决定', 409)
        require(not original.get('decision_group_id'), 'REFERENCE_INVALID',
                '已有子决定不能再次拆分', 409)
        known_refs = {i['id'] for i in project['items']} & set(original.get('related_refs') or [])
        decisions = []
        for index, item in enumerate(legacy):
            require(isinstance(item.get('question'), str) and item['question'].strip()
                    and set(item.get('related_refs') or []) <= known_refs
                    and bool(item.get('related_refs')), 'REFERENCE_INVALID',
                    '旧题子决定须有独立问题和有效关联条款', 409)
            decisions.append(dict(id='Q-'+digest([question_id, index, item['question']])[:16],
                                  question=item['question'], related_refs=item['related_refs'],
                                  source_refs=copy.deepcopy(original.get('source_refs') or []),
                                  why=item.get('why') or original.get('why','')))
        require(not ({d['id'] for d in decisions} & {q['id'] for q in project['questions']}),
                'IDENTITY_CONFLICT', '旧题映射产生重复决定身份', 409)
    else:
        decisions = [dict(id=q['id'], question=q['question'],
                          why=q.get('why',''),source_refs=copy.deepcopy(q.get('source_refs') or []),
                          related_refs=q.get('related_refs', [])) for q in project['questions']
                     if q['id'] in existing_ids]
    ids = {d['id'] for d in decisions}
    coverage = payload.get('coverage') or []
    if legacy and not coverage:
        coverage = [dict(decision_id=d['id'], status=row.get('status', 'pending'),
                         answer_quote=row.get('answer_quote', ''))
                    for d, row in zip(decisions, legacy)]
    require(len(coverage) == len(ids) and {c['decision_id'] for c in coverage} == ids,
            'REFERENCE_INVALID', '须逐项核对全部独立决定，不可遗漏或重复', 409)
    by_coverage = {c['decision_id']: c for c in coverage}
    for c in coverage:
        quote = c.get('answer_quote') or ''
        require(c.get('status') in COVERAGE and isinstance(quote, str)
                and (not quote or quote in answer['text'])
                and (c['status'] != 'clear' or bool(quote)), 'REFERENCE_INVALID',
                '覆盖状态或原话引文无效：'+c['decision_id'], 409)
    items = {i['id']: i for i in project['items']}
    groups = []
    seen_groups = set()
    for row in payload.get('groups') or []:
        gid = row.get('id')
        dids = row.get('decision_ids') or []
        cids = row.get('candidate_item_ids') or []
        tids = row.get('target_item_ids') or []
        require(isinstance(gid, str) and gid and gid not in seen_groups
                and dids and len(set(dids)) == len(dids) and set(dids) <= ids
                and len(set(cids)) == len(cids) and len(set(tids)) == len(tids)
                and (cids or tids), 'REFERENCE_INVALID', '修改组身份或决定关联无效', 409)
        seen_groups.add(gid)
        require(all(by_coverage[d]['status'] == 'clear' for d in dids),
                'REVIEW_REQUIRED', '未明确覆盖的决定不能提交关联修改：'+gid, 409)
        targets = []
        for cid in cids:
            candidate = items.get(cid)
            require(candidate is not None and candidate.get('selection_status') == 'candidate',
                    'REFERENCE_INVALID', '候选不存在或已处理：'+cid, 409)
            target_id = candidate.get('target_item_id')
            if target_id:
                require(target_id in tids and target_id in items
                        and candidate.get('kind') == items[target_id].get('kind'),
                        'REFERENCE_INVALID', '修订候选目标不匹配：'+cid, 409)
            answer_refs=set(candidate.get('answer_refs') or [])
            require(answer_refs <= set(dids), 'REFERENCE_INVALID',
                    '候选回答引用超出本修改组：'+cid, 409)
            if answer_refs:
                by_decision={d['id']:d for d in decisions}
                require(target_id is not None and all(
                    target_id in by_decision[d]['related_refs'] for d in answer_refs),
                    'REFERENCE_INVALID', '候选目标不在所引决定的直接范围：'+cid, 409)
                by_question={q['id']:q for q in project['questions']}
                require(all(d in by_question and candidate_answer_binding_current(
                    project,by_question[d],candidate) for d in answer_refs),
                    'STALE_ANSWER','候选未绑定当前已保存原话：'+cid,409)
            targets.append(dict(id=target_id or cid, candidate_id=cid,
                                title=candidate.get('title'), kind=candidate['kind'],
                                before=copy.deepcopy(items[target_id]) if target_id else None,
                                after=copy.deepcopy(candidate),
                                clause_hash=application_hash(items[target_id]) if target_id else None))
        covered_targets = {t['id'] for t in targets}
        for tid in tids:
            target = items.get(tid)
            require(target is not None and not target.get('target_item_id'),
                    'REFERENCE_INVALID', '关联原条款不存在：'+tid, 409)
            if tid not in covered_targets:
                targets.append(dict(id=tid, candidate_id=None, title=target.get('title'),
                                    kind=target['kind'], before=copy.deepcopy(target),
                                    after=copy.deepcopy(target), clause_hash=application_hash(target)))
        groups.append(dict(id=gid, title=row.get('title') or gid, decision_ids=dids,
                           candidate_item_ids=cids, target_item_ids=tids,
                           dependent_group_ids=row.get('depends_on') or [],
                           targets=targets, selected_default=False))
    group_ids={g['id'] for g in groups}
    require(all(set(g['dependent_group_ids']) <= group_ids and g['id'] not in g['dependent_group_ids']
                for g in groups), 'REFERENCE_INVALID', '修改组依赖不存在或自引用', 409)
    review_ids=payload.get('review_behavior_item_ids',[])
    visible={t['candidate_id'] or t['id'] for g in groups for t in g['targets']
             if t['after'].get('kind')=='requirement'}
    require(isinstance(review_ids,list) and all(isinstance(iid,str) for iid in review_ids)
            and len(set(review_ids))==len(review_ids) and set(review_ids)<=visible,
            'REFERENCE_INVALID','只能核对本次预览中完整展示的需求业务说明',409)
    for group in groups:
        for target in group['targets']:
            if (target['candidate_id'] or target['id']) in review_ids:
                target['review_behavior']=True
    source_refs = list(answer.get('source_refs') or [])
    for d in decisions:
        q = next((q for q in project['questions'] if q['id'] == d['id']), None)
        if q: source_refs.extend(q.get('source_refs') or [])
    for g in groups:
        for t in g['targets']:
            source_refs.extend(t['after'].get('source_refs') or [])
            if t['before']:source_refs.extend(t['before'].get('source_refs') or [])
    snapshot=dict(revision=project['revision'], answer_hash=answer['answer_hash'],
                  question_hash=digest(original) if original else None,
                  question_group_hash=preview_question_group_hash(project,question_id),
                  item_hashes={i['id']:digest(i) for g in groups for t in g['targets']
                               for i in (t['before'], t['after']) if i and i['id'] in items},
                  sources=_source_snapshot(project, source_refs))
    output=dict(expected_revision=project['revision'], question_id=question_id,
                answer_id=answer['id'], answer_text=answer['text'], answer_hash=answer['answer_hash'],
                answer_source_refs=copy.deepcopy(answer.get('source_refs') or []),
                decisions=[dict(**d, coverage=by_coverage[d['id']]['status'],
                                answer_quote=by_coverage[d['id']].get('answer_quote') or '',
                                remaining=by_coverage[d['id']]['status'] != 'clear') for d in decisions],
                groups=groups, blockers=[], legacy_split=bool(legacy), snapshot=snapshot)
    prepare_implementations(project,output,payload.get('answer_implementations'))
    output['preview_hash']=digest(output)
    return output


def validate_snapshot(project, preview):
    snap=preview['snapshot']
    require(project['revision']==snap['revision'], 'STALE_REVISION', '项目版本变化，请重新核对', 409)
    answer=_answer(project,preview['question_id'],preview.get('answer_id'))
    require(answer['answer_hash']==snap['answer_hash'], 'STALE_ANSWER', '回答已变化，请重新核对', 409)
    original=next((q for q in project['questions'] if q['id']==preview['question_id']),None)
    require((digest(original) if original else None)==snap['question_hash'],
            'STALE_QUESTION', '原问题已变化，请重新核对', 409)
    if 'question_group_hash' in snap:
        require(preview_question_group_hash(project,preview['question_id'])==snap['question_group_hash'],
                'STALE_QUESTION', '决定组问题已变化，请重新核对', 409)
    items={i['id']:i for i in project['items']}
    for iid, expected in snap['item_hashes'].items():
        require(iid in items and digest(items[iid])==expected,
                'STALE_CONTENT', '条款或候选已变化：'+iid, 409)
    refs=[dict(source_id=s['source_id'],excerpt_id=s['excerpt_id']) for s in snap['sources']]
    require(_source_snapshot(project,refs)==snap['sources'],
            'STALE_SOURCE', '来源版本已变化，请重新核对', 409)


def apply_review(project, preview, selected_group_ids, store=None):
    groups={g['id']:g for g in preview['groups']}
    selected=set(selected_group_ids)
    require(len(selected)==len(selected_group_ids) and selected<=set(groups),
            'REFERENCE_INVALID', '所选修改组无效或重复', 409)
    for gid in selected:
        require(set(groups[gid]['dependent_group_ids'])<=selected,
                'DEPENDENCY_REQUIRED', '关联修改组必须一起提交：'+gid, 409)
    items={i['id']:i for i in project['items']}
    manual = preview.get('review_mode') == 'manual_clause_revision'
    def reviewed_behavior(item):
        from .requirements import behavior_fingerprint
        after=copy.deepcopy(item)
        after['behavior_review']=dict(content_hash=behavior_fingerprint(after),
            actor='authenticated_local_user',reviewed_at=now(),method='decision_review')
        return after
    touched=set()
    for gid in selected:
        for target in groups[gid]['targets']:
            require(target['id'] not in touched, 'REFERENCE_INVALID',
                    '多个修改组同时改同一条款：'+target['id'], 409)
            touched.add(target['id'])
            candidate=items.get(target['candidate_id']) if target['candidate_id'] else None
            if candidate:
                require(candidate.get('selection_status')=='candidate',
                        'STALE_CONTENT', '候选已处理', 409)
                require_behavior_review(reviewed_behavior(candidate) if target.get('review_behavior') else candidate,project)
            else:
                current=items[target['id']]
                require(current.get('selection_status')=='selected',
                        'REVIEW_REQUIRED', '请先采纳或修订条款：'+target['id'], 409)
                if manual:
                    from .manual_decision_review import validate_manual_target
                    validate_manual_target(project, target)
                else:
                    require_behavior_review(reviewed_behavior(current) if target.get('review_behavior') else current,project)
    # The immutable preview records which displayed behaviors the final human
    # confirmation covers. Validate all selected changes before recording it.
    if not manual:
        for gid in selected:
            for target in groups[gid]['targets']:
                if target.get('review_behavior'):
                    item=items[target['candidate_id'] or target['id']]
                    item['behavior_review']=reviewed_behavior(item)['behavior_review']
    # Manual edits are only applied after all selected targets pass validation.
    # An answer confirmation does not silently approve old behavior details.
    if manual:
        from .manual_decision_review import manual_edit_text
        from .requirements import behavior_fingerprint
        from .sources import save_source
        for gid in selected:
            for target in groups[gid]['targets']:
                current=items[target['id']]
                authored=manual_edit_text(target['before'],target['after'])
                current['statement']=target['after']['statement']
                if 'behavior' in target['after']:
                    current['behavior']=copy.deepcopy(target['after']['behavior'])
                if authored:
                    require(store is not None,'INPUT_REQUIRED','人工修改须保存可追溯的来源')
                    source=save_source(store,'人工核对回答并修订.txt',authored.encode('utf-8'),'goal')
                    project['sources'].append(source)
                    current['source_refs']=list(current.get('source_refs') or [])+[
                        dict(source_id=source['id'],excerpt_id=excerpt['id']) for excerpt in source['excerpts']]
                if target.get('review_behavior'):
                    current['behavior_review']=dict(content_hash=behavior_fingerprint(current),
                        actor='authenticated_local_user',reviewed_at=now(),method='manual_decision_review')
    by_id={q['id']:q for q in project['questions']}
    mapped_answer_id=preview.get('answer_id')
    if preview['legacy_split']:
        parent=by_id[preview['question_id']]
        group_id='QG-'+digest(parent['id'])[:16]
        new_ids=[]
        for d in preview['decisions']:
            q=dict(id=d['id'],topic_key=parent.get('topic_key'),question=d['question'],
                   why=parent.get('why',''),options=[],blocking=parent.get('blocking',False),
                   blocking_stage=parent.get('blocking_stage',3),
                   source_refs=copy.deepcopy(parent.get('source_refs',[])),
                   related_refs=copy.deepcopy(d['related_refs']),status='open',answer=None,
                   decision_group_id=group_id,
                   decision_group_title=parent['question'],decision_coverage='pending',
                   prior_question_id=parent['id'])
            project['questions'].append(q);by_id[q['id']]=q;new_ids.append(q['id'])
        mapped_answer_id=ident('DA')
        project.setdefault('decision_answers',[]).append(dict(
            id=mapped_answer_id,question_id=group_id,decision_ids=new_ids,
            text=preview['answer_text'],answer_hash=preview['answer_hash'],
            source_refs=copy.deepcopy(preview.get('answer_source_refs') or []),
            created=now(),mapped_from_question_id=parent['id'],
            mapped_from_answer_id=preview.get('answer_id')))
        parent.update(superseded_by=new_ids,split_at=now(),
                      split_review=dict(actor='authenticated_local_user',reviewed_at=now(),
                                        answer_hash=preview['answer_hash']))
    for d in preview['decisions']:
        q=by_id[d['id']]
        if d['coverage']=='clear':
            from .understanding import record_answer
            if q.get('answer') != d['answer_quote']:
                record_answer(q)
            q.update(answer=d['answer_quote'],status='answered',answered_at=now(),
                     answer_source_refs=copy.deepcopy(preview.get('answer_source_refs') or []))
        else:
            if not (d['id']==preview['question_id'] and not preview['legacy_split']):
                q['status']='open'
                q['answer']=None
            q['understanding_status']='pending'
        q['decision_coverage']=d['coverage']
        q['decision_answer_id']=mapped_answer_id
        q['decision_coverage_review']=dict(answer_hash=digest(q.get('answer')),
                                           source_answer_hash=preview['answer_hash'],
                                           question_hash=question_fingerprint(q),
                                           quote=d['answer_quote'],reviewed_at=now(),
                                           actor='authenticated_local_user')
    for gid in selected:
        for target in groups[gid]['targets']:
            cid=target['candidate_id']
            if not cid:continue
            candidate=items[cid]
            target_id=candidate.get('target_item_id')
            if target_id:
                original=items[target_id]
                require(answer_target(project,by_id[groups[gid]['decision_ids'][0]],candidate) is not None
                        or not candidate.get('answer_refs'), 'REFERENCE_INVALID',
                        '候选与决定绑定不一致', 409)
                for key in ('title','statement','applies_to','epistemic_status','source_refs','related_refs',
                            'behavior','change_type','scope_evidence','classification_reason',
                            'behavior_review_required','behavior_review'):
                    if key in candidate:original[key]=copy.deepcopy(candidate[key])
                original['selection_status']='selected'
                if candidate.get('answer_refs'):
                    accept_answer_update(project,candidate,before=target['before'])
                candidate['selection_status']='deferred'
                for linked in project['items']+project['questions']:
                    linked['related_refs']=[target_id if ref==cid else ref for ref in linked.get('related_refs',[])]
                for option in project.get('options',[]):
                    option['proposed_item_refs']=[target_id if ref==cid else ref for ref in option.get('proposed_item_refs',[])]
            else:
                candidate['selection_status']='selected'
    for d in preview['decisions']:
        q=by_id[d['id']]
        selected_for_q=[g for g in groups.values() if g['id'] in selected and d['id'] in g['decision_ids']]
        all_for_q=[g for g in groups.values() if d['id'] in g['decision_ids']]
        q['decision_pending_group_ids']=sorted(g['id'] for g in all_for_q if g['id'] not in selected)
        direct_targets={item['id'] for item in answer_targets(project,q)}
        reviewed_targets={target['id'] for g in selected_for_q for target in g['targets']}
        if (d['coverage']=='clear' and all_for_q and len(selected_for_q)==len(all_for_q)
                and direct_targets <= reviewed_targets):
            from .answer_implementation import implementation_records, validate_implementations
            q['answer_implementation_contract']=2
            try:
                grouped=validate_implementations(project,q,(preview.get('answer_implementations') or {}).get(q['id'],[]))
            except Problem:
                q['understanding_status']='pending'
                q['applied_item_ids']=[]
                q['applied_requirement_ids']=[]
                q['answer_effect']='回答已明确，仍须选择并核对其在直接关联条款中的落实原文'
            else:
                q['answer_applications']=implementation_records(project,q,grouped,method='decision_review')
                q['applied_item_ids']=sorted(grouped)
                q['applied_requirement_ids']=sorted(i['id'] for i in answer_targets(project,q) if i['kind']=='requirement')
                q['understanding_status']='applied'
        else:
            q['answer_implementation_contract']=2
            q['applied_item_ids']=[]
            q['applied_requirement_ids']=[]
            q['understanding_status']='pending'
    return dict(applied_group_ids=sorted(selected),pending_group_ids=sorted(set(groups)-selected),
                clear_decision_ids=sorted(d['id'] for d in preview['decisions'] if d['coverage']=='clear'),
                implementation_pending_ids=sorted(d['id'] for d in preview['decisions']
                    if d['coverage']=='clear' and by_id[d['id']].get('understanding_status')!='applied'),
                pending_decision_ids=sorted(d['id'] for d in preview['decisions'] if d['coverage']!='clear'))
