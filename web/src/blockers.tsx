import React from 'react';
import type { Blocker, BlockerAction } from './api';
import { blockerGroups } from './blocker-navigation';
import { publicBlocker } from './workflow-labels';

export function BlockerPanel({id,rows,busy,onResolve,onRefresh}:{id:string;rows:Blocker[];busy:boolean;onResolve:(action:BlockerAction,row:Blocker)=>void;onRefresh?:()=>void}) {
  if(!rows.length)return null;
  const displayed=rows.map(publicBlocker),groups=blockerGroups(displayed),first=displayed.find(row=>row.actions.length);
  return <section id={id} className="blocker-panel" aria-label="当前阶段阻塞清单" tabIndex={-1}>
    <div className="blocker-heading"><div><h3>继续前还需处理 {rows.length} 项</h3><p>按需求或问题查看缺项，点击右侧按钮直接处理。保存后清单会更新。</p></div>
      {first&&<button type="button" disabled={busy} onClick={()=>onResolve(first.actions[0],first)}>从第一项开始处理 →</button>}
    </div>
    <div className="blocker-list">{groups.map(group=><section className="blocker-group" key={group.key}><h4>{group.title}<span>{group.rows.length} 项</span></h4>
      {group.rows.map((row,n)=><div className="blocker-row" key={row.key+'-'+n}><p>{row.detail||row.message}</p><div className="blocker-actions">
        {row.actions.map((action,index)=><button type="button" key={index} disabled={busy} onClick={()=>onResolve(action,row)}>{action.label} →</button>)}
        {!row.actions.length&&onRefresh&&<button type="button" disabled={busy} onClick={onRefresh}>重新载入处理入口</button>}
      </div></div>)}
    </section>)}</div>
  </section>;
}
