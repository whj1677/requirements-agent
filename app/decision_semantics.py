"""One bounded semantic-review request and strict application of its proposal.

The model proposes independent decisions; this module proves only identities,
current input hashes, citation continuity and structural completeness.
"""
import copy

from .core import Problem, digest, dumps, now, require
from .decision_review import append_model_proposal, review_question_hash
from .domain_context import active_source_ids
from .understanding import question_fingerprint, save_questions

STATUSES = {'pending', 'partial', 'clear', 'conflict'}


def _active(q):
    return not q.get('superseded_by') and not q.get('out_of_scope_reason')


def _answer(project, question_id, leaves):
    ids={q.get('decision_answer_id') for q in leaves if q.get('decision_answer_id')}
    require(len(ids)<=1,'REFERENCE_INVALID','决定组关联了不同原话，不能合并复核',409)
    if ids:
        aid=next(iter(ids))
        require(all(q.get('decision_answer_id')==aid for q in leaves),
                'STALE_ANSWER','决定组仅部分子项绑定当前原话',409)
        row=next((a for a in project.get('decision_answers',[])
                  if a['id']==aid and a['question_id']==question_id),None)
        require(row is not None and set(row.get('decision_ids',[]))=={q['id'] for q in leaves},
                'STALE_ANSWER','原话决定组身份已变化，请重新保存或核对',409)
        return row['text'],aid,copy.deepcopy(row.get('source_refs') or [])
    if len(leaves)==1 and isinstance(leaves[0].get('answer'),str) and leaves[0]['answer'].strip():
        q=leaves[0]
        return q['answer'],None,copy.deepcopy(q.get('answer_source_refs') or [])
    return None,None,[]


def _targets(staged,before):
    old={q['id']:q for q in before.get('questions',[])}
    roots=[];seen=set()
    for q in staged.get('questions',[]):
        if not _active(q):continue
        root=q.get('decision_group_id') or q['id']
        if root not in seen:seen.add(root);roots.append(root)
    targets=[]
    for root in roots:
        leaves=[q for q in staged['questions'] if _active(q) and
                (q.get('decision_group_id') or q['id'])==root]
        text,answer_id,answer_refs=_answer(staged,root,leaves)
        changed=any(q['id'] not in old or
                    question_fingerprint(q)!=question_fingerprint(old[q['id']]) or
                    q.get('decision_answer_id')!=old[q['id']].get('decision_answer_id') or
                    q.get('answer')!=old[q['id']].get('answer') or
                    q.get('answered_at')!=old[q['id']].get('answered_at') for q in leaves)
        pending_answer=(text is not None and any(q.get('decision_coverage')=='pending' for q in leaves)
                        and not proposal_current(staged,root))
        if not changed and not pending_answer:continue
        new_single=(len(leaves)==1 and root==leaves[0]['id']
                    and leaves[0]['id'] not in old and leaves[0].get('status')!='answered'
                    and leaves[0].get('answer') is None)
        targets.append(dict(question_id=root,question_hash=review_question_hash(staged,root),
            answer_id=answer_id,answer_hash=digest(text) if text is not None else None,
            answer_source_refs=answer_refs,decision_ids=[q['id'] for q in leaves],
            question_texts={q['id']:q['question'] for q in leaves},
            can_propose_split=len(leaves)==1,can_apply_split=new_single))
    return targets


def _schema(targets):
    decision=dict(type='object',additionalProperties=False,
        required=['id','question','coverage','answer_quote','related_refs','why'],
        properties=dict(id=dict(type='string',minLength=1),
            question=dict(type='string',minLength=1),coverage=dict(type='string',enum=sorted(STATUSES)),
            answer_quote=dict(type='string'),related_refs=dict(type='array',minItems=1,
                uniqueItems=True,items=dict(type='string',minLength=1)),
            why=dict(type='string',minLength=1)))
    target_constraints=[]
    for target in targets:
        decisions={}
        decisions['items']=dict(allOf=[{
            'if':dict(properties=dict(id=dict(const=qid)),required=['id']),
            'then':dict(properties=dict(question=dict(const=original)))}
            for qid,original in target['question_texts'].items()])
        if target['answer_hash'] is None:
            decisions['items']['properties']=dict(coverage=dict(const='pending'),
                                                  answer_quote=dict(const=''))
        if len(target['decision_ids'])>1:
            ids=target['decision_ids']
            decisions.update(minItems=len(ids),maxItems=len(ids),
                allOf=[dict(contains=dict(properties=dict(id=dict(const=qid)),
                                             required=['id']),minContains=1,maxContains=1)
                       for qid in ids])
            decisions['items'].setdefault('properties',{})['id']=dict(enum=ids)
        if decisions:
            target_constraints.append({
                'if':dict(properties=dict(question_id=dict(const=target['question_id'])),
                          required=['question_id']),
                'then':dict(properties=dict(decisions=decisions))})
    return dict(type='object',additionalProperties=False,required=['reviews'],
        properties=dict(reviews=dict(type='array',minItems=len(targets),maxItems=len(targets),
            items=dict(type='object',additionalProperties=False,
                required=['question_id','decisions'],properties=dict(
                    question_id=dict(type='string',minLength=1),
                    decisions=dict(type='array',minItems=1,items=decision)),
                allOf=target_constraints))))


def build_request(staged,before,sent_excerpts,question_ids=None):
    """Return one batch request, or None when this run changed no target."""
    targets=_targets(staged,before)
    if question_ids is not None:
        # A decision group is atomic. A row-local task must not refresh another
        # row's leaf merely because one sibling happens to be in scope.
        targets=[target for target in targets if set(target['decision_ids']) <= set(question_ids)]
    if not targets:return None
    items={i['id']:i for i in staged.get('items',[])}
    sources={s['id']:s for s in staged.get('sources',[])}
    evidence=[]
    for e in sent_excerpts:
        source=sources.get(e.get('source_id'))
        require(source is not None and not source.get('excluded')
                and not source.get('superseded_by'),
                'REFERENCE_INVALID','语义复核来源不在当前项目',409)
        original=next((x for x in source.get('excerpts',[]) if x['id']==e.get('id')),None)
        require(original is not None and original.get('text')==e.get('text'),
                'REFERENCE_INVALID','语义复核来源身份或正文与已发送片段不一致',409)
        evidence.append(dict(source_id=source['id'],excerpt_id=e['id'],text=e['text'],
                             source_version=source.get('version'),source_hash=source.get('sha256'),
                             locator=e.get('locator')))
    source_snapshot=[dict(source_id=e['source_id'],excerpt_id=e['excerpt_id'],
                          source_version=e['source_version'],source_hash=e['source_hash'],
                          excerpt_hash=digest(next(x for x in sources[e['source_id']]['excerpts']
                                                   if x['id']==e['excerpt_id']))) for e in evidence]
    for target in targets:
        target['source_snapshot']=copy.deepcopy(source_snapshot)
        target['source_selection_hash']=digest(sorted(active_source_ids(staged)))
    contexts=[]
    for target in targets:
        leaves=[q for q in staged['questions'] if q['id'] in target['decision_ids']]
        related={rid for q in leaves for rid in q.get('related_refs',[])}
        clauses=[{key:copy.deepcopy(items[rid].get(key)) for key in
                  ('id','kind','title','statement','behavior','source_refs','related_refs','selection_status')}
                 for rid in sorted(related) if rid in items]
        answer_text,_,_=_answer(staged,target['question_id'],leaves)
        contexts.append(dict(question_id=target['question_id'],
            question_hash=target['question_hash'],answer_hash=target['answer_hash'],
            questions=[{key:copy.deepcopy(q.get(key)) for key in
                       ('id','question','why','options','blocking','blocking_stage',
                        'related_refs','source_refs','status','answer','answer_history')}
                       for q in leaves],answer_text=answer_text,
            answer_source_refs=target['answer_source_refs'],clauses=clauses,
            required_decision_ids=target['decision_ids'] if len(target['decision_ids'])>1 else [],
            original_question_texts=target['question_texts'],
            can_propose_split=target['can_propose_split'],
            can_apply_split=target['can_apply_split']))
    instruction=(
        '你只审查本批已列问题是否含可独立回答的决定，并建议原话逐决定覆盖状态。'
        '所有问题都检查，包括只有一个问号的问题；不要用问号、逗号、页面数机械拆分。'
        '若多处已确认共用同一参数，只需一个共同值决定。'
        '已有事实不重复发问，未知保持待定；不得补造规则、权限、时点、重算或保持项。'
        '每个 question_id 恰好返回一次。已有决定组逐个保留原 id，不增删；'
        '单题若确有多个独立决定，必须完整列出各独立决定并给每项不同临时 id，留给人工一次核对映射；'
        '不能沿用原 id 后把原问题缩窄为已回答的一项，即使其他问题另列为待定也不行。'
        '若仍是一个决定须沿用原 id，且 question 必须逐字等于原问题；已有决定组每个叶沿用原 id 时也须逐字保留该叶原问题。'
        '每个目标的 answer_text 是覆盖判定唯一的当前原话；answer_text 为 null 时，该目标所有决定只能 pending 且 answer_quote 必须为空。'
        'sent_excerpts 是需求资料，questions.answer_history 是旧原话；两者即使含“先回答”“如果回答”等条件示例，也不是本次已保存回答，不能据此标 clear、partial 或 conflict，也不能从中摘取 answer_quote。'
        '只有 answer_text 确实明确回答该决定才可标 clear，answer_quote 必须是 answer_text 的连续原文；'
        'partial、pending、conflict 不等于已答或已落实。'
        'related_refs 只能来自该目标当前相关条款，不改变正文，不复制示例作为业务事实。'
        '这只是模型建议，旧已答问题绝不能自动迁移，最终由用户核对。'
        '只输出符合 schema 的 JSON 对象。')
    schema=_schema(targets)
    user=dict(targets=contexts,sent_excerpts=evidence,response_schema=schema,
              response_contract=dict(root='reviews',target_identity='question_id',
                  decision_fields=['id','question','coverage','answer_quote','related_refs','why'],
                  identity_rule='已有组逐叶保持原 ID；单题一个决定保持原 ID；单题多决定使用本批临时 ID'))
    return dict(messages=[dict(role='system',content=instruction),
                          dict(role='user',content=dumps(user))],
                targets=targets,schema=schema)


def proposal_current(project, question_id):
    """Server-side freshness bit for UI; model proposals never approve coverage."""
    try:
        leaves=[q for q in project['questions'] if _active(q) and
                (q.get('decision_group_id') or q['id'])==question_id]
        if not leaves:return False
        proposal=leaves[0].get('decision_review_proposal') or {}
        if proposal.get('question_id')!=question_id:return False
        if (not isinstance(proposal.get('source_snapshot'),list)
                or proposal.get('source_selection_hash')!=digest(sorted(active_source_ids(project)))
                or not _sources_current(project,proposal['source_snapshot'])):
            return False
        answer,answer_id,_=_answer(project,question_id,leaves)
        return (proposal.get('question_hash')==review_question_hash(project,question_id)
                and proposal.get('answer_hash')==(digest(answer) if answer is not None else None)
                and proposal.get('answer_id')==answer_id)
    except (Problem,KeyError,TypeError):
        return False


def _sources_current(project,snapshot):
    sources={s['id']:s for s in project.get('sources',[])}
    for snap in snapshot:
        if not isinstance(snap,dict):return False
        source=sources.get(snap.get('source_id'))
        excerpt=next((e for e in source.get('excerpts',[])
                      if e['id']==snap.get('excerpt_id')),None) if source else None
        if (source is None or excerpt is None or source.get('excluded')
                or source.get('superseded_by')
                or source.get('version')!=snap.get('source_version')
                or source.get('sha256')!=snap.get('source_hash')
                or digest(excerpt)!=snap.get('excerpt_hash')):
            return False
    return True


def _validate(staged,raw_audit,targets):
    require(isinstance(raw_audit,dict) and set(raw_audit)=={'reviews'}
            and isinstance(raw_audit['reviews'],list),
            'SCHEMA_INVALID','语义复核必须返回 reviews 列表',409)
    expected={t['question_id']:t for t in targets}
    reviews=raw_audit['reviews']
    require(len(reviews)==len(expected) and all(isinstance(r,dict) for r in reviews)
            and all(isinstance(r.get('question_id'),str) for r in reviews)
            and {r.get('question_id') for r in reviews}==set(expected),
            'REFERENCE_INVALID','语义复核遗漏或重复目标',409)
    by_id={q['id']:q for q in staged['questions']}
    existing_ids=set(by_id)
    proposed_ids=set()
    normalized=[]
    for review in reviews:
        require(set(review)=={'question_id','decisions'},'SCHEMA_INVALID',
                '语义复核目标字段不符合契约',409)
        target=expected[review['question_id']]
        require(target.get('source_selection_hash')==digest(sorted(active_source_ids(staged)))
                and _sources_current(staged,target.get('source_snapshot') or []),
                'STALE_SOURCE','语义复核来源或选择已变化',409)
        require(review_question_hash(staged,target['question_id'])==target['question_hash'],
                'STALE_QUESTION','复核期间问题已变化',409)
        current_ids={q['id'] for q in staged['questions'] if _active(q) and
                     (q.get('decision_group_id') or q['id'])==target['question_id']}
        require(current_ids==set(target['decision_ids']),
                'STALE_QUESTION','决定组叶子身份已变化',409)
        leaves=[by_id[qid] for qid in target['decision_ids']]
        answer,answer_id,_=_answer(staged,target['question_id'],leaves)
        require((digest(answer) if answer is not None else None)==target['answer_hash']
                and answer_id==target['answer_id'],
                'STALE_ANSWER','复核期间原话已变化',409)
        rows=review.get('decisions')
        require(isinstance(rows,list) and rows and all(isinstance(row,dict) for row in rows),
                'SCHEMA_INVALID','每个目标须列出独立决定',409)
        ids=[row.get('id') for row in rows]
        require(all(isinstance(x,str) and x.strip() for x in ids)
                and len(ids)==len(set(ids)), 'REFERENCE_INVALID','决定 ID 为空或重复',409)
        group=len(target['decision_ids'])>1
        if group:
            require(set(ids)==set(target['decision_ids']) and len(rows)==len(ids),
                    'REFERENCE_INVALID','已有决定组须逐叶完整复核，不能改身份',409)
        elif len(rows)==1:
            require(ids==target['decision_ids'],'REFERENCE_INVALID',
                    '单一决定须保留原 ID',409)
        else:
            require(target['can_propose_split'],'REFERENCE_INVALID',
                    '此目标不能提出新拆分',409)
            require(not (set(ids) & existing_ids), 'IDENTITY_CONFLICT',
                    '新决定临时 ID 不能冒用已有问题 ID',409)
        require(not (set(ids) & proposed_ids),'REFERENCE_INVALID',
                '不同目标复用了同一决定 ID',409)
        proposed_ids.update(ids)
        allowed={rid for q in leaves for rid in q.get('related_refs',[])}
        require(allowed <= {i['id'] for i in staged['items']},
                'REFERENCE_INVALID','原问题关联条款已不存在',409)
        if target['can_apply_split']:
            require(len(leaves)==1 and leaves[0]['status']!='answered'
                    and leaves[0].get('answer') is None,
                    'REVIEW_REQUIRED','已有回答不能自动拆分或迁移',409)
        actual_refs=set()
        result=[]
        for row in rows:
            require(set(row)=={'id','question','coverage','answer_quote','related_refs','why'}
                    and isinstance(row['question'],str) and row['question'].strip()
                    and isinstance(row['why'],str) and row['why'].strip()
                    and isinstance(row['coverage'],str) and row['coverage'] in STATUSES
                    and isinstance(row['answer_quote'],str),
                    'SCHEMA_INVALID','决定字段或覆盖状态无效',409)
            if row['id'] in target['question_texts']:
                require(row['question']==target['question_texts'][row['id']],
                    'SEMANTIC_BLOCKED',
                    '沿用原决定ID时必须逐字保留原问题；复合旧题须完整提出多决定映射，不能缩窄原题',409)
            refs=row['related_refs']
            require(isinstance(refs,list) and refs and all(isinstance(x,str) for x in refs)
                    and len(refs)==len(set(refs)) and set(refs)<=allowed,
                    'REFERENCE_INVALID','决定关联条款不属于原问题',409)
            actual_refs.update(refs)
            quote=row['answer_quote']
            require((not quote or answer is not None and quote in answer)
                    and (row['coverage']!='clear' or bool(quote))
                    and (answer is not None or row['coverage']=='pending' and not quote),
                    'REFERENCE_INVALID','覆盖引文不是当前原话连续片段或无答案却标明确',409)
            result.append(copy.deepcopy(row))
        if target['can_apply_split'] and len(rows)>1:
            require(actual_refs==allowed,'REFERENCE_INVALID',
                    '自动拆分不得遗漏原问题关联条款',409)
        normalized.append((target,review['question_id'],result))
    return normalized


def apply_audit(staged,raw_audit,targets):
    """Validate the entire batch, then mutate staged once. No external IO."""
    require(targets and len({t['question_id'] for t in targets})==len(targets),
            'REFERENCE_INVALID','语义复核目标为空或重复',409)
    normalized=_validate(staged,raw_audit,targets)
    working=copy.deepcopy(staged)
    split_ids=[];proposal_ids=[]
    for target,root,rows in normalized:
        by_id={q['id']:q for q in working['questions']}
        if len(rows)>1 and target['can_apply_split']:
            old=by_id[root]
            children=[]
            for index,row in enumerate(rows):
                child_id='Q-'+digest([root,index,row['question']])[:16]
                require(child_id not in by_id,'IDENTITY_CONFLICT',
                        '语义复核拆分产生重复决定 ID',409)
                children.append(dict(temp_id=child_id,topic_key=str(old.get('topic_key') or root)+'-'+str(index+1),
                    question=row['question'],why=row['why'],options=[],
                    blocking=bool(old.get('blocking')),blocking_stage=old.get('blocking_stage',3),
                    source_refs=copy.deepcopy(old.get('source_refs') or []),
                    related_refs=copy.deepcopy(row['related_refs'])))
            parent=dict(temp_id='QG-'+digest(root)[:16],target_question_id=root,
                        question=old['question'],blocking=bool(old.get('blocking')),
                        decision_points=children)
            save_questions(working,[parent])
            old['decision_semantic_review']=dict(question_hash=target['question_hash'],
                answer_hash=None,decision_ids=[c['temp_id'] for c in children],
                status='model_structural_proposal',reviewed_at=now())
            split_ids.append(root)
            continue
        audit=dict(question_id=root,question_hash=target['question_hash'],
                   answer_id=target['answer_id'],answer_hash=target['answer_hash'],
                   source_snapshot=copy.deepcopy(target['source_snapshot']),
                   source_selection_hash=target['source_selection_hash'],decisions=rows)
        if target['answer_hash'] is not None:
            require(append_model_proposal(working,audit), 'REFERENCE_INVALID',
                    '语义建议未能绑定当前原话',409)
        else:
            for qid in target['decision_ids']:
                by_id[qid]['decision_review_proposal']=dict(**copy.deepcopy(audit),
                    status='proposal_only',stored_at=now())
        proposal_ids.append(root)
    staged.clear();staged.update(working)
    return dict(reviewed_question_ids=[root for _,root,_ in normalized],
                split_question_ids=split_ids,proposal_question_ids=proposal_ids)
