import type { Project } from './api';

export function extractPublicUrls(text:string):string[] {
  const found=text.match(/https?:\/\/[^\s<>"'，。；！？、）】]+/gi)||[];
  return [...new Set(found.map(raw=>raw.replace(/[.,;!?，。；！？、）】]+$/g,'')))];
}

export function benchmarkSources(p:Pick<Project,'sources'>) {
  return p.sources.filter(s=>!s.excluded&&!s.superseded_by&&s.purpose!=='template'&&!('business_context' in s));
}

export function usableBenchmarkSource(s:Project['sources'][number]):boolean {
  return s.parse_status==='awaiting_vision'?Boolean(s.image_mime):
    ['read','partial'].includes(s.parse_status)&&Boolean(s.excerpts?.some((e:{text?:string})=>e.text?.trim()));
}

export function pastedWebpageReference(url:string,title:string,body:string) {
  const sourceUrl=url.trim(),pageTitle=title.trim();
  if(!sourceUrl||!/^https?:\/\/\S+$/i.test(sourceUrl))throw Error('请核对实际网页的来源网址。');
  if(!body.trim())throw Error('请粘贴你实际看到的网页正文。');
  return {title:'人工粘贴网页正文 · '+(pageTitle||'页面标题待核对').slice(0,180),
    text:`来源网址：${sourceUrl}\n页面标题：${pageTitle||'待核对'}\n采集方式：用户粘贴网页正文\n\n${body}`,purpose:'reference' as const};
}

export function initialBenchmarkSourceIds(p:Pick<Project,'sources'>):string[] {
  return benchmarkSources(p).filter(s=>(s.purpose==='reference'||s.benchmark_reference)&&usableBenchmarkSource(s)).map(s=>s.id);
}

export function restoredBenchmarkSourceIds(p:Pick<Project,'sources'>,savedIds:unknown):string[] {
  if(!Array.isArray(savedIds))return initialBenchmarkSourceIds(p);
  const usableIds=new Set(benchmarkSources(p).filter(usableBenchmarkSource).map(s=>s.id));
  return savedIds.filter((id:unknown):id is string=>typeof id==='string'&&usableIds.has(id));
}

export function prepareBenchmark(p:Pick<Project,'sources'>, target:string, focus:string, ids:string[]) {
  const name=target.trim(),description=focus.trim();
  if(!name)throw Error('请填写对标对象。');
  if(name.length>200)throw Error('对标对象不能超过 200 字。');
  if(description.length>2000)throw Error('参考范围说明不能超过 2000 字。');
  const selected=[...new Set(ids)];
  if(!selected.length)throw Error('请至少选择一份可用参考资料。');
  const available=new Map(benchmarkSources(p).map(s=>[s.id,s]));
  for(const id of selected){
    const source=available.get(id);
    if(!source)throw Error('参考资料已变化，请重新核对选择。');
    if(!usableBenchmarkSource(source))
      throw Error('所选资料尚无可用内容；请核对读取状态，或选择其他可读资料。');
  }
  return {target:name,focus:description,source_ids:selected};
}
