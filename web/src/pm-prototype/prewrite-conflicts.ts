import type {Project,JsonObject as Obj} from '../api';
import type {Sheet} from './refinement-sheet-state';
import {scopeLabels} from '../scope-draft';
import {requirementBehaviorLabels as fields} from './requirement-fields';
import {resolveDetailConflict} from './detail-contract';
import {prewriteQuoteLabel} from '../prewrite-review';

export type BatchConflict={key:string;label:string;local:any;remote:any;kind:string;id?:string;field?:string;path?:string;message?:string};
export function batchConflicts(p:Project,sheet:Sheet,fresh:Sheet|null):BatchConflict[]{
  const result:BatchConflict[]=[],messages=sheet.merge_conflicts||[];
  const add=(kind:string,id:string,field:string,label:string,local:any,remote:any,prefix=label)=>{
    const message=messages.find(m=>m.startsWith(prefix+'在其他位置也已变化'));
    if(message)result.push({key:[kind,id,field].join('-'),kind,id,field,label,local,remote,message});
  };
  for(const [id,row] of Object.entries(sheet.rows)){
    const title=p.items.find(i=>i.id===id)?.title||'需求内容',prefix='“'+title+'”',latest=fresh?.rows[id];
    for(const conflict of row.detail_conflicts||[]){
      const view={...p,items:p.items.map(i=>i.id===id?{...i,detail_contract:row.detail_contract}:i)};
      result.push({key:id+'-'+conflict.path,kind:'detail',id,path:conflict.path,label:prewriteQuoteLabel(view,{object_id:id,path:conflict.path}),local:conflict.local,remote:conflict.remote});
    }
    if(!latest)continue;
    add('statement',id,'statement',title+' · 需求正文',row.statement,latest.statement,prefix+'需求正文');
    add('change_type',id,'change_type',title+' · 本次性质',row.change_type,latest.change_type,prefix+'改动性质');
    for(const field of Object.keys(fields))add('behavior',id,field,title+' · '+fields[field],row.behavior[field],latest.behavior[field],prefix+fields[field]);
    for(const [n,ac] of row.acceptance_items.entries())if(ac.conflict_statement!==undefined)result.push({key:id+'-acceptance-'+n,kind:'acceptance',id,field:String(n),label:title+' · 验收条件',local:ac.statement,remote:ac.conflict_statement});
  }
  if(!fresh)return result;
  for(const q of p.questions)add('answer',q.id,'answer',q.question,sheet.answers[q.id],fresh.answers[q.id],'“'+(fresh.labels?.[q.id]||q.id)+'”回答');
  for(const key of Object.keys(scopeLabels)){
    add('context','',key,scopeLabels[key],sheet.values[key],fresh.values[key]);
    add('state','',key,scopeLabels[key]+'状态',sheet.states[key],fresh.states[key]);
  }
  add('profile','','profile','需求类型',sheet.profile,fresh.profile);
  add('scope','','scope','本期范围',sheet.scope,fresh.scope);
  for(const [id,row] of Object.entries(sheet.linked_clauses||{})){
    const latest=fresh.linked_clauses?.[id];if(!latest)continue;
    const title=p.items.find(i=>i.id===id)?.title||'关联规则',prefix='“'+title+'”关联规则';
    add('clause',id,'statement',title+' · 正文',row.statement,latest.statement,prefix+'正文');
    for(const field of Object.keys(fields))add('clause',id,field,title+' · '+fields[field],row.behavior[field],latest.behavior[field],prefix+fields[field]);
  }
  return result;
}

/** One explicit choice changes only the displayed conflict, never another edit. */
export function resolveBatchConflict(p:Project,sheet:Sheet,entry:BatchConflict,useAI:boolean,fresh:Sheet|null):Sheet{
  const next=structuredClone(sheet),id=entry.id||'',field=entry.field||'';
  if(entry.kind==='detail'){
    const row=next.rows[id],conflict=row.detail_conflicts?.find(c=>c.path===entry.path);
    if(!conflict)return next;
    row.detail_contract=resolveDetailConflict(row.detail_contract,conflict,useAI);
    row.detail_conflicts=row.detail_conflicts?.filter(c=>c.path!==entry.path);
    if(!row.detail_conflicts?.length){const prefix='“'+(p.items.find(i=>i.id===id)?.title||id)+'”的详细定义有 ';
      next.merge_conflicts=next.merge_conflicts?.filter(m=>!m.startsWith(prefix));}
  }else if(entry.kind==='acceptance'){
    const row=next.rows[id],n=Number(field),ac=row.acceptance_items[n];
    const {conflict_statement,invalid_candidate,...value}=ac;
    row.acceptance_items[n]={...value,statement:useAI?entry.remote:entry.local,candidate_id:undefined};
    if(!row.acceptance_items.some(a=>a.conflict_statement!==undefined)){
      const prefix='“'+(p.items.find(i=>i.id===id)?.title||id)+'”的验收内容在其他位置';
      next.merge_conflicts=next.merge_conflicts?.filter(m=>!m.startsWith(prefix));}
  }else if(useAI){
    const value=structuredClone(entry.remote);
    if(entry.kind==='answer')next.answers[id]=value;
    if(entry.kind==='context')next.values[field]=value;
    if(entry.kind==='state')next.states[field]=value;
    if(entry.kind==='profile')next.profile=value;
    if(entry.kind==='scope')next.scope=value;
    if(entry.kind==='behavior')next.rows[id].behavior[field]=value;
    if(entry.kind==='statement'){next.rows[id].statement=value;next.rows[id].candidate_id=fresh?.rows[id]?.candidate_id;}
    if(entry.kind==='change_type')next.rows[id].change_type=value;
    if(entry.kind==='clause'){if(field==='statement')next.linked_clauses![id].statement=value;else next.linked_clauses![id].behavior[field]=value;}
  }
  if(entry.message)next.merge_conflicts=next.merge_conflicts?.filter(m=>m!==entry.message);
  return next;
}
