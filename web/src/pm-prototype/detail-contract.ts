import type {Project,DetailUnknownScopeReview,DetailUnknownScopeReviewProperty} from '../api';
export type DetailSection = 'fields' | 'interactions' | 'flow' | 'state_transitions';
export type DetailEvidence = {status:'source'|'proposal'|'unknown'; source_refs?:Record<string,unknown>[]; reason?:string};
export type DetailObject = {id:string;evidence?:Record<string,DetailEvidence>;[key:string]:any};
export type DetailContract = {
  schema_version:1;
  applicability:Record<DetailSection,{applicable:boolean;reason:string}>;
  fields:DetailObject[]; interactions:DetailObject[];
  flow:{nodes:DetailObject[];edges:DetailObject[]}; state_transitions:DetailObject[];
  depends_on?:string[];
};
export type DetailIssue = {path:string;message:string;code?:string;object_id?:string};
export type DetailConflict = {path:string;local:any;remote:any;localEvidence?:DetailEvidence;remoteEvidence?:DetailEvidence};
export const detailSectionLabels:Record<DetailSection,string>={fields:'数据字段',interactions:'交互逻辑',flow:'业务流程',state_transitions:'状态变化'};
export const detailPropertyLabels:Record<string,string>={
  name:'名称',meaning:'业务含义',data_type:'数据类型',source:'数据来源',required:'必填条件',default_value:'默认值及空值处理',constraints:'取值范围与校验',permissions:'权限说明',visibility:'可见角色与条件',editability:'可编辑角色与条件',
  enum_values:'可选值',unit:'单位',precision:'精度',time_basis:'时间口径',formula:'计算规则',actor:'执行角色',entry:'操作入口',preconditions:'前置条件',trigger:'触发动作',inputs:'输入与关联数据',validation:'输入校验',success:'成功反馈',failure:'失败反馈',effects:'数据影响',pending:'处理中反馈',repeat:'重复操作处理',cancel:'取消与离开处理',
  label:'步骤名称',kind:'步骤类型',from:'起点步骤',to:'终点步骤',condition:'条件',from_state:'原状态',to_state:'新状态',prohibited_actions:'禁止操作',field_refs:'关联字段',rule_refs:'关联规则',acceptance_refs:'关联验收',depends_on:'依赖的需求',empty_value:'空值处理',uniqueness:'唯一性范围',parent_id:'所属复合字段',interaction_id:'对应操作',
};
export const detailFieldRequired=['name','meaning','data_type','source','required','default_value','constraints','visibility','editability'];
export const detailInteractionRequired=['name','actor','entry','preconditions','trigger','inputs','validation','success','failure','effects','pending','repeat','cancel'];
export const detailTransitionRequired=['from_state','to_state','trigger','condition','prohibited_actions'];
export function emptyDetailContract():DetailContract{return {schema_version:1,applicability:{fields:{applicable:true,reason:''},interactions:{applicable:true,reason:''},flow:{applicable:true,reason:''},state_transitions:{applicable:true,reason:''}},fields:[],interactions:[],flow:{nodes:[],edges:[]},state_transitions:[]};}
export function detailRows(contract:DetailContract|undefined,section:string):DetailObject[]{
  const value=section==='flow.nodes'?contract?.flow?.nodes:section==='flow.edges'?contract?.flow?.edges:(contract as any)?.[section];
  return Array.isArray(value)?value:[];
}
const same=(a:any,b:any)=>JSON.stringify(a)===JSON.stringify(b);
const clone=<T,>(value:T):T=>value===undefined?value:JSON.parse(JSON.stringify(value));
const object=(value:any)=>value&&typeof value==='object'&&!Array.isArray(value);
const nonEmpty=(value:any):value is string=>typeof value==='string'&&!!value.trim();
const validUnknownScopeProperty=(value:any):value is DetailUnknownScopeReviewProperty=>object(value)&&
  nonEmpty(value.path)&&nonEmpty(value.property_key)&&typeof value.classification==='string'&&
  nonEmpty(value.known_business)&&nonEmpty(value.unknown_technical)&&typeof value.explanation==='string';
const validUnknownScopeReview=(value:any):value is DetailUnknownScopeReview=>object(value)&&
  nonEmpty(value.requirement_id)&&nonEmpty(value.candidate_id)&&nonEmpty(value.detail_hash)&&nonEmpty(value.review_hash)&&
  Array.isArray(value.properties)&&value.properties.every(validUnknownScopeProperty);
export type DetailUnknownScopeProjection=DetailUnknownScopeReviewProperty&{readonly protocol?:string};
const technicalScopeKeys:Record<string,readonly string[]>={fields:['default_value','constraints','editability','required','source'],interactions:['cancel','pending']};
export function validTechnicalScopeProperty(contract:DetailContract,property:DetailUnknownScopeProjection):boolean{
  if(!nonEmpty(property.path)||property.classification!=='technical_only'||!nonEmpty(property.known_business)||!nonEmpty(property.unknown_technical)||typeof property.explanation!=='string')return false;
  const parts=property.path.split('.');
  if(parts.length!==4||parts[0]!=='detail_contract')return false;
  const [,collection,objectId,key]=parts,keys=technicalScopeKeys[collection];
  if(!objectId||!keys?.includes(key))return false;
  const legacy=property.protocol===undefined||property.protocol==='detail-default-scope-1';
  if(property.protocol!=='detail-technical-scope-2'&&!(legacy&&collection==='fields'&&key==='default_value'))return false;
  const rows=detailRows(contract,collection).filter(row=>row.id===objectId);
  return rows.length===1&&nonEmpty(rows[0][key])&&rows[0].evidence?.[key]?.status==='unknown';
};


/** Server-reviewed technical unknowns are a local display projection only; never submit them. */
export function detailUnknownScopeProperties(project:Project,requirementId:string,contract:DetailContract|undefined):DetailUnknownScopeProjection[]{
  if(!contract||!Array.isArray(project.detail_unknown_scope_reviews))return [];
  const records=project.detail_unknown_scope_reviews.filter(record=>validUnknownScopeReview(record)&&record.requirement_id===requirementId);
  const roots=project.items.filter(item=>item.id===requirementId&&item.kind==='requirement');
  const matching=records.filter(record=>{
    const candidates=project.items.filter(item=>item.id===record.candidate_id&&item.kind==='requirement'&&
      item.applies_to==='to_be'&&!item.superseded_by&&
      (!item.recommendation_requirement_id||item.recommendation_requirement_id===requirementId)&&
      ((item.action==='add'&&item.id===requirementId&&!item.target_item_id&&
        (item.selection_status==='candidate'||item.selection_status==='selected'))||
        (item.action==='revise'&&item.target_item_id===requirementId&&
          (item.selection_status==='candidate'||(item.selection_status==='deferred'&&roots.length===1&&
            Array.isArray(roots[0].confirmed_revisions)&&roots[0].confirmed_revisions.some(revision=>revision.candidate_id===record.candidate_id)))))&&
      item.detail_contract);
    return candidates.length===1&&same(contract,candidates[0].detail_contract);
  });
  if(matching.length!==1)return [];
  const protocol=matching[0].protocol;
  return matching[0].properties.flatMap((property:DetailUnknownScopeReviewProperty)=>{
    const projected:DetailUnknownScopeProjection={...property,protocol};
    return validTechnicalScopeProperty(contract,projected)?[projected]:[];
  });
}

const mergeMetadata=(l:any,b:any,r:any):any=>{
  if(same(l,b))return clone(r);
  if(same(r,b)||same(l,r))return clone(l);
  if(!object(l)||!object(r))return clone(r===undefined?l:r);
  const result=Object.fromEntries([...new Set([...Object.keys(l),...Object.keys(r)])].filter(key=>key!=='source_refs').flatMap(key=>{
    const value=same(l[key],b?.[key])?r[key]:same(r[key],b?.[key])?l[key]:r[key]===undefined?l[key]:r[key];
    return value===undefined?[]:[[key,clone(value)]];
  }));
  const refs=[...(Array.isArray(l.source_refs)?l.source_refs:[]),...(Array.isArray(r.source_refs)?r.source_refs:[])];
  if(refs.length||Array.isArray(l.source_refs)||Array.isArray(r.source_refs))result.source_refs=refs.filter((ref,index)=>refs.findIndex(other=>same(ref,other))===index).map(ref=>clone(ref));
  return result;
};

/** Stable IDs keep concurrent insertions and unrelated property edits independent. */
export function mergeDetailContract(local:DetailContract|undefined,base:DetailContract|undefined,remote:DetailContract|undefined):{value:DetailContract|undefined;conflicts:DetailConflict[]}{
  const conflicts:DetailConflict[]=[];
  const conflict=(path:string,l:any,r:any)=>{conflicts.push({path,local:clone(l),remote:clone(r)});return clone(l);};
  const merge=(l:any,b:any,r:any,path:string):any=>{
    if(same(l,b)||same(l,r))return clone(r);
    if(same(r,b))return clone(l);
    if(l===undefined||r===undefined)return conflict(path,l,r);
    if(Array.isArray(l)&&Array.isArray(r)&&[...l,...r,...(Array.isArray(b)?b:[])].every(row=>object(row)&&typeof row.id==='string'&&row.id)){
      const lm=new Map(l.map(row=>[row.id,row])),bm=new Map((Array.isArray(b)?b:[]).map((row:any)=>[row.id,row])),rm=new Map(r.map(row=>[row.id,row]));
      if(lm.size!==l.length||rm.size!==r.length)return conflict(path,l,r);
      const ids=[...r.map(row=>row.id),...l.filter(row=>!rm.has(row.id)).map(row=>row.id)];
      return ids.flatMap(id=>{const value=merge(lm.get(id),bm.get(id),rm.get(id),path+'.'+id);return value===undefined?[]:[value];});
    }
    if(object(l)&&object(r)){
      const result=Object.fromEntries([...new Set([...Object.keys(l),...Object.keys(r)])].flatMap(key=>{
        if(key==='evidence')return [];
        const start=conflicts.length,value=merge(l[key],object(b)?b[key]:undefined,r[key],path+'.'+key);
        for(const item of conflicts.slice(start))if(item.path===path+'.'+key){
          if(l.evidence?.[key]!==undefined)item.localEvidence=clone(l.evidence[key]);
          if(r.evidence?.[key]!==undefined)item.remoteEvidence=clone(r.evidence[key]);
        }
        return value===undefined?[]:[[key,value]];
      }));
      const evidence=Object.fromEntries([...new Set([...Object.keys(l.evidence||{}),...Object.keys(r.evidence||{})])].flatMap(key=>{
        const value=same(l[key],r[key])?mergeMetadata(l.evidence?.[key],b?.evidence?.[key],r.evidence?.[key]):same(result[key],l[key])?clone(l.evidence?.[key]):clone(r.evidence?.[key]);
        return value===undefined?[]:[[key,value]];
      }));
      if(Object.keys(evidence).length)result.evidence=evidence;
      // A retained manual value must never inherit a source claim for a different remote value.
      if(l.id&&r.id)for(const key of Object.keys(l).filter(key=>!['id','evidence'].includes(key))){
        if(!same(l[key],b?.[key])&&!same(l[key],r[key])&&same(result[key],l[key]))result.evidence={...result.evidence,[key]:clone(l.evidence?.[key]||{status:'proposal',reason:b===undefined?'保留的草稿，待整条确认':'人工修改，待整条确认'})};
      }
      return result;
    }
    return conflict(path,l,r);
  };
  return {value:merge(local,base,remote,'detail_contract'),conflicts};
}

/** Resolve only a previously displayed conflict, preserving all unrelated edits. */
export function resolveDetailConflict(contract:DetailContract|undefined,conflict:DetailConflict,useRemote:boolean):DetailContract|undefined{
  if(!useRemote)return contract;
  const parts=conflict.path.split('.').slice(1),result=clone(contract);
  if(!parts.length)return clone(conflict.remote);
  if(!result)return result;
  let current:any=result;
  for(const key of parts.slice(0,-1)){
    current=Array.isArray(current)?current.find(row=>row?.id===key):current?.[key];
    if(!current)return result;
  }
  const last=parts.at(-1)!;
  if(Array.isArray(current)){
    const index=current.findIndex(row=>row?.id===last);
    if(conflict.remote===undefined){if(index>=0)current.splice(index,1);}
    else if(index>=0)current[index]=clone(conflict.remote);else current.push(clone(conflict.remote));
  }else{
    if(conflict.remote===undefined)delete current[last];else current[last]=clone(conflict.remote);
    if(conflict.remoteEvidence!==undefined)current.evidence={...current.evidence,[last]:clone(conflict.remoteEvidence)};
    else if(current.evidence?.[last]!==undefined){current.evidence={...current.evidence};delete current.evidence[last];}
  }
  return result;
}

export function detailPathValue(contract:DetailContract|undefined,path:string):any{
  if(!contract||!path.startsWith('detail_contract.'))return '';
  const parts=path.split('.').slice(1);let value:any=contract;
  for(const part of parts)value=Array.isArray(value)?value.find(row=>row?.id===part):value?.[part];
  return value;
}
export function detailFieldValue(contract:DetailContract|undefined,path:string):string{
  const value=detailPathValue(contract,path);
  return typeof value==='string'?value:Array.isArray(value)&&value.every(v=>typeof v==='string')?value.join('、'):'';
}
export function detailWitnessOptions(contract:DetailContract|undefined):{field:string;label:string}[]{
  if(!contract)return [];
  const sections=['fields','interactions','flow.nodes','flow.edges','state_transitions'];
  return sections.flatMap(section=>{
    const top=section.startsWith('flow.')?'flow':section;
    if(contract.applicability?.[top as DetailSection]?.applicable===false)return [];
    return detailRows(contract,section).flatMap(row=>Object.keys(row).filter(key=>!['id','evidence'].includes(key)).flatMap(key=>{
      const field='detail_contract.'+section+'.'+row.id+'.'+key;
      return detailFieldValue(contract,field)?[{field,label:detailSectionLabels[top as DetailSection]+' · '+(row.name||row.label||row.id)+' · '+(detailPropertyLabels[key]||key)}]:[];
    }));
  });
}

/** Immediate editing feedback; the server performs authoritative graph and reference checks. */
export function detailEditingIssues(contract:DetailContract|undefined,technicalScope:readonly DetailUnknownScopeReviewProperty[]=[]):DetailIssue[]{
  if(!contract)return [];
  const issues:DetailIssue[]=[],add=(path:string,message:string)=>issues.push({path,message});
  const technicalOnlyPaths=new Set(technicalScope.filter(property=>validTechnicalScopeProperty(contract,property)).map(property=>property.path));
  if(contract.schema_version!==1)add('detail_contract.schema_version','详细定义版本无法识别，请重新整理。');
  for(const section of Object.keys(detailSectionLabels) as DetailSection[]){
    const setting=contract.applicability?.[section];
    if(typeof setting?.applicable!=='boolean'){add('detail_contract.applicability.'+section,detailSectionLabels[section]+'尚未说明是否适用。');continue;}
    if(!setting.applicable){if(!setting.reason?.trim())add('detail_contract.applicability.'+section+'.reason','请说明'+detailSectionLabels[section]+'不适用的原因。');if((section==='flow'?detailRows(contract,'flow.nodes').length+detailRows(contract,'flow.edges').length:detailRows(contract,section).length)>0)add('detail_contract.'+section,'已标记不适用但仍有定义，请核对并移除原内容，或改回适用。');continue;}
    const rows=section==='flow'?detailRows(contract,'flow.nodes'):detailRows(contract,section);
    if(!rows.length)add('detail_contract.'+(section==='flow'?'flow.nodes':section),'请补充'+detailSectionLabels[section]+'，或明确说明不适用。');
    const collections=section==='flow'?['flow.nodes','flow.edges']:[section];
    for(const collection of collections)for(const row of detailRows(contract,collection)){
      const path='detail_contract.'+collection+'.'+row.id;
      const required=collection==='fields'?[...detailFieldRequired,...(['number','integer'].includes(row.data_type)?['unit','precision']:[]),...(['date','datetime'].includes(row.data_type)?['time_basis']:[]),...(row.source==='calculated'?['formula']:[])]:collection==='interactions'?detailInteractionRequired:collection==='state_transitions'?detailTransitionRequired:collection==='flow.nodes'?['label','kind',...(['start','end'].includes(row.kind)?[]:['actor'])]:['from','to'];
      for(const key of required)if(typeof row[key]!=='string'||!row[key].trim())add(path+'.'+key,'请填写'+(row.name||row.label||row.id)+'的'+(detailPropertyLabels[key]||key)+'。');
      if(collection==='fields'&&row.data_type==='enum'&&(!Array.isArray(row.enum_values)||!row.enum_values.some((v:any)=>typeof v==='string'&&v.trim())))add(path+'.enum_values','请列出枚举的可选值。');
      if(collection==='flow.edges'&&detailRows(contract,'flow.nodes').find(node=>node.id===row.from)?.kind==='decision'&&!row.condition?.trim())add(path+'.condition','判断步骤的每条分支都需要说明条件。');
      for(const [key,evidence]of Object.entries(row.evidence||{}))if(evidence?.status==='unknown'&&!technicalOnlyPaths.has(path+'.'+key))add(path+'.'+key,(row.name||row.label||row.id)+'的'+(detailPropertyLabels[key]||key)+'仍待明确；请补充具体决定。');
    }
  }
  if(contract.applicability?.flow?.applicable===true&&detailRows(contract,'flow.nodes').length){
    const rows=detailRows(contract,'flow.nodes'),edges=detailRows(contract,'flow.edges'),nodes=new Map(rows.map(row=>[row.id,row]));
    const outgoing=new Map<string,DetailObject[]>(rows.map(row=>[row.id,[]])),incoming=new Map<string,DetailObject[]>(rows.map(row=>[row.id,[]]));
    for(const edge of edges){
      const path='detail_contract.flow.edges.'+edge.id;
      if(!nodes.has(edge.from)||!nodes.has(edge.to)){add(path,'这条连线的起点或终点不存在，请重新选择步骤。');continue;}
      outgoing.get(edge.from)!.push(edge);incoming.get(edge.to)!.push(edge);
    }
    const starts=rows.filter(row=>row.kind==='start').map(row=>row.id),terminals=rows.filter(row=>row.kind==='end'||row.kind==='wait').map(row=>row.id),name=(row:DetailObject)=>typeof row.label==='string'&&row.label.trim()?row.label:'未命名步骤';
    if(!starts.length)add('detail_contract.flow.nodes','业务流程缺少开始步骤，请添加开始步骤。');
    if(!terminals.length)add('detail_contract.flow.nodes','业务流程缺少结束或等待步骤，请添加结束或等待步骤。');
    for(const row of rows){
      const path='detail_contract.flow.nodes.'+row.id,out=outgoing.get(row.id)||[],inside=incoming.get(row.id)||[];
      if(row.kind==='decision'){
        if(out.length<2)add(path,'“'+name(row)+'”是判断步骤，至少需要两条条件分支。');
        const conditions=out.map(edge=>typeof edge.condition==='string'?edge.condition.trim():'');
        if(conditions.some(condition=>!condition)||new Set(conditions).size!==conditions.length)add(path,'“'+name(row)+'”的分支条件不能为空，且不能重复。');
      }
      if(row.kind==='end'&&out.length)add(path,'“'+name(row)+'”是结束步骤，不能继续连出，请移除其后续连线。');
      if(row.kind==='start'&&inside.length)add(path,'“'+name(row)+'”是开始步骤，不能有入边，请移除指向它的连线。');
      if(!['end','wait'].includes(row.kind)&&!out.length)add(path,'“'+name(row)+'”缺少后续处理去向，请添加连线。');
      if(row.kind==='wait'&&(!out.length||out.some(edge=>typeof edge.condition!=='string'||!edge.condition.trim())))add(path,'“'+name(row)+'”是等待步骤，须定义继续条件及去向，请补充连线和条件。');
    }
    const reachable=(seeds:string[],graph:Map<string,DetailObject[]>,key:'from'|'to')=>{
      const visited=new Set<string>(),todo=[...seeds];
      while(todo.length){
        const id=todo.pop()!;
        if(visited.has(id))continue;
        visited.add(id);
        for(const edge of graph.get(id)||[]){const next=edge[key];if(typeof next==='string'&&!visited.has(next))todo.push(next);}
      }
      return visited;
    };
    const reached=reachable(starts,outgoing,'to'),finishes=reachable(terminals,incoming,'from');
    for(const row of rows){
      if(!reached.has(row.id))add('detail_contract.flow.nodes.'+row.id,'“'+name(row)+'”无法从开始步骤到达，请检查前面的连线。');
      else if(!finishes.has(row.id))add('detail_contract.flow.nodes.'+row.id,'“'+name(row)+'”无法到达结束或等待步骤，请检查后续连线。');
    }
  }
  return issues.filter((issue,index)=>issues.findIndex(other=>other.path===issue.path&&other.message===issue.message)===index);
}
