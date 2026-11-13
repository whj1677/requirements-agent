import React, {useEffect,useState} from 'react';
import type {DetailUnknownScopeReviewProperty} from '../api';
import {detailEditingIssues,detailFieldRequired,detailInteractionRequired,detailPropertyLabels,detailRows,detailSectionLabels,detailTransitionRequired,emptyDetailContract,mergeDetailContract,resolveDetailConflict,validTechnicalScopeProperty,type DetailConflict,type DetailContract,type DetailIssue,type DetailObject,type DetailSection} from './detail-contract';
import './detail-contract.css';

type Props={confirmationLabel?:string;requirementId:string;title:string;value?:DetailContract;base?:DetailContract;disabled?:boolean;conflicts?:DetailConflict[];issues?:DetailIssue[];technicalScopeProperties?:readonly DetailUnknownScopeReviewProperty[];candidate?:{contract:DetailContract;candidate_id:string};references?:{id:string;title:string;kind:string}[];onChange:(value:DetailContract,conflicts?:DetailConflict[])=>void;onSources?:(refs:Record<string,any>[])=>void};
const dataTypes={text:'文本',string:'字符串',id:'标识',number:'数值',integer:'整数',boolean:'是／否',enum:'枚举',date:'日期',datetime:'日期时间',object:'复合信息',array:'列表'};
const nodeKinds={start:'开始',action:'处理步骤',decision:'条件判断',wait:'等待',end:'结束'};
const arrayKeys=['enum_values','field_refs','rule_refs','acceptance_refs'];
const prefixes:Record<string,string>={fields:'FD',interactions:'IT','flow.nodes':'ND','flow.edges':'ED',state_transitions:'ST'};
const names:Record<string,string>={fields:'字段',interactions:'操作', 'flow.nodes':'步骤','flow.edges':'连线',state_transitions:'状态变化'};
const rawDisplay=(value:any)=>value===undefined?'已移除':JSON.stringify(value,null,2);
type WholeReplacement={requirementId:string;candidateId:string;candidateSnapshot:DetailContract;adopted:DetailContract;previous:DetailContract;previousConflicts:DetailConflict[]};

function newObject(section:string):DetailObject{
  const fields=section==='fields'?detailFieldRequired:section==='interactions'?detailInteractionRequired:section==='state_transitions'?detailTransitionRequired:section==='flow.nodes'?['label','kind','actor']:['from','to','condition'];
  return {id:prefixes[section]+'-'+crypto.randomUUID().slice(0,8),...Object.fromEntries(fields.map(key=>[key,''])),...(section==='flow.nodes'?{kind:'action'}:{})};
}

export function DetailContractEditor({confirmationLabel='确认并进入交付',requirementId,title,value,base,disabled=false,conflicts=[],issues=[],technicalScopeProperties=[],candidate,references=[],onChange:notifyChange,onSources}:Props){
  const [expandedObjects,setExpandedObjects]=useState<Record<string,boolean>>({});
  const [expandedSections,setExpandedSections]=useState<Record<string,boolean>>({});
  const [wholeReplacement,setWholeReplacement]=useState<WholeReplacement|null>(null);
  useEffect(()=>{setWholeReplacement(null);},[requirementId,candidate?.candidate_id,candidate?.contract]);
  const onChange=(next:DetailContract,nextConflicts?:DetailConflict[])=>{setWholeReplacement(null);notifyChange(next,nextConflicts);};
  const cell=(path:string)=>'requirement-'+requirementId+'-'+path;
  const conflictObject=(collection:string,id:string)=>{
    for(const contract of [value,base,candidate?.contract]){
      const row=detailRows(contract,collection).find(item=>item.id===id);
      if(row)return row;
    }
  };
  const conflictLocation=(path:string)=>{
    const parts=path.split('.'),collection=parts[1]==='flow'?'flow.'+parts[2]:parts[1],idIndex=parts[1]==='flow'?3:2,id=parts[idIndex],key=parts[idIndex+1];
    if(!names[collection])return parts.slice(1).map(part=>detailPropertyLabels[part]||part).join(' / ');
    const section=(collection.startsWith('flow.')?'flow':collection) as DetailSection,row=conflictObject(collection,id),label=row?.name||row?.label;
    return detailSectionLabels[section]+' / '+(row?names[collection]+(label?'“'+label+'”':''):'未找到的引用')+(key?' / '+(detailPropertyLabels[key]||key):'');
  };
  const referenceText=(id:string,key:string)=>{
    const collection=key==='field_refs'||key==='parent_id'?'fields':key==='interaction_id'?'interactions':['from','to'].includes(key)?'flow.nodes':undefined;
    if(collection){
      const row=conflictObject(collection,id);
      return row?(row.name||row.label||'未命名'+names[collection]):'未找到的引用';
    }
    const kind=key==='rule_refs'?'rule':key==='acceptance_refs'?'acceptance':'requirement',reference=references.find(item=>item.id===id&&item.kind===kind);
    return reference?.title||'未找到的引用';
  };
  const conflictValue=(input:any,key:string):React.ReactNode=>{
    if(input===undefined)return '已移除';
    if(input===null||input==='')return '未填写';
    if(Array.isArray(input))return input.length?<ul>{input.map((item,index)=><li key={index}>{conflictValue(item,key)}</li>)}</ul>:'无';
    if(typeof input==='boolean')return input?'是':'否';
    if(typeof input==='object')return input.name||input.label||'复合内容（见技术详情）';
    if(key==='data_type')return dataTypes[input as keyof typeof dataTypes]||String(input);
    if(key==='kind')return nodeKinds[input as keyof typeof nodeKinds]||String(input);
    if(key.endsWith('_refs')||['from','to','parent_id','interaction_id','depends_on'].includes(key))return referenceText(String(input),key);
    return String(input);
  };
  const allIssues=[...issues,...detailEditingIssues(value,technicalScopeProperties)].filter((issue,index,rows)=>rows.findIndex(row=>row.path===issue.path&&row.message===issue.message)===index);
  const error=(path:string)=><>{allIssues.filter(issue=>issue.path===path).map((issue,index)=><p className="sheet-error" role="status" key={index}>{issue.message}</p>)}</>;
  if(!value)return <div className="detail-contract-empty">
    <p>用字段、交互和流程表明确研发所需的业务规则。启用后随本条需求一起确认；空定义不会算作完成。</p>
    <button type="button" disabled={disabled} onClick={()=>onChange(emptyDetailContract())}>启用详细定义</button>
    {candidate&&<button type="button" disabled={disabled} onClick={()=>onChange(structuredClone(candidate.contract))}>使用 AI 详细定义草稿</button>}
  </div>;
  const candidateDiffers=!!candidate&&JSON.stringify(value)!==JSON.stringify(candidate.contract);
  const canAdoptWhole=!!candidate&&(candidateDiffers||conflicts.length>0);
  const canUndo=!!wholeReplacement&&!!candidate&&wholeReplacement.requirementId===requirementId&&
    candidate.candidate_id===wholeReplacement.candidateId&&JSON.stringify(candidate.contract)===JSON.stringify(wholeReplacement.candidateSnapshot)&&
    JSON.stringify(value)===JSON.stringify(wholeReplacement.adopted)&&conflicts.length===0;
  const adoptWhole=()=>{
    if(disabled||!candidate||!canAdoptWhole)return;
    const next=structuredClone(candidate.contract);
    setWholeReplacement({requirementId,candidateId:candidate.candidate_id,
      candidateSnapshot:structuredClone(candidate.contract),adopted:structuredClone(next),previous:structuredClone(value),previousConflicts:structuredClone(conflicts)});
    notifyChange(next,[]);
  };
  const undoWhole=()=>{
    if(disabled||!canUndo||!wholeReplacement)return;
    const previous=structuredClone(wholeReplacement.previous);
    const previousConflicts=structuredClone(wholeReplacement.previousConflicts);
    setWholeReplacement(null);
    notifyChange(previous,previousConflicts);
  };
  const updateSection=(section:string,rows:DetailObject[])=>onChange(section.startsWith('flow.')?{...value,flow:{...value.flow,[section.split('.')[1]]:rows}}:{...value,[section]:rows});
  const changeObject=(section:string,id:string,key:string,next:any)=>updateSection(section,detailRows(value,section).map(row=>row.id!==id?row:{...row,[key]:next,evidence:{...row.evidence,[key]:{status:'proposal',reason:'人工修改，待整条确认'}}}));
  const renderProperty=(section:string,row:DetailObject,key:string,required:boolean)=>{
    const path='detail_contract.'+section+'.'+row.id+'.'+key,label=detailPropertyLabels[key]||key,aria=title+' '+(row.name||row.label||row.id)+' '+label,evidence=row.evidence?.[key];
    const technicalScope=technicalScopeProperties.find(property=>property.path===path&&validTechnicalScopeProperty(value,property));
    const update=(next:any)=>changeObject(section,row.id,key,next);
    const choices=key==='data_type'?dataTypes:key==='kind'?nodeKinds:null;
    const options=key==='field_refs'?detailRows(value,'fields').map(field=>({id:field.id,title:field.name||field.id})):references.filter(item=>key==='rule_refs'?item.kind==='rule':item.kind==='acceptance');
    let input:React.ReactNode;
    if(choices)input=<select aria-label={aria} disabled={disabled} value={row[key]||''} onChange={e=>update(e.target.value)}><option value="">请选择</option>{Object.entries(choices).map(([v,l])=><option key={v} value={v}>{l}</option>)}{row[key]&&!Object.hasOwn(choices,row[key])&&<option value={row[key]}>{row[key]}（待核对类型）</option>}</select>;
    else if(['from','to'].includes(key))input=<select aria-label={aria} disabled={disabled} value={row[key]||''} onChange={e=>update(e.target.value)}><option value="">请选择步骤</option>{detailRows(value,'flow.nodes').map(node=><option value={node.id} key={node.id}>{node.label||'未命名步骤'} · {nodeKinds[node.kind as keyof typeof nodeKinds]||node.kind}</option>)}{row[key]&&!detailRows(value,'flow.nodes').some(node=>node.id===row[key])&&<option value={row[key]}>关联步骤已移除（{row[key]}）</option>}</select>;
    else if(['parent_id','interaction_id'].includes(key)){const refs=detailRows(value,key==='parent_id'?'fields':'interactions').filter(target=>target.id!==row.id);input=<select aria-label={aria} disabled={disabled} value={row[key]||''} onChange={e=>update(e.target.value)}><option value="">不关联</option>{refs.map(target=><option key={target.id} value={target.id}>{target.name||target.id}</option>)}{row[key]&&!refs.some(target=>target.id===row[key])&&<option value={row[key]}>关联已失效（{row[key]}）</option>}</select>;}
    else if(key.endsWith('_refs'))input=<div className="detail-reference-options" aria-label={aria}>{options.map(option=><label key={option.id}><input type="checkbox" disabled={disabled} checked={(row[key]||[]).includes(option.id)} onChange={e=>update(e.target.checked?[...(row[key]||[]),option.id]:(row[key]||[]).filter((id:string)=>id!==option.id))}/>{option.title}</label>)}{!(options.length||row[key]?.length)&&<span>暂无可关联内容</span>}{(Array.isArray(row[key])?row[key]:[]).filter((id:string)=>!options.some(option=>option.id===id)).map((id:string)=><label key={id}><input type="checkbox" disabled={disabled} checked onChange={()=>update(row[key].filter((ref:string)=>ref!==id))}/>已失效的关联：{id}</label>)}</div>;
    else if(key==='source')input=<><label className="detail-calculated"><input type="checkbox" disabled={disabled} checked={row.source==='calculated'} onChange={e=>update(e.target.checked?'calculated':'')}/>由计算得到</label>{row.source!=='calculated'&&<textarea rows={2} aria-label={aria} disabled={disabled} value={row.source||''} placeholder="例如 EMS 上报、用户填写或已有业务记录" onChange={e=>update(e.target.value)}/>}</>;
    else input=<textarea rows={key==='enum_values'?3:2} aria-label={aria} disabled={disabled} value={arrayKeys.includes(key)?(Array.isArray(row[key])?row[key].join('\n'):''):row[key]||''} placeholder={key==='enum_values'?'每行一个可选值':required?'请明确说明；不适用时写明原因':'选填'} onChange={e=>update(arrayKeys.includes(key)?e.target.value.split('\n'):e.target.value)}/>;
    return <div className={'detail-property'+(allIssues.some(issue=>issue.path===path)?' detail-property-invalid':'')} key={key} data-cell-key={cell(path)}>
      <div className="detail-property-control"><span>{label}{required&&<small>{technicalScope?'技术事项保留':'需明确'}</small>}</span>{input}</div>
      <div className={'detail-evidence detail-evidence-'+(evidence?.status||'proposal')}><span>{evidence?.status==='source'?'资料依据 · 随整条核对':evidence?.status==='unknown'?'待明确':evidence?.reason?.startsWith('人工')?'人工修改 · 待确认':'建议 · 待确认'}</span>{!!evidence?.source_refs?.length&&onSources&&<button type="button" onClick={()=>onSources(evidence.source_refs!)}>查看依据</button>}{evidence?.reason&&evidence.reason!=='人工修改，待整条确认'&&<small>{evidence.reason}</small>}</div>{error(path)}
      {technicalScope&&<div className="detail-technical-scope">
        <strong>技术未定，业务确认保留</strong>
        <span>已知业务：{technicalScope.known_business}</span>
        <span>技术未定：{technicalScope.unknown_technical}</span>
        {!!technicalScope.explanation.trim()&&<small>{technicalScope.explanation}</small>}
      </div>}
    </div>;
  };
  const renderCollection=(section:string)=>{
    const rows=detailRows(value,section),path='detail_contract.'+section;
    return <div className="detail-collection" data-cell-key={cell(path)}>{error(path)}{rows.map(row=>{
      let required=section==='fields'?[...detailFieldRequired,...(['number','integer'].includes(row.data_type)?['unit','precision']:[]),...(['date','datetime'].includes(row.data_type)?['time_basis']:[]),...(row.source==='calculated'?['formula']:[]),...(row.data_type==='enum'?['enum_values']:[])]:section==='interactions'?detailInteractionRequired:section==='state_transitions'?detailTransitionRequired:section==='flow.nodes'?['label','kind',...(['start','end'].includes(row.kind)?[]:['actor'])]:['from','to',...(['decision','wait'].includes(detailRows(value,'flow.nodes').find(node=>node.id===row.from)?.kind)?['condition']:[])];
      const extra=[...new Set([...(section==='fields'?['permissions','empty_value','uniqueness','parent_id']:section==='flow.nodes'?['interaction_id']:section==='flow.edges'&&!required.includes('condition')?['condition']:[]),'field_refs','rule_refs','acceptance_refs',...Object.keys(row)])].filter(key=>!['id','evidence',...required].includes(key));
      const ownIssues=allIssues.filter(issue=>issue.path.startsWith(path+'.'+row.id+'.'));
      return <details className="detail-object" key={row.id} open={!!expandedObjects[row.id]} onToggle={e=>{const open=e.currentTarget.open;setExpandedObjects(previous=>previous[row.id]===open?previous:{...previous,[row.id]:open});}} data-cell-key={cell(path+'.'+row.id)}>
        <summary><strong>{row.name||row.label||(row.from_state?row.from_state+' → '+(row.to_state||'待填写'):row.from?(detailRows(value,'flow.nodes').find(node=>node.id===row.from)?.label||row.from)+' → '+(detailRows(value,'flow.nodes').find(node=>node.id===row.to)?.label||'待选择'): '待填写'+names[section])}</strong><span>{ownIssues.length?ownIssues.length+' 项待补充':'展开核对'}</span></summary>
        {error(path)}
        <div className="detail-properties">{required.map(key=>renderProperty(section,row,key,true))}</div><details className="detail-extra-properties"><summary>关联与补充属性（选填）</summary><div className="detail-properties">{extra.map(key=>renderProperty(section,row,key,false))}</div></details>
        <div className="detail-object-footer"><small>编号 {row.id} · 本组内容随整条需求确认</small><button type="button" disabled={disabled} onClick={()=>updateSection(section,rows.filter(item=>item.id!==row.id))}>移除{names[section]}</button></div>
      </details>;
    })}<button type="button" disabled={disabled} onClick={()=>{const row=newObject(section);setExpandedSections(previous=>({...previous,[section.split('.')[0]]:true}));setExpandedObjects(previous=>({...previous,[row.id]:true}));updateSection(section,[...rows,row]);}}>添加{names[section]}</button></div>;
  };
  return <div className="detail-contract-editor" data-detail-requirement={requirementId}>
    <p className="detail-contract-intro">字段、交互和流程均随“{confirmationLabel}”提交。标为不适用时需说明原因，已有定义请核对后移除。</p>
    {candidate&&(canAdoptWhole||canUndo)&&<details className="detail-section">
      <summary>{canUndo?'AI 详细定义已整版使用 · 尚未正式确认':'AI 详细定义修订建议 · 尚未替换当前内容'}</summary>
      <p className="detail-help">合并时按字段和步骤编号比较；保留的草稿不会被直接覆盖，冲突会展示两份内容供选择。</p>
      <p className="detail-help">整版使用只替换本条详细定义并清除本行冲突；正文、业务说明、验收和回答不变。操作后可立即撤销。</p>
      <p>{candidate.contract.fields?.length||0} 个字段 · {candidate.contract.interactions?.length||0} 项操作 · {candidate.contract.flow?.nodes?.length||0} 个流程步骤</p>
      <button type="button" disabled={disabled||!candidateDiffers} onClick={()=>{const merged=mergeDetailContract(value,base,candidate.contract);if(merged.value)onChange(merged.value,[...new Map([...conflicts,...merged.conflicts].map(conflict=>[conflict.path,conflict])).values()]);}}>合并 AI 详细定义建议</button>
      <button type="button" disabled={disabled||!canAdoptWhole} onClick={adoptWhole}>整版使用 AI 详细定义</button>
      {canUndo&&<><p className="detail-help" role="status">已整版使用 AI 详细定义，可立即撤销；仍需核对后确认。</p><button type="button" disabled={disabled} onClick={undoWhole}>撤销整版替换</button></>}
    </details>}
    {!!conflicts.length&&<div className="detail-conflicts" role="alert"><strong>详细定义有 {conflicts.length} 处版本冲突，请比较后选择。</strong>
      {conflicts.map((conflict,index)=><div key={conflict.path} data-cell-key={cell(conflict.path)}>
        <p>{conflictLocation(conflict.path)}</p>
        <div className="detail-conflict-values">
          <div><b>保留的草稿</b><div className="detail-conflict-value">{conflictValue(conflict.local,conflict.path.split('.').pop()||'')}</div></div>
          <div><b>新建议</b><div className="detail-conflict-value">{conflictValue(conflict.remote,conflict.path.split('.').pop()||'')}</div></div>
        </div>
        <details className="detail-conflict-technical"><summary>技术详情</summary><p>{conflict.path}</p>
          <div className="detail-conflict-values">
            <div><b>保留的草稿（原始 JSON）</b><pre>{rawDisplay(conflict.local)}</pre></div>
            <div><b>新建议（原始 JSON）</b><pre>{rawDisplay(conflict.remote)}</pre></div>
          </div>
        </details>
        {([false,true] as const).map(remote=><button type="button" disabled={disabled} key={String(remote)} onClick={()=>{const next=resolveDetailConflict(value,conflict,remote);if(next)onChange(next,conflicts.filter((_,n)=>n!==index));}}>{remote?'采用新建议':'保留草稿'}</button>)}
      </div>)}
    </div>}
    {(Object.keys(detailSectionLabels) as DetailSection[]).map(section=>{
      const setting=value.applicability?.[section],path='detail_contract.applicability.'+section,contentPath='detail_contract.'+section;
      const pendingCount=allIssues.filter(issue=>issue.path===path||issue.path.startsWith(path+'.')||issue.path===contentPath||issue.path.startsWith(contentPath+'.')).length;
      const count=section==='flow'?detailRows(value,'flow.nodes').length+' 步骤 · '+detailRows(value,'flow.edges').length+' 连线':detailRows(value,section).length+' 项';
      return <details className="detail-section detail-definition-section" key={section} data-detail-section={section} open={!!expandedSections[section]} onToggle={e=>{const open=e.currentTarget.open;setExpandedSections(previous=>previous[section]===open?previous:{...previous,[section]:open});}}><summary><strong>{detailSectionLabels[section]}</strong><span>{setting?.applicable===false?'不适用 · ':''}{count}<span className={pendingCount?'detail-section-pending':undefined}> · {pendingCount} 项待补充</span></span></summary>
        <div className="detail-applicability" data-cell-key={cell(path)}><label>适用情况<select aria-label={title+' '+detailSectionLabels[section]+' 适用情况'} disabled={disabled} value={setting?.applicable===true?'yes':setting?.applicable===false?'no':''} onChange={e=>onChange({...value,applicability:{...value.applicability,[section]:{...setting,applicable:e.target.value==='yes',reason:setting?.reason||''}}})}><option value="" disabled>请选择</option><option value="yes">本需求涉及</option><option value="no">本需求不适用</option></select></label>{error(path)}</div>
        {setting?.applicable===false&&<div data-cell-key={cell(path+'.reason')}><label>不适用原因<textarea aria-label={title+' '+detailSectionLabels[section]+' 不适用原因'} rows={2} disabled={disabled} value={setting.reason||''} onChange={e=>onChange({...value,applicability:{...value.applicability,[section]:{...setting,reason:e.target.value}}})}/></label>{error(path+'.reason')}<small>不适用的分组应无定义；下方原内容仍保留，核对后逐项移除，或改回适用。</small></div>}
        {section==='flow'?<><p className="detail-help">先列出开始、处理、判断和结束步骤，再添加连线。判断分支和等待步骤的继续条件都需说明；流程图会根据这些步骤生成。</p>{error('detail_contract.flow')}{renderCollection('flow.nodes')}<h4>步骤之间的连线</h4>{renderCollection('flow.edges')}</>:renderCollection(section)}
      </details>;
    })}
    {(!!references.filter(item=>item.kind==='requirement'&&item.id!==requirementId).length||!!value.depends_on?.length)&&<details className="detail-section" data-cell-key={cell('detail_contract.depends_on')}><summary>依赖其他需求（选填）</summary><p className="detail-help">依赖内容变更时，本条需求也需要重新核对。</p>{error('detail_contract.depends_on')}{references.filter(item=>item.kind==='requirement'&&item.id!==requirementId).map(item=><label className="detail-dependency" key={item.id}><input type="checkbox" disabled={disabled} checked={(value.depends_on||[]).includes(item.id)} onChange={e=>onChange({...value,depends_on:e.target.checked?[...(value.depends_on||[]),item.id]:(value.depends_on||[]).filter(id=>id!==item.id)})}/>{item.title}</label>)}{(value.depends_on||[]).filter(id=>!references.some(item=>item.id===id&&item.kind==='requirement'&&item.id!==requirementId)).map(id=><label key={id} className="detail-dependency"><input type="checkbox" disabled={disabled} checked onChange={()=>onChange({...value,depends_on:value.depends_on?.filter(ref=>ref!==id)})}/>已失效的依赖：{id}</label>)}</details>}
  </div>;
}
