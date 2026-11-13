import React, { useEffect, useRef, useState } from 'react';
import { ApiError, JsonObject, Project, ProjectMemoryEntry, ProjectMemoryMode, ProjectMemorySourceRef, ProjectMemoryTrace, Run, UserTask } from './api';
import { useDirty } from './editing';
import './project-memory.css';

const memoryMode=(value:unknown):ProjectMemoryMode=>value==='langmem'?'langmem':'off';
const stageNames:Record<string,string>={interview:'需求对话',ingest:'资料整理',clarify:'需求澄清',change:'需求调整',review:'文档复核',prd:'文档生成'};
type MemoryRun={run:Run;trace:ProjectMemoryTrace};
type SourceProps={sources:JsonObject[];onSourceRefs:(refs:ProjectMemorySourceRef[])=>void};

function readMemoryRuns(task:UserTask,runs:Run[]):MemoryRun[]{
  const ids=Array.isArray(task.run_ids)?[...new Set(task.run_ids)]:[];
  return ids.flatMap(id=>{
    const run=runs.find(item=>item.id===id);
    if(!run||!stageNames[run.stage])return [];
    const trace=run.memory_trace;
    if(!trace||trace.mode!=='langmem'||trace.protocol!=='project-langmem-1'||!['preparing','ready','failed'].includes(trace.status))return [];
    return [{run,trace}];
  });
}

function sourceRefs(value:unknown):ProjectMemorySourceRef[]{
  if(!Array.isArray(value))return [];
  return value.filter(ref=>ref&&typeof ref.source_id==='string'&&ref.source_id&&typeof ref.excerpt_id==='string'&&ref.excerpt_id);
}

function canViewSource(sources:JsonObject[],ref:ProjectMemorySourceRef){
  return sources.some(source=>source.id===ref.source_id&&Array.isArray(source.excerpts)&&source.excerpts.some((excerpt:JsonObject)=>excerpt.id===ref.excerpt_id));
}

function SourceLinks({refs,sources,onSourceRefs}:{refs:ProjectMemorySourceRef[]}&SourceProps){
  const unavailable=refs.some(ref=>!canViewSource(sources,ref));
  return <div className="project-memory-source-actions">
    {refs.map((ref,index)=>{
      const available=canViewSource(sources,ref);
      return available?<button type="button" key={ref.source_id+':'+ref.excerpt_id+':'+index} onClick={()=>onSourceRefs([ref])}>{refs.length>1?`查看来源原文 ${index+1}`:'查看来源原文'}</button>:null;
    })}
    {(unavailable||!refs.length)&&<p>原资料目前不可查看</p>}
  </div>;
}

function traceEntries(trace:ProjectMemoryTrace):ProjectMemoryEntry[]{
  return (Array.isArray(trace.entries)?trace.entries:[]).filter(entry=>entry&&typeof entry.statement==='string');
}

function traceSummary(trace:ProjectMemoryTrace,current:boolean){
  const subject=current?'本轮':'该任务';
  if(trace.status==='preparing')return current?'正在准备本轮参考信息':'该任务正在准备参考信息';
  if(trace.status==='failed')return current?'本轮参考信息准备失败':'该任务参考信息准备失败';
  if(trace.consumed!==true)return current?'信息已提取，尚未确认本轮使用':'信息已提取，尚未确认该任务是否使用';
  if(trace.status==='ready'&&Array.isArray(trace.entries)&&trace.entries.length===0)return subject+'没有可展示或沿用的记录';
  if(trace.reused===true)return subject+'参考了已记录信息';
  if(trace.reused===false)return current?'从原资料提取并用于本轮参考':'该任务从原资料提取并用于参考';
  return subject+'处理参考了已记录的信息';
}

function traceExplanation(trace:ProjectMemoryTrace,current:boolean){
  const subject=current?'本轮':'该任务';
  if(trace.status==='preparing')return '准备结果尚未完成；主任务状态请以任务记录为准。';
  if(trace.status==='failed')return '准备工作未成功，这不代表主任务成功或失败；请以任务记录为准，不会因此自动重试。';
  if(trace.consumed!==true)return current?'已提取信息，尚未确认本轮使用。':'已提取信息，尚未确认该任务是否使用。';
  if(trace.status==='ready'&&Array.isArray(trace.entries)&&trace.entries.length===0)return subject+'没有可展示或沿用的记录；主任务状态请以任务记录为准。';
  return '这里表示'+subject+'处理参考了已记录的信息。';
}

function MemoryTraceDetails({run,trace,sources,onSourceRefs,current,title}:MemoryRun&SourceProps&{current:boolean;title?:string}){
  const items=traceEntries(trace),limitations=Array.isArray(trace.limitations)&&trace.limitations.length>0;
  return <details className="project-memory-trace">
    <summary><span>{title?title+' · ':''}项目记忆{current?' · 本轮参考':''}</span><small>{traceSummary(trace,current)}</small></summary>
    <div className="project-memory-trace-body">
      <p>{traceExplanation(trace,current)}</p>
      <p className="project-memory-caution">项目记忆记录不代表正文已落实或经过业务批准。</p>
      {items.length>0?<ol className="project-memory-entries">
        {items.map((entry,index)=>
          <li key={entry.id||index}>
            <blockquote>{entry.statement}</blockquote>
            {entry.scope&&<p className="project-memory-entry-scope">适用范围：{entry.scope}</p>}
            <small className="project-memory-entry-note">原文摘录</small>
            <SourceLinks refs={sourceRefs(entry.source_refs)} sources={sources} onSourceRefs={onSourceRefs}/>
          </li>)}
        </ol>:trace.status==='ready'?<>
          <p>当前记录没有可展示的原文摘录。</p>
          <SourceLinks refs={sourceRefs(trace.source_refs)} sources={sources} onSourceRefs={onSourceRefs}/>
        </>:null}
      {limitations&&<p className="project-memory-limit">此处仅展示本次记录的参考范围。</p>}
    </div>
  </details>;
}

export function ProjectMemoryMessageTrace({message,tasks,runs,sources,onSourceRefs}:{message:JsonObject;tasks:UserTask[];runs:Run[]}&SourceProps){
  if(message.role!=='assistant'||message.stage!=='interview'||typeof message.user_task_id!=='string')return null;
  const task=tasks.find(item=>item.id===message.user_task_id);
  if(!task)return null;
  const items=readMemoryRuns(task,runs).filter(item=>item.run.stage==='interview');
  if(!items.length)return null;
  return <>
    {items.map(item=><MemoryTraceDetails key={item.run.id} {...item} current sources={sources} onSourceRefs={onSourceRefs}/>)}
  </>;
}

export function ProjectMemoryHistory({tasks,runs,sources,onSourceRefs}:{tasks:UserTask[];runs:Run[]}&SourceProps){
  const groups=tasks.map(task=>({task,items:readMemoryRuns(task,runs).filter(item=>item.run.stage!=='interview')})).filter(group=>group.items.length>0);
  const count=groups.reduce((total,group)=>total+group.items.length,0);
  if(!count)return null;
  return <details className="project-memory-history">
    <summary><span>已记录的参考过程</span><small>{count} 段记录</small></summary>
    <div className="project-memory-history-body">
      <p>以下记录属于所列原任务，不代表当前这一轮。</p>
      {groups.map(({task,items})=>
        <section className="project-memory-task" key={task.id}>
          <h4>{task.label||'未命名任务'}</h4>
          {items.map(item=><MemoryTraceDetails key={item.run.id} {...item} current={false} title={stageNames[item.run.stage]} sources={sources} onSourceRefs={onSourceRefs}/>)}
        </section>)}
    </div>
  </details>;
}

function saveError(error:unknown){
  if(error instanceof ApiError&&error.status===409){
    if(error.code==='PROJECT_BUSY')return '项目已有任务排队或执行中，设置尚未保存。你的选择已保留，请等任务结束后重试。';
    if(error.code==='STALE_REVISION')return '项目内容已更新，设置尚未保存。你的选择已保留，请核对最新内容后再次点击保存。';
    return '项目状态已变化，设置尚未保存。你的选择已保留，请刷新核对后重试。';
  }
  return '尚未确认保存结果，选择已保留；先刷新核对当前已保存状态，再决定是否重试。';
}

export function ProjectMemorySettings({project,disabled,mutate}:{project:Project;disabled:boolean;mutate:(path:string,body:JsonObject,method?:string)=>Promise<any>}){
  const confirmed=memoryMode(project.project_memory_mode);
  const [choice,setChoice]=useState<ProjectMemoryMode>(confirmed);
  const [savedChoice,setSavedChoice]=useState<ProjectMemoryMode>(confirmed);
  const [saving,setSaving]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const lock=useRef(false),server=useRef({id:project.id,mode:confirmed});
  useEffect(()=>{
    const previous=server.current;
    if(previous.id===project.id&&previous.mode===confirmed)return;
    server.current={id:project.id,mode:confirmed};
    setSavedChoice(confirmed);
    setChoice(current=>previous.id!==project.id||current===previous.mode?confirmed:current);
  },[project.id,confirmed]);
  const dirty=choice!==savedChoice;
  async function saveSettings(){
    if(disabled)throw Error('当前项目正在处理其他操作，项目记忆设置尚未保存；选择已保留，请稍后重试。');
    if(lock.current||!dirty)return;
    lock.current=true;setSaving(true);
    setError('');setNotice('');
    try{
      const saved=await mutate('',{expected_revision:project.revision,project_memory_mode:choice},'PATCH') as Project;
      const next=memoryMode(saved.project_memory_mode);
      server.current={id:project.id,mode:next};
      setSavedChoice(next);
      setChoice(next);
      setNotice(next==='langmem'?'本项目已开启项目记忆。保存设置不会开始模型任务。':'本项目已关闭项目记忆，历史记录仍保留。保存设置不会开始模型任务。');
      markSaved();
    }catch(e){setError(saveError(e));throw e;}finally{lock.current=false;setSaving(false);}
  }
  const markSaved=useDirty(project.id+'-project-memory',dirty,'项目记忆设置',saveSettings,()=>{setChoice(savedChoice);setError('');setNotice('');});
  return <section className="connected-card project-memory-settings" aria-labelledby={'project-memory-'+project.id}>
    <h3 id={'project-memory-'+project.id}>项目记忆（试用）</h3>
    <p>开启后，支持的任务可参考此前已记录的信息。可在聊天记录中展开查看本轮参考的原文摘录。</p>
    <label className="project-memory-choice">
      <input type="checkbox" checked={choice==='langmem'} disabled={disabled||saving} onChange={event=>{setChoice(event.target.checked?'langmem':'off');setError('');setNotice('');}}/>
      为此项目启用项目记忆
    </label>
    <small>当前已保存：{savedChoice==='langmem'?'已开启':'已关闭'}。保存只修改本项目，不调用模型，也不会自动提取；关闭后历史记录保留。</small>
    <div className="project-memory-save">
      <button type="button" disabled={disabled||saving||!dirty} onClick={()=>void saveSettings().catch(()=>{})}>{saving?'正在保存…':'保存项目记忆设置'}</button>
    </div>
    {dirty&&!error&&<p className="project-memory-feedback" role="status">新选择尚未保存。</p>}
    {notice&&<p className="project-memory-feedback" role="status">{notice}</p>}
    {error&&<p className="project-memory-feedback connected-error" role="alert">{error}</p>}
  </section>;
}
