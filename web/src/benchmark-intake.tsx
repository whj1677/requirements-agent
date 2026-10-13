import React, { useRef, useState } from 'react';
import { sourceStatusNames } from './api';
import type { JsonObject as Obj, Project } from './api';
import { useDirty } from './editing';
import { benchmarkSources, extractPublicUrls, initialBenchmarkSourceIds, prepareBenchmark, restoredBenchmarkSourceIds, usableBenchmarkSource } from './benchmark-intake.ts';

type Draft={target:string;focus:string;sourceIds:string[];url:string;consent:boolean;dynamic:boolean};
const sourcePurposes:Record<string,string>={goal:'目标与诉求',current:'现状资料',reference:'设计参考'};
type Props={p:Project;busy:boolean;text:string;onSources:(url?:string)=>void;onImport:(url:string,dynamic:boolean)=>Promise<Obj>;
  onPrepare:(benchmark:{target:string;focus:string;source_ids:string[]},message:string)=>Promise<void>};
const blank=(p:Project):Draft=>({target:'',focus:'',sourceIds:initialBenchmarkSourceIds(p),url:'',consent:false,dynamic:false});
function readDraft(p:Project):Draft {
  const initial=blank(p);
  try { const saved=JSON.parse(sessionStorage.getItem('ra-benchmark-draft-'+p.id)||'null');
    if(!saved||typeof saved!=='object')return initial;
    return {...initial,...saved,sourceIds:restoredBenchmarkSourceIds(p,saved.sourceIds),consent:false};
  } catch { return initial; }
}

export function BenchmarkIntake({p,busy,text,onSources,onImport,onPrepare}:Props){
  const key='ra-benchmark-draft-'+p.id;
  const [draft,setDraft]=useState<Draft>(()=>readDraft(p));
  const [expanded,setExpanded]=useState(false),[working,setWorking]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[dirty,setDirty]=useState(false);
  const lock=useRef(false);
  const suggested=extractPublicUrls(text)[0]||'';
  const cue=/对标|参考产品|竞品/.test(text);
  const sources=benchmarkSources(p);
  const urlSources=draft.url.trim()?p.sources.filter(s=>s.uri===draft.url.trim()||s.url===draft.url.trim()||s.source_url===draft.url.trim()||s.origin_url===draft.url.trim()):[];
  const failedUrlSource=urlSources.some(s=>!s.excluded&&usableBenchmarkSource(s))?undefined:urlSources.find(s=>s.parse_status==='failed');
  const change=(part:Partial<Draft>)=>{const next={...draft,...part};setDraft(next);setDirty(true);try{sessionStorage.setItem(key,JSON.stringify({...next,consent:false}));}catch{/* Keep live input. */}};
  const discard=()=>{setDraft(blank(p));setDirty(false);try{sessionStorage.removeItem(key);}catch{/* Ignore storage denial. */}};
  useDirty(p.id+'-benchmark',dirty,
    '对标输入（仅保存为本浏览器草稿）',async()=>{sessionStorage.setItem(key,JSON.stringify({...draft,consent:false}));setDirty(false);},discard);
  const selected=(id:string)=>draft.sourceIds.includes(id);
  const toggle=(id:string)=>change({sourceIds:selected(id)?draft.sourceIds.filter(x=>x!==id):[...draft.sourceIds,id]});
  const sourceForUrl=(url:string)=>{const matches=p.sources.filter(s=>s.uri===url||(s.uri&&s.title===url)||s.url===url||s.source_url===url||s.origin_url===url);return matches.find(s=>!s.excluded&&usableBenchmarkSource(s))||matches[0];};
  async function importUrl():Promise<Obj|undefined>{
    const url=draft.url.trim();
    if(!url)return undefined;
    if(!/^https?:\/\/\S+$/i.test(url))throw Error('请填写完整的 http 或 https 网址。');
    const existing=sourceForUrl(url);
    if(existing){
      if(existing.parse_status==='failed')throw Error(`这个网址此前读取失败${existing.failure_reason?'：'+existing.failure_reason:''}。请改用实际网页正文或其他可读资料；不会自动重复抓取。`);
      if(!existing.excluded)return existing;
      throw Error('这个网址的资料已排除。请在参考资料中核对后重新选择。');
    }
    if(!draft.consent)throw Error('读取前请确认你有权读取这个公开网页。');
    const imported=await onImport(url,draft.dynamic);
    const source=imported?.source||imported;
    if(!source?.id)throw Error('未取得参考资料编号。请到参考资料中核对读取状态。');
    if(!usableBenchmarkSource(source))
      throw Error(`网址无法读取${source.failure_reason?'：'+source.failure_reason:''}。请粘贴实际网页正文并注明原网址，或选择其他可读资料；当前网址已保留。`);
    return source;
  }
  async function run(prepare:boolean){
    if(lock.current||busy)return;lock.current=true;setWorking(true);setError('');setNotice('');
    try{
      const imported=await importUrl(),id=imported?.id;
      const ids=id&&!draft.sourceIds.includes(id)?[...draft.sourceIds,id]:draft.sourceIds;
      if(id)change({sourceIds:ids});
      if(!prepare){setNotice(id?'资料已加入选择，请核对读取状态。':'请先填写参考网址。');return;}
      const current=imported&&!p.sources.some(s=>s.id===id)?{...p,sources:[...p.sources,imported]}:p;
      const request=prepareBenchmark(current,draft.target,draft.focus,ids);
      await onPrepare(request,text);
      sessionStorage.setItem(key,JSON.stringify({...draft,sourceIds:ids,consent:false}));setDirty(false);
    }catch(e){setError((e as Error).message||'处理未完成；请核对资料读取状态后重试。');}
    finally{lock.current=false;setWorking(false);}
  }
  return <section className="benchmark-intake" aria-label="对标参考产品">
    <button type="button" aria-expanded={expanded} onClick={()=>setExpanded(!expanded)}>对标参考产品 {expanded?'收起':'展开'}</button>
    {!expanded&&(cue||suggested)&&<p className="benchmark-cue">你提到了参考对象或网址。展开后可读取参考资料并自动填充待审核草稿。</p>}
    {expanded&&<fieldset disabled={busy||working}><legend>对标参考产品</legend>
      <p>资料用于提出可核对的草稿；读取成功也不表示已理解或采纳其中规则。</p>
      <label>对标对象<input maxLength={200} value={draft.target} onChange={e=>change({target:e.target.value})} placeholder="请填写产品或对象名称"/></label>
      <label>参考范围说明<textarea maxLength={2000} rows={3} value={draft.focus} onChange={e=>change({focus:e.target.value})} placeholder="说明希望参考的页面、流程或问题；未知可以留空"/></label>
      <div className="benchmark-sources"><strong>选择要对标的资料</strong>{sources.length? sources.map(s=><div className="benchmark-source-option" key={s.id}><label><input type="checkbox" checked={usableBenchmarkSource(s)&&selected(s.id)} disabled={!usableBenchmarkSource(s)} onChange={()=>toggle(s.id)}/>{s.title||s.name||s.id} · {sourcePurposes[s.purpose]||'用途待核对'} · {sourceStatusNames[s.parse_status]||'读取状态待核对'}</label>{!usableBenchmarkSource(s)&&<p role={s.parse_status==='failed'?'alert':undefined}>{s.parse_status==='failed'?'网址或资料读取失败，尚无可对标内容':'尚无可用内容'}{s.failure_reason?`：${s.failure_reason}`:''}。可粘贴实际正文，或选择其他可读资料。</p>}{usableBenchmarkSource(s)&&<details><summary>查看资料片段</summary><p>{s.excerpts?.find((e:Obj)=>typeof e.text==='string'&&e.text.trim())?.text.slice(0,600)||'图片原文尚待核对，请在项目资料中查看原图。'}</p></details>}</div>):<p>尚无可选资料。</p>}
        <button type="button" onClick={()=>onSources()}>＋ 添加参考资料</button></div>
      <label>参考网址<input type="url" value={draft.url} onChange={e=>change({url:e.target.value})} placeholder="https://example.com/page"/></label>
      {suggested&&!draft.url&&<button type="button" onClick={()=>change({url:suggested})}>使用聊天中的网址：{suggested}</button>}
      <small>填写网址仅记录地址；点击读取后仍须核对资料状态。</small>
      {failedUrlSource&&<p className="error" role="alert">这个网址已有读取失败记录{failedUrlSource.failure_reason?`：${failedUrlSource.failure_reason}`:''}。未取得网页正文，也不会自动重试。</p>}
      {(failedUrlSource||error)&&<div className="benchmark-url-recovery"><p>如果你能正常查看该网页，请粘贴实际正文并核对预填的原网址、页面标题；保存后回到这里选择新资料。也可以选择其他可读网页或资料。失败记录没有可用正文。</p><button type="button" onClick={()=>onSources(draft.url.trim()||undefined)}>去粘贴网页正文或添加其他资料</button>{draft.url.trim()&&<button type="button" onClick={()=>{change({url:''});setError('');}}>使用已选资料继续（移除本次网址）</button>}</div>}
      <label><input type="checkbox" checked={draft.consent} onChange={e=>change({consent:e.target.checked})}/>我有权读取这个公开网页</label>
      <label><input type="checkbox" checked={draft.dynamic} onChange={e=>change({dynamic:e.target.checked})}/>这是动态网页，需要动态读取</label>
      {error&&<p className="error" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
      <div className="benchmark-actions"><button type="button" disabled={!draft.url.trim()||Boolean(failedUrlSource)} onClick={()=>void run(false)}>读取参考网址</button>
        <button type="button" className="primary" onClick={()=>void run(true)}>对标并填充需求草稿 →</button></div>
    </fieldset>}
  </section>;
}
