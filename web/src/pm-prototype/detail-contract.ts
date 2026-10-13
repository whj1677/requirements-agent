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
export type DetailConflict = {path:string;local:any;remote:any};
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
        const value=merge(l[key],object(b)?b[key]:undefined,r[key],path+'.'+key);return value===undefined?[]:[[key,value]];
      }));
      // A retained manual value must never inherit a source claim for a different remote value.
      if(l.id&&r.id)for(const key of Object.keys(l).filter(key=>!['id','evidence'].includes(key))){
        if(!same(l[key],b?.[key])&&!same(l[key],r[key])&&same(result[key],l[key]))result.evidence={...result.evidence,[key]:clone(l.evidence?.[key]||{status:'proposal',reason:'人工修改，待整条确认'})};
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
  }else if(conflict.remote===undefined)delete current[last];else current[last]=clone(conflict.remote);
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
export function detailEditingIssues(contract:DetailContract|undefined):DetailIssue[]{
  if(!contract)return [];
  const issues:DetailIssue[]=[],add=(path:string,message:string)=>issues.push({path,message});
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
      for(const [key,evidence]of Object.entries(row.evidence||{}))if(evidence?.status==='unknown')add(path+'.'+key,(row.name||row.label||row.id)+'的'+(detailPropertyLabels[key]||key)+'仍待明确；请补充具体决定。');
    }
  }
  return issues.filter((issue,index)=>issues.findIndex(other=>other.path===issue.path)===index);
}
