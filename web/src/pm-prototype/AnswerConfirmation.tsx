import React, { useEffect, useMemo, useRef, useState } from 'react';
import { api, Project } from '../api';
import { useDirty } from '../editing';
import { directReviewTargets, previewGroups } from './answer-actions';
import { requirementBehaviorLabels } from './requirement-fields';
import {validImplementationRefs, type AnswerImplementations, type AnswerImplementationRef} from '../answer-application-review';
import {AnswerImplementation} from './AnswerImplementation';
import {DetailContractChanges} from './DetailContractChanges';
import {DetailContractEditor} from './DetailContractEditor';
import {mergeDetailContract,detailPathValue,type DetailContract,type DetailConflict} from './detail-contract';
import './answer-confirmation.css';

type Obj = Record<string, any>;
type Edit = { statement: string; behavior: Record<string,string>; detail_contract?:DetailContract };
const fields = requirementBehaviorLabels;
const otherLabels:Record<string,string>={title:'名称',applies_to:'适用范围',epistemic_status:'内容依据',source_refs:'来源引用',related_refs:'关联条款',change_type:'需求性质',scope_evidence:'范围依据',classification_reason:'分类依据'};
const valueLabels:Record<string,string>={to_be:'本次目标',as_is:'现状',new:'新增',modified:'修改',preserved:'保持约束',existing:'现状',reported:'资料陈述',inferred:'推断待核对',confirmed:'已确认',unspecified:'待明确'};
function displayValue(value:any):string {if(value==null||value==='')return '无';if(typeof value==='string')return valueLabels[value]||value;return JSON.stringify(value);}
const identity = (item: Obj) => JSON.stringify([item.statement,item.behavior,item.detail_contract,item.source_refs,item.selection_status,item.content_version]);

/** One answer, visible clause changes, one explicit confirmation. */
export function AnswerConfirmation({ project, question, decisions, answer, answerDirty, onAnswerChange,
  onSaveAnswer, busy, running, blockReason, proposalCurrent, onOrganize, mutate, onSaved, onSources, onScope, onRefresh,
  variant='default', documentDiscussion, confirmLabel }: {
  project: Project; question: Obj; decisions: Obj[]; answer: string; answerDirty: boolean;
  onAnswerChange:(text:string)=>void; onSaveAnswer:()=>Promise<any>; busy:boolean; running:boolean;
  blockReason:string; proposalCurrent:boolean; onOrganize:()=>void;
  mutate:(path:string,body:Obj,method?:string)=>Promise<any>;
  onSaved:(latest:Project,notice:string,implementationPending?:boolean)=>void; onSources:(refs?:Obj[])=>void; onScope:()=>void; onRefresh:()=>void;
  confirmLabel?:string;
  variant?:'default'|'document'; documentDiscussion?:{document_id:string;section_id:string};
}) {
  const root='/projects/'+project.id, qid=question.decision_group_id||question.id;
  const documentMode=variant==='document';
  const [mode,setMode]=useState<'assistant'|'manual'>('assistant');
  const [manualExpanded,setManualExpanded]=useState(false);
  const [edits,setEdits]=useState<Record<string,Edit>>({}),[baseline,setBaseline]=useState<Record<string,string>>({});
  const [editingField,setEditingField]=useState('');
  const [confirmed,setConfirmed]=useState<string[]>([]);
  const [implementations,setImplementations]=useState<AnswerImplementations>({}),[implementationDirty,setImplementationDirty]=useState(false);
  const [review,setReview]=useState<Obj|null>(null),[selectedGroups,setSelectedGroups]=useState<string[]>([]);
  const [error,setError]=useState(''),[working,setWorking]=useState(false),[touched,setTouched]=useState(false);
  const [notice,setNotice]=useState('');
  const [switchRequested,setSwitchRequested]=useState(false);
  const [conflicts,setConflicts]=useState<{itemId:string;field:string;current:string;mine:string}[]>([]);
  const [detailConflicts,setDetailConflicts]=useState<Record<string,DetailConflict[]>>({});
  const originals=useRef<Record<string,Edit>>({});
  const previewRef=useRef<HTMLElement|null>(null),editorRef=useRef<HTMLDivElement|null>(null);
  const answerRef=useRef<HTMLTextAreaElement|null>(null);
  const intent=useRef<{signature:string;key:string}|null>(null);
  const lock=useRef(false),autoPreview=useRef('');
  const targets=useMemo(()=>[...new Map(decisions.flatMap(d=>directReviewTargets(project,d)).map(i=>[i.id,i])).values()],[project.items,qid]);
  const disabled=busy||running||working;
  const grouped=decisions.length>1;
  const activeDecisions=grouped?decisions.filter(d=>confirmed.includes(d.id)):decisions;
  const activeTargetIds=new Set(activeDecisions.flatMap(d=>directReviewTargets(project,d).map(i=>i.id)));
  const stale=!!review&&review.expected_revision!==project.revision;

  function resetEdits(){
    const values=Object.fromEntries(targets.map(item=>[item.id,{statement:item.statement||'',behavior:{...(item.behavior||{})},...(item.detail_contract?{detail_contract:structuredClone(item.detail_contract)}:{})}]));
    originals.current=values;setEdits(values);
    setBaseline(Object.fromEntries(targets.map(item=>[item.id,identity(item)])));
    setTouched(false);setReview(null);setEditingField('');setConflicts([]);setDetailConflicts({});
    setImplementations(previous=>Object.fromEntries(Object.entries(previous).map(([id,refs])=>[id,validImplementationRefs(refs,targets)])));setImplementationDirty(false);
  }
  useEffect(()=>{resetEdits();setImplementations({});setMode('assistant');setManualExpanded(false);setConfirmed([]);setError('');setNotice('');autoPreview.current='';},[project.id,qid]);
  useEffect(()=>{if(!touched&&!review)resetEdits();},[project.revision]);
  useEffect(()=>{setReview(null);setSelectedGroups([]);setImplementations({});setImplementationDirty(false);},[answer]);
  useEffect(()=>{if(review)requestAnimationFrame(()=>{previewRef.current?.scrollIntoView({block:'start'});previewRef.current?.focus({preventScroll:true});});},[review?.preview_hash]);
  useDirty(project.id+'-answer-clause-edits-'+qid,touched,'当前回答的需求修改',async()=>{throw Error('请查看需求修改并确认，或放弃本次未确认修改。');},()=>{resetEdits();setConfirmed([]);});

  function edit(itemId:string,patch:Partial<Edit>){
    const next={...edits,[itemId]:{...edits[itemId],...patch}};setEdits(next);
    const final=targets.map(item=>({...item,...next[item.id]}));
    setImplementations(previous=>Object.fromEntries(Object.entries(previous).map(([id,refs])=>[id,validImplementationRefs(refs,final)])));
    setTouched(true);setReview(null);setImplementationDirty(false);setError('');
  }
  function implementationTargets(decision:Obj,source:'manual'|'assistant'|'preview'=mode){
    return directReviewTargets(project,decision).map(item=>{
      if(source==='manual')return {...item,...edits[item.id]};
      if(source==='preview'){
        const target=(review?.groups||[]).flatMap((group:Obj)=>group.targets||[]).find((target:Obj)=>target.id===item.id);
        return target?.after?{...target.after,id:item.id}:item;
      }
      const candidate=project.items.filter(value=>value.target_item_id===item.id&&value.selection_status==='candidate'&&(value.answer_refs||[]).includes(decision.id)&&!value.superseded_by).at(-1);
      return candidate?{...candidate,id:item.id}:item;
    });
  }
  function implementationPayload(source:'manual'|'assistant'):AnswerImplementations{
    const included=source==='manual'?activeDecisions:decisions;
    return Object.fromEntries(included.map(decision=>[decision.id,validImplementationRefs(implementations[decision.id]||[],implementationTargets(decision,source))]));
  }
  function chooseImplementation(id:string,refs:AnswerImplementationRef[]){
    setImplementations(previous=>({...previous,[id]:refs}));setImplementationDirty(!!review);setTouched(true);setError('');
  }

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
        edits:included.map(item=>({item_id:item.id,statement:edits[item.id]?.statement||'',behavior:edits[item.id]?.behavior||{},...(edits[item.id]?.detail_contract?{detail_contract:edits[item.id].detail_contract}:{})})),
        review_behavior_item_ids:reviewIds,
        answer_implementations:implementationPayload('manual'),
        ...(grouped?{decisions:decisions.map(d=>({decision_id:d.id,confirmed:confirmed.includes(d.id),answer_quote:confirmed.includes(d.id)?answer:''}))}:{})
      });
      setReview(value);setSelectedGroups([]);setEditingField('');setImplementationDirty(false);
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
        groups:legacy?[]:previewGroups(project,decisions,coverage,Object.fromEntries(decisions.map(decision=>[decision.id,(implementationPayload('assistant')[decision.id]||[]).map(ref=>ref.item_id)]))),
        answer_implementations:legacy?{}:implementationPayload('assistant'),
        review_behavior_item_ids:legacy?[]:project.items.filter(item=>item.selection_status==='candidate'&&item.kind==='requirement'&&Object.values(coverage).some(row=>row.status==='clear'&&row.candidate_item_ids.includes(item.id))&&Object.keys(fields).every(key=>String(item.behavior?.[key]||'').trim())).map(item=>item.id),
        ...(legacy?{legacy_decisions:rows.map(r=>({question:r.question||'',related_refs:r.related_refs||[],status:r.coverage||'pending',answer_quote:r.answer_quote||''}))}:{})
      });
      setReview(value);setSelectedGroups([]);setImplementationDirty(false);
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
    if(lock.current||!review||stale||implementationDirty)return;
    const groups:Obj[]=review.groups||[];
    const chosen=groups.length===1?[groups[0].id]:selectedGroups;
    if(!chosen.length&&!review.legacy_split)return;
    lock.current=true;setWorking(true);setError('');let committed=false;
    try{
      const documentContext=documentDiscussion?.section_id.trim()?documentDiscussion:undefined;
      const signature=review.preview_hash+':'+chosen.slice().sort().join(',')+(documentContext?':'+JSON.stringify(documentContext):'');
      if(intent.current?.signature!==signature)intent.current={signature,key:crypto.randomUUID()};
      const result=await mutate('/decision-review/commit',{expected_revision:review.expected_revision,preview_id:review.preview_id,preview_hash:review.preview_hash,selected_group_ids:chosen,idempotency_key:intent.current.key,review_confirmed:true,...(documentContext?{document_discussion:documentContext}:{})});
      committed=true;setTouched(false);setReview(null);intent.current=null;
      const latest=await api<Project>(root);
      const pending=result.implementation_pending_ids||review.implementation_pending_ids||[];
      onSaved(latest,pending.length?'条款修改已保存，'+pending.length+' 项回答仍待落实到正文。':documentMode?'已确认本次变更。':'已确认本次需求修改。',pending.length>0);
    }catch(e){setError((committed?'修改已保存，状态刷新未完成，请刷新后继续。':'本次尚未确认，编辑已保留。')+(e as Error).message);}
    finally{lock.current=false;setWorking(false);}
  }
  async function reloadLatest(){
    setWorking(true);
    try{
      const latest=await api<Project>(root);
      const current=[...new Map(decisions.flatMap(d=>directReviewTargets(latest,d)).map(i=>[i.id,i])).values()];
      const merged:Record<string,Edit>={},nextConflicts:typeof conflicts=[],nextDetailConflicts:Record<string,DetailConflict[]>={};
      for(const item of current){
        const old=edits[item.id],base=originals.current[item.id],now:Edit={statement:item.statement||'',behavior:{...(item.behavior||{})},...(item.detail_contract?{detail_contract:structuredClone(item.detail_contract)}:{})};
        const merge=(field:string,previous:string,mine:string,value:string)=>{
          if(mine===previous)return value;
          if(mine!==value&&(value!==previous||conflicts.some(c=>c.itemId===item.id&&c.field===field)))nextConflicts.push({itemId:item.id,field,current:value,mine});
          return mine;
        };
        const detail=mergeDetailContract(old?.detail_contract,base?.detail_contract,now.detail_contract);
        nextDetailConflicts[item.id]=[...new Map([...(detailConflicts[item.id]||[]).filter(conflict=>JSON.stringify(detailPathValue(detail.value,conflict.path))!==JSON.stringify(conflict.remote)),...detail.conflicts].map(conflict=>[conflict.path,conflict])).values()];
        merged[item.id]=!old||!base?now:{statement:merge('statement',base.statement,old.statement,now.statement),behavior:Object.fromEntries([...new Set([...Object.keys(now.behavior),...Object.keys(old.behavior)])].map(field=>[field,merge(field,base.behavior[field]||'',old.behavior[field]||'',now.behavior[field]||'')])),...(detail.value?{detail_contract:detail.value}:{})};
      }
      originals.current=Object.fromEntries(current.map(item=>[item.id,{statement:item.statement||'',behavior:{...(item.behavior||{})},...(item.detail_contract?{detail_contract:structuredClone(item.detail_contract)}:{})}]));
      setBaseline(Object.fromEntries(current.map(item=>[item.id,identity(item)])));
      setEdits(merged);setConflicts(nextConflicts);setDetailConflicts(nextDetailConflicts);
      setReview(null);setError('');setMode('manual');setManualExpanded(true);onRefresh();
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
  const clausesChanged=targets.some(item=>JSON.stringify(edits[item.id])!==JSON.stringify(originals.current[item.id]));
  const originalOnlyPreview=!!review&&documentMode&&!review.legacy_split&&groups.length>0&&groups.every(group=>(group.targets||[]).every((target:Obj)=>
    ['title','statement','applies_to','epistemic_status','source_refs','related_refs','behavior','detail_contract','change_type','scope_evidence','classification_reason'].every(key=>JSON.stringify(target.before?.[key])===JSON.stringify(target.after?.[key]))));
  function implementationEditors(rows:Obj[],source:'manual'|'assistant'|'preview'){
    return rows.map(decision=><div key={decision.id}>
      {rows.length>1&&<p><b>{decision.question||decision.title}</b></p>}
      <AnswerImplementation questionId={decision.id} questionText={decision.question} answer={answer} targets={implementationTargets(decision,source)} value={implementations[decision.id]||[]} disabled={disabled} onChange={refs=>chooseImplementation(decision.id,refs)}/>
    </div>);
  }
  function chooseAssistant(){if(mode==='manual'&&touched)setSwitchRequested(true);else setMode('assistant');}
  return <section className={'answer-confirmation'+(documentMode?' answer-confirmation-document':'')} aria-label={documentMode?'本次讨论与修改确认':'回答与需求确认'}>
    <p className="refine-question-title">{question.decision_group_title||question.title||question.question}</p>
    <label className="connected-label" htmlFor={'answer-text-'+project.id}>{documentMode?'继续补充这次讨论':'我的回答原文'}</label>
    <textarea id={'answer-text-'+project.id} ref={answerRef} rows={documentMode?2:3} value={answer} disabled={disabled} onChange={e=>onAnswerChange(e.target.value)} placeholder={documentMode?'直接说明本次已明确的规则；不确定的地方仍可写待确认。':'填写已确定的业务规则；未确定的部分可以写待确认。'}/>
    <p className="refine-answer-hint">{documentMode?(!answer.trim()?'直接补充这次讨论，不必另填一份需求表。':answerDirty?'补充尚未发送，下一步会先保留你的原话。':'本次讨论原话已保留，修改仍需预览后确认。'):(!answer.trim()?'先填写已明确的回答，再核对关联需求。':answerDirty?'回答尚未保存，下一步会先保留原话。':'回答原话已保留。核对下面的需求内容后，确认本项。')}</p>
    {!review&&!documentMode&&<div className="answer-method" aria-label="需求修改方式">
      <button type="button" aria-pressed={mode==='assistant'} disabled={disabled} onClick={chooseAssistant}>让助手整理</button>
      <button type="button" aria-pressed={mode==='manual'} disabled={disabled} onClick={()=>setMode('manual')}>直接修改需求</button>
    </div>}
    {switchRequested&&<div role="alert" className="answer-switch-warning"><p>你有尚未确认的手工修改。请选择继续编辑，或明确放弃这些修改后使用助手。</p><button type="button" onClick={()=>setSwitchRequested(false)}>保留手工修改</button><button type="button" onClick={()=>{resetEdits();setSwitchRequested(false);setMode('assistant');}}>放弃手工修改并使用助手</button></div>}
    {!review&&mode==='assistant'&&<div className="answer-prepare">
      <p>{documentMode?'助手根据这次补充整理修改，先展示修改前后，再由你确认。':'助手根据回答整理需求修改，你核对修改前后，再确认保存。'}</p>
      {targets.length>0&&<p className="muted">关联内容：{targets.map(item=>item.title||item.id).join('、')}</p>}
      {proposalCurrent&&implementationEditors(decisions,'assistant')}
      <button type="button" className="primary" disabled={disabled||!answer.trim()||!!blockReason} onClick={()=>void(proposalCurrent?prepareModel():organize())}>{working?'正在准备修改…':documentMode?'发送补充并查看修改':proposalCurrent?'查看需求修改':answerDirty?'保存回答并整理修改':'整理需求修改'}</button>
      {documentMode&&targets.length>0&&<button type="button" disabled={disabled||!answer.trim()} onClick={()=>{setMode('manual');autoPreview.current='manual';setNotice('沿用原文时，请选择承载答案的位置并核对原句；尚未体现答案的内容继续保留为待落实。');}}>核对原文的落实位置</button>}
      {documentMode&&<button type="button" disabled={disabled} onClick={()=>{setMode('manual');setManualExpanded(true);}}>需要时直接编辑条款</button>}
      {blockReason&&<p role="status">{blockReason}。可以选择“{documentMode?'需要时直接编辑条款':'直接修改需求'}”继续人工核对。</p>}
      {!proposalCurrent&&!running&&<p className="muted">整理没有成功时，原话仍保留，也可以直接修改关联需求。</p>}
    </div>}
    {!review&&mode==='manual'&&<div className="answer-manual-editor" ref={editorRef}>
      <h4>{documentMode?'核对本次关联条款':'把需求改成你确认的内容'}</h4><p>原文已表达答案时，选择具体位置并核对即可；尚未表达时，可以修改正文或业务说明，也可以先保存为待落实。</p>
      {documentMode&&<div className="answer-method"><button type="button" disabled={disabled} onClick={chooseAssistant}>继续让助手整理</button>{!!targets.length&&<button type="button" disabled={disabled} aria-expanded={manualExpanded} onClick={()=>setManualExpanded(value=>!value)}>{manualExpanded?'收起条款编辑':'展开条款编辑'}</button>}</div>}
      {conflicts.map(c=><div key={c.itemId+':'+c.field} className="answer-field-conflict" role="alert"><b>{targets.find(t=>t.id===c.itemId)?.title} · {c.field==='statement'?'需求正文':fields[c.field]||c.field}存在不同修改</b><p>最新保存：{c.current}</p><p>你的编辑：{c.mine}</p><button type="button" onClick={()=>setConflicts(v=>v.filter(x=>x!==c))}>保留我的修改</button><button type="button" onClick={()=>{edit(c.itemId,c.field==='statement'?{statement:c.current}:{behavior:{...edits[c.itemId].behavior,[c.field]:c.current}});setConflicts(v=>v.filter(x=>x!==c));}}>采用最新内容</button></div>)}
      {grouped&&<fieldset><legend>{documentMode?'本次补充明确了哪些问题？':'本次已经回答清楚哪些问题？'}</legend>{decisions.map(d=><label className="answer-choice" key={d.id}><input type="checkbox" checked={confirmed.includes(d.id)} disabled={disabled} onChange={e=>{setConfirmed(v=>e.target.checked?[...v,d.id]:v.filter(id=>id!==d.id));setReview(null);setTouched(true);}}/>{d.question||d.title}</label>)}<p className="muted">未选的问题继续保留为待确认，回答原话自动作为本次依据。</p></fieldset>}
      {targets.filter(item=>!grouped||activeTargetIds.has(item.id)).map(item=><article className="answer-clause-editor" key={item.id}>
        <h4>{item.title||item.id}</h4><p className="answer-original"><b>当前内容</b>{item.statement}</p>
        {(!documentMode||manualExpanded)&&<label>{item.title||item.id}（修改后）<textarea rows={3} disabled={disabled} value={edits[item.id]?.statement||''} onChange={e=>edit(item.id,{statement:e.target.value})}/></label>}
        {(!documentMode||manualExpanded)&&item.kind==='requirement'&&<details className="answer-behavior-editor"><summary>业务说明 · 核对或修改</summary>{Object.entries(fields).map(([field,label])=><div key={field} className="answer-behavior-row"><b>{label}</b>{editingField===item.id+':'+field?<label>{label}（修改后）<textarea disabled={disabled} rows={2} value={edits[item.id]?.behavior[field]||''} onChange={e=>edit(item.id,{behavior:{...edits[item.id]?.behavior,[field]:e.target.value}})}/></label>:<><p>{edits[item.id]?.behavior[field]||'尚未填写'}</p><button type="button" disabled={disabled} onClick={()=>setEditingField(item.id+':'+field)}>修改{label}</button></>}</div>)}</details>}
        <button type="button" onClick={()=>onSources(item.source_refs||[])}>查看来源</button>
        {(!documentMode||manualExpanded)&&item.kind==='requirement'&&<details className="answer-detail-editor"><summary>字段、交互、流程与状态 · 核对或修改</summary><DetailContractEditor requirementId={item.id} title={item.title||item.id} value={edits[item.id]?.detail_contract} base={originals.current[item.id]?.detail_contract} disabled={disabled} conflicts={detailConflicts[item.id]||[]} confirmationLabel={confirmLabel||(documentMode?"确认本次变更":"确认并继续")} references={project.items.filter(value=>!value.target_item_id&&!value.superseded_by&&!['rejected','deferred','excluded'].includes(value.selection_status)).map(value=>({id:value.id,title:value.title||value.statement,kind:value.kind}))} onSources={onSources} onChange={(detail_contract,remaining)=>{edit(item.id,{detail_contract});if(remaining)setDetailConflicts(previous=>({...previous,[item.id]:remaining}));}}/></details>}
      </article>)}
      {implementationEditors(activeDecisions,'manual')}
      {!targets.length&&<div role="status"><p>本问题没有已采纳的关联条款。先核对本期需求，或让助手生成候选修改。</p><button type="button" onClick={onScope}>核对本期需求</button></div>}
      {!!targets.length&&<div className="answer-confirm-bar"><button type="button" className="primary" disabled={disabled||!!conflicts.length||Object.values(detailConflicts).some(rows=>rows.length>0)||!answer.trim()||grouped&&!confirmed.length} onClick={()=>void prepareManual()}>{working?'正在准备核对…':documentMode?clausesChanged?'查看本次修改':'预览本次落实核对':'查看修改并确认'}</button><span>预览不会修改需求。</span></div>}
    </div>}
    {review&&<section className="answer-change-preview" ref={previewRef} tabIndex={-1}>
      <h4>{originalOnlyPreview?'请核对回答与原文的对应':'请核对这次需求修改'}</h4>{originalOnlyPreview&&<p>正文沿用原文。本次仅确认回答与所选原句的对应，明确确认后才记为已落实。</p>}
      {(review.decisions||[]).map((d:Obj)=><p key={d.id}>{d.title||d.question} · {d.coverage==='clear'?'待你确认':d.coverage==='partial'?'部分已明确，其余保留':'仍待明确'}</p>)}
      {(review.blockers||[]).map((b:Obj,n:number)=><p className="connected-error" key={n}>{b.message||b.title||String(b)}</p>)}
      {!!review.implementation_pending_ids?.length&&<p role="status" className="answer-switch-warning">有 {review.implementation_pending_ids.length} 项回答尚未落实到正文。可以核对下面的原文位置，或先保存条款，保持回答待落实。</p>}
      {(review.implementation_issues||[]).map((issue:Obj,n:number)=><p key={'implementation-'+n} role="status">{issue.message}</p>)}
      {groups.map(g=><article key={g.id} className="answer-change-group">
        {groups.length>1&&<label className="answer-choice"><input type="checkbox" disabled={disabled} checked={selectedGroups.includes(g.id)} onChange={e=>toggleGroup(g.id,e.target.checked)}/>{g.title}：纳入本次确认</label>}
        {(g.targets||[]).map((t:Obj)=><div className="answer-difference" key={t.id+'-'+(t.candidate_id||'')}><h4>{t.title||t.id}</h4><div className="answer-before-after"><div><b>修改前</b><p>{t.before?.statement||'新增条款'}</p></div><div><b>确认后</b><p>{t.after?.statement||'无正文变化'}</p></div></div>
          {Object.entries(otherLabels).filter(([key])=>JSON.stringify(t.before?.[key])!==JSON.stringify(t.after?.[key])).map(([key,label])=><div className="answer-other-change" key={key}><b>{label}也将变化</b><p>原来：{displayValue(t.before?.[key])}</p><p>确认后：{displayValue(t.after?.[key])}</p></div>)}
          {t.after?.kind==='requirement'&&<div className="answer-behavior-preview"><h5>{t.review_behavior?'同时核对业务说明':'业务说明（缺项继续保留）'}</h5>{Object.entries(fields).map(([field,label])=>{const before=t.before?.behavior?.[field]||'',after=t.after?.behavior?.[field]||'';return <div className="answer-behavior-row" key={field}><b>{label}</b>{before!==after&&<p className="answer-old">原来：{before||'未填写'}</p>}<p>{after||'待补充'}</p></div>;})}</div>}
          <DetailContractChanges before={t.before?.detail_contract} after={t.after?.detail_contract}/>
          <button type="button" onClick={()=>onSources(t.after?.source_refs||t.before?.source_refs||[])}>核对来源</button>
        </div>)}
      </article>)}
      {implementationEditors(decisions.filter(decision=>(review.implementation_pending_ids||[]).includes(decision.id)),'preview')}
      {implementationDirty&&<p role="status">原文位置已重新核对，请更新预览后再确认。<button type="button" disabled={disabled} onClick={()=>void(mode==='manual'?prepareManual():prepareModel())}>更新落实核对预览</button></p>}
      {!groups.length&&!review.legacy_split&&<p>当前建议还没有可确认的条款修改。可以返回直接编辑，或补充回答后重新整理。</p>}
      {review.legacy_split&&<p>这个回答包含多个问题，先确认拆分，未解决的问题会继续显示。</p>}
      {stale&&<p className="connected-error" role="alert">项目内容已变化，本次预览不能提交。请返回编辑，核对最新内容后重新预览。</p>}
      <div className="answer-confirm-bar"><button type="button" disabled={disabled} onClick={()=>{setReview(null);setMode(documentMode&&!touched?'assistant':'manual');autoPreview.current=documentMode?qid+':'+question.decision_review_proposal?.stored_at+':'+project.revision:'manual';requestAnimationFrame(()=>{if(documentMode)answerRef.current?.focus();else editorRef.current?.scrollIntoView({block:'start'});});}}>{documentMode?'返回补充':'返回修改'}</button><button type="button" className="primary" disabled={disabled||stale||implementationDirty||!!review.blockers?.length||!review.legacy_split&&(!groups.length||groups.length>1&&!selectedGroups.length)} onClick={()=>void commit()}>{working?'正在确认…':review.legacy_split?'确认拆分并继续':review.implementation_pending_ids?.length?'保存条款并保留待落实':confirmLabel?confirmLabel:documentMode?originalOnlyPreview?'确认本次落实核对':'确认本次变更':'确认并继续'}</button><p>确认后保存以上条款及已展示的核对结果；未解决的问题仍会保留。</p></div>
    </section>}
    {error&&<div className="connected-error" role="alert"><p>{error}</p><button type="button" disabled={disabled} onClick={()=>void reloadLatest()}>重新核对最新内容</button></div>}
    {notice&&<p role="status">{notice}</p>}
  </section>;
}
