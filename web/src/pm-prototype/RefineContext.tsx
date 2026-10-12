import React, { useEffect, useRef, useState } from 'react';
import { api, Project, JsonObject as Obj } from '../api';
import { ConflictReview, DraftConflictError, useDirty, useNavigation } from '../editing';
import { contextSnapshot, defaultScopeProfile, fieldStatus, isScopeFieldRequired, scopeDraft, scopeLabels, FieldState, ScopeProfile } from '../scope-draft';
import './RefineContext.css';

type Props = {project:Project;busy:boolean;mutate:(path:string,body:Obj)=>Promise<any>;focus?:{field?:string;nonce:number};focusedOnly?:boolean;onChanged?:()=>void;onProjectUpdated?:(project:Project)=>void;onTaskSaved?:()=>void;onOrganize?:()=>void;organizeBlocked?:string;onSources?:(refs:Obj[])=>void};
const statusNames:Record<FieldState['status'],string>={known:'已明确',unknown:'待确认',not_applicable:'不适用',none:'已确认没有额外约束'};
function choices(key:string,profile:ScopeProfile){const result:FieldState['status'][]=['known','unknown'];if(['value','priority','out_of_scope'].includes(key))result.push('not_applicable','none');if(['current_state','preserve_scope'].includes(key)&&!isScopeFieldRequired(key,profile))result.push('not_applicable');if(key==='preserve_scope')result.push('none');return result;}
const equal=(a:unknown,b:unknown)=>{const canonical=(value:any):any=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;return JSON.stringify(canonical(a))===JSON.stringify(canonical(b));};
function savedField(project:Project,key:string){return {value:project.product_context?.[key]||'',state:(project as Obj).context_field_states?.[key]||null};}
function unresolvedSummary(text:string,field:string){
  const value=text.trim().replace(/[。.!！]+$/,'');
  const labels=[scopeLabels[field],{intent:'目标',users:'目标用户',change_scope:'改动范围'}[field as 'intent'|'users'|'change_scope']||''];
  const rest=labels.reduce((result,label)=>label&&result.startsWith(label)?result.slice(label.length).replace(/^[：:，,\s]+/,''):result,value);
  return /^(?:待确认|待明确|待定|待补充|未知|不确定|尚未明确|尚未确定|暂不确定|有冲突)(?:[，,：:].*)?$/.test(value)||!!rest&&/^(?:待确认|待明确|待定|待补充|未知|不确定|尚未明确|尚未确定|暂不确定|有冲突)(?:[，,：:].*)?$/.test(rest);
}
function usableEvidence(project:Project,evidence:Obj|undefined){return !evidence||Array.isArray(evidence.source_refs)&&evidence.source_refs.length>0&&evidence.source_refs.every((ref:Obj)=>project.sources.some(source=>source.id===ref.source_id&&!source.excluded&&!(source as Obj).superseded_by&&source.excerpts?.some((excerpt:Obj)=>excerpt.id===ref.excerpt_id)));}

/** A field write never carries scope_ids or unrelated candidate suggestions. */
function ContextField({project,busy,mutate,onChanged,onProjectUpdated,onTaskSaved,field,fieldFocusNonce,focusedOnly,onOrganize,organizeBlocked,onSources}:Props&{field:string;fieldFocusNonce:number}){
  const profile=((project as Obj).scope_profile||defaultScopeProfile) as ScopeProfile;
  const currentState=()=>{const original=(project as Obj).context_field_states?.[field];return original||{status:fieldStatus(field,project.product_context||{}, {},profile),note:''};};
  const initialText=()=>!String(project.product_context?.[field]||'').trim()&&['none','not_applicable'].includes(currentState().status)?'':String(scopeDraft(project)[field]||'');
  const [text,setText]=useState(initialText),[state,setState]=useState<FieldState>(currentState);
  const [dirty,setDirty]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[conflict,setConflict]=useState<DraftConflictError|null>(null);
  const [editing,setEditing]=useState(false);
  const baseline=useRef(savedField(project,field)),lock=useRef(false);
  const proposed=String(project.product_context_proposal?.[field]||'').trim(),saved=String(project.product_context?.[field]||'').trim();
  const suggestion=!saved&&!!proposed&&!['none','not_applicable'].includes(state.status);
  const evidence=(project as Obj).product_context_proposal_evidence?.[field];
  const currentEvidence=String(evidence?.text||'').trim()===proposed?evidence:undefined;
  const evidenceValid=usableEvidence(project,currentEvidence);
  const uncertain=!!currentEvidence?.uncertain&&text.trim()===proposed||unresolvedSummary(text,field);
  const alternative=!!proposed&&proposed!==saved&&(!suggestion||['none','not_applicable'].includes(state.status));
  const hasInformation=project.messages.some(message=>message.role==='user')||project.sources.some(source=>!source.excluded&&source.excerpts?.length);
  useEffect(()=>{if(!dirty){setText(initialText());setState(currentState());baseline.current=savedField(project,field);}},[project,field,dirty]);
  useEffect(()=>{const timer=requestAnimationFrame(()=>{const section=document.getElementById('refine-context-field-'+project.id+'-'+field),editor=section?.querySelector('textarea');if(editor){if(!focusedOnly)section?.scrollIntoView({block:'start'});editor.focus({preventScroll:true});}});return()=>cancelAnimationFrame(timer);},[fieldFocusNonce]);
  function discard(){setText(initialText());setState(currentState());baseline.current=savedField(project,field);setDirty(false);setEditing(false);setError('');setNotice('');setConflict(null);}
  async function save(confirmSuggestion=false,continueAfterSave=false){
    if(lock.current)throw Error('当前字段正在保存。');
    const nextState:FieldState={...state,status:uncertain?'unknown':confirmSuggestion?'known':state.status};
    const originalField=baseline.current,reviewedProposal={text:project.product_context_proposal?.[field]||'',evidence:(project as Obj).product_context_proposal_evidence?.[field]||null};
    if(nextState.status==='known'&&!text.trim()){const message='已明确的字段需要填写内容；未知请保留待确认。';setError(message);throw Error(message);}
    if(!choices(field,profile).includes(nextState.status)){const message='当前场景不支持此字段状态，请先核对适用场景。';setError(message);throw Error(message);}
    if(confirmSuggestion){setState(nextState);setDirty(true);}
    lock.current=true;setSaving(true);setError('');setNotice('');
    try{
      const latest=await api<Project>('/projects/'+project.id);
      onProjectUpdated?.(latest);
      const latestValue=savedField(latest,field);
      if(!equal(latestValue,originalField))throw new DraftConflictError(originalField,latestValue,{value:text,state:nextState});
      if(proposed&&text.trim()===proposed&&(suggestion||dirty&&text.trim()!==saved)){
        if(!equal(reviewedProposal,{text:latest.product_context_proposal?.[field]||'',evidence:(latest as Obj).product_context_proposal_evidence?.[field]||null}))throw Error('AI 建议已经更新。你的草稿已保留，请取消本字段修改以查看新建议，或修改成你的判断后保存。');
        if(!usableEvidence(latest,currentEvidence))throw Error('这份建议的来源已不可用。请重新整理，或修改为你确认的内容后保存。');
      }
      const latestProfile=((latest as Obj).scope_profile||defaultScopeProfile) as ScopeProfile;
      if(!choices(field,latestProfile).includes(nextState.status))throw Error('适用场景已变化，请重新核对当前字段状态。');
      const snapshot=contextSnapshot(latest),values={[field]:text},states={[field]:nextState};
      const written=await mutate('/product-context',{expected_revision:latest.revision,values,field_states:states,
        __edit:{kind:'context',before:snapshot,mine:{...snapshot,values:{...snapshot.values,...values},field_states:{...snapshot.field_states,...states}}}}) as Project;
      onProjectUpdated?.(written);baseline.current=savedField(written,field);setState(nextState);setDirty(false);setEditing(false);setConflict(null);setNotice(scopeLabels[field]+'已保存，状态：'+statusNames[nextState.status]+'。'+(nextState.status==='unknown'?'文字已记录，仍待确认。':''));onChanged?.();if(continueAfterSave&&nextState.status!=='unknown')onTaskSaved?.();
    }catch(e){if(e instanceof DraftConflictError){if((e.latest as Obj)?.values)await api<Project>('/projects/'+project.id).then(value=>onProjectUpdated?.(value)).catch(()=>{});setConflict(e);}setError((e as Error).message);throw e;}finally{lock.current=false;setSaving(false);}
  }
  useDirty(project.id+'-refine-context-'+field,dirty,'业务摘要：'+scopeLabels[field],async()=>{await save();},discard);
  return <section className="refine-context-field" id={'refine-context-field-'+project.id+'-'+field}>
    <h4>{scopeLabels[field]}{!isScopeFieldRequired(field,profile)&&<small>按需补充</small>}</h4>
    {!editing&&text.trim()?<div className="refine-context-recommendation" aria-label={scopeLabels[field]+'待核对内容'}>
      <strong>{suggestion?'AI 已整理 · 待你确认':state.status==='unknown'?'已保存内容 · 待你确认':'已确认内容'}</strong>
      <p>{text}</p>
      {suggestion&&<small>核对下面这段理解即可，无需再写一遍。确认前不会作为已确定的摘要。</small>}
      {uncertain&&<p className="notice">这段建议仍含未明确的内容。可补充或修改后确认；仅保存文字不会解除待确认状态。</p>}
      {suggestion&&!evidenceValid&&<p className="notice">这份建议的来源已不可用，请重新整理或人工修订后确认。</p>}
      <div className="connected-actions"><button type="button" disabled={busy||saving} onClick={()=>setEditing(true)}>我来修改</button>{suggestion&&currentEvidence?.source_refs?.length>0&&onSources&&<button type="button" disabled={busy||saving} onClick={()=>onSources(currentEvidence.source_refs)}>查看整理依据</button>}</div>
    </div>:!editing?<div className="refine-context-empty" role="status">
      <strong>{['none','not_applicable'].includes(state.status)?statusNames[state.status]:hasInformation?'已有聊天或资料，尚未形成这项摘要':'这项内容尚无可核对建议'}</strong>
      <p>{['none','not_applicable'].includes(state.status)?'已保留你的判断；AI 建议不会自动替换。':hasInformation?'可以让助手根据已保存的信息整理为待确认草稿，无需重新填写已有信息。':'先提供想法或资料，助手整理后再由你确认；也可以直接填写。'}</p>
      <div className="connected-actions">{hasInformation&&onOrganize&&<button type="button" className="primary" disabled={busy||saving||!!organizeBlocked} onClick={onOrganize}>从已有聊天和资料整理草稿</button>}<button type="button" disabled={busy||saving} onClick={()=>setEditing(true)}>我来直接填写</button></div>
      {organizeBlocked&&<p className="notice">{organizeBlocked}</p>}
    </div>:<label>{scopeLabels[field]}内容<textarea autoFocus rows={3} maxLength={6000} disabled={busy||saving} value={text} onChange={e=>{setText(e.target.value);setState(s=>({...s,status:e.target.value.trim()?'known':'unknown'}));setDirty(true);setNotice('');}}/></label>}
    {alternative&&<details className="refine-context-alternative"><summary>另有一份 AI 建议，尚未采用</summary><p>{proposed}</p><p className="muted">当前人工内容和判断保持不变。选择建议后还需明确确认。</p><div className="connected-actions"><button type="button" disabled={busy||saving} onClick={()=>{setText(proposed);setState({status:currentEvidence?.uncertain?'unknown':'known',note:''});setDirty(true);setEditing(true);setNotice('');}}>用这份建议修改</button>{currentEvidence?.source_refs?.length>0&&onSources&&<button type="button" onClick={()=>onSources(currentEvidence.source_refs)}>查看建议依据</button>}</div></details>}
    <details className="refine-context-state-options"><summary>暂不确定或不适用时，在这里说明</summary><div className="refine-context-field-state"><label>{scopeLabels[field]}的状态<select disabled={busy||saving} value={state.status} onChange={e=>{setState(s=>({...s,status:e.target.value as FieldState['status']}));setDirty(true);setNotice('');}}>{Array.from(new Set([...choices(field,profile),state.status])).map(status=><option key={status} value={status} disabled={!choices(field,profile).includes(status)}>{statusNames[status]}</option>)}</select></label><label>状态依据（可选）<input disabled={busy||saving} maxLength={2000} value={state.note||''} onChange={e=>{setState(s=>({...s,note:e.target.value}));setDirty(true);setNotice('');}}/></label></div></details>
    <ConflictReview conflict={conflict} onDiscard={discard} onReviewed={()=>{const latest=conflict?.latest as Obj;if(latest&&'value' in latest)baseline.current=latest as ReturnType<typeof savedField>;else if(latest?.values)baseline.current={value:latest.values[field]||'',state:latest.field_states?.[field]||null};else baseline.current=savedField(project,field);setConflict(null);setError('');}}/>
    {error&&!conflict&&<p className="error" role="alert">{error}</p>}
    {notice&&<p className="connected-status" role="status">{notice}</p>}
    <div className="connected-actions">{dirty?<button type="button" className="primary" disabled={busy||saving||!!conflict} onClick={()=>void save(false,!!focusedOnly).catch(()=>{})}>{state.status==='unknown'?'保存为待确认':focusedOnly?'确认并继续':'确认这项内容'}</button>:suggestion||state.status==='unknown'&&!!text.trim()?<button type="button" className="primary" disabled={busy||saving||!!conflict||suggestion&&!evidenceValid} onClick={()=>void save(true,!!focusedOnly).catch(()=>{})}>{uncertain?'保存建议，仍待补充':focusedOnly?'确认并继续':'确认这项内容'}</button>:null}{editing&&<button type="button" disabled={busy||saving} onClick={discard}>{dirty?'取消本字段修改':'返回查看'}</button>}</div>
  </section>;
}

function ContextProfile({project,busy,mutate,onChanged,onProjectUpdated}:Props){
  const initial=()=>((project as Obj).scope_profile||defaultScopeProfile) as ScopeProfile;
  const [profile,setProfile]=useState<ScopeProfile>(initial),[dirty,setDirty]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(''),[conflict,setConflict]=useState<DraftConflictError|null>(null);
  const baseline=useRef(initial()),lock=useRef(false);
  useEffect(()=>{if(!dirty){setProfile(initial());baseline.current=initial();}},[project,dirty]);
  function discard(){setProfile(initial());baseline.current=initial();setDirty(false);setError('');setConflict(null);}
  async function save(){if(lock.current)throw Error('适用场景正在保存。');lock.current=true;setSaving(true);setError('');try{
    const latest=await api<Project>('/projects/'+project.id),latestProfile=((latest as Obj).scope_profile||defaultScopeProfile) as ScopeProfile;
    onProjectUpdated?.(latest);
    if(!equal(latestProfile,baseline.current))throw new DraftConflictError(baseline.current,latestProfile,profile);
    const snapshot=contextSnapshot(latest);const written=await mutate('/product-context',{expected_revision:latest.revision,values:{},scope_profile:profile,__edit:{kind:'context',before:snapshot,mine:{...snapshot,scope_profile:profile}}}) as Project;onProjectUpdated?.(written);baseline.current=profile;setDirty(false);setConflict(null);onChanged?.();
  }catch(e){if(e instanceof DraftConflictError){if((e.latest as Obj)?.scope_profile)await api<Project>('/projects/'+project.id).then(value=>onProjectUpdated?.(value)).catch(()=>{});setConflict(e);}setError((e as Error).message);throw e;}finally{lock.current=false;setSaving(false);}}
  useDirty(project.id+'-refine-context-profile',dirty,'业务摘要适用场景',save,discard);
  function change(next:ScopeProfile){setProfile(next);setDirty(true);}
  return <section className="refine-context-profile"><label>适用场景<select disabled={busy||saving} value={profile.scenario} onChange={e=>change({...profile,scenario:e.target.value as ScopeProfile['scenario']})}><option value="unspecified">暂不确定</option><option value="new_product">全新产品</option><option value="new_module">已有产品新增模块</option><option value="modify_existing">修改已有功能</option></select></label>{profile.scenario==='new_module'&&<label>模块依赖<select disabled={busy||saving} value={profile.dependencies} onChange={e=>change({...profile,dependencies:e.target.value as ScopeProfile['dependencies']})}><option value="unknown">尚未确定</option><option value="independent">独立模块</option><option value="shared">关联已有模块或共享能力</option></select></label>}<p className="muted">场景仅决定字段适用性；保存不会确认其他摘要字段、采纳条款或提交阶段。</p><ConflictReview conflict={conflict} onDiscard={discard} onReviewed={()=>{const latest=conflict?.latest as Obj;baseline.current=latest&&'scenario' in latest?latest as ScopeProfile:initial();setConflict(null);setError('');}}/>{error&&!conflict&&<p role="alert" className="error">{error}</p>}{dirty&&<div className="connected-actions"><button type="button" disabled={busy||saving||!!conflict} onClick={()=>void save().catch(()=>{})}>保存适用场景</button><button type="button" disabled={busy||saving} onClick={discard}>取消场景修改</button></div>}</section>;
}

export function RefineContext(props:Props){
  const [latestProject,setLatestProject]=useState(props.project);
  const project=latestProject.id===props.project.id&&latestProject.revision>props.project.revision?latestProject:props.project;
  const {focus}=props,profile=((project as Obj).scope_profile||defaultScopeProfile) as ScopeProfile,states=((project as Obj).context_field_states||{}) as Record<string,FieldState>;
  function syncProject(next:Project){setLatestProject(previous=>previous.id!==next.id||next.revision>=previous.revision?next:previous);props.onProjectUpdated?.(next);}
  const [open,setOpen]=useState(false),[field,setField]=useState(''),[fieldFocusNonce,setFieldFocusNonce]=useState(0),navigate=useNavigation();
  const missing=Object.keys(scopeLabels).filter(key=>{const status=fieldStatus(key,project.product_context||{},states,profile);return !choices(key,profile).includes(status)||status==='known'&&!String(project.product_context?.[key]||'').trim()||isScopeFieldRequired(key,profile)&&status==='unknown';});
  function edit(key:string){const openField=()=>{setField(key);setOpen(true);setFieldFocusNonce(n=>n+1);};if(field===key)openField();else navigate(openField);}
  useEffect(()=>{if(focus?.field&&scopeLabels[focus.field])edit(focus.field);},[project.id,focus?.nonce]);
  return <section className={'refine-context'+(props.focusedOnly?' refine-context-focused':'')}>
    <details open={open} onToggle={e=>setOpen(e.currentTarget.open)}><summary>业务摘要 · {missing.length?`还有 ${missing.length} 项需核对`:'已填信息可按需核对'}<span className="refine-context-overview">{[['intent','目标'],['users','用户'],['change_scope','改动']].map(([key,label])=>{const saved=String(project.product_context?.[key]||'').trim(),proposal=String(project.product_context_proposal?.[key]||'').trim();return <span key={key}><b>{label}：</b>{saved?(fieldStatus(key,project.product_context||{},states,profile)==='unknown'?'已保存，待确认：':'')+saved:proposal?'助手建议，待核对：'+proposal:'尚无内容，待补充'}</span>;})}</span></summary>
      <p className="muted">只保存正在修改的字段；未确认建议、未知与不适用分别保留。优先级按需补充。</p>
      {!!missing.length&&<div className="refine-context-missing" aria-label="待核对摘要字段">{missing.map(key=><button type="button" key={key} onClick={()=>edit(key)}>{scopeLabels[key]}</button>)}</div>}
      {field&&<ContextField key={project.id+'-'+field} {...props} project={project} onProjectUpdated={syncProject} field={field} fieldFocusNonce={fieldFocusNonce}/>}
      <details className="refine-context-more"><summary>适用场景与全部字段（按需）</summary><ContextProfile key={project.id} {...props} project={project} onProjectUpdated={syncProject}/><div className="refine-context-field-list">{Object.keys(scopeLabels).map(key=><button type="button" key={key} onClick={()=>edit(key)}>{scopeLabels[key]}<small>{statusNames[fieldStatus(key,project.product_context||{},states,profile)]}</small></button>)}</div></details>
    </details>
  </section>;
}
