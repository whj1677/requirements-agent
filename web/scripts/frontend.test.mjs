import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source=fs.readFileSync(new URL('../src/draft-storage.ts',import.meta.url),'utf8');
const {outputText}=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ES2022,target:ts.ScriptTarget.ES2022}});
const {session,createDraftWriter,retainedWorkspaces,taskFingerprint}=await import('data:text/javascript;base64,'+Buffer.from(outputText).toString('base64'));
globalThis.window={sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}}};

test('a burst of edits is serialized and stored only after the pause',async()=>{
  let writes=0,serializations=0;window.sessionStorage.setItem=()=>{writes++};
  const writer=createDraftWriter(()=>assert.fail('unexpected storage failure'),20);
  for(let n=0;n<100;n++)writer.schedule(()=>({large:{toJSON(){serializations++;return {text:'last input'};}}}));
  assert.equal(writes,0);assert.equal(serializations,0);
  await new Promise(resolve=>setTimeout(resolve,40));
  assert.equal(writes,1);assert.equal(serializations,1);assert.equal(writer.flush(),true);
});
test('explicit flush saves the latest input and cancel cannot resurrect discarded drafts',()=>{
  const saved=[];window.sessionStorage.setItem=(key,value)=>saved.push([key,value]);
  const writer=createDraftWriter(()=>assert.fail('unexpected failure'));
  writer.schedule(()=>({answer:'old'}));writer.schedule(()=>({answer:'new'}));assert.equal(writer.flush(),true);
  assert.deepEqual(saved,[['answer','"new"']]);writer.schedule(()=>({answer:'discarded'}));writer.cancel();writer.flush();assert.equal(saved.length,1);
});
test('blocked and full storage retains the latest text without interrupting edits',()=>{
  let failures=0;window.sessionStorage.setItem=()=>{throw Error('QuotaExceededError')};
  window.sessionStorage.getItem=()=>{throw Error('SecurityError')};
  const writer=createDraftWriter(()=>failures++);
  writer.schedule(()=>({isolated:'manual input'}));assert.equal(writer.flush(),false);
  assert.equal(failures,1);assert.equal(session.getItem('isolated'),'"manual input"');
  session.removeItem('isolated');assert.equal(session.getItem('isolated'),null);
});
test('LRU bounds clean workspaces while drafts and running tasks remain pinned',()=>{
  const visited=Array.from({length:10},(_,i)=>'P-'+i);
  assert.deepEqual(retainedWorkspaces(visited,'P-9',new Set()),['P-8','P-9']);
  assert.deepEqual(retainedWorkspaces(visited,'P-9',new Set(['P-1'])),['P-1','P-8','P-9']);
  assert.equal(retainedWorkspaces(visited,'P-0',new Set()).length,2);
});
test('task progress and terminal states invalidate a lightweight polling snapshot',()=>{
  const task={id:'T1',status:'running',completed_steps:0,calls:1,step_states:[{stage:'review',status:'running'}]};
  assert.equal(taskFingerprint([task]),taskFingerprint([{...task}]));
  assert.notEqual(taskFingerprint([task]),taskFingerprint([{...task,completed_steps:1}]));
  assert.notEqual(taskFingerprint([task]),taskFingerprint([{...task,status:'failed'}]));
  assert.notEqual(taskFingerprint([task]),taskFingerprint([{...task,step_states:[{stage:'review',status:'succeeded'}]}]));
});
