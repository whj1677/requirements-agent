import React, { useEffect, useRef, useState } from 'react';
import { downloadFile, sourceStatusNames } from './api';
import { useDialog } from './shell';

type Obj = Record<string, any>;
export const sourcePurposes: Record<string, string> = { goal: '目标与诉求', current: '现状资料', reference: '设计参考', template: '文档模板' };
const descriptions: Record<string, string> = {
  goal: '本次想解决的问题、期望的结果与约束。', current: '已有系统、当前流程和实际页面。',
  reference: '墨刀原型截图、页面样式或竞品参考；不自动成为本期业务要求。', template: '文档结构与格式参考。',
};

export function IntakeMaterialGroups({ sources, onOpen }: { sources: Obj[]; onOpen?: (sourceId?: string) => void }) {
  const parents = sources.filter(s => !s.container_source_id);
  return <>{Object.entries(sourcePurposes).map(([purpose, label]) => {
    const members = parents.filter(s => s.purpose === purpose);
    return members.length ? <section className="intake-material-group" key={purpose} aria-label={label}><h4>{label} · {members.length}</h4><div className="material-cards">{members.map(source => <button className="material-file" key={source.id} onClick={() => onOpen?.(source.id)}>{source.image_mime ? <img src={'/api/projects/' + source.project_id + '/sources/' + source.id + '/image'} alt="材料缩略图" /> : <span aria-hidden="true">▤</span>}<span><b>{source.title}</b><small>{source.excluded ? '已排除' : sourceStatusNames[source.parse_status] || source.parse_status} · 点击查看内容</small></span></button>)}</div></section> : null;
  })}<button type="button" className="material-upload" onClick={() => onOpen?.()}><span>点击添加相关资料</span><small>文档、墨刀截图、文字或公开网址</small></button></>;
}

function MaterialImage({ source, root }: { source: Obj; root: string }) {
  const [loaded, setLoaded] = useState(false), [error, setError] = useState(false), [retry, setRetry] = useState(0), [zoom, setZoom] = useState(false);
  return <section className="material-image-view"><div className="toolbar"><button type="button" onClick={() => setZoom(!zoom)}>{zoom ? '适应窗口' : '查看原图尺寸'}</button></div>
    {!loaded && !error && <p role="status">正在读取图片…</p>}
    {error && <p role="alert">图片暂未取得。<button type="button" onClick={() => { setLoaded(false); setError(false); setRetry(n => n + 1); }}>重新加载图片</button></p>}
    <div className={zoom ? 'material-image-scroll original-size' : 'material-image-scroll'}><img src={'/api' + root + '/sources/' + source.id + '/image?view=' + retry} alt={source.title} onLoad={() => setLoaded(true)} onError={() => setError(true)} /></div>
  </section>;
}

function SourcePreview({ source, p, root, focusExcerpt, onClose }: { source: Obj; p: Obj; root: string; focusExcerpt?: string; onClose: () => void }) {
  const [downloading, setDownloading] = useState(false), [error, setError] = useState(''), [limit, setLimit] = useState(50);
  const lock = useRef(false);
  const children = p.sources.filter((s: Obj) => s.container_source_id === source.id);
  useDialog(true, () => { if (!downloading) onClose(); });
  useEffect(() => {
    if (!focusExcerpt) return;
    const index = source.excerpts.findIndex((x: Obj) => x.id === focusExcerpt);
    if (index >= limit) { setLimit(index + 1); return; }
    document.getElementById('excerpt-' + focusExcerpt)?.scrollIntoView({ block: 'center' });
  }, [focusExcerpt, source.id, limit]);
  async function download() {
    if (lock.current) return;
    lock.current = true; setDownloading(true); setError('');
    try { await downloadFile('/api' + root + '/sources/' + source.id + '/file', source.title); }
    catch (e) { setError((e as Error).message); }
    finally { lock.current = false; setDownloading(false); }
  }
  return <div className="modal-backdrop"><section className="modal source-preview" role="dialog" aria-modal="true" aria-label="附件内容">
    <header className="section-heading"><div><h2>{source.title}</h2><p>{sourcePurposes[source.purpose] || '其他资料'} · {sourceStatusNames[source.parse_status] || '状态待核对'}</p></div><button disabled={downloading} onClick={onClose}>关闭附件</button></header>
    {source.failure_reason && <p className="notice">{source.failure_reason}</p>}
    {source.purpose === 'reference' && <p className="notice">设计参考用于理解界面与交互，不表示其中规则已纳入本期。</p>}
    {source.uri && !source.container_source_id && <p className="material-origin">来源网址：{source.uri}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {!source.uri && source.sha256 && <button disabled={downloading} onClick={download}>{downloading ? '正在准备原始附件…' : '下载原始附件'}</button>}
    {source.image_mime ? <MaterialImage source={source} root={root} /> : <>
      <h3>已读取内容</h3><p className="muted">下方展示已提取的内容，不代表原始排版或未提取对象。完整 Office 文件可下载后在本机打开。</p>
      {!source.excerpts.length && <p className="notice">尚未取得可预览的正文，请核对读取状态；上传成功不表示已读取。</p>}
      {source.excerpts.slice(0, limit).map((ex: Obj) => <article key={ex.id} id={'excerpt-' + ex.id} className={focusExcerpt === ex.id ? 'material-excerpt source-highlight' : 'material-excerpt'}><small>{ex.locator || '正文片段'}</small><pre>{ex.text}</pre></article>)}
      {source.excerpts.length > limit && <button onClick={() => setLimit(n => n + 50)}>继续显示内容（已显示 {limit}/{source.excerpts.length}）</button>}
    </>}
    {children.length > 0 && <section><h3>文档内图片 · {children.length}</h3>{children.map((child: Obj) => <article key={child.id}><p>{child.container_locator} · {sourceStatusNames[child.parse_status]}</p><MaterialImage source={child} root={root} /></article>)}</section>}
    <details><summary>来源与读取信息</summary><p>{source.id} · 材料版本 {source.version} · {source.created}</p><p>读取方式：{source.reading?.method || '尚未确认'}</p><p>内容摘要：{source.sha256 || '尚未取得'}</p></details>
  </section></div>;
}

export function SourceLibrary({ p, root, busy, act, mutate, focusRefs = [] }: { p: Obj; root: string; busy: boolean; act: (fn: () => Promise<unknown>) => void; mutate: (path: string, body: Obj) => Promise<unknown>; focusRefs?: Obj[] }) {
  const [selected, setSelected] = useState('');
  useEffect(() => { if (focusRefs.length) setSelected(focusRefs[0].source_id); }, [focusRefs]);
  const source = p.sources.find((s: Obj) => s.id === selected);
  const parents = p.sources.filter((s: Obj) => !s.container_source_id);
  const categories = [...Object.keys(sourcePurposes), ...(parents.some((s: Obj) => !sourcePurposes[s.purpose]) ? ['other'] : [])];
  return <div className="source-library"><div className="section-heading"><h2>已导入资料</h2><span>{parents.length} 份材料 · 内嵌图片归属原文档</span></div>
    {categories.map(category => { const members = parents.filter((s: Obj) => category === 'other' ? !sourcePurposes[s.purpose] : s.purpose === category); return <section className="source-category" key={category} aria-label={sourcePurposes[category] || '其他资料'}>
      <header><h3>{sourcePurposes[category] || '其他资料'} <span>{members.length}</span></h3><p>{descriptions[category]}</p></header>
      {!members.length && <p className="material-empty">尚未添加此类资料</p>}
      <div className="source-grid">{members.map((s: Obj) => <article className={'card material-card' + (s.excluded ? ' material-excluded' : '')} key={s.id} id={'source-' + s.id}>
        <div className="section-heading"><h4>{s.title}</h4><span className="pill">{s.excluded ? '已排除' : sourceStatusNames[s.parse_status] || s.parse_status}</span></div>
        {s.image_mime && <button type="button" className="material-thumbnail" aria-label={'预览图片：' + s.title} onClick={() => setSelected(s.id)}><img loading="lazy" src={'/api' + root + '/sources/' + s.id + '/image'} alt={s.title} /></button>}
        <p className="material-summary">{s.image_mime ? '图片可预览，文字与交互含义须经分析核对。' : `${s.excerpts.length} 个已提取片段${s.embedded_image_ids?.length ? ` · ${s.embedded_image_ids.length} 张内嵌图片` : ''}`}</p>
        {s.failure_reason && <p className="notice">{s.failure_reason}</p>}
        <div className="toolbar"><button type="button" className="primary" aria-label={'查看内容：' + s.title} onClick={() => setSelected(s.id)}>查看内容</button><button disabled={busy} onClick={() => act(() => mutate('/sources/' + s.id + '/exclude', {}))}>{s.excluded ? '恢复使用' : '排除材料'}</button></div>
        <details><summary>分类与读取操作</summary><label>资料分类<select aria-label={'资料分类：' + s.title} disabled={busy} value={s.purpose} onChange={e => { const purpose = e.target.value; act(() => mutate('/sources/' + s.id + '/purpose', { purpose })); }}>{Object.entries(sourcePurposes).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><button disabled={busy} onClick={() => act(() => mutate('/sources/' + s.id + '/retry', {}))}>重新读取（保留原件）</button><small>{s.id}</small></details>
      </article>)}</div>
    </section>; })}
    {source && <SourcePreview key={source.id} source={source} p={p} root={root} focusExcerpt={focusRefs.find(ref => ref.source_id === source.id)?.excerpt_id} onClose={() => setSelected('')} />}
  </div>;
}
