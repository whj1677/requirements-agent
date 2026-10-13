import type { Project } from './api';

type Transition = {
  phase:number;revision:number;save?:()=>Promise<Project>;
  read:()=>Promise<Project>;
  check:(revision:number,hash:string)=>Promise<unknown>;
  blocked?:(project:Project)=>void;
};

/** Never certify a stale render or turn a failed save/check into navigation. */
export async function confirmCheckpoint({phase,revision,save,read,check,blocked}:Transition):Promise<number> {
  const saved=save?await save():null;
  const latest=await read();
  if(latest.revision!==(saved?.revision??revision))throw Error('内容版本已变化，请核对最新内容后再继续；本次输入仍保留。');
  const state=latest.product_flow?.[phase];
  if(!state?.available||state.missing.length){
    blocked?.(latest);
    throw Error(!state?.available?'请先完成前一步的内容核对。':'还有 '+state.missing.length+' 项需要处理，请使用阻塞清单中的对应入口。');
  }
  await check(latest.revision,state.content_hash);
  return phase===2?4:phase+1;
}
