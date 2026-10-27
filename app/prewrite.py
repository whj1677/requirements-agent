"""Version-bound pre-writing review, using existing review runs and decisions.

Model findings are suggestions. Only the controlled connector creates questions;
answers, disputes and a successful request alone never make a review pass.
"""
import copy
import re

from .core import digest, ident, now, require
from .requirements import delivery_items, item_content

VERSION = 'prewrite-1'
_UNSET = object()


def input_hash(p):
    from .business_context import selection_identity
    questions=[]
    for q in p.get('questions',[]):
        if q.get('prewrite_origin'):
            # Automatically emitted questions are outputs of the check. A saved
            # human answer/application is new input and invalidates that check.
            if not q.get('answer'):
                continue
            questions.append({k:q.get(k) for k in ('id','answer','answer_source_refs','answer_applications',
                'understanding_status','decision_coverage','decision_coverage_review','decision_answer_id')})
        else:
            questions.append({k:v for k,v in q.items() if k not in
                ('decision_review_proposal_current','decision_coverage_complete')})
    return digest(dict(version=VERSION,
        items=[dict(id=i['id'],selection_status=i.get('selection_status'),content=item_content(i))
               for i in p.get('items',[])],questions=questions,
        sources=[{k:s.get(k) for k in ('id','sha256','excluded','parse_status','excerpts','purpose')}
                 for s in p.get('sources',[])],
        context={k:p.get(k) for k in ('product_context','scope_profile','context_field_states',
            'requirement_relations','delivery_scope','domain_context','business_context')},
        decision_answers=p.get('decision_answers',[]),business_selection=selection_identity(p),
        responses=p.get('prewrite_responses',[])))


def structural_findings(p):
    from .requirement_details import detail_issues
    from .semantic_quality import current_counting_checks
    rows=[]
    for item in delivery_items(p):
        if item['kind']!='requirement':continue
        for issue in detail_issues(item,p,require_confirmed=False):
            rows.append(dict(id='PW-'+digest([item['id'],issue['code'],issue.get('path')])[:20],
                title='详细定义需要核对',message=issue['message'],suggested_resolution='请在关联需求中核对并修改后复检。',
                severity='blocker',related_refs=[item['id']],source_refs=[],quotes=[],options=[],
                kind='structure',path=issue.get('path'),code=issue['code'],
                hard_block=issue['code'] in ('DETAIL_INVALID','DETAIL_STRUCTURE','DETAIL_FLOW_REFERENCE',
                    'DETAIL_FIELD_REFERENCE','DETAIL_INTERACTION_REFERENCE','DETAIL_ITEM_REFERENCE','DETAIL_SOURCE_INVALID'),
                status='open'))
    for check in current_counting_checks(p):
        rows.append(dict(id='PW-'+check['check_id'],title='规则存在可复现的矛盾',message=check.get('message') or str(check),
            suggested_resolution='请明确计数口径并同步关联规则。',severity='blocker',related_refs=check['related_refs'],
            source_refs=[],quotes=[],options=[],kind='structure',status='open',hard_block=True))
    return rows


def review_project(p):
    value=copy.deepcopy(p)
    value['_prewrite_review']=True
    value['documents']={}
    value['ui']=None
    return value


def _resolve(p, oid, path):
    obj=(p.get('product_context') if oid=='product_context' else
         next((i for i in p.get('items',[])+p.get('questions',[]) if i['id']==oid),None))
    if obj is None:return None
    for part in path.split('.'):
        if isinstance(obj,dict):obj=obj.get(part)
        elif isinstance(obj,list):
            obj=next((x for x in obj if isinstance(x,dict) and x.get('id')==part),None)
        else:return None
    return obj


def normalize_quotes(p,response):
    """Recover only a punctuation suffix added to a complete source property.

    Raw model bytes remain in evidence. No partial quote, word, object or path is
    guessed, and ordinary review normalization is deliberately unaffected.
    """
    value=copy.deepcopy(response);changes=[]
    if not isinstance(value,dict):return value,changes
    for fi,finding in enumerate(value.get('findings',[])):
        if not isinstance(finding,dict):continue
        for qi,quote in enumerate(finding.get('evidence_quotes',[])):
            if not isinstance(quote,dict) or not all(isinstance(quote.get(k),str) for k in ('object_id','path','quote')):continue
            source=_resolve(p,quote['object_id'],quote['path'])
            if not isinstance(source,str) or not source or quote['quote'] in source:continue
            if quote['quote'] not in (source+'。',source+'.') or source.endswith(('。','.','？','?','！','!')):continue
            changes.append(dict(path=f'findings[{fi}].evidence_quotes[{qi}].quote',
                object_id=quote['object_id'],source_path=quote['path'],before=quote['quote'],after=source))
            quote['quote']=source
    return value,changes


def normalize_decisions(p,response):
    """Required decisions cannot remain nonblocking suggestions.

    Match only an exact question, optionally followed by a parenthesized list
    of choices. No keyword or semantic matching creates a new association.
    """
    value=copy.deepcopy(response);changes=[]
    findings=value.get('findings',[])
    for decision in value.get('result',{}).get('required_decisions',[]):
        matches=[]
        for index,finding in enumerate(findings):
            question=finding.get('decision_question')
            if not isinstance(question,str) or not question or not _clause_refs(p,finding.get('related_refs',[])):
                continue
            tail=decision[len(question):] if decision.startswith(question) else None
            if decision==question or (tail and question.endswith(('?','？'))
                    and re.fullmatch(r'\s*[（(][^?？]+[）)]\s*',tail)):
                matches.append((index,finding))
        if len(matches)!=1:continue
        index,finding=matches[0]
        if finding.get('severity')=='blocker' and finding['decision_question']==decision:continue
        changes.append(dict(path=f'findings[{index}]',before=dict(severity=finding['severity'],decision_question=finding['decision_question']),
            after=dict(severity='blocker',decision_question=decision)))
        finding.update(severity='blocker',decision_question=decision)
    return value,changes


def validate_result(p,response):
    required={i['id'] for i in delivery_items(p) if i['kind'] in ('requirement','rule','acceptance')}
    result=response['result']
    require(required<=set(result.get('reviewed_refs',[])) and result.get('perspectives'),
        'PREWRITE_COVERAGE_REQUIRED','成文检查未覆盖全部本期需求、规则和验收，请重新检查。')
    for f in response.get('findings',[]):
        require(not f.get('decision_question') or _clause_refs(p,f.get('related_refs',[])),
            'PREWRITE_DECISION_BINDING_REQUIRED','待决定问题必须关联本期条款。')
        for quote in f.get('evidence_quotes',[]):
            source=_resolve(p,quote['object_id'],quote['path'])
            require(isinstance(source,str) and quote['quote'] in source,
                'REFERENCE_INVALID','成文检查引文不在指定对象属性原文中：'+quote['object_id']+'.'+quote['path'])
    blockers=list({digest(f):f for f in response.get('findings',[]) if f['severity']=='blocker'}.values())
    for decision in result.get('required_decisions',[]):
        require(any(_clause_refs(p,f.get('related_refs',[])) and (f.get('decision_question')==decision
            or any(ref in decision for ref in f['related_refs'])
            or len(blockers)==1 and len(result.get('required_decisions',[]))==1)
            for f in blockers), 'PREWRITE_DECISION_BINDING_REQUIRED',
            '每个待决定问题必须绑定一个有真实条款关联的 blocker，不能生成无关联的问题。')


def _quotes(p,f):
    quotes=[dict(label=q['object_id']+'.'+q['path'],text=q['quote'],object_id=q['object_id'],path=q['path'])
            for q in f.get('evidence_quotes',[])]
    if quotes:return quotes
    for oid in f.get('related_refs',[]):
        row=next((i for i in p['items']+p['questions'] if i['id']==oid),None)
        if row:
            prop='statement' if 'statement' in row else 'question'
            quotes.append(dict(label=row.get('title') or oid,text=row[prop],object_id=oid,path=prop))
    return quotes


def _question_for(p,card,question):
    card['related_refs']=_clause_refs(p,card['related_refs'])
    prior=[q for q in p['questions'] if q.get('prewrite_key')==card['id']]
    existing=next((q for q in reversed(prior) if q['question']==question),prior[-1] if prior else None)
    if existing and existing['question']!=question and existing.get('status')=='answered':
        # A changed question cannot inherit an answer to its previous meaning.
        existing=None
    if not existing:
        # Exact reuse of an existing unanswered decision avoids duplicate PM work.
        existing=next((q for q in p['questions'] if q.get('question')==question
            and set(q.get('related_refs',[]))==set(card['related_refs']) and not q.get('superseded_by')),None)
    if not existing:
        existing=dict(id=ident('Q'),question=question,why=card['message'],options=copy.deepcopy(card.get('options') or []),
            topic_key='prewrite:'+card['id'],source_refs=copy.deepcopy(card['source_refs']),
            related_refs=copy.deepcopy(card['related_refs']),blocking=True,blocking_stage=3,
            status='open',answer=None,prewrite_origin=VERSION,prewrite_key=card['id'])
        p['questions'].append(existing)
    card['question_id']=existing['id']
    existing['related_refs']=list(dict.fromkeys(existing.get('related_refs',[])+card['related_refs']))
    existing.pop('prewrite_resolution',None)
    return existing


def _clause_refs(p,refs):
    items={i['id'] for i in p['items'] if i.get('kind') in ('requirement','rule','acceptance')
        and not i.get('target_item_id') and i.get('applies_to')=='to_be'}
    questions={q['id']:q for q in p['questions']}
    return sorted(set(refs)&items | {r for ref in refs if ref in questions for r in questions[ref].get('related_refs',[]) if r in items})


def _implemented(p,question):
    from .understanding import answer_targets
    from .answer_implementation import record_current
    targets=answer_targets(p,question) if question else []
    return bool(question and question.get('status')=='answered' and targets and all(
        record_current(p,question,item,(question.get('answer_applications') or {}).get(item['id'],{})) for item in targets))


def _finding_input_hash(p,card):
    related=set(card.get('related_refs',[]))
    question=next((q for q in p['questions'] if q['id']==card.get('question_id')),None)
    return digest(dict(items=[dict(id=i['id'],content=item_content(i),selection_status=i.get('selection_status'))
        for i in p['items'] if i['id'] in related],answer=question.get('answer') if question else None,
        context={k:p.get(k) for k in ('product_context','scope_profile','context_field_states')},
        sources=[{k:s.get(k) for k in ('id','sha256','excluded','parse_status')} for s in p['sources']
            if s['id'] in {r['source_id'] for r in card.get('source_refs',[])}]))


def _legacy_finding_id(finding):
    topic=finding.get('topic_key') or digest([finding['type'],sorted(finding['related_refs']),
        sorted(q['path'] for q in finding.get('evidence_quotes',[])),finding['message']])[:20]
    return 'PW-'+digest([finding['type'],sorted(finding['related_refs']),topic])[:20]


def _evidence_identity(finding):
    # Only an exact, explicitly quoted evidence set can survive prose changes.
    # A new path, quote or source remains a separate finding; no fuzzy matching.
    quotes=finding.get('evidence_quotes')
    if not quotes:return None
    return digest(dict(type=finding['type'],refs=sorted(set(finding['related_refs'])),
        quotes=sorted({(q['object_id'],q['path'],q['quote']) for q in quotes}),
        sources=sorted({(r['source_id'],r['excerpt_id']) for r in finding['source_refs']})))


def _restore_evidence_identities(previous,previous_response):
    # Older saved cards did not store the finding type. Recover it only from
    # their own persisted, validated response, never by interpreting prose.
    if not previous or not previous_response:return
    by_id={_legacy_finding_id(f):f for f in previous_response.get('findings',[])}
    for card in previous.get('findings',[]):
        finding=by_id.get(card['id'])
        if card.get('evidence_identity') or not finding or not finding.get('evidence_quotes'):continue
        candidate=dict(type=finding['type'],related_refs=card['related_refs'],source_refs=card['source_refs'],
            evidence_quotes=[dict(object_id=q['object_id'],path=q['path'],quote=q['text']) for q in card.get('quotes',[])])
        key=_evidence_identity(finding)
        if _evidence_identity(candidate)==key:
            card.update(finding_type=finding['type'],evidence_identity=key)


def _blocks_document(finding):
    return finding['severity']=='blocker' or finding.get('blocks_document',False)


def _resolution_current(p,card,question):
    resolution=(question or {}).get('prewrite_resolution',{})
    return (not question or resolution.get('basis')=='reviewed_application' and _implemented(p,question)
        or resolution.get('basis')=='reviewed_dispute' and resolution.get('evidence_hash')==_finding_input_hash(p,card))


def apply_result(p,response,run_id,*,previous_response=None,previous_review=_UNSET):
    validate_result(p,response)
    previous=copy.deepcopy(p.get('prewrite_review') if previous_review is _UNSET else previous_review)
    history_snapshot=copy.deepcopy(previous)
    _restore_evidence_identities(previous,previous_response)
    previous_findings=(previous or {}).get('findings',[])
    previous_ids={row['id']:row for row in previous_findings}
    prior_evidence={}
    for old in previous_findings:
        if old.get('evidence_identity'):
            prior_evidence.setdefault(old['evidence_identity'],[]).append(old)
    rows=structural_findings(p)
    decisions=list(response['result'].get('required_decisions') or [])
    needs_changes=response['result']['assessment']!='ready_for_human_review'
    model_has_blockers=any(f['severity']=='blocker' for f in response.get('findings',[]))
    seen=set()
    for f in response.get('findings',[]):
        evidence_identity=_evidence_identity(f)
        matches=prior_evidence.get(evidence_identity,[]) if evidence_identity else []
        fid=('PW-'+evidence_identity[:20]) if evidence_identity else _legacy_finding_id(f)
        if len(matches)==1:fid=matches[0]['id']
        if fid in seen:continue
        seen.add(fid)
        card=dict(id=fid,title=(f.get('decision_question') or f['message'])[:100],
            message=f['message'],suggested_resolution=f['suggested_resolution'],severity=f['severity'],
            related_refs=copy.deepcopy(f['related_refs']),source_refs=copy.deepcopy(f['source_refs']),
            quotes=_quotes(p,f),options=copy.deepcopy(f.get('decision_options') or []),kind='semantic',status='open',hard_block=False,
            finding_type=f['type'],evidence_identity=evidence_identity)
        if f.get('recommended_answer'):
            card['recommended_answer']=f['recommended_answer']
        correction_warning=(needs_changes and not model_has_blockers and f['severity']=='warning'
            and bool(_clause_refs(p,f['related_refs'])))
        question=f.get('decision_question')
        # Older compatible reviewers only have required_decisions. Bind by an
        # explicit object ID; never guess which of several findings it belongs to.
        if not question:
            # Shared clauses do not prove that two findings ask the same thing.
            # An explicitly bound decision belongs to that finding alone.
            explicitly_bound={x.get('decision_question') for x in response['findings'] if x is not f}
            matches=[d for d in decisions if d not in explicitly_bound and any(ref in d for ref in f['related_refs'])
                and sum(any(ref in d for ref in x['related_refs']) for x in response['findings'])==1]
            if len(matches)==1:question=matches[0]
            elif len(decisions)==1 and decisions[0] not in explicitly_bound and f['severity']=='blocker' and len({digest(x) for x in response['findings'] if x['severity']=='blocker'})==1:
                question=decisions[0]
        if not question and (f['severity']=='blocker' or correction_warning) and _clause_refs(p,f['related_refs']):
            # A known-source correction still requires a displayed human change
            # review. It is not invented as a new unknown business rule.
            card['decision_kind']='correction'
            question='请核对是否按已引用依据修正以下问题，并说明修改意见：'+f['suggested_resolution']
        if question and (f['severity']=='blocker' or correction_warning):
            card.update(requires_confirmation=True,blocks_document=True)
            _question_for(p,card,question)
            if question in decisions:decisions.remove(question)
        old=previous_ids.get(fid,{})
        prior_question=next((q for q in p['questions'] if q['id']==old.get('question_id')),None)
        if not card.get('question_id') and old.get('question_id') and (old.get('status')!='resolved'
                or not _resolution_current(p,old,prior_question)):
            # Merely downgrading the model severity cannot close an unanswered
            # correction. The existing decision still needs application/recheck.
            card.update(question_id=old['question_id'],requires_confirmation=True,blocks_document=True)
            if old.get('decision_kind'):card['decision_kind']=old['decision_kind']
            if prior_question:prior_question.pop('prewrite_resolution',None)
        rows.append(card)
    for decision in decisions:
        refs=sorted({ref for f in response['findings'] if f['severity']=='blocker'
            and (f.get('decision_question')==decision or ref_in_decision(f,decision)) for ref in f['related_refs']})
        require(refs,'PREWRITE_DECISION_BINDING_REQUIRED','待决定问题缺少关联条款，请重新检查。')
        card=dict(id='PW-'+digest(['decision',decision])[:20],title=decision[:100],message=decision,
            suggested_resolution='请补充业务决定后核对关联修改并复检。',severity='blocker',related_refs=refs,
            source_refs=[],quotes=[],options=[],kind='semantic',status='open',hard_block=False)
        _question_for(p,card,decision);rows.append(card)
    # A negative/unknown assessment cannot pass merely because no finding was returned.
    if needs_changes and not any(_blocks_document(f) for f in rows):
        rows.append(dict(id='PW-assessment',title='检查尚未通过',message=response['summary'],
            suggested_resolution='请核对审查说明并补齐后复检。',severity='blocker',related_refs=[],source_refs=[],
            quotes=[],options=[],kind='semantic',status='open',hard_block=False))
    active={r['id'] for r in rows}
    responses={r['finding_id']:r for r in p.get('prewrite_responses',[])}
    for old in (previous or {}).get('findings',[]):
        if old['id'] in active:continue
        question=next((q for q in p['questions'] if q['id']==old.get('question_id')),None)
        if old.get('status')=='resolved':
            if _resolution_current(p,old,question):
                rows.append(old);continue
            question.pop('prewrite_resolution',None)
            rows.append(dict(old,status='open'));continue
        objection=responses.get(old['id'],{})
        dispute=objection.get('mode')=='dispute' and objection.get('review_id')==(previous or {}).get('id')
        # The disappearance of a model finding alone cannot silently adopt a
        # business decision. Require the reviewed application or an explicit PM objection.
        implemented=_implemented(p,question)
        if question and not (implemented or dispute):
            rows.append(dict(old,status='open'));continue
        if question and question.get('prewrite_origin'):
            question['prewrite_resolution']=dict(run_id=run_id,at=now(),basis='reviewed_application' if implemented else 'reviewed_dispute',
                evidence_hash=_finding_input_hash(p,old))
        rows.append(dict(old,status='resolved'))
    rows=list({r['id']:r for r in rows}.values())
    blocking=[r for r in rows if _blocks_document(r) and r['status']!='resolved']
    report=dict(id=ident('PWR'),version=VERSION,input_hash=input_hash(p),input_revision=p['revision'],
        run_id=run_id,created=now(),status='needs_confirmation' if blocking else 'passed',
        message='请处理业务问题并复检。' if blocking else '当前版本成文检查通过，请继续核对生成的文档。',
        findings=rows,blocking_count=len(blocking),resolved_count=sum(r['status']=='resolved' for r in rows),
        assessment=response['result']['assessment'])
    if history_snapshot:p.setdefault('prewrite_history',[]).append(history_snapshot)
    p['prewrite_review']=report
    return report


def ref_in_decision(finding,decision):
    return any(ref in decision for ref in finding.get('related_refs',[]))


def status(p):
    report=copy.deepcopy(p.get('prewrite_review'))
    if not report:return dict(status='not_run',current=False,input_hash=input_hash(p),findings=[],blocking_count=0,resolved_count=0,message='尚未执行成文检查。')
    report['current']=report['input_hash']==input_hash(p)
    if not report['current'] and report['status'] not in ('running','failed'):report['status']='stale'
    questions={q['id']:q for q in p.get('questions',[])}
    for row in report.get('findings',[]):
        from .prewrite_correction import plan as correction_plan
        correction=correction_plan(p,row)
        if correction:row['saved_correction']=correction
        q=questions.get(row.get('question_id'))
        if row.get('status')!='resolved' and q and q.get('status')=='answered':
            row['status']=('awaiting_recheck' if not report['current'] else 'open') if q.get('understanding_status')=='applied' else 'answered'
        response=next((r for r in reversed(p.get('prewrite_responses',[])) if r['finding_id']==row['id']),None)
        if response:row['response']=copy.deepcopy(response)
    report['responses']=copy.deepcopy(p.get('prewrite_responses',[]))
    return report


def formal_issues(p):
    # Historical projects remain readable. Every newly generated PRD carries
    # this contract and cannot inherit a historical approval.
    artifact=p.get('documents',{}).get('prd',{})
    if not p.get('prewrite_review') and not artifact.get('prewrite_snapshot') and not artifact.get('prewrite_required'):return []
    report=status(p)
    if report['status']=='passed' and report['current']:return []
    return [dict(code='PREWRITE_REQUIRED',message='当前需求尚未通过成文一致性检查，请处理问题并复检。')]


def document_change_summary(previous,new):
    before={i['id']:i for i in (previous or {}).get('item_snapshot',[])}
    after={i['id']:i for i in new.get('item_snapshot',[])}
    changed=sorted(i for i in before.keys()&after.keys() if item_content(before[i])!=item_content(after[i]))
    added=sorted(after.keys()-before.keys());removed=sorted(before.keys()-after.keys())
    impacted=set(changed+added+removed)
    old_sections={s['section_id']:s for s in (previous or {}).get('content',{}).get('sections',[])}
    sections=[dict(section_id=s['section_id'],title=s['title']) for s in new['content']['sections']
        if s!=old_sections.get(s['section_id']) or any(impacted&set(b.get('ref_ids',[])) for b in s['blocks'])]
    return dict(summary='首次生成评审稿。' if not previous else f'相较上一版，新增 {len(added)} 项、修改 {len(changed)} 项、移除 {len(removed)} 项；请核对受影响章节。',
        previous_document_id=(previous or {}).get('id'),changed_item_ids=changed,added_item_ids=added,
        removed_item_ids=removed,affected_sections=sections)


def register(app,store):
    from .prewrite_correction import register as register_corrections
    register_corrections(app,store)
    @app.post('/api/projects/{pid}/prewrite-review/findings/{finding_id}/response')
    async def respond(pid:str,finding_id:str,body:dict):
        require(body.get('mode') in ('defer','dispute') and isinstance(body.get('reason'),str)
            and 0<len(body['reason'].strip())<=6000,'INPUT_REQUIRED','请说明暂未确定或认为不是冲突的理由。')
        with store.edit(pid,body.get('expected_revision'),'保存成文检查处理意见') as (p,db):
            report=p.get('prewrite_review') or {}
            require(report.get('id')==body.get('review_id') and report.get('input_hash')==body.get('input_hash'),
                'STALE_REVISION','成文检查结果已更新，请重新核对当前卡片。',409)
            require(any(f['id']==finding_id and f['status']!='resolved' for f in report.get('findings',[])),
                'NOT_FOUND','此成文问题不存在或已解决。',404)
            row=dict(finding_id=finding_id,review_id=report['id'],mode=body['mode'],reason=body['reason'].strip(),created=now())
            p.setdefault('prewrite_responses',[]).append(row)
        return dict(project=p,prewrite_review=status(p))
