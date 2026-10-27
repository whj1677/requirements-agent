"""Review already saved corrections without another model request."""
import copy
import re
from .core import digest, ident, now, require


def plan(project,finding):
    if finding.get('status')=='resolved':return None
    text=finding.get('suggested_resolution')
    if not isinstance(text,str) or not isinstance(finding.get('related_refs'),list):return None
    items={i['id']:i for i in project['items']}
    mentioned=set(re.findall(r'(?:REQ|AC|RULE|CON)-[A-Za-z0-9_-]+',text))
    if not mentioned or not mentioned<=items.keys():return None
    candidates=[i for i in project['items'] if i['id'] in mentioned and i.get('target_item_id')]
    if not candidates or any(i.get('selection_status')!='candidate' or i.get('superseded_by') for i in candidates):return None
    refs=set(finding.get('related_refs',[]))
    roots={r for r in refs if r in items and items[r]['kind']=='requirement'}
    roots.update(r for iid in refs if iid in items for r in items[iid].get('related_refs',[]) if r in items and items[r]['kind']=='requirement')
    for candidate in candidates:
        target=items.get(candidate['target_item_id'])
        if not target or target.get('selection_status')!='selected' or target.get('superseded_by'):return None
        if target['id'] not in roots and not roots.intersection(target.get('related_refs',[])):return None
    linked={qid for i in candidates for qid in i.get('answer_refs',[])}
    if len(linked)!=1:return None
    qids=list(dict.fromkeys([next(iter(linked)),finding.get('question_id')]))
    questions={q['id']:q for q in project['questions']}
    if any(qid not in questions or questions[qid].get('status')!='answered' or not questions[qid].get('answer') or questions[qid].get('superseded_by')
           or questions[qid].get('decision_group_id') or questions[qid].get('kind')=='parent' for qid in qids):return None
    return dict(candidate_ids=[i['id'] for i in candidates],question_ids=qids)


def build(project,finding):
    from .decision_review import build_preview,apply_review
    from .understanding import answer_targets
    selected=plan(project,finding)
    require(selected,'CORRECTION_UNAVAILABLE','已有修改不完整或已变化，请核对当前需求；不会重新调用 AI。',409)
    prospective=copy.deepcopy(project);reviews=[]
    items={i['id']:i for i in project['items']}
    candidates=[items[iid] for iid in selected['candidate_ids']]
    for index,qid in enumerate(selected['question_ids']):
        question=next(q for q in prospective['questions'] if q['id']==qid)
        targets=answer_targets(prospective,question)
        after={i['target_item_id']:i for i in candidates} if index==0 else {}
        group=dict(id='saved-'+qid,title='已有建议修改' if index==0 else '同时核对已保存的确认回答',decision_ids=[qid],
            candidate_item_ids=selected['candidate_ids'] if index==0 else [],
            target_item_ids=list(dict.fromkeys([i['target_item_id'] for i in candidates] if index==0 else [i['id'] for i in targets])))
        payload=dict(expected_revision=prospective['revision'],question_id=qid,
            coverage=[dict(decision_id=qid,status='clear',answer_quote=question['answer'])],groups=[group],
            review_behavior_item_ids=[i['id'] for i in candidates if index==0 and i['kind']=='requirement'],
            answer_implementations={qid:[dict(item_id=i['id'],field='statement',quote=after.get(i['id'],i)['statement']) for i in targets]})
        preview=build_preview(prospective,payload)
        require(not preview.get('blockers') and not preview.get('implementation_pending_ids'),
            'CORRECTION_UNAVAILABLE','这份修改尚不能完整落实回答，请核对关联条款。',409)
        reviews.append(preview)
        apply_review(prospective,preview,[group['id']])
    return dict(snapshot_hash=digest(project),reviews=reviews,
        targets=reviews[0]['groups'][0]['targets'],answers=[dict(question=r['decisions'][0]['question'],answer=r['answer_text']) for r in reviews])


def register(app,store):
    @app.post('/api/projects/{pid}/prewrite-review/findings/{fid}/saved-correction-preview')
    async def preview(pid:str,fid:str,body:dict):
        project=store.get(pid)
        require(project['revision']==body.get('expected_revision'),'STALE_REVISION','项目已变化，请重新查看修改。',409)
        finding=next((f for f in (project.get('prewrite_review') or {}).get('findings',[]) if f['id']==fid),None)
        require(finding is not None,'NOT_FOUND','此检查项已变化，请刷新核对。',404)
        payload=build(project,finding);key=ident('PCP')
        result=dict(payload=payload,preview_hash=digest(payload),expected_revision=project['revision'],finding_id=fid,created=now())
        store.record(pid,'prewrite_correction_preview',result,key)
        return dict(preview_id=key,**result)

    @app.post('/api/projects/{pid}/prewrite-review/saved-correction-commit')
    async def commit(pid:str,body:dict):
        from .decision_review import apply_review
        require(body.get('review_confirmed') is True and isinstance(body.get('idempotency_key'),str)
            and 8<=len(body['idempotency_key'])<=100,'REVIEW_REQUIRED','请先核对显示的修改并明确确认。')
        request_hash=digest(body)
        prior=next((r for r in store.records(pid,'prewrite_correction_receipt') if r['key']==body['idempotency_key']),None)
        if prior:
            require(prior['request_hash']==request_hash,'IDEMPOTENCY_CONFLICT','确认标识已用于另一份修改。',409)
            return dict(project=store.get(pid),receipt=prior)
        preview=store.get_record(pid,body.get('preview_id'),'prewrite_correction_preview')
        require(preview['preview_hash']==body.get('preview_hash')==digest(preview['payload']),
            'SNAPSHOT_INVALID','修改预览已变化，请重新核对。',409)
        with store.edit(pid,body.get('expected_revision'),'人工确认成文检查已有修改') as (project,db):
            require(not any(r['status'] in ('queued','running') for kind in ('run','user_task') for r in store.records(pid,kind,db)),
                'PROJECT_BUSY','项目任务正在运行，请结束后再确认。',409)
            require(digest(project)==preview['payload']['snapshot_hash'],'STALE_REVISION','需求、回答或来源已变化，请重新查看修改。',409)
            for review in preview['payload']['reviews']:
                apply_review(project,review,[g['id'] for g in review['groups']],store=store)
            receipt=dict(key=body['idempotency_key'],request_hash=request_hash,preview_id=body['preview_id'],created=now(),
                candidate_ids=[t['candidate_id'] for t in preview['payload']['targets']],external_model_calls=0)
            store.record(pid,'prewrite_correction_receipt',receipt,db=db)
        return dict(project=project,receipt=receipt)
