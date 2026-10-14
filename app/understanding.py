"""Evidence-backed change classifications and stable, atomic decision records."""
import copy
import unicodedata
import re
from .core import Problem, require, now, digest
from .requirements import TRACKED

ANSWER_TARGET_KINDS = (*TRACKED, "constraint", "goal")


def contract():
    return dict(version='understanding-3',
        classification_separation_example=dict(
            instruction='同项同时含本期变化和既有保持时不能混写change/preserve。可分离的变化及保持约束分别引用各自原句；变更原条目继续revise原ID。未知现状留未知，不把新要求编成current。示例仅演示分类，不能作为业务来源或新增交付。',
            change=dict(kind='requirement',change_type='new',scope_states=['change']),
            preserve=dict(kind='constraint',change_type='preserved',scope_states=['preserve'])),
        classification='功能需求需分别表达现状/current、变化/change、保持/preserve；scope_evidence逐条引用本次真实片段的原文quote及source_ref。current+change=modified，只有change=new，只有preserve=preserved，只有current=existing；没有充分依据则unspecified。一个功能的权限、分页、排序等保持性约束可写在该功能的behavior或关联rule中供PM核对，不要把每个字段维度机械拆成独立requirement；单条requirement的scope_evidence不得混写change与preserve状态，也不得把保持内容说成新增。引用原句同时含变化和保持时保留原文，change_type只对应本条statement的变化身份。classification_reason说明判定依据及未知。applies_to=as_is仅用于existing。原条目变化用revise/target_item_id，不新建同义编号。',
        questions='一个问题只问一个可独立回答的决定。复合问题必须列出decision_points（完整Question对象，各自独立temp_id/topic_key/关联需求/阻塞属性），程序展开为独立回答。target_question_id仅用于明确拆分可追问的未答问题，decision_points必须非空；已答决定使用同类revise候选的answer_refs落实，不输出target_question_id重问或拆分。原问题及回答保留；独立的新未知使用target_question_id=null另建问题。失效/超范围回答不能用于落实当前条款。已答问题若问题文字或风险条件发生变化，不能假定原答案仍适用；材料已回答的事项不要再次提问，编号错字记findings。',
        answer_binding='answer_refs只用于基于已保存回答修订该问题直接关联的原requirement/rule/acceptance/constraint：action=revise、target_item_id为该问题related_refs中的原条目ID，kind必须与目标一致，不能指向另一修订候选。使用本轮真实回答片段的source_refs说明依据；新增acceptance等action=add候选不得填写answer_refs，其回答依据放source_refs并用related_refs关联原需求。保留原条目编号与内容，回答不会自动采纳候选；所有直接关联条目的修订明确采纳后才闭环。',
        question_shape='普通单问题省略decision_points或写空数组[]；不能写仅含一个子项的数组。只有确有两项及以上独立决定时才用decision_points分组，每个子项都要是完整Question。',
        question_self_check='输出前检查每个可回答问题只包含一个决定、至多一个问号。'
            '例如“在哪里编辑？只读用户如何展示？”是两个决定，必须从首轮就分别写成两个完整questions项，'
            '或保留父问题并用decision_points放两个完整子项；不能把两个决定混入同一组选项。'
            '判断依据是可以独立回答的决定数量，不是问号、逗号或“和”的数量。'
            '单问号的“版本能否追溯生效，迟到数据如何选择版本并重算？”也含独立决定：'
            '能否追溯生效、迟到数据选择哪个版本、迟到数据怎样触发重算，须分别表达。'
            '反例：“站点总览和设备详情统一使用哪个刷新周期？”若已确定两处必须一致，只问一个共同刷新值，不应按两个页面拆问。'
            '示例只解释结构，不得复制到项目问题。材料已经说明的编辑入口或权限不得重复追问。',
        single_question_example=dict(temp_id='QTMP-example',topic_key='example',question='一个待决定的问题？',why='影响实现或验收',
            options=[],blocking=True,related_refs=['实际需求ID'],source_refs=[],decision_points=[]),
        classification_shape='kind=requirement时，change_type为new/modified/preserved/existing必须有非空scope_evidence；每条须有state、逐字quote、真实source_ref。没有充分材料依据时用unspecified并可用空scope_evidence，不能猜测分类。示例只说明字段，禁止复制占位文本或ID。',
        classification_self_check='输出前逐条核对 requirement 的 scope_evidence.state 集合与 change_type 是否一致。'
            '例如同一条需求同时引用“现有列表展示姓名”的 current 证据和“本次新增备注字段”的 change 证据，'
            '必须是 modified；句子中出现“新增”不改变 current+change=modified 的规则。'
            '只有真实证据仅描述本期全新功能、没有该功能现状证据时才用 new。'
            '不得为凑 new 删除真实 current 证据；此例只解释分类规则，不得复制其业务内容。',
        classification_example=dict(change_type='new',scope_evidence=[dict(state='change',quote='本轮真实片段的原文',
            source_ref=dict(source_id='实际来源ID',excerpt_id='实际片段ID'))],classification_reason='依据所引变化原文'),
        revision_identity='action=revise 表示修订需求记录，change_type 表示本期功能相对真实系统现状的变化，两者独立。补齐一个尚未实现的新增功能的回答或验收，仍是 new；不能因底稿里已经有该候选就变成 modified。只有来源同时说明该功能已有行为和本期改动，才是 modified。原候选、上轮模型输出和本次修订动作都不是 current 的业务现状证据。scope_evidence 的 quote 必须来自同一片段的连续原文，不拼接省略句。',
        revision_dependencies='已保存回答变更时，逐项核对关联的已采纳需求、规则和验收。旧值仍在已采纳验收里时，对该原验收提出 revise，不另建同义验收而遗留旧值；未直接绑定该问题的条款不能填写 answer_refs，其修订依据使用本轮真实回答的 source_refs。尚未采纳的未来功能不能写成 as_is 现状。',
        source_pair_check='每一个 source_id 与 excerpt_id 必须成对复制自本轮 excerpts 的同一条记录。不同文件的片段不能共用另一文件的 source_id；used_source_refs 同样逐对核对，不能凭编号外观拼接。',
        boundaries='每个问题关联实际requirement、rule或acceptance。回答只解除该决定的未知，关联业务条款更新需明确采纳修订候选；selected不代表正式业务批准。')


def answer_targets(p,q):
    """Only the directly bound original clauses can discharge this decision."""
    return [i for i in p['items'] if i['kind'] in ANSWER_TARGET_KINDS and not i.get('target_item_id')
            and i['id'] in q.get('related_refs',[])]


def answer_target(p,q,candidate):
    return next((i for i in answer_targets(p,q)
                 if i['id']==candidate.get('target_item_id')
                 and i['kind']==candidate.get('kind') and candidate.get('action')=='revise'),None)


def active_question(q):
    return not q.get('superseded_by') and not q.get('out_of_scope_reason') and not q.get('prewrite_resolution')


def current_answer(q):
    return active_question(q) and q.get('status')=='answered' and q.get('answer') is not None


def current_decision_answer(p,q):
    """Current saved QG sentence for candidate binding, not leaf coverage."""
    if (p.get('workflow_mode')!='shared-draft' or not active_question(q)
            or not q.get('decision_group_id') or not q.get('decision_answer_id')):
        return None
    group=q['decision_group_id']
    leaves=[part for part in p['questions'] if active_question(part)
            and part.get('decision_group_id')==group]
    if not leaves or any(part.get('decision_answer_id')!=q['decision_answer_id']
                         for part in leaves):
        return None
    rows=[row for row in p.get('decision_answers',[])
          if row.get('id')==q['decision_answer_id'] and row.get('question_id')==group]
    if len(rows)!=1:return None
    row=rows[0]
    if (not isinstance(row.get('text'),str) or not row['text'].strip()
            or row.get('answer_hash')!=digest(row['text'])
            or set(row.get('decision_ids') or [])!={part['id'] for part in leaves}):
        return None
    return row


def answer_bindable(p,q):
    return bool(current_decision_answer(p,q)) if q.get('decision_group_id') else current_answer(q)


def candidate_answer_binding_current(p,q,candidate):
    if q.get('decision_group_id'):
        row=current_decision_answer(p,q)
        return bool(row and (candidate.get('decision_answer_bindings') or {}).get(q['id'])
                    ==dict(answer_id=row['id'],answer_hash=row['answer_hash']))
    return bool(current_answer(q) and (candidate.get('answer_versions') or {}).get(q['id'])
                ==digest(q['answer']))


def question_fingerprint(q):
    """Meaning-bearing question identity, independent of answer and UI state."""
    value={key:q.get(key) for key in ('id','topic_key','question','why','options',
        'blocking','blocking_stage','related_refs','source_refs','decision_group_id')}
    # Preserve historical fingerprints unless the question has explicit anchors.
    if 'unresolved_locations' in q:
        value['unresolved_locations']=q['unresolved_locations']
    return digest(value)


def coverage_complete(q):
    """Do not promote a newly saved sentence to a reviewed decision.

    Unmarked historical single questions keep their existing behavior; a
    marked question needs an explicit current human coverage review.
    """
    if not current_answer(q):return False
    if 'decision_coverage' not in q:
        proposal=q.get('decision_review_proposal') or {}
        if (len(proposal.get('decisions') or [])>1
                and proposal.get('question_hash')==digest([question_fingerprint(q)])
                and proposal.get('answer_hash')==digest(q['answer'])):
            return False
        return True
    review=q.get('decision_coverage_review') or {}
    return (q['decision_coverage']=='clear' and review.get('answer_hash')==digest(q['answer'])
            and review.get('question_hash')==question_fingerprint(q))


def question_action_contract(p):
    """Derived action permissions; never discard historical questions or answers."""
    return dict(
        answered_decision_ids=[q['id'] for q in p['questions'] if current_answer(q)],
        saved_group_answer_decision_ids=[q['id'] for q in p['questions']
                                         if current_decision_answer(p,q)],
        splittable_question_ids=[q['id'] for q in p['questions'] if active_question(q)
                                 and not q.get('decision_group_id')
                                 and q.get('status')!='answered' and q.get('answer') is None],
        inactive_question_ids=[q['id'] for q in p['questions'] if not active_question(q)
                               or (q.get('status')=='answered')!=(q.get('answer') is not None)],
        instruction='已答决定保留身份与原回答；saved_group_answer_decision_ids仅表示决定组原话已保存，子决定仍待逐项核对。两类当前原话均可通过同kind revise/answer_refs提出待人工核对候选，但不自动采纳或把组内全部决定标为已答；target_question_id只允许splittable_question_ids中的未答单题且须有decision_points。新的独立未知用null目标；失效记录仅供历史核对，不恢复或采纳。')


def leaves(questions):
    return [child for q in questions for child in (q.get('decision_points') or [q])]


def normal(text):
    return ''.join(c for c in unicodedata.normalize('NFKC',text).lower() if c.isalnum())


def equivalent(a,b):
    # Topic is only a grouping hint; it cannot prove that two answered decisions mean the same thing.
    same_subject=bool(set(a.get('related_refs',[])) & set(b.get('related_refs',[]))) or not (a.get('related_refs') or b.get('related_refs'))
    return (a['topic_key']==b['topic_key'] and same_subject) or normal(a['question'])==normal(b['question'])


def question_ids(p, questions, item_mapping):
    """Resolve each question once, before any proposal or question refs are rewritten."""
    from .core import ident
    from .workflow import replace_refs
    mapping={}
    planned=[q for q in p['questions'] if not q.get('superseded_by')]
    for parent in questions:
        split_targets={q.get('target_question_id') for q in questions if q.get('decision_points')}
        children=parent.get('decision_points') or [parent]
        for child in children:
            candidate=replace_refs(child,item_mapping)
            prior=next((q for q in planned if q['id'] not in split_targets and equivalent(q,candidate)
                        and not (q.get('status')=='answered' and
                                 (normal(q['question'])!=normal(candidate['question'])
                                  or candidate['blocking'] and not q.get('blocking')
                                  or candidate.get('blocking_stage',3)<q.get('blocking_stage',3)
                                  or candidate.get('options',[])!=q.get('options',[])))),None)
            stable=prior['id'] if prior else ident('Q')
            mapping[child['temp_id']]=stable
            if not prior:
                planned.append(dict(candidate,id=stable))
        # A grouping prompt has no answer identity. In particular it must never
        # silently inherit the first child's identity in answer_refs.
    return mapping


def classification(item):
    states={e['state'] for e in item.get('scope_evidence',[])}
    require(not ('preserve' in states and 'change' in states),'SEMANTIC_BLOCKED',
            item['temp_id']+' 同一需求混入保持与改变，请拆分后由用户核对，不能整项标为新增')
    if not states:return 'unspecified'
    if 'change' in states:return 'modified' if 'current' in states else 'new'
    if 'preserve' in states:return 'preserved'
    return 'existing'


def pure_preservation_quote(quote):
    """Only reject change claims supported solely by explicit preservation clauses."""
    clauses=[part.strip() for part in re.split(r'[。；;，,\n]+',quote) if part.strip()]
    preservation=re.compile(r'保持[^。；，,\n]{0,30}(?:一致|不变|现状)|沿用原有|不改动')
    explicit_change=re.compile(r'新增|增加|修改|调整|改变|变更|改为|替换|支持|提供|改进')
    return bool(clauses) and all(preservation.search(part) and not explicit_change.search(part) for part in clauses)


def normalize_classification_labels(value, excerpts):
    """Correct only a redundant new label contradicted by its own valid evidence.

    Statements, evidence states/quotes, scope and approval stay exactly as emitted.
    Preservation conflicts and unverifiable citations still reach the strict gate.
    The raw provider output remains in call evidence.
    """
    result = copy.deepcopy(value)
    changes = []
    if not isinstance(result, dict) or not isinstance(result.get('proposals'), list):
        return result, changes
    actual = {(e.get('source_id'), e.get('id')): e.get('text', '') for e in excerpts}
    for item in result['proposals']:
        if not isinstance(item, dict) or item.get('kind') != 'requirement' or item.get('change_type') != 'new' or item.get('applies_to') != 'to_be':
            continue
        support = item.get('scope_evidence')
        if not isinstance(support, list) or not support or not all(isinstance(e, dict) for e in support):
            continue
        if {e.get('state') for e in support} != {'current', 'change'}:
            continue
        verified = True
        for evidence in support:
            ref, quote = evidence.get('source_ref'), evidence.get('quote')
            text = actual.get((ref.get('source_id'), ref.get('excerpt_id')), '') if isinstance(ref, dict) else ''
            if not isinstance(quote, str) or not quote.strip() or quote not in text or (evidence['state'] == 'change' and pure_preservation_quote(quote)):
                verified = False
                break
        if verified:
            item['change_type'] = 'modified'
            changes.append(dict(temp_id=item.get('temp_id'), original='new', derived='modified'))
    return result, changes


def validate(value,p,excerpts):
    if value['stage'] not in ('ingest','clarify','change'):return
    actual={(e['source_id'],e['id']):e['text'] for e in excerpts}
    for i in value['proposals']:
        if i['kind']=='requirement':
            for e in i.get('scope_evidence',[]):
                ref=e['source_ref'];text=actual.get((ref['source_id'],ref['excerpt_id']),'')
                require(e['quote'] in text,'REFERENCE_INVALID',i['temp_id']+' 分类依据不是所引片段原文')
                # A narrow contradiction check, not a general keyword classifier.
                # Ambiguous mixed clauses must be split/reviewed, never auto relabeled.
                require(not (e['state']=='change' and pure_preservation_quote(e['quote'])),'SEMANTIC_BLOCKED',
                        i['temp_id']+' 变化依据仅含明确保持表述，不能把保持事项标为新增/修改')
            expected=classification(i)
            require(i.get('change_type','unspecified')==expected,'SEMANTIC_BLOCKED',
                    i['temp_id']+' 分类缺少依据或与依据冲突；应为 '+expected+'，不能凭功能描述推定新增')
            require(expected=='unspecified' or (i['applies_to']=='as_is')==(expected=='existing'),
                    'SEMANTIC_BLOCKED',i['temp_id']+' 现状/目标身份与分类不一致')
        for qid in i.get('answer_refs',[]):
            q=next((q for q in p['questions'] if q['id']==qid),None)
            identity=i['temp_id']+' ('+i['kind']+'/'+i['action']+', target_item_id='+str(i['target_item_id'])+')'
            allowed='、'.join(q.get('related_refs',[])) if q else '无（问题不存在）'
            advice='；answer_refs仅用于该问题related_refs中原requirement/rule/acceptance/constraint的同kind revise候选；新增条目使用真实回答片段的source_refs，不能以新增条目代替原条款修订'
            advice+='；已存在的间接关联条款仍保留 action=revise 和原 target_item_id，删除不适用的 answer_refs，保留真实回答 source_refs 作为依据；不要把修订改成新增。'
            if q is None or not answer_bindable(p,q):
                raise Problem('REFERENCE_INVALID',identity+' 的 answer_refs='+qid+
                    ' 没有当前有效的已保存回答；允许目标='+allowed+advice,model_repair_allowed=False)
            expected_kind=next((t['kind'] for t in answer_targets(p,q) if t['id']==i.get('target_item_id')),None)
            advice+='；该目标实际 kind='+str(expected_kind)+'，必须保持原目标类型，不能把约束改成规则'
            require(answer_target(p,q,i) is not None,
                    'REFERENCE_INVALID',identity+' 的 answer_refs='+qid+' 目标不在该问题关联范围；允许目标='+allowed+advice)
    item_ids={i['id'] for i in p['items'] if i['kind'] in TRACKED}|{i['temp_id'] for i in value['proposals'] if i['kind'] in TRACKED}
    seen=set()
    for parent in value['questions']:
        target=parent.get('target_question_id')
        old=next((q for q in p['questions'] if q['id']==target),None) if target else None
        if target:
            if (old is None or not active_question(old) or old.get('decision_group_id')
                    or old['status']=='answered'
                    or old.get('answer') is not None or not parent.get('decision_points')):
                raise Problem('REFERENCE_INVALID','只能明确拆分尚未回答的现有问题；已有回答必须保留',
                              model_repair_allowed=False)
        for q in parent.get('decision_points') or [parent]:
            if p.get('workflow_mode') != 'shared-draft':
                require(q['question'].count('？')+q['question'].count('?')<=1,'SCHEMA_INVALID',
                        q['temp_id']+' 含多个独立问句，请用decision_points分别表达')
            require(q['temp_id'] not in seen,'REFERENCE_INVALID','拆分问题temp_id重复');seen.add(q['temp_id'])
            # Existing unbound historical questions remain readable, but new split decisions must be bound.
            if parent.get('decision_points'):
                require(bool(item_ids & set(q['related_refs'])),'REFERENCE_INVALID','拆分问题必须关联实际需求、规则或验收条目')
                require(not (parent['blocking'] and not q['blocking']),'SEMANTIC_BLOCKED','拆分不能降低重要未知的阻塞属性')
            if old:
                require(not old['blocking'] or (q['blocking'] and q.get('blocking_stage',3)<=old.get('blocking_stage',3)),
                        'SEMANTIC_BLOCKED','不能通过拆分解除旧问题门禁')


def save_questions(p,questions):
    from .core import ident
    for parent in questions:
        children=[]
        points=parent.get('decision_points') or []
        existing_group=next((x.get('decision_group_id') for point in points
            for x in p['questions'] if x['id']==point['temp_id'] and x.get('decision_group_id')),None)
        group_id=(existing_group or ident('QG')) if points else None
        for q in parent.get('decision_points') or [parent]:
            prior=next((x for x in p['questions'] if x['id']==q['temp_id']),None)
            if prior:
                if group_id:
                    prior.setdefault('decision_group_id',group_id)
                    prior.setdefault('decision_group_title',parent['question'])
                # Link additional provenance without replacing question, answer or decision identity.
                for key in ('source_refs','related_refs'):
                    prior[key]=list(prior.get(key,[]))+[v for v in q.get(key,[]) if v not in prior.get(key,[])]
                if prior['question']!=q['question'] or prior.get('options',[])!=q.get('options',[]):
                    variant={k:copy.deepcopy(q.get(k)) for k in ('question','why','options','blocking','blocking_stage','source_refs','related_refs')}
                    variants=prior.setdefault('incoming_variants',[])
                    if variant not in variants:variants.append(variant)
                if q['blocking']:
                    prior['blocking']=True
                    prior['blocking_stage']=min(prior.get('blocking_stage',3),q.get('blocking_stage',3))
                children.append(prior['id']);continue
            earlier=next((x for x in p['questions'] if x.get('status')=='answered' and equivalent(x,q)),None)
            row=dict(id=q['temp_id'],**{k:copy.deepcopy(v) for k,v in q.items() if k!='temp_id'},status='open',answer=None)
            if group_id:
                row.update(decision_group_id=group_id,decision_group_title=parent['question'],decision_coverage='pending')
            if earlier:row['prior_question_id']=earlier['id']
            p['questions'].append(row);children.append(row['id'])
        if parent.get('target_question_id'):
            old=next(q for q in p['questions'] if q['id']==parent['target_question_id'])
            old.update(superseded_by=children,split_at=now())


def record_answer(q):
    if q.get('deferred_at'):
        q.setdefault('deferred_history',[]).append(dict(note=q.get('deferred_note',''),created=q['deferred_at']))
        q.pop('deferred_at', None)
        q.pop('deferred_note', None)
    if q.get('answer') is not None:
        q.setdefault('answer_history',[]).append({k:copy.deepcopy(q.get(k)) for k in ('answer','answered_at','answer_source_refs')})
    if q.get('decision_review_proposal'):
        q.setdefault('decision_review_proposal_history',[]).append(q.pop('decision_review_proposal'))
    q['understanding_status']='pending'
    q['decision_coverage']='pending'
    q.pop('decision_coverage_review',None)
    q['applied_item_ids']=[]
    q['answer_applications']={}
    q['decision_pending_group_ids']=[]
    q['applied_requirement_ids']=[]
    q['answer_effect']='回答已保存，关联条款仍待核对更新'


def annotate_candidates(p,items):
    for i in items:
        if i.get('answer_refs'):
            i['answer_versions']={q['id']:digest(q.get('answer')) for q in p['questions']
                                  if q['id'] in i['answer_refs'] and not q.get('decision_group_id')}
            i['decision_answer_bindings']={q['id']:dict(answer_id=row['id'],
                answer_hash=row['answer_hash']) for q in p['questions']
                if q['id'] in i['answer_refs'] for row in [current_decision_answer(p,q)] if row}
            for q in p['questions']:
                if q['id'] in i['answer_refs']:
                    q['understanding_status']='candidate_ready'
                    q['answer_effect']='已有相关修订候选待逐决定核对；尚未替换原条款'


def accept_answer_update(p,candidate,*,answer_implementations=None,before=None):
    refs=set(candidate.get('answer_refs',[]))
    questions=[q for q in p['questions'] if q['id'] in refs]
    require(len(questions)==len(refs),'REFERENCE_INVALID','修订候选关联的回答不存在')
    # Validate every answer before mutating any state, including direct callers.
    for q in questions:
        require(answer_target(p,q,candidate) is not None,
                'REFERENCE_INVALID','回答只能应用于该问题直接关联的原需求、规则或验收的同类修订')
        if q.get('decision_group_id'):
            row=current_decision_answer(p,q)
            require(row is not None and coverage_complete(q), 'REVIEW_REQUIRED',
                    '决定组原话仍须逐子决定人工核对明确覆盖，不能直接采纳修订',409)
            require(candidate_answer_binding_current(p,q,candidate),
                    'STALE_ANSWER','决定组原话或候选绑定已变化，请重新生成修订候选',409)
        else:
            require(current_answer(q), 'REFERENCE_INVALID',
                    '回答只能应用于当前已答问题',409)
            require(candidate_answer_binding_current(p,q,candidate),
                    'STALE_ANSWER','回答已变化，请根据最新回答重新生成修订候选',409)
    from .answer_implementation import (derive_changed_witnesses, implementation_records,
        saved_implementations, validate_implementation_map, validate_implementations)
    supplied=validate_implementation_map(p,answer_implementations)
    prepared=[]
    for q in questions:
        targets=answer_targets(p,q)
        target=next(i for i in targets if i['id']==candidate['target_item_id'])
        witnesses=supplied.get(q['id'])
        if witnesses is None:
            witnesses=[w for w in saved_implementations(p,q) if w['item_id']!=target['id']]
            witnesses+=derive_changed_witnesses(before,target)
        try:
            scope=targets if q['id'] in supplied else [i for i in targets if any(w['item_id']==i['id'] for w in witnesses)]
            grouped=validate_implementations(p,q,witnesses,targets=scope)
        except Problem:
            if q['id'] in supplied:
                raise
            grouped={}
        prepared.append((q,targets,grouped))
    for q,targets,grouped in prepared:
        q['answer_implementation_contract']=2
        if grouped:
            q['answer_applications']=implementation_records(p,q,grouped,method='candidate_adoption')
            q['applied_item_ids']=sorted(grouped)
        applied=applied_targets(p,q)
        remaining={i['id'] for i in targets}-applied
        q['applied_item_ids']=sorted(applied)
        q['applied_requirement_ids']=sorted(i['id'] for i in targets
                                            if i['kind']=='requirement' and i['id'] in applied)
        q.update(understanding_status='candidate_ready' if remaining else 'applied',
            answer_effect=('部分修订已采纳，仍需核对：'+'、'.join(sorted(remaining))) if remaining
            else '已明确采纳基于此回答的关联条款修订；不代表正式确认')


def application_hash(item):
    """Exact reviewed clause content, excluding transient selection/version metadata."""
    from .requirements import item_content
    return digest(item_content(item))


def applied_targets(p,q):
    targets={i['id']:i for i in answer_targets(p,q)}
    applied=set(q.get('applied_item_ids',q.get('applied_requirement_ids',[]))) & set(targets)
    records=q.get('answer_applications',{})
    from .answer_implementation import record_current
    def current(iid):
        record=records.get(iid,{})
        if record.get('contract_version')==2 or q.get('answer_implementation_contract')==2:
            return record_current(p,q,targets[iid],record)
        return not record or (record.get('answer_hash')==digest(q.get('answer'))
                              and record.get('clause_hash')==application_hash(targets[iid]))
    return {iid for iid in applied if current(iid)}


def invalidate_answer_applications(p, before=None):
    for q in p['questions']:
        if q.get('status')!='answered':continue
        current=applied_targets(p,q)
        previous=next((old for old in (before or {}).get('questions',[]) if old['id']==q['id']),None)
        if previous:
            old_targets={i['id']:i for i in answer_targets(before,previous)}
            current={iid for iid in current if iid in q.get('answer_applications',{}) or iid not in old_targets or
                     application_hash(old_targets[iid])==application_hash(next(i for i in answer_targets(p,q) if i['id']==iid))}
        remaining={i['id'] for i in answer_targets(p,q)}-current
        if (current!=set(q.get('applied_item_ids',q.get('applied_requirement_ids',[]))) or
                q.get('understanding_status')=='applied' and remaining):
            q['applied_item_ids']=sorted(current)
            q['applied_requirement_ids']=[i['id'] for i in answer_targets(p,q)
                                           if i['kind']=='requirement' and i['id'] in current]
            q.update(understanding_status='pending',answer_effect='已核对条款或回答发生变化，请重新核对回答是否落实；不会自动批准。')


def confirm_manual_answer(p,q,answer_hash,clause_hashes,review_confirmed,*,answer_implementations=None):
    from .requirements import require_behavior_review
    require(review_confirmed,'REVIEW_REQUIRED','请先逐项核对回答与当前条款，不会自动批准',409)
    require(q.get('status')=='answered' and not q.get('superseded_by') and not q.get('out_of_scope_reason'),
            'ANSWER_REQUIRED','只能核对本期已保存的具体回答',409)
    if not q.get('decision_group_id') and len((q.get('decision_review_proposal') or {}).get('decisions') or [])>1:
        proposal=q['decision_review_proposal']
        same_answer=(proposal.get('question_hash')==digest([question_fingerprint(q)])
                     and proposal.get('answer_hash')==digest(q.get('answer')))
        require(not same_answer, 'DECISION_MAPPING_REQUIRED',
                '该旧问题包含多个独立决定，请先逐项核对映射；不能整题标为已回答',409)
    require(answer_hash==digest(q.get('answer')),'STALE_ANSWER','回答已变化，请重新查看后核对',409)
    targets=answer_targets(p,q)
    require(targets and set(clause_hashes)=={i['id'] for i in targets},
            'REFERENCE_INVALID','必须逐项核对该问题直接关联的全部原条款，不可换目标或遗漏')
    for item in targets:
        require(item['selection_status']=='selected','REVIEW_REQUIRED','请先编辑、核对并采纳 '+item['id'],409)
        require(clause_hashes[item['id']]==application_hash(item),'STALE_REVISION','条款已变化，请重新查看核对',409)
        require_behavior_review(item,p)
    from .answer_implementation import (implementation_records, saved_implementations,
        validate_implementation_map, validate_implementations)
    supplied=validate_implementation_map(p,answer_implementations)
    grouped=validate_implementations(p,q,supplied.get(q['id'],saved_implementations(p,q)))
    q['answer_implementation_contract']=2
    q['answer_applications']=implementation_records(p,q,grouped,method='manual_review')
    q['applied_item_ids']=sorted(clause_hashes)
    q['applied_requirement_ids']=sorted(i['id'] for i in targets if i['kind']=='requirement')
    q.update(understanding_status='applied',answer_effect='人工核对当前已采纳条款承载此回答；非正式业务批准。')
    q['decision_coverage']='clear'
    q['decision_coverage_review']=dict(answer_hash=answer_hash,
        question_hash=question_fingerprint(q),method='manual_review',
        actor='authenticated_local_user',reviewed_at=now())
