"""Input-only evidence roles for generation; no adoption or response rewriting."""
from .understanding import current_answer

STAGES = ('ingest', 'clarify', 'change')
SOURCE_POLICY = (
    '按原文、对象和来源时序核对明确本期决定；历史资料保留为历史。'
    '时间或purpose本身不证明采纳：只有原文明确给出本期选择、撤回或替代时才说明其演变；'
    '仍有当前效力不明或内部矛盾才联合追问。不伪造保存回答、正式确认或实现证据。')


def contract(schema):
    definitions=schema['$defs']
    return dict(version='generation-evidence-1',source_policy=SOURCE_POLICY,
        identity_patterns={key:definitions[name]['properties']['temp_id']['pattern']
                           for key,name in [('proposal','Proposal'),('question','Question')]},
        revision_policy='已有条目只按revision_targets的真实ID及同kind提出revise；同名不能作为身份映射，也不另建同义add。没有实质变化时复用原ID。新问题含decision_points子项都用question模式，不能用TMP-Q冒充QTMP。引用使用原ID或本轮真实temp_id。',
        evidence_policy='items是待核对的条目记录，reported不使未采纳候选成为独立事实。result_condition_facts.facts是有限来源谓词，仍须核原文；clause_assertions保留条目断言及采纳身份，只用于比较纠错，不证明旧实现或成功充分条件。必要条件不能补成充分保证，摘要/理解也保留拒绝和未知分支；有明确充分来源时保留其实际作用域。',
        decision_policy='questions.open是存储状态，不证明材料没有决定。实际来源已明确回答同对象本期决定时引用该来源提出修订，不重复索要选择；原问题不改，无有效保存回答则不写answer_refs。未采纳建议不是决定；当前来源自身仍矛盾时保留同一绑定问题。')


def history(p):
    """Keep user material; do not duplicate old model summaries/responses."""
    return [dict(m) if m.get('role')=='user' else
            dict(**{k:m[k] for k in ('id','run_id','role','stage','created') if k in m},use='historical_model_event')
            for m in p.get('messages',[])[-8:]]


def evidence(p,excerpts):
    return dict(version='generation-evidence-1',source_policy=SOURCE_POLICY,
        sources=[dict(source_id=s['id'],created=s.get('created'),purpose=s.get('purpose'),sha256=s.get('sha256'),
            stored_order=n,excluded=s.get('excluded',False),superseded_by=s.get('superseded_by'),
            parent_source_id=s.get('parent_source_id'),origin=s.get('origin'),
            excerpt_ids=[e['id'] for e in excerpts if e['source_id']==s['id']],evidence_role='source_statement')
            for n,s in enumerate(p.get('sources',[]))],
        revision_targets=[dict(target_item_id=i['id'],kind=i['kind'],selection_status=i.get('selection_status'),
                               **({'content_version':i['content_version']} if 'content_version' in i else {}))
                          for i in p.get('items',[])],
        saved_decision_ids=[q['id'] for q in p.get('questions',[]) if current_answer(q)],
        product_context_proposal=dict(evidence_role='unadopted_proposal',content=p.get('product_context_proposal',{})))


def condition_layers(projection,p):
    """Preserve each predicate/quote once in its evidence role, not as new facts."""
    items={i['id']:i for i in p.get('items',[])}
    assertions=[]
    for fact in projection['facts']:
        if fact['origin']!='model':continue
        item=items.get(fact.get('object_id'),{})
        assertions.append(dict(fact,selection_status=item.get('selection_status'),
            epistemic_status=item.get('epistemic_status'),
            evidence_role='selected_clause' if item.get('selection_status')=='selected' else 'unadopted_clause'))
    return dict(projection,facts=[f for f in projection['facts'] if f['origin']!='model'],clause_assertions=assertions)
