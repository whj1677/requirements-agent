import type { Project, JsonObject as Obj } from './api';

/** Literal sources contain only text actually edited by the human. */
export async function saveScopeDraft({p,revision,values,ids,profile,states,editedKeys,all,proof,mutate,read,onWritten}: {
  p:Project;revision:number;values:Obj;ids:string[];profile:Obj;states:Obj;editedKeys:string[];all:boolean;proof:Obj;
  mutate:(path:string,body:Obj)=>Promise<unknown>;read:()=>Promise<Project>;onWritten?:(project:Project)=>void;
}):Promise<Project> {
  const first=!(p as Obj).intake&&!p.product_flow?.[0]?.complete;
  const literal=Object.fromEntries(editedKeys.filter(key=>String(values[key]||'').trim()).map(key=>[key,values[key]]));
  let result:Project|undefined;
  if(first&&Object.keys(literal).length){
    result=await mutate('/intake/direct',{expected_revision:revision,values:literal,scope_profile:profile,
      field_states:Object.fromEntries(Object.entries(states).filter(([key])=>key in literal))}) as Project;
    onWritten?.(result);
    revision=result.revision;
  }
  // Scope ids never enter the raw-input endpoint and never imply item adoption.
  result=await mutate('/product-context',{expected_revision:revision,values,scope_ids:ids,
    scope_profile:profile,field_states:states,...(!result?{__edit:proof}:{})}) as Project;
  onWritten?.(result);
  if(first&&all&&!(result as Obj).intake){
    const fresh=await read();
    if(fresh.revision!==result.revision)throw Error('内容版本已变化，请重新核对原始资料和本页内容。');
    const intake=fresh.product_flow?.[0];
    if(!intake||intake.missing.length)throw Error(intake?.missing.join('；')||'原始资料尚未核对。');
    // Checkpoint endpoints return flow rows, not a Project receipt.
    await mutate('/stage-checks/1',{expected_revision:fresh.revision,expected_hash:intake.content_hash});
    result=await read();
    if(result.revision!==fresh.revision+1)throw Error('内容版本已变化，请重新核对原始资料和本页内容。');
    onWritten?.(result);
  }
  return result;
}
