import React, { useEffect, useRef, useState } from 'react';
import { downloadFile, sourceStatusNames } from './api';
import { useDialog } from './shell';
import { Icon } from './ui-icons';

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
    <div className={zoom ? 'material-image-scroll original-size' : 'material-image-scroll'} hidden={error}><img src={'/api' + root + '/sources/' + source.id + '/image?view=' + retry} alt={source.title} onLoad={() => { setLoaded(true); setError(false); }} onError={() => setError(true)} /></div>
  </section>;
}

function SourcePreview({ source, p, root, focusExcerpt, onClose }: { source: Obj; p: Obj; root: string; focusExcerpt?: string; onClose: () => void }) {
  const [downloading, setDownloading] = useState(false), [error, setError] = useState(''), [limit, setLimit] = useState(50);
  const [view, setView] = useState<'text' | 'images'>(source.image_mime ? 'images' : 'text');
  const lock = useRef(false);
  const reader = useRef<HTMLDivElement>(null);
  const children = p.sources.filter((s: Obj) => s.container_source_id === source.id);
  const fileType = source.image_mime ? '图片' : /\.(docx?|xlsx?|pptx?|pdf|txt|md)$/i.exec(source.title)?.[1].toUpperCase() || '资料';
  const status = sourceStatusNames[source.parse_status] || '状态待核对';
  const limited = source.parse_status !== 'read';
  useDialog(true, () => { if (!downloading) onClose(); });
  useEffect(() => {
    if (!focusExcerpt) return;
    if (!source.image_mime) setView('text');
    const index = source.excerpts.findIndex((x: Obj) => x.id === focusExcerpt);
    setLimit(n => Math.max(n, index + 1));
  }, [focusExcerpt, source.id]);
  useEffect(() => {
    if (view !== 'text' || !focusExcerpt) return;
    // Scope the lookup to this reader: another mounted project can contain the same source.
    const target = Array.from(reader.current?.querySelectorAll<HTMLElement>('.material-excerpt') || []).find(el => el.id === 'excerpt-' + focusExcerpt);
    target?.scrollIntoView({ block: 'center' });
  }, [focusExcerpt, source.id, limit, view]);
  function changeView(next: 'text' | 'images') { setView(next); reader.current?.scrollTo({ top: 0 }); }
  async function download() {
    if (lock.current) return;
    lock.current = true; setDownloading(true); setError('');
    try { await downloadFile('/api' + root + '/sources/' + source.id + '/file', source.title); }
    catch (e) { setError((e as Error).message); }
    finally { lock.current = false; setDownloading(false); }
  }
  return <div className="modal-backdrop preview-backdrop"><section className="modal source-preview" role="dialog" aria-modal="true" aria-label="附件内容">
    <header className="preview-header">
      <div className="preview-file-icon" aria-hidden="true"><Icon name="document" /><span>{fileType}</span></div>
      <div className="preview-heading"><p className="preview-eyebrow">附件预览</p><h2>{source.title}</h2><div className="preview-tags"><span>{sourcePurposes[source.purpose] || '其他资料'}</span><span className={limited ? 'preview-status limited' : 'preview-status'}>{status}</span>{source.excluded && <span>已排除</span>}</div></div>
      <button className="preview-close" disabled={downloading} onClick={onClose} aria-label="关闭附件" title="关闭附件"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
    </header>
    <div className="preview-toolbar">
      <div className="preview-views" role="group" aria-label="预览内容">
        {!source.image_mime && <button aria-pressed={view === 'text'} onClick={() => changeView('text')}>正文</button>}
        {(source.image_mime || children.length > 0) && <button aria-pressed={view === 'images'} onClick={() => changeView('images')}>{source.image_mime ? '图片' : '文档内图片'} · {source.image_mime ? 1 : children.length}</button>}
      </div>
      {!source.uri && source.sha256 && <button className="preview-download" disabled={downloading} onClick={download}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" /></svg>{downloading ? '正在准备原始附件…' : '下载原始附件'}</button>}
    </div>
    {error && <p className="error preview-download-error" role="alert">{error}</p>}
    <div className="preview-reading-area" ref={reader} tabIndex={0} role="region" aria-label={view === 'text' ? '已读取内容' : '图片预览'}>
      <div className="preview-notes">
        {source.failure_reason && <p className="notice">{source.failure_reason}</p>}
        {source.purpose === 'reference' && <p className="notice">设计参考用于理解界面与交互，不表示其中规则已纳入本期。</p>}
      </div>
      {view === 'text' ? <>
        <p className="preview-reading-hint">以下为提取内容，保留原文文字；原始排版及未提取对象请下载附件查看。</p>
        <div className="preview-paper">
          {!source.excerpts.length && <div className="preview-empty"><Icon name="document" /><h3>暂时没有可预览的正文</h3><p>请核对附件的读取状态。上传成功不表示内容已读取。</p></div>}
          {source.excerpts.slice(0, limit).map((ex: Obj) => <article key={ex.id} id={'excerpt-' + ex.id} className={focusExcerpt === ex.id ? 'material-excerpt source-highlight' : 'material-excerpt'}><pre>{ex.text}</pre></article>)}
          {source.excerpts.length > limit && <button className="preview-more" onClick={() => setLimit(n => n + 50)}>继续显示内容</button>}
        </div>
      </> : source.image_mime ? <div className="preview-image-card"><MaterialImage source={source} root={root} /></div> : <section className="preview-gallery"><h3>文档内图片 · {children.length}</h3><p className="preview-reading-hint">图片按原文位置排列，可单独查看原图尺寸。</p>{children.map((child: Obj, index: number) => <article className="preview-image-card" key={child.id}><header><span className="preview-image-number">{String(index + 1).padStart(2, '0')}</span><div><h4>{child.container_locator || '文档内图片'}</h4><small>{sourceStatusNames[child.parse_status] || '状态待核对'}</small></div></header><MaterialImage source={child} root={root} /></article>)}</section>}
    </div>
    <details className="preview-provenance"><summary>来源与读取信息</summary><dl>
      {source.uri && !source.container_source_id && <><dt>来源网址</dt><dd>{source.uri}</dd></>}
      <dt>材料编号</dt><dd>{source.id}</dd><dt>材料版本</dt><dd>{source.version}</dd><dt>导入时间</dt><dd>{source.created}</dd>
      {focusExcerpt && source.excerpts.some((ex: Obj) => ex.id === focusExcerpt) && <><dt>引用位置</dt><dd>{source.excerpts.find((ex: Obj) => ex.id === focusExcerpt)?.locator || focusExcerpt}</dd></>}
      <dt>读取方式</dt><dd>{source.reading?.method || '尚未确认'}</dd><dt>SHA-256</dt><dd>{source.sha256 || '尚未取得'}</dd>
    </dl></details>
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
