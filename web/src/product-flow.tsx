import React, { useEffect, useRef, useState } from 'react';
import { Project, JsonObject as Obj, sourceStatusNames, api } from './api';
import { ConflictReview, DraftConflictError, useDirty, useNavigation, useSaveDrafts } from './editing';
import { Icon } from './ui-icons';
import { IntakeMaterialGroups } from './source-library';
import { DownloadButton } from './feedback';
import { contextSnapshot, scopeDraft, scopeLabels, defaultScopeProfile, fieldStatus, suggestedKeys, scopeSaveStates, isScopeFieldRequired, ScopeProfile, FieldState } from './scope-draft';
import { confirmCheckpoint } from './checkpoint-transition';
import { saveScopeDraft } from './scope-save';
import { BulkAdoption } from './bulk-adoption';
import { ManualAnswerApplication } from './answer-application';
import { BlockerFocus, focusBlockerElement } from './blocker-navigation';
import { retainBatchSelection, scopeSelection } from './scope-selection';
import { canStartDirect, isScopeQuestion } from './workflow-access';

const scope: Obj = scopeLabels;
const behavior: Obj = {actor:'谁操作',entry:'入口及前置条件',flow:'主要流程',data:'业务数据',permissions:'权限约束',result:'操作结果',exceptions:'边界和异常'};
type Props={p:Project;phase:number;busy:boolean;mutate:(path:string,body:Obj)=>Promise<unknown>;go:(n:number)=>void;getProject?:()=>Project;onAnalyze?:()=>Promise<void>;analysisBlocked?:string;analysisStatus?:React.ReactNode;analyzing?:boolean;onCandidate?:()=>void;onAcceptance?:()=>void;onItem?:(item:Obj,status:string)=>void;onSource?:(refs:Obj[])=>void;onSources?:(sourceId?:string)=>void;onHelp?:(target:Obj)=>void;focusRequirementId?:string;focusRequirementNonce?:number;blockerFocus?:BlockerFocus;onBlocked?:(latest:Project)=>void};

export function ProductCheckpoint({p,phase,busy,mutate,go,getProject,onAnalyze,analysisBlocked,analysisStatus,analyzing,onSources,onHelp,onCandidate,onAcceptance,onItem,onSource,focusRequirementId,focusRequirementNonce,blockerFocus,onBlocked,active=true,inputDraft}:Props & {active?:boolean;inputDraft?:{text:string;nonce:number}}) {
  const init=()=>phase===0?((p as Obj).intake||{text:p.product_context?.intent||'',platform:[p.product_context?.product,p.product_context?.module].filter(Boolean).join(' / '),preserved:p.product_context?.preserve_scope||''}):phase===3?(p.sketch_review||{applicable:true}):scopeDraft(p);
  const [values,setValues]=useState<Obj>(init),[ids,setIds]=useState<string[]>(p.product_context?.scope_ids||[]),[dirty,setDirty]=useState(false),[editing,setEditing]=useState<string|null>(null),[error,setError]=useState(''),[saving,setSaving]=useState(false),[checking,setChecking]=useState(false),[planning,setPlanning]=useState(false),[conflict,setConflict]=useState<DraftConflictError|null>(null);
  const [profile,setProfile]=useState<ScopeProfile>((p as Obj).scope_profile||defaultScopeProfile),[fieldStates,setFieldStates]=useState<Record<string,FieldState>>((p as Obj).context_field_states||{});
  const editedKeys=useRef(new Set<string>()),literalKeys=useRef(new Set<string>());
  const checkingRef=useRef(false),saveLock=useRef(false);
  const scopeInputs=useRef<Record<string,HTMLTextAreaElement|null>>({});
  const [focusField,setFocusField]=useState<{key:string}|null>(null);
  const [bulkBusy,setBulkBusy]=useState(false);
  const [batchSelection,setBatchSelection]=useState<string[]>([]);
  const [carriedText,setCarriedText]=useState('');
  const base=useRef(p.revision),state=p.product_flow?.[phase],navigate=useNavigation(),saveDrafts=useSaveDrafts();
  const behaviorReviewStatuses=(p as Obj).behavior_review_statuses||{};
  const selection=scopeSelection(p,ids,dirty);
  const adoptionDone=selection.ready&&selection.adopted===selection.scoped.length;
  const batchIds=retainBatchSelection(batchSelection,selection.eligible,active,selection.ready);
  const pendingBatch=phase===1&&batchIds.length>0;
  const eligibleKey=JSON.stringify(selection.eligible);
  useEffect(()=>{setBatchSelection(current=>{const next=retainBatchSelection(current,selection.eligible,active,selection.ready);return current.length===next.length&&current.every((id,n)=>id===next[n])?current:next;});},[active,p.id,selection.ready,eligibleKey]);
  const revisionCount=p.items.filter(i=>ids.includes(i.id)&&i.target_item_id).length;
  const hasAnalysis=p.messages.some(m=>m.role==='assistant'&&m.stage==='ingest')||p.items.length>0||p.questions.length>0;
  const before=useRef<Obj>({});
  useEffect(()=>{if(!dirty){setValues(init());setIds(p.product_context?.scope_ids||[]);setProfile((p as Obj).scope_profile||defaultScopeProfile);setFieldStates((p as Obj).context_field_states||{});base.current=p.revision;}},[p,phase,dirty]);
  const fields=phase===1?scope:phase===3?(values.applicable===false?{reason:'无界面变化的原因'}:{changes:'新增或修改区域',preserved:'原有保持部分',behavior:'入口、操作、结果与重要未决项'}):{};
  const capture=()=>{if(!dirty){base.current=p.revision;before.current=phase===0?((p as Obj).intake||{}):contextSnapshot(p);}};
  const edit=(key:string,value:unknown,literal=true)=>{capture();editedKeys.current.add(key);if(literal)literalKeys.current.add(key);else literalKeys.current.delete(key);setValues(v=>({...v,[key]:value}));if(phase===1)setFieldStates(v=>({...v,[key]:{status:String(value||'').trim()?'known':'unknown'}}));setDirty(true);};
  useEffect(()=>{
    if(phase!==1||!inputDraft)return;
    if(!inputDraft.text.trim()){setCarriedText('');return;}
    // A shortcut may carry text into an empty field, never replace an existing draft.
    if(!String(values.intent||'').trim()){edit('intent',inputDraft.text);setCarriedText('');}
    else setCarriedText(('\n\n'+values.intent+'\n\n').includes('\n\n'+inputDraft.text+'\n\n')?'':inputDraft.text);
  },[inputDraft?.nonce]);
  const draftKey=p.id+'-checkpoint-'+phase;
  async function save(all=false):Promise<Project>{
    if(saveLock.current)throw Error('保存仍在进行，请等待结果。');
    saveLock.current=true;setError('');setSaving(true);
    const keys=all?Object.keys(scope):Array.from(editedKeys.current);
    const states=scopeSaveStates(p,values,fieldStates,profile,keys,all);
    const mine=phase===0?{text:values.text||'',platform:values.platform||'',preserved:values.preserved||''}:{values,scope_ids:ids,scope_profile:profile,field_states:states};
    try{
      const proof={kind:phase===0?'intake':'context',before:dirty?before.current:contextSnapshot(p),mine};
      const result=phase===1?await saveScopeDraft({p,revision:base.current,values:Object.fromEntries(keys.map(k=>[k,values[k]||''])),ids,profile,states,editedKeys:Array.from(literalKeys.current),all,proof,mutate,read:()=>api<Project>('/projects/'+p.id),onWritten:written=>{base.current=written.revision;before.current=contextSnapshot(written);}}):await mutate(phase===0?'/intake':'/sketch-review',{
        expected_revision:base.current,__edit:phase===0||phase===1?{kind:phase===0?'intake':'context',before:dirty?before.current:contextSnapshot(p),mine}:undefined,
        ...(phase===0?mine:{applicable:values.applicable!==false,reason:values.reason||'',changes:values.changes||'',preserved:values.preserved||'',behavior:values.behavior||''})
      }) as Project;
      base.current=result.revision;before.current=contextSnapshot(result);editedKeys.current.clear();literalKeys.current.clear();setDirty(false);setEditing(null);setConflict(null);markSaved();return result;
    }catch(e){if(e instanceof DraftConflictError)setConflict(e);throw e;}finally{saveLock.current=false;setSaving(false);}
  }
  const discard=()=>{editedKeys.current.clear();literalKeys.current.clear();setDirty(false);setValues(init());setIds(p.product_context?.scope_ids||[]);setProfile((p as Obj).scope_profile||defaultScopeProfile);setFieldStates((p as Obj).context_field_states||{});setEditing(null);setConflict(null);setError('');base.current=p.revision;};
  const markSaved=useDirty(draftKey,dirty,'第'+(phase+1)+'步编辑',async()=>{await save();},discard);
  async function attempt(fn:()=>Promise<unknown>){try{await fn();}catch(e){setError((e as Error).message);}}
  async function check(){
    if(checkingRef.current)return;checkingRef.current=true;setChecking(true);setError('');
    try{
      const next=await confirmCheckpoint({phase,revision:p.revision,
        save:async()=>{await saveDrafts(draftKey);return phase===1||dirty?await save(true):getProject?.()||p;},
        read:()=>api<Project>('/projects/'+p.id),
        check:(revision,hash)=>mutate('/stage-checks/'+(phase+1),{expected_revision:revision,expected_hash:hash}),
        blocked:latest=>{if(onBlocked)onBlocked(latest);else focusBlockerElement('blockers-'+p.id+'-'+phase);}
      });
      go(next);
    }catch(e){setError((e as Error).message);throw e;}finally{checkingRef.current=false;setChecking(false);}
  }
  const locked=busy||saving||checking||planning||bulkBusy;
  const suggestions=suggestedKeys(p,values,fieldStates);
  function changeProfile(next:ScopeProfile){capture();setProfile(next);setDirty(true);}
  function changeFieldState(key:string,status:FieldState['status']){
    capture();editedKeys.current.add(key);
    setFieldStates(v=>({...v,[key]:{status}}));setDirty(true);
  }
  function stateChoices(key:string){const list:FieldState['status'][]=['known','unknown'];if(['value','priority','out_of_scope'].includes(key))list.push('not_applicable','none');if(['current_state','preserve_scope'].includes(key)&&!isScopeFieldRequired(key,profile))list.push('not_applicable');if(key==='preserve_scope')list.push('none');return list;}
  const stateNames:Record<FieldState['status'],string>={known:'已填写',unknown:'待确认',not_applicable:'不适用',none:'已确认没有额外约束'};
  function fieldDecision(key:string){return <label className="scope-field-decision">{scope[key]}的状态<select aria-label={scope[key]+'的状态'} disabled={locked} value={fieldStatus(key,values,fieldStates,profile)} onChange={e=>changeFieldState(key,e.target.value as FieldState['status'])}>{Array.from(new Set([...stateChoices(key),fieldStatus(key,values,fieldStates,profile)])).map(status=><option key={status} value={status} disabled={!stateChoices(key).includes(status)}>{stateNames[status]}</option>)}</select></label>;}
  function openScopeField(key:string,proposal?:string){
    const panel=['product','module','users','value','priority'].includes(key)?'facts':key;
    const open=()=>{setEditing(panel);setFocusField({key});if(proposal!==undefined)edit(key,proposal,false);};
    if(editing===panel)open();else navigate(open);
  }
  function focusRequirement(id?:string,reviewBehavior=false){
    const el=document.getElementById(id?'scope-requirement-'+id:'scope-requirements-'+p.id);
    if(el instanceof HTMLDetailsElement)el.open=true;
    el?.scrollIntoView({block:'center'});
    if(el instanceof HTMLDetailsElement)el.querySelector('summary')?.focus();
    if(reviewBehavior&&id){const details=document.getElementById('behavior-review-'+draftKey+'-'+id+'-behavior');if(details instanceof HTMLDetailsElement){details.open=true;details.scrollIntoView({block:'center'});details.querySelector('summary')?.focus();}}
  }
  useEffect(()=>{
    if(phase!==1||!focusField)return;
    const input=scopeInputs.current[focusField.key];
    if(input){const panel=input.closest('.scope-card-editor')||input.parentElement?.parentElement;const target=input.disabled?panel?.querySelector<HTMLSelectElement>('select'):input;target?.focus({preventScroll:true});target?.scrollIntoView({block:'center'});}
  },[editing,focusField,phase]);
  useEffect(()=>{if(phase!==1||!focusRequirementId)return;const timer=setTimeout(()=>focusRequirement(focusRequirementId,true),80);return()=>clearTimeout(timer);},[phase,focusRequirementId,focusRequirementNonce]);
  useEffect(()=>{
    if(!blockerFocus)return;
    const action=blockerFocus.action;
    if(phase===1&&action.kind==='context'&&action.field){openScopeField(action.field);return;}
    const timer=setTimeout(()=>{
      if(phase===1&&action.kind==='scope')focusBlockerElement('scope-range-'+p.id,'button:not(:disabled)');
      if(phase===1&&action.kind==='requirement'&&action.item_id){
        focusBlockerElement('scope-requirement-'+action.item_id,'select');
        const item=p.items.find(i=>i.id===action.item_id);
        if(item?.change_type==='unspecified')focusBlockerElement('behavior-review-'+draftKey+'-'+action.item_id+'-behavior','select');
      }
      if(phase===2&&action.kind==='behavior'&&action.item_id){
        const field=action.fields?.find(key=>Object.keys(behavior).includes(key));
        focusBlockerElement('behavior-review-'+draftKey+'-'+action.item_id+'-behavior',field?'[data-behavior-field="'+field+'"]':'textarea');
      }
    },80);
    return()=>clearTimeout(timer);
  },[phase,blockerFocus?.nonce]);
  return <article className={'card checkpoint step-check-'+phase}>
    <div className="section-heading"><h2>{['描述这次想解决的问题','核对需求草稿','核对业务行为与验收','核对草图表达','核对两份交付文档'][phase]}</h2><span className="pill">{dirty?'未保存':state?.complete?(phase===0?'已提供':'已核对'):state?.needs_recheck?'需重新核对':'待核对'}</span></div>
    {phase===0&&<>
      <div className="intake-layout"><div className="intake-fields">
        <label className="intake-row"><span><b>1. 现有平台 / 页面</b><small>请描述涉及的系统、平台或具体页面，已有资料可不重复填写。</small></span><textarea disabled={locked} rows={3} value={values.platform||''} onChange={e=>edit('platform',e.target.value)} placeholder="例如：目前使用的平台、希望改动的页面…"/></label>
        <label className="intake-row"><span><b>2. 本次想新增或修改什么 <em>*</em></b><small>请描述你的需求、期望达到的目标或需要解决的问题。</small></span><textarea disabled={locked} aria-label="核心诉求" rows={3} value={values.text||''} onChange={e=>edit('text',e.target.value)} placeholder="描述这次想解决的问题，零散信息也可以…"/></label>
        <label className="intake-row"><span><b>3. 保持不变 / 暂不涉及</b><small>请说明哪些内容保持不变，避免不必要的改动。</small></span><textarea disabled={locked} rows={3} value={values.preserved||''} onChange={e=>edit('preserved',e.target.value)} placeholder="哪些内容保持原状，哪些暂时不做…"/></label>
        <section className="intake-materials"><h3>4. 相关资料（可选）</h3><p>添加页面截图、需求文档或参考链接，帮助理解本次需求。</p><IntakeMaterialGroups sources={p.sources.map(source=>({...source,project_id:p.id}))} onOpen={onSources}/></section>
      </div><aside className="intake-guide"><h3>✦ AI 将帮助你整理为</h3><p>基于你的填写内容，整理出以下结果：</p>{[['document','现状摘要','梳理当前平台、页面现状与问题'],['target','改动目标','提炼本次改动的核心目标与价值'],['shield','保持项','识别需要保持不变的内容'],['question','待补充信息','发现可能缺失的关键信息']].map(([icon,title,desc],n)=><section className={'tone-'+n} key={title}><span className="tone-icon"><Icon name={icon}/></span><div><b>{title}</b><p>{desc}</p></div></section>)}</aside></div>
      <details className="assistant-note"><summary><Icon name="light"/><b>助手建议</b><span>先提供已知信息，缺失项稍后澄清。</span></summary><p>材料上传不等于已经理解。点击分析后先保存本次输入，再核对实际接收端与材料范围；保存失败不会发送模型请求。</p></details>
      <div className="step-actions intake-actions">{hasAnalysis&&<button className="primary" disabled={locked} onClick={()=>go(1)}>核对整理结果 →</button>}<button className={hasAnalysis?'':'primary'} disabled={locked||(!values.text?.trim()&&!values.platform?.trim()&&!p.sources.some(x=>!x.excluded))} onClick={()=>attempt(async()=>{setPlanning(true);try{if(dirty||!(p as Obj).intake)await save();await onAnalyze?.();}finally{setPlanning(false);}})}>{saving?'正在保存输入…':analyzing?'正在分析现状与诉求…':planning?'正在核对本次任务…':hasAnalysis?'重新分析现状与诉求':'✦ 分析现状与诉求 →'}</button><button disabled={locked||(!dirty&&!!(p as Obj).intake)} onClick={()=>attempt(()=>save())}>保存草稿</button><button onClick={()=>onSources?.()}>添加资料</button><span>{saving?'保存中…':dirty?'有未保存输入':(p as Obj).intake?([values.text,values.platform,values.preserved].some(value=>String(value||'').trim())?'输入已保存':'已保存资料范围，未填写文字'):'输入后即可发起分析预检'}</span></div>
      {analysisStatus}
    </>}
    {phase===1&&<>
      {canStartDirect(p)&&<p className="notice">已进入直接填写：助手对话可跳过。先填写并保存已知需求，再核对本期范围和采纳条目。</p>}
      {carriedText&&<aside className="notice" aria-label="保留的输入草稿"><p>原输入已保留，本页已有的需求内容未被覆盖。</p><blockquote className="preserve-lines">{carriedText}</blockquote><button type="button" disabled={locked} onClick={()=>{edit('intent',[values.intent,carriedText].filter(Boolean).join('\n\n'));setCarriedText('');}}>补充到本次需求</button><button type="button" disabled={locked} onClick={()=>setCarriedText('')}>保留原文，暂不补充</button></aside>}
      <section className="scope-profile" aria-label="需求场景">
        <label>这次属于哪种需求？<select aria-label="需求场景" disabled={locked} value={profile.scenario} onChange={e=>changeProfile({...profile,scenario:e.target.value as ScopeProfile['scenario']})}><option value="unspecified">暂不确定，待核对</option><option value="new_product">全新产品</option><option value="new_module">已有产品新增模块</option><option value="modify_existing">修改已有功能</option></select></label>
        {profile.scenario==='new_module'&&<label>是否关联已有模块或共享能力？<select aria-label="模块关联" disabled={locked} value={profile.dependencies} onChange={e=>changeProfile({...profile,dependencies:e.target.value as ScopeProfile['dependencies']})}><option value="unknown">尚未确认</option><option value="independent">独立模块，无既有功能关联</option><option value="shared">有关联，如账号、权限或数据</option></select></label>}
        <p>全新产品或确认独立的模块，可以不填写现状和保持项。优先级可选；需要澄清的信息会明确标出。</p>
      </section>
      {!hasAnalysis&&<label>本次要做什么<textarea aria-label="本次要做什么" disabled={locked} rows={3} value={values.intent||''} onChange={e=>edit('intent',e.target.value)} placeholder="直接填写需求、目标或问题，也可以添加已有资料。"/></label>}
      {canStartDirect(p)&&<div className="toolbar"><button type="button" className="primary" disabled={locked||!String(values.intent||'').trim()} onClick={()=>attempt(()=>save())}>{saving?'正在保存…':'保存需求输入'}</button><small>保存到项目后继续在本页核对，不调用模型。</small></div>}
      <div className="toolbar scope-autofill"><button type="button" className="primary" disabled={locked||!!analysisBlocked||!onAnalyze||(!Object.values(values).some(v=>String(v||'').trim())&&!p.sources.some(s=>!s.excluded))} title={analysisBlocked} onClick={()=>attempt(async()=>{setPlanning(true);try{await saveDrafts(draftKey);if(dirty)await save();await onAnalyze?.();}finally{setPlanning(false);}})}>{planning?'正在准备 AI 填充…':'✦ 一键 AI 填充'}</button><span>根据已保存信息生成待审核草稿，保留人工填写；未知内容不会强行补齐。</span><button type="button" onClick={()=>onSources?.()}>添加资料</button></div>
      {analysisBlocked&&<p className="notice">{analysisBlocked}；仍可直接填写和保存需求。</p>}
      {!!suggestions.length&&<p className="notice" role="status">AI 已为 {suggestions.length} 个空项提供建议：{suggestions.map(key=>scope[key]).join('、')}。请核对后点击“确认并进入下一步”；不同建议会单独展示。</p>}
      <ol className="scope-sequence" aria-label="本页采纳顺序">
        <li data-current={!selection.ready}><b>1. 保存本期范围</b><small>{selection.ready?`已保存 ${ids.length} 条`:selection.reason}</small></li>
        <li data-current={selection.ready&&!adoptionDone&&!batchIds.length}><b>2. 临时选择待采纳项</b><small>{adoptionDone?'没有待采纳项':`本次选择 ${batchIds.length} 条；离开后清空`}</small></li>
        <li data-current={selection.ready&&!!batchIds.length}><b>3. 核对后采纳</b><small>{adoptionDone?'本期条目已采纳，状态已保留':'对照内容和来源，再明确确认'}</small></li>
      </ol>
      <details className="scope-range" id={'scope-range-'+p.id} aria-label="本期范围设置" open={!selection.ready}>
        <summary>1. 本期范围 · {selection.ready?`已保存 ${ids.length} 条，点击查看或调整`:'需要设置并保存'}</summary>
        <div className="section-heading"><div><h3>1. 确定并保存本期范围</h3><p>范围会保留。纳入本期后，还需要在下方核对并采纳。</p></div><button type="button" disabled={locked} onClick={onCandidate}>＋ 新增候选需求</button></div>
        <ul className="scope-range-list">{p.items.filter(i=>i.kind==='requirement'&&i.applies_to==='to_be').map(i=><li key={i.id}><strong>{i.title}</strong><span className="scope-membership">{ids.includes(i.id)?'已纳入本期':'本期不包含'}</span><button type="button" disabled={locked} aria-label={(ids.includes(i.id)?'移出本期 ':'纳入本期 ')+i.title} onClick={()=>{capture();setIds(current=>current.includes(i.id)?current.filter(id=>id!==i.id):[...current,i.id]);setDirty(true);}}>{ids.includes(i.id)?'移出本期':'纳入本期'}</button></li>)}</ul>
        <div className="toolbar"><button type="button" disabled={locked} onClick={()=>{capture();setIds(p.items.filter(i=>i.kind==='requirement'&&i.applies_to==='to_be').map(i=>i.id));setDirty(true);}}>全部纳入本期</button><button type="button" disabled={locked||!ids.length} onClick={()=>{capture();setIds([]);setDirty(true);}}>全部移出本期</button><button type="button" className={selection.ready?'':'primary'} disabled={locked||(!dirty&&selection.ready)} onClick={()=>attempt(async()=>{await save();if(ids.length)focusBlockerElement('scope-requirements-'+p.id);})}>{saving?'正在保存…':'保存本期范围'}</button><span role="status">{dirty?'范围或背景有修改，尚未保存':`已保存范围 ${ids.length} 条；其中已采纳 ${selection.adopted} 条`}</span></div>
      </details>
      <section className="scope-requirements" id={'scope-requirements-'+p.id}><div className="section-heading"><div><h3><Icon name="list"/>2. 选择并核对待采纳条目</h3><p>下方勾选只用于本次批量操作。已采纳状态单独显示，不用勾选表示。</p></div></div>
      <div className="scope-bulk-adoption" aria-label="批量采纳本期需求">
        {!selection.ready&&<p className="notice">{selection.reason}<button type="button" onClick={()=>focusBlockerElement('scope-range-'+p.id,'button:not(:disabled)')}>去设置并保存范围 ↑</button></p>}
        <div className="toolbar"><span>本次选择 {batchIds.length} 条（临时）</span><button type="button" disabled={locked||!selection.ready||!selection.eligible.length} onClick={()=>setBatchSelection(selection.eligible)}>选择全部待采纳项</button><button type="button" disabled={locked||!batchIds.length} onClick={()=>setBatchSelection([])}>清空本次选择</button>
          <BulkAdoption p={p} ids={batchIds} busy={busy||saving||checking||planning||!selection.ready} onBusy={setBulkBusy} mutate={mutate} save={async()=>{if(!selection.ready)throw Error(selection.reason);await saveDrafts(draftKey,batchIds.map(id=>draftKey+'-'+id+'-behavior'));}} onEdit={id=>focusRequirement(id,true)} onSelectionReset={()=>setBatchSelection([])}/>
        </div><p>{adoptionDone?'本期条目均已采纳，无需重复勾选。继续核对本页背景信息后，可进入规则澄清。':selection.ready&&!selection.eligible.length?'没有可批量采纳的候选。暂缓或不采纳的条目需先改回候选；修订条目需单独核对。':selection.ready&&!batchIds.length?'请先勾选下方待采纳条目，再点击“批量核对并采纳”。':'点击后先保存所选条目的行为草稿，再对照来源核对并采纳。'}{revisionCount>0&&`其中 ${revisionCount} 条修订候选需单独对照原条款采纳，不参与本次批量操作。`}</p>
      </div>
      {p.items.filter(i=>i.kind==='requirement').map(i=><details className="scope-requirement" id={'scope-requirement-'+i.id} key={i.id}><summary><label onClick={e=>e.stopPropagation()}>{i.selection_status!=='selected'&&<input type="checkbox" aria-label={'本次采纳 '+i.title} disabled={locked||!selection.ready||!selection.eligible.includes(i.id)} checked={batchIds.includes(i.id)} onChange={e=>setBatchSelection(current=>e.target.checked?[...current,i.id]:current.filter(id=>id!==i.id))}/>}<strong>{i.title}</strong></label><span className="scope-membership">{ids.includes(i.id)?'本期范围':'本期以外'}</span><span className={'change-tag '+i.change_type}>{({new:'新增',modified:'修改',preserved:'保持',existing:'现状',unspecified:'无法确定'} as Obj)[i.change_type]||'待核对'}</span><span className="requirement-statement">{i.statement}</span><span className="selection-tag">{({selected:'草稿已采纳',candidate:'候选未采纳',deferred:'暂缓',rejected:'未采纳'} as Obj)[i.selection_status]}</span><span>详情⌄</span></summary><div className="requirement-details"><small className="requirement-id">{i.id} · v{i.content_version||1}</small><p>{i.statement}</p>{i.classification_reason&&<p>分类依据：{i.classification_reason}</p>}{i.scope_evidence?.map((e:Obj,n:number)=><blockquote key={n}>{({current:'现状',change:'变化',preserve:'保持'} as Obj)[e.state]}：{e.quote}</blockquote>)}<p>{({reported:'材料陈述',inferred:'推断，待核实',proposed:'建议，未成为事实'} as Obj)[i.epistemic_status]}</p><BehaviorEditor draftKey={draftKey+'-'+i.id+'-behavior'} item={i} mutate={mutate} busy={locked} revision={p.revision} onSource={onSource} reviewStatus={behaviorReviewStatuses[i.id]}/>{!selection.ready&&<p className="notice">{selection.reason}</p>}{!ids.includes(i.id)&&<p className="muted">本期以外的条目可查看和修改；需采纳时先纳入本期并保存。</p>}{['deferred','rejected'].includes(i.selection_status)&&<p className="muted">如需重新考虑，请先将下方状态改回“候选”，再选择本次采纳。</p>}{behaviorReviewStatuses[i.id]==='required'&&<p className="notice">此条行为尚未核对。<button type="button" disabled={locked} onClick={()=>focusRequirement(i.id,true)}>去核对此条行为</button>也可在列表上方批量核对并采纳勾选项。</p>}<div className="toolbar"><button onClick={()=>onSource?.(i.source_refs||[])}>查看来源</button><button onClick={()=>onHelp?.({kind:'item',id:i.id,label:i.title})}>帮助澄清此项</button><label>独立采纳状态<select disabled={locked} aria-label={'采纳状态 '+i.title} value={i.selection_status} onChange={e=>onItem?.(i,e.target.value)}>{[['candidate','候选'],['selected','采纳此条'],['deferred','暂缓'],['rejected','不采纳']].map(([v,t])=><option key={v} value={v} disabled={v==='selected'&&(!selection.ready||!ids.includes(i.id)||behaviorReviewStatuses[i.id]==='required')}>{t}</option>)}</select></label></div></div></details>)}
      {!p.items.some(i=>i.kind==='requirement')&&<p>尚未形成需求条目；可返回分析或补充候选，不能把空列表当作已完成。</p>}</section>
      <div className="scope-cards">{['current_state','intent','change_scope','preserve_scope','out_of_scope'].map((key,n)=>{
        const status=fieldStatus(key,values,fieldStates,profile),decided=['none','not_applicable'].includes(status);
        const suggested=suggestions.includes(key),saved=!!String(p.product_context?.[key]||'').trim()&&p.product_context?.[key]===values[key]&&!suggested;
        return <section className={'tone-'+n} key={key}><div className="section-heading"><h3><span className="tone-icon"><Icon name={['document','target','list','shield','question'][n]}/></span>{({current_state:'现状摘要',intent:'希望达到的目标',change_scope:'本次改动',preserve_scope:'保持项',out_of_scope:'不在本期范围'} as Obj)[key]}</h3><button aria-label={'编辑'+scope[key]} disabled={locked} onClick={()=>openScopeField(key)}><Icon name="edit"/>编辑</button></div>
          {editing===key?<div className="scope-card-editor">{fieldDecision(key)}<label>{scope[key]}<textarea ref={el=>{scopeInputs.current[key]=el;}} aria-label={scope[key]} disabled={locked||decided} rows={4} value={values[key]||''} onChange={e=>edit(key,e.target.value)}/></label><button disabled={locked} onClick={()=>attempt(()=>save())}>保存此卡</button><button onClick={()=>navigate(discard)}>取消</button></div>:<p>{decided?stateNames[status]:values[key]||'待确认'}</p>}
          {p.product_context?.[key]&&p.product_context_proposal?.[key]&&p.product_context[key]!==p.product_context_proposal[key]&&<details className="scope-proposal"><summary>最新分析提出了不同建议，待核对</summary><p>{p.product_context_proposal[key]}</p><button disabled={locked} onClick={()=>openScopeField(key,p.product_context_proposal![key])}>放入编辑框核对</button></details>}
          <small>{suggested?'AI 建议 · 待审核':decided?((p as Obj).context_field_states?.[key]?.status===status?'已保存的适用状态':'场景或人工选择 · 待保存'):status==='unknown'?'待确认 · 尚未成为核对事实':saved?'已保存的核对内容':values[key]?'人工编辑 · 待保存':isScopeFieldRequired(key,profile)?'进入下一步前需核对':'可选 · 不阻挡继续'}</small>
        </section>;
      })}</div>
      <section className="scope-facts"><span className="tone-icon"><Icon name="question"/></span><div><h3>使用者与补充信息</h3><small>使用者需明确；产品名称、价值与优先级按需补充</small></div><div className="facts-list">{['product','module','users','value','priority'].map(key=>{
        const status=fieldStatus(key,values,fieldStates,profile),decided=['none','not_applicable'].includes(status),suggested=suggestions.includes(key);
        return <p key={key}><span>{status==='known'&&!suggested&&String(values[key]||'').trim()?'✓':'○'}</span>{scope[key]}：{decided?stateNames[status]:values[key]||(isScopeFieldRequired(key,profile)?'待确认':'可选，尚未填写')}{suggested?<small>AI 建议 · 待审核</small>:status==='unknown'&&values[key]?<small>待确认 · 尚未成为核对事实</small>:null}</p>;
      })}</div><button disabled={locked} onClick={()=>openScopeField('users')}>编辑补充信息</button></section>
      {editing==='facts'&&<div className="inline-editor">{['product','module','users','value','priority'].map(key=><div key={key}>{fieldDecision(key)}<label>{scope[key]}<textarea ref={el=>{scopeInputs.current[key]=el;}} aria-label={scope[key]} disabled={locked||['none','not_applicable'].includes(fieldStatus(key,values,fieldStates,profile))} rows={2} value={values[key]||''} onChange={e=>edit(key,e.target.value)}/></label></div>)}<button disabled={locked} onClick={()=>attempt(()=>save())}>保存补充信息</button><button onClick={()=>navigate(discard)}>取消</button></div>}
      <div className="assistant-note"><Icon name="light"/><b>核对提示</b><span>重点检查“不在本期范围”；候选采纳是独立决定。</span><button disabled={locked||!dirty} onClick={()=>attempt(()=>save())}>保存范围草稿</button></div>
    </>}
    {phase===2&&<><div className="toolbar"><button disabled={locked||!p.items.some(i=>i.kind==='requirement'&&i.applies_to==='to_be'&&p.product_context?.scope_ids?.includes(i.id))} onClick={onAcceptance}>＋ 补充关联验收条件</button><small>先保存为候选，核对后再明确采纳。</small></div><details className="behavior-details"><summary>核对业务行为、验收与需求关系</summary><p>行为说明与验收来自当前条目，不会用通用规则补齐空缺。</p>{p.items.filter(i=>i.kind==='requirement'&&p.product_context?.scope_ids?.includes(i.id)).map(i=><BehaviorEditor draftKey={draftKey+'-'+i.id+'-behavior'} key={i.id} item={i} mutate={mutate} busy={locked} revision={p.revision} onSource={onSource} reviewStatus={behaviorReviewStatuses[i.id]}/>)}<details><summary>本期以外的未决事项及范围决定</summary>{p.questions.filter(q=>!q.superseded_by&&q.status!=='answered').map(q=><QuestionScope key={q.id} question={q} mutate={mutate} revision={p.revision}/>)}</details><RequirementRelations p={p} mutate={mutate}/></details></>}
    {phase===3&&<><p>只核对功能、入口、操作、保持项与未决事项，布局细节交由 UI 设计继续完善。</p><div className="sketch-notes">{[['changes','新增或修改'],['preserved','保持不变'],['behavior','行为与待定']].map(([key,label])=><section key={key}><h3>{label}</h3><p>{values[key]||'待核对说明'}</p></section>)}<section><h3>关联需求编号</h3>{p.items.filter(i=>i.kind==='requirement'&&p.product_context?.scope_ids?.includes(i.id)).map(i=><p key={i.id}>{i.id} · v{i.content_version||1}<br/>{i.title}</p>)}</section></div><button onClick={()=>setEditing(editing?null:'sketch')}>{editing?'收起草图说明编辑':'调整草图核对说明'}</button>{editing&&<div className="inline-editor"><label className="check"><input disabled={locked} type="checkbox" checked={values.applicable!==false} onChange={e=>edit('applicable',e.target.checked)}/>本次涉及界面或交互变化</label>{Object.entries(fields).map(([key,label])=><label key={key}>{String(label)}<textarea disabled={locked} rows={2} value={values[key]||''} onChange={e=>edit(key,e.target.value)}/></label>)}<button disabled={locked} onClick={()=>attempt(()=>save())}>保存草图核对说明</button></div>}</>}
    {phase===4&&<><details className="document-pair-summary"><summary>两份文档的版本与核对状态</summary><p>MRD 与 PRD 分别核对各自版本，两份有效后才能进入交接。</p><div className="document-pair">{['mrd','prd'].map(k=><div key={k}><strong>{k.toUpperCase()}</strong><p>{p.documents[k]?`文档 v${p.documents[k].document_version||'未记录'} · 底稿 v${p.documents[k].draft_revision}`:'尚未生成'}</p><p>{(p as Obj).document_review_status?.[k]?.reviewed?'当前版本已核对':'当前版本尚未核对'}</p></div>)}</div></details></>}
    {error&&!conflict&&<div role="alert" className="error"><p>{error} · 输入仍保留。</p></div>}
    <ConflictReview conflict={conflict} onDiscard={discard} onReviewed={()=>{before.current=conflict?.latest as Obj;base.current=p.revision;setConflict(null);setError('');}}/>
    {phase>0&&<>{state&&!state.available&&!(phase===1&&!(p as Obj).intake)&&<p className="notice">可提前查看，继续前请先完成前序核对。<button onClick={()=>go(phase===4?2:Math.max(0,phase-1))}>返回前一步</button></p>}{!!state?.missing.length&&<p className="blocker-shortcut">还有 {state.missing.length} 项需处理。<button type="button" disabled={locked} onClick={()=>focusBlockerElement('blockers-'+p.id+'-'+phase)}>查看阻塞清单与处理入口 ↑</button></p>}
      <div className="step-actions"><button className="back-step" disabled={locked} onClick={()=>go(phase===4?2:phase-1)}>← {phase===1?'返回修改输入':phase===2?'返回范围核对':'返回规则澄清'}</button><span>{pendingBatch?`本次勾选的 ${batchIds.length} 条尚未采纳。请先批量核对，或清空本次选择。`:phase===1?'先保存范围并采纳本期需求，再确认进入下一步。':'保存与核对均不调用模型'}{pendingBatch&&<button type="button" onClick={()=>focusBlockerElement('scope-requirements-'+p.id)}>去批量核对 ↑</button>}</span><button id={"checkpoint-continue-"+p.id+"-"+phase} className="primary" disabled={locked||pendingBatch||Boolean(state?.missing.length&&!dirty)} onClick={()=>attempt(check)}>{checking?'正在保存、核对并进入下一步…':state?.missing.length&&!dirty?`先处理 ${state.missing.length} 项待办`:phase===1?'确认并进入下一步 →':(['','','核对规则并继续','确认草图并继续','完成文档评审并进入交接'][phase]+' →')}</button></div>
    </>}

  </article>;
}

export function QuestionList({p:fullProject,scopeOnly=false,active=true,mutate,busy,onHelp,focusQuestionId,focusQuestionNonce,onReviewItem,focusAnswerId,focusAnswerNonce}:{p:Project;scopeOnly?:boolean;active?:boolean;mutate:Props['mutate'];busy:boolean;onHelp:(target:Obj)=>void;focusQuestionId?:string;focusQuestionNonce?:number;focusAnswerId?:string;focusAnswerNonce?:number;onReviewItem?:(item:Obj,behavior?:boolean,questionId?:string,afterNavigate?:()=>void)=>void}){
 const p=scopeOnly?{...fullProject,questions:fullProject.questions.filter(isScopeQuestion)}:fullProject;
 const questionPrefix=scopeOnly?'scope-':'';
 const questionDraftKey=p.id+(scopeOnly?'-scope':'');
 const [answers,setAnswers]=useState<Obj>({}),[notes,setNotes]=useState<Obj>({}),[error,setError]=useState(''),[saving,setSaving]=useState(false),[conflict,setConflict]=useState<DraftConflictError|null>(null),[noteConflict,setNoteConflict]=useState<{id:string;error:DraftConflictError}|null>(null);
 const base=useRef(p.revision),before=useRef<Obj>({}),savingRef=useRef(false),noteBaselines=useRef<Record<string,{revision:number;before:string}>>({});
 const dirty=Object.keys(answers).length>0;
 useEffect(()=>{if(!active||!focusQuestionId)return;const timer=setTimeout(()=>{const el=document.getElementById('question-'+questionPrefix+focusQuestionId);const answered=el?.closest('details.answered-editor');if(answered instanceof HTMLDetailsElement)answered.open=true;el?.scrollIntoView({block:'center'});el?.querySelector('textarea')?.focus({preventScroll:true});},80);return()=>clearTimeout(timer);},[active,focusQuestionId,focusQuestionNonce]);
 async function save(){const values=Object.fromEntries(Object.entries(answers).filter(([,v])=>String(v).trim()));if(Object.keys(values).length!==Object.keys(answers).length)throw Error('空白回答不会被视为已回答，请填写或选择保留未知。');const old=Object.fromEntries(Object.keys(values).map(id=>[id,before.current[id]||'']));try{await mutate('/answers',{answers:values,expected_revision:base.current,__edit:{kind:'answers',before:old,mine:values}});setAnswers({});setNotes(current=>Object.fromEntries(Object.entries(current).filter(([id])=>!(id in values))));for(const id of Object.keys(values))delete noteBaselines.current[id];setConflict(null);}catch(e){if(e instanceof DraftConflictError)setConflict(e);throw e;}}
 const discard=()=>{setAnswers({});setConflict(null);setError('');before.current={};base.current=p.revision;};
 useDirty(questionDraftKey+'-answers',dirty,'问题回答',save,discard);
 useDirty(questionDraftKey+'-question-notes',Object.keys(notes).length>0,'未知备注',async()=>{for(const id of Object.keys(notes)){if(id in answers)continue;const q=p.questions.find(q=>q.id===id);if(q)await defer(q,true,notes[id]);}},()=>{setNotes({});noteBaselines.current={};setNoteConflict(null);});
 function edit(id:string,value:string){if(!dirty){base.current=p.revision;before.current={};}if(!(id in before.current))before.current[id]=p.questions.find(q=>q.id===id)?.answer||'';setAnswers(v=>({...v,[id]:value}));}
 function editNote(q:Obj,value:string){if(!noteBaselines.current[q.id])noteBaselines.current[q.id]={revision:p.revision,before:q.deferred_note||''};setNotes(v=>({...v,[q.id]:value}));}
 function markUnknown(q:Obj){
   const draft=answers[q.id]??notes[q.id]??q.deferred_note??'';
   if(!noteBaselines.current[q.id])noteBaselines.current[q.id]={revision:p.revision,before:q.deferred_note||''};
   setNotes(current=>({...current,[q.id]:draft}));
   setAnswers(current=>{const next={...current};delete next[q.id];return next;});
   void defer(q,false,draft).catch(e=>setError((e as Error).message));
 }
 async function defer(q:Obj,rethrow=false,draftOverride?:string){
   if(savingRef.current)throw Error('当前保存仍在进行，请等待结果。');
   const draft=draftOverride??notes[q.id]??q.deferred_note??'';
   const baseline=noteBaselines.current[q.id]||{revision:p.revision,before:q.deferred_note||''};
   savingRef.current=true;setSaving(true);setError('');
   try{
     await mutate('/questions/'+q.id+'/defer',{note:draft,expected_revision:baseline.revision,__edit:{kind:'question_note',id:q.id,before:baseline.before,mine:draft}});
     setNotes(current=>{const next={...current};if(next[q.id]===draft)delete next[q.id];return next;});
     delete noteBaselines.current[q.id];setNoteConflict(null);
   }catch(e){setNotes(current=>({...current,[q.id]:draft}));if(e instanceof DraftConflictError)setNoteConflict({id:q.id,error:e});else setError((e as Error).message+'；备注仍保留，请核对后再保存。');if(rethrow)throw e;}
   finally{savingRef.current=false;setSaving(false);}
 }
 function card(q:Obj){
   const note=notes[q.id]??q.deferred_note??'';
   const hasNote=Boolean(q.deferred_at||q.deferred_note);
   const answer=answers[q.id]??q.answer??'';
   return <article className="question-item" id={'question-'+questionPrefix+q.id} key={q.id}>
     <div className="question-description"><div className="section-heading"><h3>{q.question}</h3><span className="pill">{answers[q.id]!==undefined?'回答未保存':q.status==='answered'?'已保存回答':hasNote?'未知备注已保存 · 仍待决定':q.blocking?'待回答 · 阻塞':'待决定'}</span></div><p className="muted">{q.why}</p>{q.status==='answered'&&<p>{q.answer_effect}</p>}<details className="question-refs"><summary>查看关联编号</summary><small>{q.id} · {q.affected_requirements?.map((r:Obj)=>r.id).join('、')||q.related_refs?.join('、')||'尚未关联'}</small></details></div>
     <div className="question-answer"><label>你的回答<textarea rows={2} aria-label={q.question} disabled={saving||busy} value={answer} onChange={e=>edit(q.id,e.target.value)}/></label>{q.status!=='answered'&&(hasNote||notes[q.id]!==undefined)&&<label>未知备注（仍需核实）<textarea rows={2} aria-label={'未知备注 '+q.question} disabled={saving||busy} value={note} onChange={e=>editNote(q,e.target.value)}/></label>}</div>
     <div className="question-suggestion">{scopeOnly&&q.status!=='answered'&&<QuestionScope question={q} mutate={mutate} revision={p.revision} busy={busy||saving} draftPrefix="scope-page-"/>}<b>✦ 局部建议（模型提出，尚未决定）</b>{q.options?.length>0&&<div className="choice-list">{q.options.map((x:string)=><button disabled={saving||busy} key={x} onClick={()=>edit(q.id,x)}>填入建议：{x}</button>)}</div>}<div className="toolbar">{q.status!=='answered'&&<button disabled={saving||busy} onClick={()=>markUnknown(q)}>{saving?'正在保存…':hasNote?'保存未知备注':'暂不确定，保存未知备注'}</button>}<button disabled={saving||busy} onClick={()=>onHelp({kind:'question',id:q.id,label:q.question})}>帮我分析这个问题</button></div>{q.status!=='answered'&&hasNote&&<p className="notice">备注已保存；仍未解决，不会解除阻塞。若已决定，请填写回答并保存。</p>}</div>
   </article>;
 }
 const unanswered=p.questions.filter(q=>!q.superseded_by&&q.status!=='answered'&&!q.out_of_scope_reason);
 return <article className="card questions-panel"><div className="section-heading"><h2>{scopeOnly?'先明确影响本期范围的问题':'待澄清的问题（按需求分组）'}</h2><small>{unanswered.length} 个待答问题 · 建议不是答案</small></div>{[...p.items.filter(i=>i.kind==='requirement').map(i=>({id:i.id,label:i.title})),{id:'unbound',label:'尚未关联具体需求'}].map(group=>{const questions=unanswered.filter(q=>{const actual=p.items.find(i=>i.kind==='requirement'&&((q.related_refs||[]).includes(i.id)||(q.affected_requirements||[]).some((r:Obj)=>r.id===i.id)));return (actual?.id||'unbound')===group.id;});return questions.length?<section className="question-group" key={group.id}><h3>{group.label}<small>{questions.length} 项待决定{group.id!=='unbound'&&<span title={group.id}> · {group.id.slice(0,12)}{group.id.length>12?'…':''}</span>}</small></h3>{questions.map(card)}</section>:null;})}{!p.questions.length&&<p>尚无澄清问题。可先核对资料理解与本期范围。</p>}<details><summary>已拆分的原问题（保留历史）</summary>{p.questions.filter(q=>q.superseded_by).map(q=><p key={q.id}>{q.question}<br/>分别回答：{q.superseded_by.join('、')}</p>)}</details><details className="answered-editor"><summary>已回答 · {p.questions.filter(q=>q.status==='answered').length}（可修改）</summary>{p.questions.filter(q=>q.status==='answered').map(card)}</details>{error&&!conflict&&!noteConflict&&<p role="alert" className="error">{error}</p>}<ConflictReview conflict={conflict} onDiscard={discard} onReviewed={()=>{before.current=conflict?.latest as Obj;base.current=p.revision;setConflict(null);setError('');}}/>{noteConflict&&<ConflictReview conflict={noteConflict.error} onDiscard={()=>{setNotes(current=>{const next={...current};delete next[noteConflict.id];return next;});delete noteBaselines.current[noteConflict.id];setNoteConflict(null);setError('');}} onReviewed={()=>{noteBaselines.current[noteConflict.id]={revision:p.revision,before:String(noteConflict.error.latest||'')};setNoteConflict(null);setError('');}}/>}<button className="primary" disabled={!dirty||busy||saving} onClick={async()=>{if(savingRef.current)return;savingRef.current=true;setSaving(true);setError('');try{await save();}catch(e){setError((e as Error).message);}finally{savingRef.current=false;setSaving(false);}}}>{saving?'正在保存回答…':'保存本次回答'}</button>{scopeOnly&&<><p className="muted">先保存这些范围问题的回答，再核对下方需求。回答与条款是否一致，在后续规则澄清中继续核对。</p><details><summary>已明确移出本期的问题（可恢复）</summary>{p.questions.filter(q=>!q.superseded_by&&q.status!=='answered'&&q.out_of_scope_reason).map(q=><QuestionScope key={q.id} question={q} mutate={mutate} revision={p.revision} busy={busy||saving} draftPrefix="scope-page-"/>)}</details></>}{!scopeOnly&&<div className="question-results"><section><h3><Icon name="check"/>已保存回答 <small>{p.questions.filter(q=>q.status==='answered').length} 项</small></h3>{p.questions.filter(q=>q.status==='answered'&&!q.superseded_by).map(q=><section id={'answer-result-'+q.id} key={q.id}><p><b>{q.question}</b><br/>{q.answer}</p><ManualAnswerApplication p={p} q={q} mutate={mutate} busy={busy||dirty||saving} onReviewItem={onReviewItem} focusReviewNonce={focusAnswerId===q.id?focusAnswerNonce:0}/></section>)}{!p.questions.some(q=>q.status==='answered')&&<p>尚无已保存回答。未保存输入和建议不会列入此处。</p>}</section><section><h3><Icon name="question"/>仍待决定 <small>{p.questions.filter(q=>!q.superseded_by&&q.status!=='answered').length} 项</small></h3>{p.questions.filter(q=>!q.superseded_by&&q.status!=='answered').map(q=><p key={q.id}>{q.question}<small>{q.out_of_scope_reason?'本期以外，仍未知':q.blocking?'本期阻塞项':'待决定'}</small></p>)}</section></div>}</article>;
}

function RequirementRelations({p,mutate}:{p:Project;mutate:Props['mutate']}){
  const [source,setSource]=useState(''),[target,setTarget]=useState(''),[relation,setRelation]=useState('refines'),[error,setError]=useState(''),[saving,setSaving]=useState(false),[saved,setSaved]=useState(false);const savingRef=useRef(false);
  const reqs=p.items.filter(i=>i.kind==='requirement');
  async function submit(){if(savingRef.current)return;savingRef.current=true;setSaving(true);setSaved(false);setError('');try{await mutate('/requirement-relations',{source_id:source,target_id:target,relation});setSaved(true);}catch(e){setError((e as Error).message);}finally{savingRef.current=false;setSaving(false);}}
  return <details><summary>需求的父子、细化与依赖关系</summary><p>拆分或取代时保留原编号，各子需求有自己的编号。关系不代表实现或测试通过。</p>{(p.requirement_relations||[]).map((r:Obj,n:number)=><p key={n}>{r.source.id} v{r.source.version} → {r.type} → {r.target.id} v{r.target.version}</p>)}{reqs.length>1&&<><label>起始需求<select disabled={saving} value={source} onChange={e=>{setSource(e.target.value);setSaved(false);}}><option value="">选择需求</option>{reqs.map(i=><option key={i.id} value={i.id}>{i.id}｜{i.title}</option>)}</select></label><label>关系<select disabled={saving} value={relation} onChange={e=>{setRelation(e.target.value);setSaved(false);}}>{[['parent_of','是其父需求'],['refines','细化'],['replaces','取代'],['depends_on','依赖']].map(([k,n])=><option key={k} value={k}>{n}</option>)}</select></label><label>目标需求<select disabled={saving} value={target} onChange={e=>{setTarget(e.target.value);setSaved(false);}}><option value="">选择需求</option>{reqs.map(i=><option key={i.id} value={i.id}>{i.id}｜{i.title}</option>)}</select></label><button disabled={saving||!source||!target||source===target} onClick={submit}>{saving?'正在记录关系…':'记录关系'}</button></>}{saved&&<p role="status" className="notice">需求关系已记录。</p>}{error&&<p role="alert" className="error">{error}</p>}<p><DownloadButton href={'/api/projects/'+p.id+'/requirements-exchange'} filename={p.name+'-需求交换.json'}>导出结构化需求与关系</DownloadButton>（当前草稿；正式交接使用确认基线）</p></details>;
}

export function BehaviorEditor({item,mutate,busy,revision,onSource,reviewStatus='legacy',draftKey}:{item:Obj;mutate:Props['mutate'];busy:boolean;revision:number;onSource?:Props['onSource'];reviewStatus?:string;draftKey:string}){
  const [values,setValues]=useState<Obj>(item.behavior||{}),[kind,setKind]=useState(item.change_type||'unspecified'),[dirty,setDirty]=useState(false),[confirmed,setConfirmed]=useState(false),[error,setError]=useState(''),[conflict,setConflict]=useState<DraftConflictError|null>(null),[saving,setSaving]=useState(false);
  const savingRef=useRef(false);
  const reviewRequired=reviewStatus==='required'||(reviewStatus==='reviewed'&&dirty);
  useEffect(()=>{if(!dirty){setValues(item.behavior||{});setKind(item.change_type||'unspecified');setConfirmed(false);}},[item,dirty]);
  const base=useRef(revision),before=useRef<Obj>({values:item.behavior||{},change_type:item.change_type||'unspecified'});
  useEffect(()=>{if(!dirty){base.current=revision;before.current={values:item.behavior||{},change_type:item.change_type||'unspecified'};}},[revision,item,dirty]);
  const save=async(reviewConfirmed=false)=>{
    if(savingRef.current)return;
    if(reviewConfirmed&&!confirmed)throw new Error('请先逐项核对业务行为及来源，再确认没有无依据的新决策。');
    savingRef.current=true;setSaving(true);setError('');
    try{
      await mutate('/items/'+item.id+'/behavior',{values,change_type:kind,review_confirmed:reviewConfirmed,expected_revision:base.current,__edit:{kind:'behavior',id:item.id,before:before.current,mine:{values,change_type:kind}}});
      setDirty(false);setConfirmed(false);setError('');setConflict(null);
    }catch(e){if(e instanceof DraftConflictError)setConflict(e);throw e;}
    finally{savingRef.current=false;setSaving(false);}
  };
  const discard=()=>{setValues(item.behavior||{});setKind(item.change_type||'unspecified');setDirty(false);setConfirmed(false);setConflict(null);setError('');base.current=revision;};
  useDirty(draftKey,dirty,'需求行为 '+item.title,save,discard);
  return <details id={'behavior-review-'+draftKey}><summary>{item.id}｜{item.title} · v{item.content_version||1}{reviewStatus==='required'?' · 行为待核对':''}{dirty?' · 未保存':''}</summary>
    <p>{item.statement}</p>
    {reviewStatus==='required'&&<p className="notice">模型整理的行为尚未由人核对。请逐项对照来源；无依据的写入时点、粒度等决策可留空待定，并在澄清中处理。</p>}
    <p><button type="button" disabled={!onSource||!item.source_refs?.length} onClick={()=>onSource?.(item.source_refs||[])}>查看来源原文</button>{!item.source_refs?.length&&<small>此候选未提供来源引用，请核对后再采纳。</small>}</p>
    <label>本次性质<select disabled={busy||saving} value={kind} onChange={e=>{setKind(e.target.value);setDirty(true);setConfirmed(false);}}>{[['unspecified','无法确定'],['new','新增'],['modified','修改'],['preserved','保持约束'],['existing','现状']].map(([v,t])=><option key={v} value={v}>{t}</option>)}</select></label>
    {Object.entries(behavior).map(([key,label])=><label key={key}>{String(label)}<textarea data-behavior-field={key} aria-label={String(label)+' · '+item.title} disabled={busy||saving} value={values[key]||''} onChange={e=>{setValues(v=>({...v,[key]:e.target.value}));setDirty(true);setConfirmed(false);}}/></label>)}
    {reviewRequired&&<label className="check"><input type="checkbox" disabled={busy||saving} checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已对照来源核对七个行为字段，确认没有把无依据的新决策写成已定规则。</label>}
    {error&&!conflict&&<p role="alert">{error}</p>}
    <ConflictReview conflict={conflict} onDiscard={discard} onReviewed={()=>{before.current=conflict?.latest as Obj;base.current=revision;setConfirmed(false);setConflict(null);setError('');}}/>
    {reviewRequired&&<button disabled={busy||saving||!dirty} onClick={async()=>{try{await save();}catch(e){setError((e as Error).message);}}}>保存行为草稿，稍后核对</button>}
    <button disabled={busy||saving||(!dirty&&reviewStatus!=='required')||(reviewRequired&&!confirmed)} onClick={async()=>{try{await save(reviewRequired&&confirmed);}catch(e){setError((e as Error).message);}}}>{saving?'正在保存需求行为…':reviewRequired?'核对并保存此需求行为':'保存此需求行为'}</button>
  </details>;
}
function QuestionScope({question:q,mutate,revision,busy=false,draftPrefix=""}:{question:Obj;mutate:Props['mutate'];revision:number;busy?:boolean;draftPrefix?:string}){
  const [reason,setReason]=useState(q.out_of_scope_reason||''),[error,setError]=useState(''),[saving,setSaving]=useState(false),[saved,setSaved]=useState(false);
  const [conflict,setConflict]=useState<DraftConflictError|null>(null),base=useRef(revision),before=useRef(q.out_of_scope_reason||''),savingRef=useRef(false);
  const dirty=reason!==(q.out_of_scope_reason||'');
  useEffect(()=>{if(!dirty&&!saving){base.current=revision;before.current=q.out_of_scope_reason||'';setReason(q.out_of_scope_reason||'');}},[q,revision,dirty,saving]);
  const discard=()=>{setReason(q.out_of_scope_reason||'');setConflict(null);setError('');setSaved(false);base.current=revision;before.current=q.out_of_scope_reason||'';};
  async function save(){if(savingRef.current)return;savingRef.current=true;setSaving(true);setSaved(false);setError('');try{await mutate('/questions/'+q.id+'/scope',{out_of_scope_reason:reason,__edit:{kind:'scope',id:q.id,before:before.current,mine:reason},expected_revision:base.current});setConflict(null);before.current=reason;setSaved(true);}catch(e){if(e instanceof DraftConflictError)setConflict(e);setError((e as Error).message);throw e;}finally{savingRef.current=false;setSaving(false);}}
  useDirty(draftPrefix+q.id+'-scope',dirty,'问题范围决定',save,discard);
  return <details><summary>本期范围决定：{q.question}{q.out_of_scope_reason?' · 已移出本期，仍未知':''}{dirty?' · 未保存':''}</summary><p>影响需求：{q.affected_requirements?.map((r:Obj)=>r.id).join('、')||q.related_refs?.join('、')||'尚待关联范围'}</p><p>{q.why} · 影响第{q.blocking_stage||3}步</p><label>明确移出本期的理由（清空可恢复）<textarea disabled={saving||busy} value={reason} onChange={e=>{setReason(e.target.value);setSaved(false);}}/></label><p>不改变原问题、答案或条目采纳状态；本期仍依赖的规则不得移出。</p><button disabled={saving||busy||!dirty} onClick={()=>{void save().catch(()=>{});}}>{saving?'正在保存范围决定…':'保存范围决定'}</button>{saved&&<p role="status" className="notice">范围决定已保存。</p>}{error&&!conflict&&<p role="alert" className="error">{error}</p>}<ConflictReview conflict={conflict} onDiscard={discard} onReviewed={()=>{before.current=String(conflict?.latest||'');base.current=revision;setConflict(null);setError('');}}/></details>;
}
