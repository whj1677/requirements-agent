"""Local recovery of a complete, unambiguous ordinary ingest response."""
import hashlib
import json
import copy
import re

from . import ingest
from .benchmark import ordinary_response
from .contracts import validate_response
from .core import digest, execution_hash, ident, now, require
from .model_json import identical_members
from .understanding import normalize_classification_labels


def rejected_detail_reference(store, project, run, context, excerpts):
    """Read-only reference for a normal single-row generation, never a resume."""
    from .core import Problem
    scope=run.get('requirement_recommendation_scope') or {}
    if not scope.get('requirement_id') or not scope.get('detail_required') or run.get('draft_request'):
        return None
    try:
        return _rejected_detail_reference(store,project,run,context,excerpts,scope['requirement_id'])
    except (Problem,OSError,ValueError,KeyError,TypeError,IndexError) as error:
        return dict(status='not_reused',reason=error.message if isinstance(error,Problem)
                    else '原详细候选或来源证据不完整，继续本次正常生成与审查。')


def _rejected_detail_reference(store,project,run,context,excerpts,requirement_id):
    from .business_draft import _aliases, _item_aliases
    from .domain_context import source_snapshot
    code='DETAIL_REFERENCE_UNAVAILABLE'
    def check(value,message):require(value,code,message,409)
    # Do not reach past a newer outcome for this row.
    review=next((r for r in reversed(project.get('business_draft_reviews',[]))
        if any(x.get('requirement_id')==requirement_id for x in
               [*r.get('accepted',[]),*r.get('rejected',[]),*r.get('field_rejected',[])])),None)
    check(review is not None,'本行没有可验证的未采纳详细候选。')
    rejected=next((x for x in review.get('field_rejected',[]) if x.get('requirement_id')==requirement_id
        and x.get('path',[None])[-1]=='detail_contract'),None)
    accepted=next((x for x in review.get('accepted',[]) if x.get('requirement_id')==requirement_id),None)
    check(rejected and accepted and (review.get('business_source_review') or {}).get('status')=='passed',
          '仅复用正文已核对且详细定义未采纳的同一行参考。')
    raw=rejected['raw'];semantic=rejected.get('semantic_review') or {}
    check(isinstance(raw,dict) and semantic.get('status') in ('failed','pending')
          and digest(raw)==semantic.get('raw_candidate_hash'),'末次未采纳详细候选身份不匹配。')
    old=store.get_record(project['id'],review['run_id'],'run')
    check(old.get('result_applied') is True and old.get('status')=='partial'
          and old.get('revision')==review['input_revision'],'原步骤不是已保存正文的部分结果。')
    check(old.get('business_draft_review',{}).get('raw_response_hash')==review.get('raw_response_hash'),
          '原步骤与已保存评审不匹配。')
    with store.connect() as db:
        row=db.execute('SELECT payload FROM revisions WHERE project_id=? AND revision=?',
                       (project['id'],old['revision'])).fetchone()
        before=json.loads(row['payload']) if row is not None else None
        after=None
        for row in db.execute(
            'SELECT payload FROM revisions WHERE project_id=? AND revision>=? AND revision<=?',
            (project['id'],old['revision'],project['revision'])):
            snapshot=json.loads(row['payload'])
            if execution_hash(snapshot)==old.get('output_state_hash'):
                check(after is None,'缺少唯一原输入或部分结果保存后的版本。')
                after=snapshot
            del snapshot
    check(before is not None and after is not None,'缺少唯一原输入或部分结果保存后的版本。')
    check(any(r==review for r in after.get('business_draft_reviews',[])), '原部分结果评审已变化。')
    # Only a verified chain of this exact normal clarification input may
    # intervene. Cancelled attempts retain their real sources and evidence.
    evidence_hashes={}
    current=copy.deepcopy(project)
    if current.get('current_task_source_id')!=after.get('current_task_source_id'):
        from .actions import target_context
        task=store.get_record(project['id'],run['user_task_id'],'user_task')
        submitted=task.get('retry_input',{})
        target=submitted.get('target') or {}
        original_message=submitted.get('message')
        check(isinstance(original_message,str) and original_message.strip()
              and target.get('kind')=='item' and target.get('id')==requirement_id
              and target.get('detail_required') is True,'本次任务的原始输入或目标绑定已变化。')
        expected_source_hash=hashlib.sha256(original_message.encode('utf-8')).hexdigest()
        tasks=store.records(project['id'],'user_task')
        previous=after
        with store.connect() as db:
            for record in db.execute('SELECT payload FROM revisions WHERE project_id=? AND revision>? AND revision<=? ORDER BY revision ASC',
                                    (project['id'],after['revision'],project['revision'])):
                snapshot=json.loads(record['payload'])
                check(snapshot['revision']==previous['revision']+1,'操作输入修订链不连续。')
                owners=[t for t in tasks if t.get('input_revision')==snapshot['revision']]
                check(len(owners)==1,'操作输入修订缺少唯一正常任务绑定。')
                owner=owners[0];prior=owner['id']!=task['id'];body=owner.get('retry_input',{})
                check(owner.get('action')=='clarify' and body.get('target')==target
                      and body.get('message')==original_message and owner.get('completed_steps',0)==0
                      and owner.get('input_state_hash')==execution_hash(snapshot),
                      '中间任务不是同文同目标且未应用的正常输入。')
                check(not prior or owner.get('status') in ('cancelled','failed'),
                      '历史操作尚未取消或失败，不能复用旧候选。')
                ids=owner.get('run_ids',[])
                check(len(ids)==1,'操作输入缺少唯一实际步骤绑定。')
                step=store.get_record(project['id'],ids[0],'run') if prior else run
                expected_message=('本次局部讨论对象：'+target_context(snapshot,target)+
                    '\n仅围绕此对象提出候选；保留其他需求、未知和采纳状态。\n用户修改意图：'+original_message)
                actual_message=step.get('message')
                if prior or step.get('status') in ('cancelled','failed'):
                    # Terminal progress messages replace run.message. The original
                    # dispatched request, not a parsed status string, binds it.
                    attempts=[a for a in step.get('attempts',[]) if a.get('actual_input_hash')==step.get('input_hash')]
                    check(len(attempts)==1,'历史操作缺少唯一原主请求证据。')
                    attempt=attempts[0];cid=attempt.get('call_id','')
                    check(re.fullmatch(r'CALL-[A-Za-z0-9_-]+',cid),'历史操作调用编号无效。')
                    folder=(store.folder/'evidence'/'model-calls').resolve();path=(folder/(cid+'.json')).resolve()
                    check(path.parent==folder,'历史调用证据路径超出当前项目。')
                    data=path.read_bytes();call=json.loads(data);messages=json.loads(call['request_input'])
                    check(call.get('run_id')==step['id'] and call.get('call_id')==cid
                          and digest(messages)==call.get('actual_input_hash')==attempt['actual_input_hash'],
                          '历史操作主请求身份或输入已变化。')
                    content=messages[1]['content'];packet=json.loads(content if isinstance(content,str) else content[0]['text'])
                    actual_message=packet.get('user_message');evidence_hashes[cid]=hashlib.sha256(data).hexdigest()
                check(step.get('id')==ids[0] and step.get('user_task_id')==owner['id']
                      and step.get('revision')==snapshot['revision'] and step.get('discussion_target')==target
                      and actual_message==expected_message
                      and not step.get('result_applied')
                      and (not prior or step.get('status') in ('cancelled','failed')),
                      '操作步骤与任务目标、原话或未应用状态不一致。')
                if not prior:
                    check(owner['id']==run.get('user_task_id') and snapshot['revision']==project['revision'],
                          '本次操作不是输入链末步骤。')
                sid=snapshot.get('current_task_source_id')
                added=[s for s in snapshot['sources'] if s['id'] not in {s['id'] for s in previous['sources']}]
                check(len(added)==1 and added[0]['id']==sid and added[0].get('sha256')==expected_source_hash,
                      '原任务后存在其它资料或不同输入。')
                normalized=copy.deepcopy(snapshot)
                normalized['sources']=[s for s in normalized['sources'] if s['id']!=sid]
                normalized['revision']=previous['revision']
                if 'current_task_source_id' in previous:normalized['current_task_source_id']=previous['current_task_source_id']
                else:normalized.pop('current_task_source_id',None)
                check(execution_hash(normalized)==execution_hash(previous),
                      '操作输入间来源、已答、规范或目标候选关系已变化。')
                previous=snapshot
        check(previous['revision']==project['revision'] and execution_hash(project)==execution_hash(previous),
              '当前业务状态与操作输入链不一致。')
    else:
        current['revision']=after['revision']
        check(execution_hash(current)==execution_hash(after),'来源、已答、规范或目标候选关系已变化。')
    sent=old.get('sent_excerpt_ids',[])
    old_excerpts={e['id']:dict(e,source_id=s['id']) for s in before['sources'] for e in s.get('excerpts',[])}
    check(sent and len(sent)==len(set(sent)) and all(e in old_excerpts for e in sent),'原读取范围不完整。')
    old_excerpts=[old_excerpts[e] for e in sent]
    check(source_snapshot(before,old_excerpts)==old.get('source_snapshot'), '原来源快照不匹配。')
    current_refs={(e['source_id'],e['id']):e for e in excerpts}
    check(all(current_refs.get((e['source_id'],e['id']))==e for e in old_excerpts),'原来源版本或摘录已变化。')
    old_aliases,reverse,_=_aliases(old_excerpts);new_aliases,_,_=_aliases(excerpts)
    old_items={alias:iid for iid,alias in _item_aliases(before).items()};new_items=_item_aliases(project)
    attempts={a['call_id']:a for a in old.get('attempts',[])}
    def load(cid):
        check(cid in attempts and re.fullmatch(r'CALL-[A-Za-z0-9_-]+',cid),'原调用关联不完整。')
        folder=(store.folder/'evidence'/'model-calls').resolve()
        path=(folder/(cid+'.json')).resolve()
        check(path.parent==folder,'原调用证据路径超出当前项目证据目录。')
        data=path.read_bytes();record=json.loads(data)
        check(record.get('call_id')==cid and record.get('run_id')==old['id'] and not record.get('evidence_limitations')
              and not attempts[cid].get('evidence_limitations'),'原调用身份或完整性不匹配。')
        messages=json.loads(record['request_input'])
        check(digest(messages)==record.get('actual_input_hash')==attempts[cid].get('actual_input_hash'),
              '原调用请求已变化。')
        check(record.get('http_status')==200 and record.get('finish_reason')=='stop'
              and record.get('response_received') is True,'原审查未完整返回。')
        evidence_hashes[cid]=hashlib.sha256(data).hexdigest()
        content=messages[1]['content'];packet=json.loads(content if isinstance(content,str) else content[0]['text'])
        return record,packet
    # The main request establishes the old S/L and item namespace, not the detail content.
    main_id=next((a['call_id'] for a in old['attempts'] if a.get('actual_input_hash')==old.get('input_hash')),None)
    main,original_context=load(main_id)
    main_review=main.get('business_draft_review') or {}
    check(main_review.get('business_source_review')==review.get('business_source_review') and
          any(r.get('raw')==raw and r.get('semantic_review')==semantic
              for r in main_review.get('field_rejected',[])), '保存的末次候选与原调用证据不一致。')
    check({(e['source'],e['locator']):e['text'] for e in original_context['excerpts']}==
          {(a['source'],a['locator']):old_excerpts[i]['text'] for i,e in enumerate(old_excerpts)
           for a in [old_aliases[(e['source_id'],e['id'])]]},'原生成来源别名与快照不匹配。')
    def stable(value):
        if isinstance(value,list):return [stable(v) for v in value]
        if not isinstance(value,dict):return copy.deepcopy(value)
        result={}
        for key,v in value.items():
            if key=='sources':
                check(all((r.get('source'),r.get('locator')) in reverse for r in v),'旧typed来源引用无唯一身份。')
                result['source_refs']=[copy.deepcopy(reverse[(r['source'],r['locator'])]) for r in v]
            elif key in ('depends_on','rule_refs','acceptance_refs'):
                check(all(r in old_items and old_items[r] in new_items
                          and new_items[old_items[r]] in context.get('detail_reference_options',{}).get(key,[])
                          for r in v),'旧条目局部引用无当前可见稳定身份，未猜测或删除。')
                result[key]=[old_items[r] for r in v]
            else:result[key]=stable(v)
        return result
    canonical=stable(raw)
    check(digest(canonical)==semantic.get('candidate_hash'),'末次原文与规范引用映射不一致。')
    history=review.get('detail_semantic_history') or []
    check(history,'末次详细审查链缺失。')
    last,packet=load(history[-1]['call_id'])
    candidates=packet.get('candidate')
    if not isinstance(candidates,list):
        candidates=packet.get('rows',[])
    matches=[r for r in candidates if r.get('row_key')==accepted['row_key'] and r.get('detail_contract')==canonical]
    check(len(matches)==1,'末次真实审查输入不是该完整详细候选。')
    # No inherited model verdict or scope exemption is exposed to generation.
    from .detail_draft import input_detail
    projected=input_detail(canonical,new_aliases,new_items)
    return dict(status='unaccepted_ai_candidate',detail_contract=projected,
        notice='完整未采纳AI参考，不是来源或已确认规范；逐项按本轮资料重新核对，缺引用仍缺，不继承旧审查结论。',
        identity=dict(run_id=old['id'],input_revision=old['revision'],applied_revision=after['revision'],
                      raw_candidate_hash=digest(raw),projected_hash=digest(projected),evidence_sha256=evidence_hashes))


def derive_intake_resume(store, project, task_id, body=None):
    from .core import Problem
    try:return _derive_intake_resume(store,project,task_id,body)
    except Problem as error:
        if error.code in ('INTAKE_RESUME_UNAVAILABLE','STALE_REVISION'):raise
        raise Problem('INTAKE_RESUME_UNAVAILABLE','原候选、来源或审查证据不能通过续审身份检查。',409) from error
    except (OSError,ValueError,KeyError,TypeError,IndexError) as error:
        raise Problem('INTAKE_RESUME_UNAVAILABLE','原候选或调用证据不完整，不能复用；原失败保留。',409) from error


def _derive_intake_resume(store, project, task_id, body=None):
    """Read only: the last unapplied initial candidate and its exact paired-review input."""
    code='INTAKE_RESUME_UNAVAILABLE'
    pid=project['id']
    task=next((t for t in store.records(pid,'user_task') if t['id']==task_id),None)
    require(task is not None,code,'原任务不属于当前项目。',409)
    require(task.get('action')=='organize' and task.get('status')=='failed'
        and task.get('error') in ('INTAKE_GROUNDING_BLOCKED','INTAKE_GROUNDING_UNVERIFIED') and task.get('run_ids')
        and not task.get('completed_steps') and not task.get('benchmark') and not task.get('draft_request'),
        code,'仅支持未应用的初始整理来源核对失败；请保留原任务，另行核对正常整理。',409)
    old_input=task.get('retry_input',{})
    require(not old_input.get('target') and not old_input.get('draft_request') and not old_input.get('benchmark'),code,'任务范围不支持候选续审。',409)
    if body is not None:
        for key,default in (('action','organize'),('message',''),('document_type','prd'),('option_id',None),
                            ('target',None),('benchmark',None),('draft_request',None),('attachment_source_ids',[]),('allow_review_draft',False)):
            require(body.get(key,default)==old_input.get(key,default),code,'原任务输入或范围已改变，不能复用旧候选。',409)
    run=next((r for r in store.records(pid,'run') if r['id']==task['run_ids'][-1]),None)
    require(run is not None,code,'原任务末步骤不存在。',409)
    require(run.get('user_task_id')==task_id and run.get('stage')=='ingest' and run.get('status')=='failed'
        and run.get('error')==task['error'] and not run.get('result_applied')
        and not run.get('draft_request') and not run.get('requirement_recommendation_scope') and not run.get('benchmark'),
        code,'原步骤不满足未应用初始整理条件。',409)
    require(execution_hash(project)==run.get('input_state_hash'), 'STALE_REVISION',
        '原任务之后资料、聊天或业务状态已变化，不能复用旧候选。',409)
    attempts={a['call_id']:a for a in run.get('attempts',[])};files={};loaded={}
    def load(call_id):
        require(call_id in attempts and re.fullmatch(r'CALL-[A-Za-z0-9_-]+',call_id) is not None,code,'原调用关联不完整。',409)
        path=store.folder/'evidence'/'model-calls'/(call_id+'.json')
        require(path.is_file(),code,'原调用证据缺失，不能续审。',409)
        data=path.read_bytes();record=json.loads(data)
        require(record.get('call_id')==call_id and record.get('run_id')==run['id'] and record.get('stage')=='ingest'
            and not record.get('evidence_limitations') and not attempts[call_id].get('evidence_limitations'),code,'原调用身份或证据完整性不满足续审。',409)
        request=json.loads(record['request_input'])
        require(digest(request)==record.get('actual_input_hash')==attempts[call_id].get('actual_input_hash'),code,'原调用输入证据校验失败。',409)
        files[call_id]=hashlib.sha256(data).hexdigest();loaded[call_id]=(record,request)
        return record,request
    grounding=run.get('intake_grounding') or {}
    from . import intake_grounding
    interrupted=task['error']=='INTAKE_GROUNDING_UNVERIFIED'
    require(grounding.get('status')==('unverified' if interrupted else 'blocked') and not grounding.get('candidate_applied')
        and grounding.get('review_call_ids') and (interrupted or grounding.get('challenge_call_ids'))
        and grounding.get('review_protocol')==intake_grounding.PAIRED_VERSION
        and grounding.get('coverage_protocol')==intake_grounding.COVERAGE_VERSION
        and grounding.get('challenge_protocol')==intake_grounding.CHALLENGE_VERSION,code,'缺少原完整末次双审身份。',409)
    main=None
    for call_id in attempts:
        record,request=load(call_id)
        if record.get('intake_grounding')==grounding:
            require(main is None,code,'原候选证据身份不唯一。',409)
            main=(record,request)
    require(main is not None,code,'没有绑定末次候选的原生成证据。',409)
    record,messages=main
    complete=record.get('http_status')==200 and record.get('finish_reason')=='stop'
    if record.get('dsh_intake'):
        http=record.get('dsh_http_requests',[]);last=http[-1] if http else {}
        reading=record.get('dsh_reading_coverage',{});files_read=reading.get('files',[])
        snapshot=record['dsh_intake'].get('snapshot',{})
        complete=(last.get('reading_phase')=='candidate_only' and last.get('status')=='response_received'
            and last.get('http_status')==200 and last.get('sse_done') is True and last.get('relay_stream_complete') is True
            and snapshot.get('input_hash')==record.get('actual_input_hash') and reading.get('skill_loaded') is True
            and bool(files_read) and all(f.get('complete') is True and not f.get('missing_ranges') for f in files_read)
            and sorted((f['path'],f['sha256'],f['total_chars']) for f in files_read)==sorted(
                (f['path'],f['sha256'],f['total_chars']) for f in last.get('finalization',{}).get('snapshot_identity',[])))
    require(record.get('response_received') and complete
        and not record.get('transport_rejection') and record.get('response_status') in (None,'completed'),code,'原生成响应不完整。',409)
    require(digest(json.loads(record['final_output']))==grounding.get('original_candidate_hash'),code,'原生成候选正文身份不一致。',409)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    require(header.get('initial_intake') is True and not header.get('detail_required')
        and header.get('response_protocol')=='business-draft-v1',code,'原请求不是无详细定义的初始整理。',409)
    candidate=json.loads(record['final_output']) if interrupted else record.get('intake_grounding_corrected_candidate')
    require(isinstance(candidate,dict) and digest(candidate)==grounding.get('candidate_hash')
        and not any(r.get('detail_contract') for r in candidate.get('requirements',[])),code,'末次完整候选不存在或身份已变。',409)
    if interrupted:
        import jsonschema
        require(jsonschema.Draft202012Validator(header['schema']).is_valid(candidate),
            code,'原候选不满足原请求的完整业务结构，不能续审。',409)
    history=record.get('intake_grounding_history',[])
    if interrupted:
        last=run['attempts'][-1];failure=grounding.get('error')
        last_ids=[grounding['review_call_ids'][-1],last['call_id']]
        failed=loaded[last['call_id']][0]
        require(failure in ('TIMEOUT','NETWORK_ERROR') and grounding.get('corrections')==0
            and not grounding.get('challenge_call_ids') and len(history)==1
            and history[0].get('call_id')==last_ids[0] and history[0].get('kind')=='coverage'
            and last.get('protocol')==intake_grounding.CHALLENGE_VERSION and last.get('repair_of')==last_ids[0]
            and last.get('error')==last.get('validation_result')==failed.get('validation_result')==failure
            and failed.get('repair_of')==last_ids[0] and failed.get('response_received') is False
            and failed.get('final_output') is None and failed.get('http_status') is None,
            code,'仅支持首轮完整候选在来源质疑请求无响应时续审。',409)
    else:
        last_ids=[grounding['review_call_ids'][-1],grounding['challenge_call_ids'][-1]]
        require(len(history)>=2 and [x.get('call_id') for x in history[-2:]]==last_ids
            and [x.get('kind') for x in history[-2:]]==['coverage','source_challenge'],code,'原末次双审链不完整。',409)
    packets=[]
    for call_id,protocol in zip(last_ids,(intake_grounding.COVERAGE_VERSION,intake_grounding.CHALLENGE_VERSION)):
        review,request=loaded[call_id]
        require(json.loads(request[0]['content'].split('可信任务头：',1)[1]).get('grounding_protocol')==protocol,
            code,'原末次双审协议不匹配。',409)
        if not (interrupted and call_id==last_ids[1]):
            require(review.get('response_received') and review.get('http_status')==200 and review.get('finish_reason')=='stop',code,'末次双审响应不完整。',409)
        content=request[1]['content'];packet=json.loads(content if isinstance(content,str) else content[0]['text'])
        require(packet['candidate']==candidate and packet['candidate_hash']==digest(candidate)
            and digest(packet['context'])==packet['input_hash']==grounding['input_hash'],code,'末次双审候选或上下文不一致。',409)
        packets.append(packet)
    require(packets[0]['context']==packets[1]['context'],code,'末次双审上下文不一致。',409)
    context=packets[0]['context'];plan=intake_grounding.prepare(candidate,context)
    coverage=intake_grounding.validate_coverage(plan,json.loads(loaded[last_ids[0]][0]['final_output']))
    if not interrupted:
        challenge=intake_grounding.validate_challenge(plan,json.loads(loaded[last_ids[1]][0]['final_output']))
        require('blocked' in (coverage['status'],challenge['status']),code,'原末次双审不是来源拒收。',409)
    current={e['id']:dict(e,source_id=s['id']) for s in project['sources'] if not s.get('excluded') for e in s['excerpts']}
    ids=run.get('sent_excerpt_ids',[])
    require(ids and len(ids)==len(set(ids)) and all(eid in current for eid in ids),code,'原读取范围已改变。',409)
    excerpts=[current[eid] for eid in ids]
    from .business_draft import _aliases,adapt
    aliases,_,_=_aliases(excerpts)
    expected={(aliases[(e['source_id'],e['id'])]['source'],aliases[(e['source_id'],e['id'])]['locator']):e['text'] for e in excerpts}
    actual={(e['source'],e['locator']):e['text'] for e in context['excerpts']}
    require(actual==expected and len(actual)==len(context['excerpts']),code,'原来源内容或别名绑定已变化。',409)
    from .domain_context import source_snapshot
    require(source_snapshot(project,excerpts)==run.get('source_snapshot'),code,'原来源快照已变化。',409)
    _,report=adapt(candidate,project,excerpts,'ingest',initial_intake_scope=True)
    # An interrupted first review may still have business findings to repair.
    # Eligibility only resumes review; the workflow retains source/adaptation
    # checks before applying any candidate. Completed BLOCKED reviews keep the
    # original stricter eligibility contract.
    if not interrupted:
        require(report.get('complete'),code,'末次候选不满足当前结构与引用要求。',409)
    identity=dict(task_id=task_id,run_id=run['id'],call_id=record['call_id'],candidate_hash=digest(candidate),
        context_hash=digest(context),input_state_hash=execution_hash(project),task_hash=digest(task),run_hash=digest(run),
        evidence_sha256=files,generation_skipped=True)
    return dict(identity=identity,candidate=copy.deepcopy(candidate),context=copy.deepcopy(context),
        messages=copy.deepcopy(messages),excerpts=excerpts)


def intake_resume_available(store,project,task):
    from .core import Problem
    if task.get('error') not in ('INTAKE_GROUNDING_BLOCKED','INTAKE_GROUNDING_UNVERIFIED'):return False
    try:derive_intake_resume(store,project,task['id']);return True
    except (Problem,OSError,ValueError,KeyError,TypeError,IndexError):return False


class IntakeResumeAudit:
    """Local run audit, never a model-call record or a fabricated HTTP response."""
    def __init__(self,store,pid,rid,identity):
        self.store,self.pid,self.rid=store,pid,rid
        self.value=dict(origin=copy.deepcopy(identity),generation_skipped=True)
        self.limitations=[]
    def save(self):
        self.store.update_run(self.pid,self.rid,intake_resume_audit=copy.deepcopy(self.value))
    def finish(self,status,elapsed=None):
        self.value['validation_result']=status
        self.save()


def derive(project, task, run, evidence):
    runtime = task['action'] in ('clarify', 'change') and run['stage'] == task['action']
    require((runtime or task['action'] == 'organize' and run['stage'] == 'ingest'
            and not task.get('retry_input', {}).get('target')) and task['status'] == 'failed' and
            not task.get('benchmark') and not run.get('requirement_recommendation_scope') and
            not run.get('draft_request') and run['status'] == 'failed' and
            not run.get('benchmark') and not run.get('result_applied') and
            run.get('user_task_id') == task['id'] and task['run_ids'][-1] == run['id'],
            'RECOVERY_UNAVAILABLE', '该任务不支持本地恢复，请保留原输入继续核对。', 409)
    require(execution_hash(project) == run.get('input_state_hash'), 'STALE_REVISION',
            '聊天、资料或草稿已有变化，不能将旧结果写入当前版本；已有内容保留。', 409)
    attempts = run.get('attempts', [])
    require((len(attempts) >= 1 if runtime else len(attempts) == 1) and not attempts[0].get('repair_of'), 'RECOVERY_UNAVAILABLE',
            '该输出涉及多轮修复，不能直接恢复到当前草稿。', 409)
    attempt = attempts[0]
    require(evidence.get('call_id') == attempt['call_id'] and evidence.get('run_id') == run['id'] and
            evidence.get('stage') == run['stage'] and evidence.get('response_received') and
            evidence.get('http_status') == 200 and evidence.get('finish_reason') == 'stop' and
            not evidence.get('transport_rejection') and evidence.get('response_status') in (None, 'completed') and
            not evidence.get('evidence_limitations') and not attempt.get('evidence_limitations'),
            'RECOVERY_UNAVAILABLE', '原始输出证据不完整，不能安全恢复；已保存资料保留。', 409)
    request = json.loads(evidence['request_input'])
    require(digest(request) == evidence.get('actual_input_hash') == attempt.get('actual_input_hash'),
            'RECOVERY_UNAVAILABLE', '原始调用输入校验不一致，不能恢复。', 409)
    context = json.loads(request[1]['content'][0]['text'])
    excerpts = context['excerpts']
    require([e['id'] for e in excerpts] == run['sent_excerpt_ids'], 'RECOVERY_UNAVAILABLE',
            '原始调用实际读取范围不一致，不能恢复。', 409)
    current = {(s['id'], e['id']): e for s in project['sources'] if not s['excluded'] for e in s['excerpts']}
    require(all((e['source_id'], e['id']) in current and
                e['text'] == current[(e['source_id'], e['id'])]['text'] for e in excerpts),
            'RECOVERY_UNAVAILABLE', '原始调用引用的来源内容已变化，不能恢复。', 409)
    if runtime:
        from .core import strict_model_json
        from .model_json import schema_closers
        from .response_normalization import normalize
        from .source_quotes import normalize_scope_quotes
        header = json.loads(request[0]['content'].split('可信任务头：', 1)[1])
        require(not header.get('response_protocol'), 'RECOVERY_UNAVAILABLE', '业务表格请从原任务核对恢复。', 409)
        raw = evidence.get('final_output')
        syntax = {}
        try:
            value = strict_model_json(raw)
        except (ValueError, TypeError):
            recovered = identical_members(raw) or schema_closers(raw, header['schema'])
            require(recovered is not None, 'RECOVERY_UNAVAILABLE', '原始输出内容不完整或有冲突，不能无损恢复。', 409)
            value, syntax = recovered
        value, slots = normalize(value, project, excerpts, run['stage'])
        value, quotes = normalize_scope_quotes(value, excerpts)
        value, classifications = normalize_classification_labels(value, excerpts)
        validate_response(value, run['stage'], project, excerpts, run['document_type'])
        return value, dict(rule='original-runtime-output-recovery-1', syntax=syntax,
            slots=slots, quotes=quotes, classification_corrections=classifications,
            raw_hash=digest(raw), derived_response_hash=digest(value))
    normalized = identical_members(evidence.get('final_output'))
    require(normalized is not None, 'RECOVERY_UNAVAILABLE',
            '重复字段内容有冲突或输出不完整，无法无损恢复；不自动选择或改写业务内容。', 409)
    value, paths = normalized
    value, empty_benchmark = ordinary_response(value)
    ingest.validate(value, excerpts)
    value, gates = ingest.inherit_new_question_gates(value, project)
    value, classifications = normalize_classification_labels(value, excerpts)
    validate_response(value, 'ingest', project, excerpts, run['document_type'])
    return value, dict(rule='identical-json-members-1', paths=paths,
        omitted_empty_benchmark=empty_benchmark, inherited_question_gates=gates,
        classification_corrections=classifications, raw_hash=digest(evidence['final_output']),
        derived_response_hash=digest(value))


def recover(store, pid, tid, revision):
    # An independent record is the idempotency key. Original runs/calls stay failed.
    previous = next((r for r in store.records(pid, 'local_recovery') if r['task_id'] == tid), None)
    if previous:
        return previous
    task = store.get_record(pid, tid, 'user_task')
    require(bool(task.get('run_ids')), 'RECOVERY_UNAVAILABLE', '任务没有可恢复的输出。', 409)
    run = store.get_record(pid, task['run_ids'][-1], 'run')
    require(bool(run.get('attempts')), 'RECOVERY_UNAVAILABLE', '没有原始输出可供恢复。', 409)
    call_id = run['attempts'][0]['call_id']
    import re
    require(bool(re.fullmatch(r'CALL-[A-Za-z0-9_-]+', call_id)), 'RECOVERY_UNAVAILABLE', '调用证据身份无效。', 409)
    path = store.folder / 'evidence' / 'model-calls' / (call_id + '.json')
    require(path.is_file(), 'RECOVERY_UNAVAILABLE', '原始调用证据不存在，不能恢复。', 409)
    raw_file = path.read_bytes()
    try:
        evidence = json.loads(raw_file)
    except (ValueError, UnicodeError):
        require(False, 'RECOVERY_UNAVAILABLE', '原始调用证据无法读取，不能恢复。', 409)
    record_id = ident('RECOVERY')
    with store.edit(pid, revision, '本地恢复待核对需求草稿') as (project, db):
        tasks = store.records(pid, 'user_task', db)
        runs = store.records(pid, 'run', db)
        require(not any(r['status'] in ('queued', 'running') for r in tasks + runs),
                'TASK_ACTIVE', '当前项目仍有任务运行，结束后才能恢复草稿。', 409)
        require(not any(r['task_id'] == tid for r in store.records(pid, 'local_recovery', db)),
                'RECOVERY_ALREADY_APPLIED', '本次草稿已恢复，请刷新查看。', 409)
        task = next(t for t in tasks if t['id'] == tid)
        run = next(r for r in runs if r['id'] == run['id'])
        require(path.read_bytes() == raw_file, 'RECOVERY_UNAVAILABLE', '调用证据已变化，请重新核对。', 409)
        try:
            value, proof = derive(project, task, run, evidence)
        except (ValueError, KeyError, TypeError, IndexError):
            require(False, 'RECOVERY_UNAVAILABLE', '原始调用证据结构不完整，不能恢复。', 409)
        from .response_application import apply_response
        before = {i['id'] for i in project['items']}
        apply_response(project, value)
        project['messages'][-1].update(local_recovery_id=record_id, run_id=run['id'])
        record = dict(task_id=tid, run_id=run['id'], call_id=call_id,
            created=now(), input_state_hash=run['input_state_hash'], input_revision=revision,
            output_revision=revision + 1, evidence_sha256=hashlib.sha256(raw_file).hexdigest(),
            validation_result='validated_candidates', normalization=proof, external_model_calls=0,
            candidate_ids=[i['id'] for i in project['items'] if i['id'] not in before])
        store.record(pid, 'local_recovery', record, record_id, db)
    store.update_record(pid, record_id, 'local_recovery', output_state_hash=execution_hash(project))
    return store.get_record(pid, record_id, 'local_recovery')


def available(store, project, task):
    """Expose recovery only when the saved first output passes current gates."""
    if task['status'] != 'failed' or task['action'] not in ('clarify', 'change') or not task.get('run_ids'):
        return False
    from .core import Problem
    import re
    try:
        run = store.get_record(project['id'], task['run_ids'][-1], 'run')
        call_id = run['attempts'][0]['call_id']
        if not re.fullmatch(r'CALL-[A-Za-z0-9_-]+', call_id):
            return False
        evidence = json.loads((store.folder/'evidence'/'model-calls'/(call_id+'.json')).read_text('utf-8'))
        derive(project, task, run, evidence)
        return True
    except (Problem, OSError, ValueError, KeyError, TypeError, IndexError):
        return False
