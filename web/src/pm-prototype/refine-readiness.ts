import type { Project, Blocker } from '../api';

export function refinementBlockers(project:Project):Blocker[] {
  const rows=new Map<string,Blocker>();
  for(const state of project.product_flow?.slice(0,3)||[]){
    for(const row of state.blockers||[])if(!rows.has(row.key))rows.set(row.key,row);
  }
  return [...rows.values()];
}

/** Explicit confirmation only. Never saves suggestions or adopts candidates. */
export async function confirmRefinement(project:Project, read:()=>Promise<Project>, check:(step:number, revision:number, hash:string)=>Promise<unknown>) {
  let expectedRevision=project.revision;
  const hashes=project.product_flow?.slice(0,3).map(state=>state.content_hash);
  if(hashes?.length!==3)throw Error('核对状态未取得，请刷新项目。');
  if(refinementBlockers(project).length)throw Error('还有待处理事项，请先在本页核对后再确认。');
  for(let index=0;index<3;index++){
    const current=await read(),states=current.product_flow?.slice(0,3);
    if(current.revision!==expectedRevision||states?.some((state,n)=>state.content_hash!==hashes[n]))throw Error('内容已变化，请核对最新内容后重新确认。');
    const state=states?.[index];
    if(!state||state.missing.length||!state.available)throw Error('还有待处理事项，请刷新后在本页处理。');
    if(state.complete)continue;
    await check(index+1,current.revision,state.content_hash);
    // This endpoint returns stage rows, not a Project receipt. Re-read even
    // after the final check so a concurrent write cannot be reported as done.
    const written=await read(),writtenStates=written.product_flow?.slice(0,3);
    if(written.revision!==current.revision+1||writtenStates?.length!==3||writtenStates.some((row,n)=>row.content_hash!==hashes[n])||!writtenStates[index].complete)
      throw Error('内容已变化，请核对最新内容后重新确认；已记录的核对仍保留。');
    expectedRevision=written.revision;
  }
}
