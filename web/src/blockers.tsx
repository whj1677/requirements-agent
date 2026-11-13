import React from 'react';
import type { Blocker, BlockerAction } from './api';
import { blockerGroups } from './blocker-navigation';
import { publicBlocker } from './workflow-labels';

export function blockerActionLabel(action:BlockerAction,row:Blocker){
  if(action.kind!=='document')return action.label;
  const kind=(action.document||'').toUpperCase();
  if(row.code==='document_review_pending')return '去核对 '+kind;
  if(['document_gap','document_reading'].includes(row.code))return '查看 '+kind;
  return (row.code==='document_stale'||row.code==='document_update_needed'?'去更新 ':'去准备 ')+kind;
}

export function BlockerPanel({id,rows,busy,onResolve,onRefresh}:{id:string;rows:Blocker[];busy:boolean;onResolve:(action:BlockerAction,row:Blocker)=>void;onRefresh?:()=>void}) {
  if(!rows.length)return null;
  const displayed=rows.map(publicBlocker),groups=blockerGroups(displayed);
  return <section id={id} className="blocker-panel" aria-label="当前阶段阻塞清单" tabIndex={-1} role="alert">
    <div className="blocker-heading"><div><span className="blocker-flag">阻止继续</span><h3>先处理以下 {rows.length} 项，再进入下一步</h3><p>选择一项进入对应处理区；真正的生成、采纳或确认操作只在处理区内进行。</p></div><span className="blocker-count">{rows.length} 项待办</span></div>
    <div className="blocker-list">{groups.map((group,groupIndex)=><section className="blocker-group" key={group.key}><h4>{group.title}<span>{group.rows.length} 项</span></h4>
      {group.rows.map((row,n)=><div className="blocker-row" key={row.key+'-'+n}><p>{row.detail||row.message}</p><div className="blocker-actions">
        {row.actions.map((action,index)=><button type="button" className={groupIndex===0&&n===0&&index===0?'primary':undefined} key={index} disabled={busy} onClick={()=>onResolve(action,row)}>{blockerActionLabel(action,row)} →</button>)}
        {!row.actions.length&&onRefresh&&<button type="button" disabled={busy} onClick={onRefresh}>重新载入处理入口</button>}
      </div></div>)}
    </section>)}</div>
  </section>;
}
