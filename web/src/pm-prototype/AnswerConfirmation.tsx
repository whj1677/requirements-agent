import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, Project } from '../api';
import { useDirty } from '../editing';
import { directReviewTargets, previewGroups } from './answer-actions';
import { requirementBehaviorLabels } from './requirement-fields';
import './answer-confirmation.css';

type Obj = Record<string, any>;
type Edit = { statement: string; behavior: Record<string,string> };
const fields = requirementBehaviorLabels;
const otherLabels:Record<string,string>={title:'名称',applies_to:'适用范围',epistemic_status:'内容依据',source_refs:'来源引用',related_refs:'关联条款',change_type:'需求性质',scope_evidence:'范围依据',classification_reason:'分类依据'};
const valueLabels:Record<string,string>={to_be:'本次目标',as_is:'现状',new:'新增',modified:'修改',preserved:'保持约束',existing:'现状',reported:'资料陈述',inferred:'推断待核对',confirmed:'已确认',unspecified:'待明确'};
function displayValue(value:any):string {if(value==null||value==='')return '无';if(typeof value==='string')return valueLabels[value]||value;return JSON.stringify(value);}
const identity = (item: Obj) => JSON.stringify([item.statement,item.behavior,item.source_refs,item.selection_status,item.content_version]);

/** One answer, visible clause changes, one explicit confirmation. */
export function AnswerConfirmation({ project, question, decisions, answer, answerDirty, onAnswerChange,
  onSaveAnswer, busy, running, blockReason, proposalCurrent, onOrganize, mutate, onSaved, onSources, onScope, onRefresh }: {
  project: Project; question: Obj; decisions: Obj[]; answer: string; answerDirty: boolean;
  onAnswerChange:(text:string)=>void; onSaveAnswer:()=>Promise<any>; busy:boolean; running:boolean;
  blockReason:string; proposalCurrent:boolean; onOrganize:()=>void;
  mutate:(path:string,body:Obj,method?:string)=>Promise<any>;
  onSaved:(latest:Project,notice:string)=>void; onSources:(refs?:Obj[])=>void; onScope:()=>void; onRefresh:()=>void;
}) {
  const root='/projects/'+project.id, qid=question.decision_group_id||question.id;
  const [mode,setMode]=useState<'assistant'|'manual'>('assistant');
  const [edits,setEdits]=useState<Record<string,Edit>>({}),[baseline,setBaseline]=useState<Record<string,string>>({});
  const [editingField,setEditingField]=useState('');
  const [confirmed,setConfirmed]=useState<string[]>([]);
  const [review,setReview]=useState<Obj|null>(null),[selectedGroups,setSelectedGroups]=useState<string[]>([]);
  const [error,setError]=useState(''),[working,setWorking]=useState(false),[touched,setTouched]=useState(false);
  const [notice,setNotice]=useState('');
  const [switchRequested,setSwitchRequested]=useState(false);
  const [conflicts,setConflicts]=useState<{itemId:string;field:string;current:string;mine:string}[]>([]);
  const originals=useRef<Record<string,Edit>>({});
  const previewRef=useRef<HTMLElement|null>(null),editorRef=useRef<HTMLDivElement|null>(null);
  const intent=useRef<{signature:string;key:string}|null>(null);
  const lock=useRef(false),autoPreview=useRef('');
  const targets=useMemo(()=>[...new Map(decisions.flatMap(d=>directReviewTargets(project,d)).map(i=>[i.id,i])).values()],[project.items,qid]);
  const disabled=busy||running||working;
  const grouped=decisions.length>1;
  const activeDecisions=grouped?decisions.filter(d=>confirmed.includes(d.id)):decisions;
  const activeTargetIds=new Set(activeDecisions.flatMap(d=>directReviewTargets(project,d).map(i=>i.id)));
  const stale=!!review&&review.expected_revision!==project.revision;

  function resetEdits(){
    const values=Object.fromEntries(targets.map(item=>[item.id,{statement:item.statement||'',behavior:{...(item.behavior||{})}}]));
    originals.current=values;setEdits(values);
    setBaseline(Object.fromEntries(targets.map(item=>[item.id,identity(item)])));
    setTouched(false);setReview(null);setEditingField('');setConflicts([]);
  }
  useEffect(()=>{resetEdits();setMode('assistant');setConfirmed([]);setError('');setNotice('');autoPreview.current='';},[project.id,qid]);
  useEffect(()=>{if(!touched&&!review)resetEdits();},[project.revision]);
  useEffect(()=>{setReview(null);setSelectedGroups([]);},[answer]);
  useEffect(()=>{if(review)requestAnimationFrame(()=>{previewRef.current?.scrollIntoView({block:'start'});previewRef.current?.focus({preventScroll:true});});},[review?.preview_hash]);
  useDirty(project.id+'-answer-clause-edits',touched,'当前回答的需求修改',async()=>{throw Error('请查看需求修改并确认，或放弃本次未确认修改。');},()=>{resetEdits();setConfirmed([]);});

  function edit(itemId:string,patch:Partial<Edit>){setEdits(current=>({...current,[itemId]:{...current[itemId],...patch}}));setTouched(true);setReview(null);setError('');}

  async function latestAfterSave(){
    if(answerDirty)await onSaveAnswer();
    return api<Project>(root);
  }

  async function prepareManual(){
    if(lock.current)return;
    lock.current=true;setWorking(true);setError('');setNotice('');
    try{
      const latest=await latestAfterSave();
      const record=[...((latest as Obj).decision_answers||[])].reverse().find((r:Obj)=>r.question_id===qid);
      const stored=record?.text??latest.questions.find(q=>q.id===question.id)?.answer??'';
      if(stored!==answer)throw Error('这条回答已经在别处更新。你的输入仍保留，请对照最新回答后重新保存。');
      for(const item of targets){
        const now=latest.items.find(i=>i.id===item.id);
        if(!now||identity(now)!==baseline[item.id])throw Error('关联需求已被修改。你的输入仍保留，请刷新并对照最新内容后重新核对。');
      }
      const included=targets.filter(item=>activeTargetIds.has(item.id));
      const reviewIds=included.filter(item=>item.kind==='requirement'&&Object.keys(fields).every(key=>String(edits[item.id]?.behavior[key]||'').trim())).map(item=>item.id);
      const value=await api<Obj>(root+'/decision-review/manual-preview','POST',{
        expected_revision:latest.revision,question_id:qid,answer_id:record?.id,expected_answer_text:answer,
        edits:included.map(item=>({item_id:item.id,statement:edits[item.id]?.statement||'',behavior:edits[item.id]?.behavior||{}})),
        review_behavior_item_ids:reviewIds,
        ...(grouped?{decisions:decisions.map(d=>({decision_id:d.id,confirmed:confirmed.includes(d.id),answer_quote:confirmed.includes(d.id)?answer:''}))}:{})
      });
      setReview(value);setSelectedGroups([]);setEditingField('');
    }catch(e){setError((e as Error).message);}
    finally{lock.current=false;setWorking(false);}
  }

  async function prepareModel(){
    if(lock.current||!proposalCurrent||answerDirty||!answer.trim()||!question.decision_review_proposal?.decisions?.length)return;
    lock.current=true;setWorking(true);setError('');
    try{
      const proposal=question.decision_review_proposal,rows:Obj[]=proposal?.decisions||[];
      const record=[...((project as Obj).decision_answers||[])].reverse().find((r:Obj)=>r.question_id===qid);
      const legacy=rows.length>1&&!question.decision_group_id&&question.status==='answered';
      const coverage:Record<string,{status:'pending'|'partial'|'clear'|'conflict';answer_quote:string;candidate_item_ids:string[]}>=Object.fromEntries(decisions.map(d=>{
        const row=rows.find(r=>r.id===d.id)||{};
        return[d.id,{status:row.coverage||'pending',answer_quote:row.answer_quote||'',candidate_item_ids:project.items.filter(i=>i.selection_status==='candidate'&&(i.answer_refs||[]).includes(d.id)).map(i=>i.id)}];
      }));
      const value=await api<Obj>(root+'/decision-review','POST',{
        expected_revision:project.revision,question_id:qid,answer_id:record?.id,
        coverage:legacy?[]:decisions.map(d=>({decision_id:d.id,...coverage[d.id]})),
        groups:legacy?[]:previewGroups(project,decisions,coverage),
        review_behavior_item_ids:legacy?[]:project.items.filter(item=>item.selection_status==='candidate'&&item.kind==='requirement'&&Object.values(coverage).some(row=>row.status==='clear'&&row.candidate_item_ids.includes(item.id))&&Object.keys(fields).every(key=>String(item.behavior?.[key]||'').trim())).map(item=>item.id),
        ...(legacy?{legacy_decisions:rows.map(r=>({question:r.question||'',related_refs:r.related_refs||[],status:r.coverage||'pending',answer_quote:r.answer_quote||''}))}:{})
      });
      setReview(value);setSelectedGroups([]);
    }catch(e){setError((e as Error).message);}
    finally{lock.current=false;setWorking(false);}
  }
  useEffect(()=>{
    const key=qid+':'+question.decision_review_proposal?.stored_at+':'+project.revision;
    if(mode==='assistant'&&proposalCurrent&&answer.trim()&&question.decision_review_proposal?.decisions?.length&&!answerDirty&&!disabled&&!review&&!error&&autoPreview.current!==key){autoPreview.current=key;void prepareModel();}
  },[mode,proposalCurrent,answer,answerDirty,disabled,project.revision]);

  async function organize(){
    setError('');
    try{if(answerDirty)await onSaveAnswer();onOrganize();}catch(e){setError((e as Error).message);}
  }
  async function commit(){
    if(lock.current||!review||stale)return;
    const groups:Obj[]=review.groups||[];
    const chosen=groups.length===1?[groups[0].id]:selectedGroups;
    if(!chosen.length&&!review.legacy_split)return;
    lock.current=true;setWorking(true);setError('');let committed=false;
    try{
      const signature=review.preview_hash+':'+chosen.slice().sort().join(',');
      if(intent.current?.signature!==signature)intent.current={signature,key:crypto.randomUUID()};
      await mutate('/decision-review/commit',{expected_revision:review.expected_revision,preview_id:review.preview_id,preview_hash:review.preview_hash,selected_group_ids:chosen,idempotency_key:intent.current.key,review_confirmed:true});
      committed=true;setTouched(false);setReview(null);intent.current=null;
      const latest=await api<Project>(root);
      onSaved(latest,'已确认本次需求修改。');
    }catch(e){setError((committed?'修改已保存，状态刷新未完成，请刷新后继续。':'本次尚未确认，编辑已保留。')+(e as Error).message);}
    finally{lock.current=false;setWorking(false);}
  }
  async function reloadLatest(){
    setWorking(true);
    try{
      const latest=await api<Project>(root);
      const current=[...new Map(decisions.flatMap(d=>directReviewTargets(latest,d)).map(i=>[i.id,i])).values()];
      const merged:Record<string,Edit>={},nextConflicts:typeof conflicts=[];
      for(const item of current){
        const old=edits[item.id],base=originals.current[item.id],now:Edit={statement:item.statement||'',behavior:{...(item.behavior||{})}};
        const merge=(field:string,previous:string,mine:string,value:string)=>{
          if(mine===previous)return value;
          if(mine!==value&&(value!==previous||conflicts.some(c=>c.itemId===item.id&&c.field===field)))nextConflicts.push({itemId:item.id,field,current:value,mine});
          return mine;
        };
        merged[item.id]=!old||!base?now:{statement:merge('statement',base.statement,old.statement,now.statement),behavior:Object.fromEntries([...new Set([...Object.keys(now.behavior),...Object.keys(old.behavior)])].map(field=>[field,merge(field,base.behavior[field]||'',old.behavior[field]||'',now.behavior[field]||'')]))};
      }
      originals.current=Object.fromEntries(current.map(item=>[item.id,{statement:item.statement||'',behavior:{...(item.behavior||{})}}]));
      setBaseline(Object.fromEntries(current.map(item=>[item.id,identity(item)])));
      setEdits(merged);setConflicts(nextConflicts);
      setReview(null);setError('');setMode('manual');onRefresh();
      setNotice('你的编辑已保留。当前原文已重新读取，请对照最新内容后再次预览。');
    }catch(e){setError('最新内容未能读取，编辑仍保留。'+(e as Error).message);}
    finally{setWorking(false);}
  }
  function toggleGroup(groupId:string,checked:boolean){
    const byId=new Map<string,Obj>((review?.groups||[]).map((g:Obj)=>[g.id,g]));
    const dependencies=(start:string,seen=new Set<string>()):Set<string>=>{if(seen.has(start))return seen;seen.add(start);for(const needed of byId.get(start)?.dependent_group_ids||[])dependencies(needed,seen);return seen;};
    setSelectedGroups(current=>checked?[...new Set([...current,...dependencies(groupId)])]:current.filter(id=>!dependencies(id).has(groupId)));
  }
  const groups:Obj[]=review?.groups||[];
  return <section className="answer-confirmation" aria-label="回答与需求确认">
    <p className="refine-question-title">{question.decision_group_title||question.title||question.question}</p>
    <label className="connected-label" htmlFor={'answer-text-'+project.id}>我的回答原文</label>
    <textarea id={'answer-text-'+project.id} rows={3} value={answer} disabled={disabled} onChange={e=>onAnswerChange(e.target.value)} placeholder="填写已确定的业务规则；未确定的部分可以写待确认。"/>
    <p className="refine-answer-hint">{!answer.trim()?'先填写已明确的回答，再核对关联需求。':answerDirty?'回答尚未保存，下一步会先保留原话。':'回答原话已保留。核对下面的需求内容后，确认本项。'}</p>
    {!review&&<div className="answer-method" aria-label="需求修改方式">
      <button type="button" aria-pressed={mode==='assistant'} disabled={disabled} onClick={()=>{if(mode==='manual'&&touched)setSwitchRequested(true);else setMode('assistant');}}>让助手整理</button>
      <button type="button" aria-pressed={mode==='manual'} disabled={disabled} onClick={()=>setMode('manual')}>直接修改需求</button>
    </div>}
    {switchRequested&&<div role="alert" className="answer-switch-warning"><p>你有尚未确认的手工修改。请选择继续编辑，或明确放弃这些修改后使用助手。</p><button type="button" onClick={()=>setSwitchRequested(false)}>保留手工修改</button><button type="button" onClick={()=>{resetEdits();setSwitchRequested(false);setMode('assistant');}}>放弃手工修改并使用助手</button></div>}
    {!review&&mode==='assistant'&&<div className="answer-prepare">
      <p>助手根据回答整理需求修改，你核对修改前后，再确认保存。</p>
      {targets.length>0&&<p className="muted">关联内容：{targets.map(item=>item.title||item.id).join('、')}</p>}
      <button type="button" className="primary" disabled={disabled||!answer.trim()||!!blockReason} onClick={()=>void(proposalCurrent?prepareModel():organize())}>{working?'正在准备修改…':proposalCurrent?'查看需求修改':answerDirty?'保存回答并整理修改':'整理需求修改'}</button>
      {blockReason&&<p role="status">{blockReason}。可以选择“直接修改需求”继续人工核对。</p>}
      {!proposalCurrent&&!running&&<p className="muted">整理没有成功时，原话仍保留，也可以直接修改关联需求。</p>}
    </div>}
    {!review&&mode==='manual'&&<div className="answer-manual-editor" ref={editorRef}>
      <h4>把需求改成你确认的内容</h4><p>已自动找出本问题关联的条款。核对原文，在这里直接修改。</p>
      {conflicts.map(c=><div key={c.itemId+':'+c.field} className="answer-field-conflict" role="alert"><b>{targets.find(t=>t.id===c.itemId)?.title} · {c.field==='statement'?'需求正文':fields[c.field]||c.field}存在不同修改</b><p>最新保存：{c.current}</p><p>你的编辑：{c.mine}</p><button type="button" onClick={()=>setConflicts(v=>v.filter(x=>x!==c))}>保留我的修改</button><button type="button" onClick={()=>{edit(c.itemId,c.field==='statement'?{statement:c.current}:{behavior:{...edits[c.itemId].behavior,[c.field]:c.current}});setConflicts(v=>v.filter(x=>x!==c));}}>采用最新内容</button></div>)}
      {grouped&&<fieldset><legend>本次已经回答清楚哪些问题？</legend>{decisions.map(d=><label className="answer-choice" key={d.id}><input type="checkbox" checked={confirmed.includes(d.id)} disabled={disabled} onChange={e=>{setConfirmed(v=>e.target.checked?[...v,d.id]:v.filter(id=>id!==d.id));setReview(null);setTouched(true);}}/>{d.question||d.title}</label>)}<p className="muted">未选的问题继续保留为待确认，回答原话自动作为本次依据。</p></fieldset>}
      {targets.filter(item=>!grouped||activeTargetIds.has(item.id)).map(item=><article className="answer-clause-editor" key={item.id}>
        <h4>{item.title||item.id}</h4><p className="answer-original"><b>当前内容</b>{item.statement}</p>
        <label>{item.title||item.id}（修改后）<textarea rows={3} disabled={disabled} value={edits[item.id]?.statement||''} onChange={e=>edit(item.id,{statement:e.target.value})}/></label>
        {item.kind==='requirement'&&<details className="answer-behavior-editor"><summary>业务说明 · 核对或修改</summary>{Object.entries(fields).map(([field,label])=><div key={field} className="answer-behavior-row"><b>{label}</b>{editingField===item.id+':'+field?<label>{label}（修改后）<textarea disabled={disabled} rows={2} value={edits[item.id]?.behavior[field]||''} onChange={e=>edit(item.id,{behavior:{...edits[item.id]?.behavior,[field]:e.target.value}})}/></label>:<><p>{edits[item.id]?.behavior[field]||'尚未填写'}</p><button type="button" disabled={disabled} onClick={()=>setEditingField(item.id+':'+field)}>修改{label}</button></>}</div>)}</details>}
        <button type="button" onClick={()=>onSources(item.source_refs||[])}>查看来源</button>
      </article>)}
      {!targets.length&&<div role="status"><p>本问题没有已采纳的关联条款。先核对本期需求，或让助手生成候选修改。</p><button type="button" onClick={onScope}>核对本期需求</button></div>}
      {!!targets.length&&<div className="answer-confirm-bar"><button type="button" className="primary" disabled={disabled||!!conflicts.length||!answer.trim()||grouped&&!confirmed.length} onClick={()=>void prepareManual()}>{working?'正在准备核对…':'查看修改并确认'}</button><span>预览不会修改需求。</span></div>}
    </div>}
    {review&&<section className="answer-change-preview" ref={previewRef} tabIndex={-1}>
      <h4>请核对这次需求修改</h4>
      {(review.decisions||[]).map((d:Obj)=><p key={d.id}>{d.title||d.question} · {d.coverage==='clear'?'待你确认':d.coverage==='partial'?'部分已明确，其余保留':'仍待明确'}</p>)}
      {(review.blockers||[]).map((b:Obj,n:number)=><p className="connected-error" key={n}>{b.message||b.title||String(b)}</p>)}
      {groups.map(g=><article key={g.id} className="answer-change-group">
        {groups.length>1&&<label className="answer-choice"><input type="checkbox" disabled={disabled} checked={selectedGroups.includes(g.id)} onChange={e=>toggleGroup(g.id,e.target.checked)}/>{g.title}：纳入本次确认</label>}
        {(g.targets||[]).map((t:Obj)=><div className="answer-difference" key={t.id+'-'+(t.candidate_id||'')}><h4>{t.title||t.id}</h4><div className="answer-before-after"><div><b>修改前</b><p>{t.before?.statement||'新增条款'}</p></div><div><b>确认后</b><p>{t.after?.statement||'无正文变化'}</p></div></div>
          {Object.entries(otherLabels).filter(([key])=>JSON.stringify(t.before?.[key])!==JSON.stringify(t.after?.[key])).map(([key,label])=><div className="answer-other-change" key={key}><b>{label}也将变化</b><p>原来：{displayValue(t.before?.[key])}</p><p>确认后：{displayValue(t.after?.[key])}</p></div>)}
          {t.after?.kind==='requirement'&&<div className="answer-behavior-preview"><h5>{t.review_behavior?'同时核对业务说明':'业务说明（缺项继续保留）'}</h5>{Object.entries(fields).map(([field,label])=>{const before=t.before?.behavior?.[field]||'',after=t.after?.behavior?.[field]||'';return <div className="answer-behavior-row" key={field}><b>{label}</b>{before!==after&&<p className="answer-old">原来：{before||'未填写'}</p>}<p>{after||'待补充'}</p></div>;})}</div>}
          <button type="button" onClick={()=>onSources(t.after?.source_refs||t.before?.source_refs||[])}>核对来源</button>
        </div>)}
      </article>)}
      {!groups.length&&!review.legacy_split&&<p>当前建议还没有可确认的条款修改。可以返回直接编辑，或补充回答后重新整理。</p>}
      {review.legacy_split&&<p>这个回答包含多个问题，先确认拆分，未解决的问题会继续显示。</p>}
      {stale&&<p className="connected-error" role="alert">项目内容已变化，本次预览不能提交。请返回编辑，核对最新内容后重新预览。</p>}
      <div className="answer-confirm-bar"><button type="button" disabled={disabled} onClick={()=>{setReview(null);setMode('manual');autoPreview.current='manual';requestAnimationFrame(()=>editorRef.current?.scrollIntoView({block:'start'}));}}>返回修改</button><button type="button" className="primary" disabled={disabled||stale||!!review.blockers?.length||!review.legacy_split&&(!groups.length||groups.length>1&&!selectedGroups.length)} onClick={()=>void commit()}>{working?'正在确认…':review.legacy_split?'确认拆分并继续':'确认并继续'}</button><p>确认后保存以上条款及已展示的核对结果；未解决的问题仍会保留。</p></div>
    </section>}
    {error&&<div className="connected-error" role="alert"><p>{error}</p><button type="button" disabled={disabled} onClick={()=>void reloadLatest()}>重新核对最新内容</button></div>}
    {notice&&<p role="status">{notice}</p>}
  </section>;
}
