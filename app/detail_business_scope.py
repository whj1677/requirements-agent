"""Bounded technical-property business scope decisions from server-owned full reviews.

No keyword classification, status rewriting, client exemptions or new calls.
"""
import copy
from .core import digest,require

LEGACY_PROTOCOL='detail-default-scope-1'
PROTOCOL='detail-technical-scope-2'


# One finite catalog for both actual eligibility and the displayed action plan.
ATTRIBUTES={
    LEGACY_PROTOCOL: ('fields.*.default_value',),
    PROTOCOL: ('fields.*.default_value','fields.*.constraints','fields.*.editability',
               'fields.*.required','fields.*.source','interactions.*.cancel','interactions.*.pending'),
}


def scope_attributes(protocol):
    return list(ATTRIBUTES.get(protocol,()))


def eligible_path(path,protocol):
    parts=path.split('.')
    if len(parts)!=4 or parts[0]!='detail_contract' or not parts[2]:return False
    return parts[1]+'.*.'+parts[3] in scope_attributes(protocol)


def eligible(plan,row):
    scopes=plan.get('unknown_scope',{}).get('rows',[])
    entry=next((r for r in scopes if r['row_key']==row['row_key']),{})
    protocol=(plan.get('default_scope_review') or {}).get('protocol',LEGACY_PROTOCOL)
    return [p for p in entry.get('properties',[]) if eligible_path(p['path'],protocol)]


def prepare_bindings(plan,project,protocol=PROTOCOL):
    allowed=[]
    for q in plan['quote_pool']:
        m=q['material']
        if q['kind']!='human' or m.get('kind')!='saved_answer':continue
        question=next((x for x in (project or {}).get('questions',[]) if x['id']==m.get('question_id')),None)
        if question and question.get('status')=='answered' and not question.get('superseded_by') and not question.get('out_of_scope_reason'):
            if question.get('question')==m.get('question_text') and digest(question.get('answer',''))==q['content_hash']:
                allowed.append(q['key'])
    return dict(protocol=protocol,allowed_saved_human_quote_keys=allowed,source_identities={s['id']:{k:copy.deepcopy(s.get(k)) for k in ('version','sha256')} for s in (project or {}).get('sources',[])})


def bind_schema(plan,row,item):
    require(plan['default_scope_review']['protocol'] in (LEGACY_PROTOCOL,PROTOCOL),'DETAIL_REVIEW_INVALID','默认值范围协议无法识别。')
    from .detail_semantics import obj,TEXT
    keys=[p['key'] for p in eligible(plan,row)]
    source=[q['key'] for q in plan['quote_pool'] if q['kind']=='source']
    human=[q['key'] for q in plan['quote_pool'] if q['kind']=='human' and q['key'] in plan['default_scope_review']['allowed_saved_human_quote_keys'] and q['material']['row_key'] in (None,row['row_key'])]
    def refs(values):return dict(type='array',uniqueItems=True,maxItems=300,items={'enum':values} if values else {'not':{}})
    entry=obj(dict(property_key={'enum':keys} if keys else {'not':{}},classification={'enum':['business_gap','technical_only','unclear']},
        known_business=dict(type='string',maxLength=6000),unknown_technical=dict(type='string',maxLength=6000),
        source_quote_keys=refs(source),human_quote_keys=refs(human),explanation=TEXT))
    entry['allOf']=[{'if':dict(required=['classification'],properties={'classification':{'const':'technical_only'}}),
        'then':dict(properties={'known_business':dict(minLength=1),'unknown_technical':dict(minLength=1)},
            anyOf=[dict(properties={name:dict(minItems=1)}) for name in ('source_quote_keys','human_quote_keys')])}]
    item['properties']['default_scopes']=dict(type='array',minItems=len(keys),maxItems=len(keys),items=entry,uniqueItems=True)
    item.setdefault('allOf',[]).append({'if':dict(required=['scope','dimension'],properties={'scope':{'const':'detail'},'dimension':{'const':'field_conditions'}}),
        'then':dict(required=['default_scopes']), 'else':{'not':{'required':['default_scopes']}}})


def scope_errors(plan,row,check):
    if not plan.get('default_scope_review'):return []
    if (check.get('scope'),check.get('dimension'))!=('detail','field_conditions'):return []
    values=check.get('default_scopes',[]);keys=[p['key'] for p in eligible(plan,row)]
    errors=[]
    if len(values)!=len(keys) or {p.get('property_key') for p in values}!=set(keys):
        errors.append('范围判定必须逐项覆盖本行全部可分类未知属性，不能重复或跨属性。' if plan['default_scope_review']['protocol']==PROTOCOL
                      else '默认值范围判定必须逐项覆盖本行全部未知默认值，不能重复或跨属性。')
    pool={q['key']:q for q in plan['quote_pool']}
    for value in values:
        selected=[]
        for field,kind in [('source_quote_keys','source'),('human_quote_keys','human')]:
            for key in value.get(field,[]):
                q=pool.get(key)
                if not q or q['kind']!=kind:errors.append('范围引文类型或身份不匹配：'+key);continue
                if kind=='human' and (key not in plan['default_scope_review']['allowed_saved_human_quote_keys'] or q['material']['row_key'] not in (None,row['row_key'])):
                    errors.append('范围人工引文无法按本行已保存回答复核：'+key)
                selected.append(q)
        if value.get('classification')=='technical_only' and (not selected or not value.get('known_business','').strip() or not value.get('unknown_technical','').strip()):
            errors.append('仅技术未定须同时说明已知业务、未定技术内容并引用有效材料。')
    return errors


def validate_scopes(plan,row,check):
    errors=scope_errors(plan,row,check)
    require(not errors,'DETAIL_REVIEW_INVALID','；'.join(errors))


def review_record(plan,row,response):
    origin=next(r for r in plan['rows'] if r['row_key']==row['row_key'])
    props={p['key']:p for p in eligible(plan,origin)};pool={q['key']:q for q in plan['quote_pool']}
    check=next(c for c in row['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions')
    decisions=[]
    for v in check['default_scopes']:
        p=props[v['property_key']]
        quotes=[copy.deepcopy(pool[k]) for k in [*v['source_quote_keys'],*v['human_quote_keys']]]
        for quote in quotes:
            if quote['kind']=='source':quote['source_identity']=copy.deepcopy(plan['default_scope_review']['source_identities'].get(quote['material']['source_id']))
        decisions.append(dict(copy.deepcopy(v),path=p['path'],quotes=quotes))
    record=dict(protocol=plan['default_scope_review']['protocol'],row_key=origin['row_key'],detail_hash=origin['candidate_hash'],
        plan_hash=digest(plan),review_hash=digest(response),properties=decisions)
    record['record_hash']=digest(record)
    return record


def bind_stored_reviews(report,canonical,mapping,project):
    """Seal only the existing server ID substitution, retaining the reviewed hash."""
    from .response_application import replace_business_ids
    originals={mapping.get(p['temp_id'],p['temp_id']):p for p in canonical.get('proposals',[])}
    stored={p['id']:p for p in project.get('items',[])}
    for accepted in report.get('accepted',[]):
        semantic=accepted.get('detail_semantic_review') or {};record=semantic.get('default_scope_review')
        # Failed/pending detail is retained as evidence, never a stored definition.
        if not record or semantic.get('status')!='passed':continue
        cid=accepted.get('candidate_temp_id');before=originals.get(cid);after=stored.get(cid)
        require(before is not None and after is not None and semantic.get('status')=='passed',
            'DETAIL_SCOPE_BINDING_INVALID','默认值范围审查缺少对应已保存候选。')
        require(record.get('record_hash')==digest({k:v for k,v in record.items() if k!='record_hash'})
            and digest(before.get('detail_contract'))==semantic.get('candidate_hash')==record.get('detail_hash')
            and replace_business_ids(before['detail_contract'],mapping)==after.get('detail_contract'),
            'DETAIL_SCOPE_BINDING_INVALID','默认值范围审查候选与既有ID映射结果不一致。')
        record['storage_binding']=dict(normalization='existing_business_id_mapping',reviewed_detail_hash=semantic['candidate_hash'],
            stored_detail_hash=digest(after['detail_contract']),id_mapping_hash=digest(mapping))
        record['detail_hash']=record['storage_binding']['stored_detail_hash']
        record['record_hash']=digest({k:v for k,v in record.items() if k!='record_hash'})


def _quote_current(project,quote,owner):
    material=quote.get('material',{})
    if quote.get('kind')=='source':
        matches=[s for s in project.get('sources',[]) if s['id']==material.get('source_id')]
        if len(matches)!=1:return False
        source=matches[0]
        if not source or source.get('excluded') or source.get('superseded_by'):return False
        if quote.get('source_identity') is None or quote['source_identity']!={k:source.get(k) for k in ('version','sha256')}:return False
        excerpts=[e for e in source.get('excerpts',[]) if e['id']==material.get('excerpt_id')]
        if len(excerpts)!=1:return False
        excerpt=excerpts[0]
        text=excerpt.get('text','')
    elif quote.get('kind')=='human' and material.get('kind')=='saved_answer':
        questions=[q for q in project.get('questions',[]) if q['id']==material.get('question_id')]
        if len(questions)!=1:return False
        question=questions[0]
        if not question or question.get('status')!='answered' or question.get('superseded_by') or question.get('out_of_scope_reason'):return False
        if question.get('question')!=material.get('question_text'):return False
        related=set(question.get('related_refs',[]));items={i['id']:i for i in project.get('items',[])}
        owner_refs={owner,*items.get(owner,{}).get('related_refs',[])}
        owner_refs.update(i['id'] for i in items.values() if owner in i.get('related_refs',[]) or i.get('target_item_id')==owner)
        if not related.intersection(owner_refs):return False
        text=question.get('answer','')
    else:return False
    start,end=quote.get('start'),quote.get('end')
    return (isinstance(start,int) and isinstance(end,int) and 0<=start<end<=len(text)
            and digest(text)==quote.get('content_hash') and text[start:end]==quote.get('text'))


def scope_review_identity(record):
    # Stable across the existing canonical-to-stored business-ID substitution.
    original=(record.get('storage_binding') or {}).get('reviewed_detail_hash',record.get('detail_hash'))
    return digest(dict(protocol=record.get('protocol'),row_key=record.get('row_key'),
        reviewed_detail_hash=original,plan_hash=record.get('plan_hash'),review_hash=record.get('review_hash'),
        properties=record.get('properties')))


def scope_audit_current(semantic,record):
    if record.get('protocol')==LEGACY_PROTOCOL:return True
    if record.get('protocol')!=PROTOCOL:return False
    audit=semantic.get('scope_source_audit') or {};challenge=semantic.get('source_challenge') or {}
    return (audit.get('protocol')=='detail-scope-source-audit-1' and audit.get('status')=='passed'
        and audit.get('scope_identity_hash')==scope_review_identity(record)
        and audit.get('property_keys')==sorted(p['property_key'] for p in record.get('properties',[]))
        and challenge.get('status')=='passed' and challenge.get('findings')==[]
        and audit.get('challenge_hash')==digest(challenge))


def current_reviews(project,item):
    """Return only server-persisted, owner-bound, currently reproducible decisions."""
    if not project or not isinstance(item.get('detail_contract'),dict):return []
    owner=item.get('target_item_id') or item.get('id');items={i['id']:i for i in project.get('items',[])}
    if len(items)!=len(project.get('items',[])):return []
    detail_hash=digest(item['detail_contract']);result=[]
    latest=None
    for report in reversed(project.get('business_draft_reviews',[])):
        for accepted in reversed(report.get('accepted',[])):
            candidate=items.get(accepted.get('candidate_temp_id'))
            if candidate and accepted.get('requirement_id')==owner and (candidate.get('target_item_id') or candidate['id'])==owner and digest(candidate.get('detail_contract'))==detail_hash:
                latest=accepted;break
        if latest is not None:break
    # Validate the latest matching server review; never fall back past its failure/staleness.
    for report in [dict(accepted=[latest] if latest is not None else [])]:
        for accepted in report['accepted']:
            candidate=items.get(accepted.get('candidate_temp_id'))
            semantic=accepted.get('detail_semantic_review') or {};record=semantic.get('default_scope_review') or {}
            if not candidate or accepted.get('requirement_id')!=owner or record.get('row_key')!=accepted.get('row_key'):continue
            if candidate.get('kind')!='requirement' or (candidate.get('target_item_id') or candidate['id'])!=owner or candidate.get('superseded_by'):continue
            root=items.get(owner,{})
            adopted=any(r.get('candidate_id')==candidate['id'] for r in root.get('confirmed_revisions',[]))
            self_selected=candidate['id']==owner and not candidate.get('target_item_id') and candidate.get('selection_status')=='selected'
            if candidate.get('selection_status')!='candidate' and not self_selected and not (candidate.get('selection_status')=='deferred' and adopted):continue
            if semantic.get('status')!='passed' or semantic.get('findings') or record.get('protocol') not in (LEGACY_PROTOCOL,PROTOCOL):continue
            if not scope_audit_current(semantic,record):continue
            binding=record.get('storage_binding') or {}
            if binding.get('normalization')!='existing_business_id_mapping' or binding.get('reviewed_detail_hash')!=semantic.get('candidate_hash') or binding.get('stored_detail_hash')!=detail_hash:continue
            if record.get('detail_hash')!=detail_hash or digest(candidate.get('detail_contract'))!=detail_hash:continue
            if record.get('record_hash')!=digest({k:v for k,v in record.items() if k!='record_hash'}):continue
            properties=[]
            for decision in record.get('properties',[]):
                path=decision.get('path','');parts=path.split('.')
                if not eligible_path(path,record['protocol']):continue
                field=next((f for f in item['detail_contract'].get(parts[1],[]) if f['id']==parts[2]),None)
                if not field or parts[3] not in field:continue
                if decision.get('classification')!='technical_only' or not decision.get('known_business','').strip() or not decision.get('unknown_technical','').strip():continue
                quotes=decision.get('quotes',[])
                if not quotes or not all(_quote_current(project,q,owner) for q in quotes):continue
                properties.append({k:copy.deepcopy(decision[k]) for k in ('path','property_key','classification','known_business','unknown_technical','explanation')})
            if properties:
                projected=dict(requirement_id=owner,candidate_id=candidate['id'],detail_hash=detail_hash,review_hash=record['review_hash'],properties=properties)
                # Preserve the legacy projection shape included in confirmation hashes.
                if record['protocol']==PROTOCOL:projected['protocol']=PROTOCOL
                result.append(projected)
    # Conflicting snapshots never silently overwrite one another.
    if len({digest(r) for r in result})>1:return []
    return result[:1]


def projection(project):
    rows=[];seen=set()
    for item in project.get('items',[]):
        if item.get('kind')!='requirement':continue
        for review in current_reviews(project,item):
            identity=digest(review)
            if identity not in seen:rows.append(review);seen.add(identity)
    return rows
