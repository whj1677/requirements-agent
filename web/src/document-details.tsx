import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useDialog } from './shell';

type Obj = Record<string, any>;

const diagramSource = (value: unknown) => typeof value === 'string' && value.startsWith('data:image/svg+xml;base64,') ? value : '';

export function DetailTable({node}: {node: Obj}) {
  const compactAcceptance=node.table_role==='acceptance'&&node.columns.length===2;
  const itemIds=Array.isArray(node.item_ids)?node.item_ids:[];
  const tableItemId=node.item_id??(itemIds.length===1?itemIds[0]:undefined);
  return <div className={'document-detail-table'+(compactAcceptance?' document-acceptance-table':'')} data-detail-hash={node.detail_hash} data-item-id={tableItemId}>
    <div className="document-table-scroll" tabIndex={0} aria-label={node.title+'，可横向滚动'}>
      <table><caption>{node.title}</caption><thead><tr>{node.columns.map((label: string, index: number)=><th key={index} scope="col">{label}</th>)}</tr></thead>
        <tbody>{node.rows.map((row: string[], index: number)=><tr key={index} data-item-id={itemIds.length===node.rows.length?itemIds[index]:undefined}>{row.map((value, cell)=><td key={cell}>{value}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </div>;
}

export function DetailDiagram({node, stale=false}: {node:Obj; stale?:boolean}) {
  const [expanded,setExpanded]=useState(false),[zoom,setZoom]=useState(100),[failed,setFailed]=useState(false);
  const [requestedViewId,setRequestedViewId]=useState<string|null>(null);
  useDialog(expanded,()=>setExpanded(false));
  // Only server-generated, self-contained image data is accepted. Never insert
  // SVG/HTML into the document DOM or fetch a model-supplied remote URL.
  const views=(Array.isArray(node.views)?node.views:[]).filter((view: Obj)=>view && typeof view.id==='string' && view.id && typeof view.title==='string' && view.title && diagramSource(view.image_uri) && Number.isFinite(view.width) && view.width>0 && Number.isFinite(view.height) && view.height>0);
  const selectedView=requestedViewId===null?views[0]:views.find((view: Obj)=>view.id===requestedViewId);
  const selectedIndex=selectedView?views.indexOf(selectedView):-1;
  const viewId=selectedView?selectedView.id:'';
  const source=diagramSource(selectedView?selectedView.image_uri:node.image_uri);
  const width=selectedView?selectedView.width:node.width;
  const title=selectedView?node.title+'：'+selectedView.title:node.title;
  const unavailable=failed||!source;
  useEffect(()=>{setRequestedViewId(null);setFailed(false);setZoom(100);},[node.detail_hash,node.image_uri]);
  useEffect(()=>{setFailed(false);setZoom(100);},[source]);
  const chooseView=(id:string)=>{setRequestedViewId(id);setFailed(false);setZoom(100);};
  const stepView=(direction:number)=>{const nextIndex=selectedView?selectedIndex+direction:0;const nextView=views[nextIndex];if(nextView)chooseView(nextView.id);};
  const viewControls=views.length>0&&<>
    <label className="document-diagram-view">流程视图<select value={viewId} onChange={event=>chooseView(event.target.value)}><option value="">完整总览（索引）</option>{views.map((view: Obj)=><option key={view.id} value={view.id}>{view.title}</option>)}</select></label>
    <span className="document-diagram-view-position" aria-live="polite">{selectedView?`分图 ${selectedIndex+1} / ${views.length}`:`总览（索引） · 共 ${views.length} 张分图`}</span>
    {views.length>1&&<div className="document-diagram-view-nav" aria-label="分图导航"><button type="button" disabled={!selectedView||selectedIndex===0} onClick={()=>stepView(-1)}>上一张</button><button type="button" disabled={!!selectedView&&selectedIndex===views.length-1} onClick={()=>stepView(1)}>下一张</button></div>}
  </>;
  return <figure className="document-detail-diagram" data-detail-hash={node.detail_hash}>
    <figcaption><strong>{node.title}</strong><div className="document-diagram-controls">{viewControls}<button type="button" disabled={unavailable} onClick={()=>{setZoom(100);setExpanded(true);}}>放大流程图</button></div></figcaption>
    {stale&&<p className="notice">这张图对应文档原版本；详细需求已变化，请更新文档后核对。</p>}
    {unavailable ? <p role="alert" className="error">流程图暂时无法显示，请重新生成文档。下方流程步骤与分支表仍可核对。</p> : <button type="button" className="document-diagram-thumbnail" aria-label={'放大 '+title} onClick={()=>{setZoom(100);setExpanded(true);}}><img key={source} src={source} alt={title+'；完整条件见下方分支表'} onError={()=>setFailed(true)}/></button>}
    {expanded&&createPortal(<div className="document-image-backdrop" onClick={()=>setExpanded(false)}><section className="document-image-dialog" role="dialog" aria-modal="true" aria-label={title+'放大预览'} onClick={event=>event.stopPropagation()}>
      <header><strong>{node.title}</strong><div className="document-image-dialog-actions">{viewControls}<div className="toolbar"><button type="button" disabled={zoom<=50} onClick={()=>setZoom(Math.max(50,zoom-25))} aria-label="缩小流程图">−</button><span aria-live="polite">{zoom}%</span><button type="button" disabled={zoom>=250} onClick={()=>setZoom(Math.min(250,zoom+25))} aria-label="放大流程图">＋</button><button type="button" onClick={()=>setZoom(100)}>重置</button><button type="button" autoFocus onClick={()=>setExpanded(false)}>关闭</button></div></div></header>
      {stale&&<p className="notice">旧版本流程图，需更新文档。</p>}
      <div className="document-image-scroll" tabIndex={0}><img key={source} src={source} alt={title} style={{width:width*zoom/100,maxWidth:'none'}}/></div>
    </section></div>,document.body)}
  </figure>;
}
