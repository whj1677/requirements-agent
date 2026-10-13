import { useState } from 'react';
import { createPortal } from 'react-dom';
import { useDialog } from './shell';

type Obj = Record<string, any>;

export function DetailTable({node}: {node: Obj}) {
  return <div className="document-detail-table" data-detail-hash={node.detail_hash}>
    <div className="document-table-scroll" tabIndex={0} aria-label={node.title+'，可横向滚动'}>
      <table><caption>{node.title}</caption><thead><tr>{node.columns.map((label: string, index: number)=><th key={index} scope="col">{label}</th>)}</tr></thead>
        <tbody>{node.rows.map((row: string[], index: number)=><tr key={index}>{row.map((value, cell)=><td key={cell}>{value}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </div>;
}

export function DetailDiagram({node, stale=false}: {node:Obj; stale?:boolean}) {
  const [expanded,setExpanded]=useState(false),[zoom,setZoom]=useState(100),[failed,setFailed]=useState(false);
  useDialog(expanded,()=>setExpanded(false));
  // Only server-generated, self-contained image data is accepted. Never insert
  // SVG/HTML into the document DOM or fetch a model-supplied remote URL.
  const source=typeof node.image_uri==='string' && node.image_uri.startsWith('data:image/svg+xml;base64,') ? node.image_uri : '';
  const unavailable=failed||!source;
  return <figure className="document-detail-diagram" data-detail-hash={node.detail_hash}>
    <figcaption><strong>{node.title}</strong><button type="button" disabled={unavailable} onClick={()=>{setZoom(100);setExpanded(true);}}>放大流程图</button></figcaption>
    {stale&&<p className="notice">这张图对应文档原版本；详细需求已变化，请更新文档后核对。</p>}
    {unavailable ? <p role="alert" className="error">流程图暂时无法显示，请重新生成文档。下方流程步骤与分支表仍可核对。</p> : <button type="button" className="document-diagram-thumbnail" aria-label={'放大 '+node.title} onClick={()=>{setZoom(100);setExpanded(true);}}><img src={source} alt={node.title+'；完整条件见下方分支表'} onError={()=>setFailed(true)}/></button>}
    {expanded&&createPortal(<div className="document-image-backdrop" onClick={()=>setExpanded(false)}><section className="document-image-dialog" role="dialog" aria-modal="true" aria-label={node.title+'放大预览'} onClick={event=>event.stopPropagation()}>
      <header><strong>{node.title}</strong><div className="toolbar"><button type="button" disabled={zoom<=50} onClick={()=>setZoom(Math.max(50,zoom-25))} aria-label="缩小流程图">−</button><span aria-live="polite">{zoom}%</span><button type="button" disabled={zoom>=250} onClick={()=>setZoom(Math.min(250,zoom+25))} aria-label="放大流程图">＋</button><button type="button" onClick={()=>setZoom(100)}>重置</button><button type="button" autoFocus onClick={()=>setExpanded(false)}>关闭</button></div></header>
      {stale&&<p className="notice">旧版本流程图，需更新文档。</p>}
      <div className="document-image-scroll" tabIndex={0}><img src={source} alt={node.title} style={{width:node.width*zoom/100,maxWidth:'none'}}/></div>
    </section></div>,document.body)}
  </figure>;
}
