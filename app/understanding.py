"""Evidence-backed change classifications and stable, atomic decision records."""
import copy
import unicodedata
import re
from .core import require, now, digest


def contract():
    return dict(version='understanding-1',
        classification='功能需求需分别表达现状/current、变化/change、保持/preserve；scope_evidence逐条引用本次真实片段的原文quote及source_ref。current+change=modified，只有change=new，只有preserve=preserved，只有current=existing；没有充分依据则unspecified。一个功能的权限、分页、排序等保持性约束可写在该功能的behavior或关联rule中供PM核对，不要把每个字段维度机械拆成独立requirement；单条requirement的scope_evidence不得混写change与preserve状态，也不得把保持内容说成新增。引用原句同时含变化和保持时保留原文，change_type只对应本条statement的变化身份。classification_reason说明判定依据及未知。applies_to=as_is仅用于existing。原条目变化用revise/target_item_id，不新建同义编号。',
        questions='一个问题只问一个可独立回答的决定。复合问题必须列出decision_points（完整Question对象，各自独立temp_id/topic_key/关联需求/阻塞属性），程序展开为独立回答。拆分已有未答问题使用target_question_id；原问题保留为分组记录，不能拆掉已有回答。已答问题若问题文字或风险条件发生变化，不能假定原答案仍适用；材料已回答的事项不要再次提问，编号错字记findings。',
        answer_binding='answer_refs只用于基于已保存回答提出原requirement修订：kind=requirement、action=revise、target_item_id为该问题related_refs中的原requirement ID；不得把answer_refs写在rule修订或新建acceptance上。rule/acceptance若引用回答事实，使用本轮真实回答片段的source_refs，并用related_refs关联原requirement；新验收仍为action=add且target_item_id=null。保留原需求编号与内容，回答不会自动采纳候选。',
        question_shape='普通单问题省略decision_points或写空数组[]；不能写仅含一个子项的数组。只有确有两项及以上独立决定时才用decision_points分组，每个子项都要是完整Question。',
        single_question_example=dict(temp_id='QTMP-example',topic_key='example',question='一个待决定的问题？',why='影响实现或验收',
            options=[],blocking=True,related_refs=['实际需求ID'],source_refs=[],decision_points=[]),
        classification_shape='kind=requirement时，change_type为new/modified/preserved/existing必须有非空scope_evidence；每条须有state、逐字quote、真实source_ref。没有充分材料依据时用unspecified并可用空scope_evidence，不能猜测分类。示例只说明字段，禁止复制占位文本或ID。',
        classification_example=dict(change_type='new',scope_evidence=[dict(state='change',quote='本轮真实片段的原文',
            source_ref=dict(source_id='实际来源ID',excerpt_id='实际片段ID'))],classification_reason='依据所引变化原文'),
        revision_identity='action=revise 表示修订需求记录，change_type 表示本期功能相对真实系统现状的变化，两者独立。补齐一个尚未实现的新增功能的回答或验收，仍是 new；不能因底稿里已经有该候选就变成 modified。只有来源同时说明该功能已有行为和本期改动，才是 modified。原候选、上轮模型输出和本次修订动作都不是 current 的业务现状证据。scope_evidence 的 quote 必须来自同一片段的连续原文，不拼接省略句。',
        boundaries='每个问题关联实际requirement。回答只解除该决定的未知，关联业务条款更新需明确采纳修订候选；selected不代表正式业务批准。')


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
        if parent.get('decision_points'):
            mapping[parent['temp_id']]=mapping[children[0]['temp_id']]
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
            advice='；仅原requirement的revise候选使用answer_refs；rule/acceptance请移除answer_refs，使用真实回答片段的source_refs并以related_refs关联原需求，保留业务内容'
            require(q is not None and q['status']=='answered','REFERENCE_INVALID',
                    identity+' 的 answer_refs='+qid+' 没有已保存回答；允许目标='+allowed+advice)
            require(i['kind']=='requirement' and i['action']=='revise'
                    and i['target_item_id'] in q.get('related_refs',[])
                    and any(old['id']==i['target_item_id'] and old['kind']=='requirement' for old in p['items']),
                    'REFERENCE_INVALID',identity+' 的 answer_refs='+qid+' 目标不在该问题关联范围；允许目标='+allowed+advice)
    req_ids={i['id'] for i in p['items'] if i['kind']=='requirement'}|{i['temp_id'] for i in value['proposals'] if i['kind']=='requirement'}
    seen=set()
    for parent in value['questions']:
        target=parent.get('target_question_id')
        old=next((q for q in p['questions'] if q['id']==target),None) if target else None
        if target:
            require(old is not None and old['status']!='answered' and not old.get('superseded_by') and parent.get('decision_points'),
                    'REFERENCE_INVALID','只能明确拆分尚未回答的现有问题；已有回答必须保留')
        for q in parent.get('decision_points') or [parent]:
            require(q['question'].count('？')+q['question'].count('?')<=1,'SCHEMA_INVALID',
                    q['temp_id']+' 含多个独立问句，请用decision_points分别表达')
            require(q['temp_id'] not in seen,'REFERENCE_INVALID','拆分问题temp_id重复');seen.add(q['temp_id'])
            # Existing unbound historical questions remain readable, but new split decisions must be bound.
            if parent.get('decision_points'):
                require(bool(req_ids & set(q['related_refs'])),'REFERENCE_INVALID','拆分问题必须关联实际需求')
                require(not (parent['blocking'] and not q['blocking']),'SEMANTIC_BLOCKED','拆分不能降低重要未知的阻塞属性')
            if old:
                require(not old['blocking'] or (q['blocking'] and q.get('blocking_stage',3)<=old.get('blocking_stage',3)),
                        'SEMANTIC_BLOCKED','不能通过拆分解除旧问题门禁')


def save_questions(p,questions):
    for parent in questions:
        children=[]
        for q in parent.get('decision_points') or [parent]:
            prior=next((x for x in p['questions'] if x['id']==q['temp_id']),None)
            if prior:
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
            if earlier:row['prior_question_id']=earlier['id']
            p['questions'].append(row);children.append(row['id'])
        if parent.get('target_question_id'):
            old=next(q for q in p['questions'] if q['id']==parent['target_question_id'])
            old.update(superseded_by=children,split_at=now())


def record_answer(q):
    if q.get('answer') is not None:
        q.setdefault('answer_history',[]).append({k:copy.deepcopy(q.get(k)) for k in ('answer','answered_at','answer_source_refs')})
    q['understanding_status']='pending'
    q['applied_requirement_ids']=[]
    q['answer_effect']='回答已保存，关联需求仍待核对更新'


def annotate_candidates(p,items):
    for i in items:
        if i.get('answer_refs'):
            i['answer_versions']={q['id']:digest(q.get('answer')) for q in p['questions'] if q['id'] in i['answer_refs']}
            for q in p['questions']:
                if q['id'] in i['answer_refs']:
                    q['understanding_status']='candidate_ready'
                    q['answer_effect']='已有相关修订候选待核对；尚未替换原条款'


def accept_answer_update(p,candidate):
    for q in p['questions']:
        if q['id'] in candidate.get('answer_refs',[]):
            require(any(i['id']==candidate.get('target_item_id') and i['kind']=='requirement'
                        for i in p['items']) and candidate.get('target_item_id') in q.get('related_refs',[]),
                    'REFERENCE_INVALID','回答只能应用于该问题关联的原需求修订')
            require(candidate.get('answer_versions',{}).get(q['id'])==digest(q.get('answer')),
                    'STALE_ANSWER','回答已变化，请根据最新回答重新生成修订候选',409)
            affected={i['id'] for i in p['items'] if i['kind']=='requirement' and i['id'] in q.get('related_refs',[])}
            applied=set(q.get('applied_requirement_ids',[])) | {candidate['target_item_id']}
            remaining=affected-applied
            q['applied_requirement_ids']=sorted(applied)
            q.update(understanding_status='candidate_ready' if remaining else 'applied',
                answer_effect=('部分修订已采纳，仍需核对：'+'、'.join(sorted(remaining))) if remaining
                else '已明确采纳基于此回答的关联需求修订；不代表正式确认')
