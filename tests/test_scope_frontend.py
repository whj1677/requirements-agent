"""Exercise the scope draft and checkpoint transition from their TypeScript modules."""
import shutil
import subprocess
from pathlib import Path

import pytest


WEB = Path(__file__).resolve().parents[1] / "web"


def run_ts(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=WEB, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_scope_draft_respects_saved_values_explicit_absence_and_profile():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/scope-draft.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {scopeDraft,suggestedKeys,fieldStatus,isScopeFieldRequired,scopeSaveStates}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const p={product_context:{intent:'人工目标',current_state:'  '},product_context_proposal:{intent:'模型目标',current_state:'模型现状',users:'模型用户',preserve_scope:'模型保持',out_of_scope:'模型排除'},context_field_states:{preserve_scope:{status:'not_applicable'},out_of_scope:{status:'none'}}};
      const before=structuredClone(p);
      const draft=scopeDraft(p);
      assert.equal(draft.intent,'人工目标');
      assert.equal(draft.current_state,'模型现状');
      assert.equal(draft.users,'模型用户');
      assert.equal(draft.preserve_scope,'');
      assert.equal(draft.out_of_scope,'');
      assert.deepEqual(suggestedKeys(p,draft,p.context_field_states).sort(),['current_state','users']);
      assert.deepEqual(p,before,'draft helpers must not mutate project evidence');
      const newProduct={scenario:'new_product',dependencies:'unknown'};
      const independent={scenario:'new_module',dependencies:'independent'};
      const shared={scenario:'new_module',dependencies:'shared'};
      const modify={scenario:'modify_existing',dependencies:'unknown'};
      for(const profile of [newProduct,independent]){
        for(const key of ['current_state','preserve_scope']){
          assert.equal(isScopeFieldRequired(key,profile),false);
          assert.equal(fieldStatus(key,{}, {},profile),'not_applicable');
        }
        assert.equal(isScopeFieldRequired('priority',profile),false);
      }
      for(const profile of [shared,modify]){
        for(const key of ['current_state','preserve_scope']){
          assert.equal(isScopeFieldRequired(key,profile),true);
          assert.equal(fieldStatus(key,{}, {},profile),'unknown');
        }
      }
      assert.equal(fieldStatus('preserve_scope',{preserve_scope:'人工值'},{preserve_scope:{status:'none'}},shared),'none');
      const previous={product_context:{intent:'已保存原文'},product_context_proposal:{intent:'模型想改写',users:'模型新建议',priority:'模型优先级'},context_field_states:{intent:{status:'unknown'},users:{status:'unknown'},priority:{status:'not_applicable'}}};
      const shown=scopeDraft(previous),keys=['intent','users','priority'];
      assert.equal(shown.intent,'已保存原文');
      assert.equal(shown.users,'模型新建议');
      assert.equal(shown.priority,'');
      const draftStates=scopeSaveStates(previous,shown,previous.context_field_states,newProduct,keys);
      assert.equal(draftStates.users.status,'unknown','draft save must not accept AI suggestion');
      const confirmedStates=scopeSaveStates(previous,shown,previous.context_field_states,newProduct,keys,true);
      assert.equal(confirmedStates.users.status,'known','final review accepts visible suggestion');
      assert.equal(confirmedStates.intent.status,'unknown','saved unknown text remains unknown');
      assert.equal(confirmedStates.priority.status,'not_applicable');
      assert.equal(confirmedStates.intent.status,draftStates.intent.status);
    ''')


def test_checkpoint_transition_uses_fresh_version_and_blocks_failed_steps():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/checkpoint-transition.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {confirmCheckpoint}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const project=(revision,phase=1,state={available:true,missing:[],content_hash:'fresh-hash'})=>({revision,product_flow:{[phase]:state}});
      let calls=[];
      const args={phase:1,revision:4,save:async()=>{calls.push('save');return project(5);},read:async()=>{calls.push('read');return project(5);},check:async(rev,hash)=>{calls.push(['check',rev,hash]);}};
      assert.equal(await confirmCheckpoint(args),2);
      assert.deepEqual(calls,['save','read',['check',5,'fresh-hash']]);
      calls=[];
      assert.equal(await confirmCheckpoint({...args,phase:2,save:undefined,revision:7,read:async()=>{calls.push('read');return project(7,2);}}),4);
      assert.deepEqual(calls,['read',['check',7,'fresh-hash']]);
      calls=[];
      await assert.rejects(()=>confirmCheckpoint({...args,save:async()=>{calls.push('save');throw Error('save failed');}}),/save failed/);
      assert.deepEqual(calls,['save']);
      calls=[];
      await assert.rejects(()=>confirmCheckpoint({...args,read:async()=>{calls.push('read');return project(6);}}),/版本/);
      assert.deepEqual(calls,['save','read']);
      for(const state of [{available:true,missing:['缺少使用者'],content_hash:'x'},{available:false,missing:[],content_hash:'x'}]){
        calls=[];
        await assert.rejects(()=>confirmCheckpoint({...args,save:undefined,read:async()=>{calls.push('read');return project(4,1,state);},blocked:p=>{calls.push(['blocked',p.revision]);}}));
        assert.deepEqual(calls,['read',['blocked',4]]);
      }
      calls=[];
      await assert.rejects(()=>confirmCheckpoint({...args,check:async(rev,hash)=>{calls.push(['check',rev,hash]);throw Error('check failed');}}),/check failed/);
      assert.deepEqual(calls,['save','read',['check',5,'fresh-hash']]);
    ''')


def test_rendered_scope_shows_ai_entry_profile_and_explicit_decisions():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/product-flow.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const p={id:'P-scope',revision:4,messages:[],sources:[],items:[],questions:[],product_context:{intent:'人工目标',current_state:'人工现状',priority:'旧优先级原文'},product_context_proposal:{intent:'AI 的不同目标',users:'建议使用者',preserve_scope:'不应恢复的保持项',out_of_scope:'不应恢复的排除项'},context_field_states:{preserve_scope:{status:'not_applicable'},out_of_scope:{status:'none'},priority:{status:'not_applicable'}},scope_profile:{scenario:'new_product',dependencies:'unknown'},product_flow:[{}, {available:true,missing:[],complete:false}],documents:{}};
      const html=renderToStaticMarkup(React.createElement(module.exports.ProductCheckpoint,{p,phase:1,busy:false,mutate:()=>assert.fail('render must not write'),go:()=>{},onAnalyze:()=>{}}));
      for(const label of ['一键 AI 填充','需求场景','本次改动','可选','全新产品','人工目标','人工现状','AI 的不同目标','建议使用者','不适用','已确认没有额外约束','最新分析提出了不同建议，待核对'])assert(html.includes(label),label);
      assert(!html.includes('不应恢复的保持项'));
      assert(!html.includes('不应恢复的排除项'));
      assert(!html.includes('旧优先级原文'),'explicit NA must not render retained text as a fact');
      for(const label of ['不适用','已确认没有额外约束'])assert(!html.includes(label+'<small>进入下一步前需核对'));
      assert.equal(p.product_context.intent,'人工目标');
    ''')


def test_scope_save_preserves_literal_source_and_revision_boundaries():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/scope-save.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {saveScopeDraft}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const p={revision:3,product_context:{},product_flow:[{complete:false}]};
      const values={intent:'人工目标',users:'AI 预填使用者',preserve_scope:'保留的人工原文',out_of_scope:''};
      const common={p,revision:3,values,ids:['REQ-1'],profile:{scenario:'new_product',dependencies:'unknown'},states:{preserve_scope:{status:'not_applicable'},out_of_scope:{status:'none'}},editedKeys:['intent'],all:false,proof:{intent:'old'}};
      let calls=[],written=[];
      const mutate=async(path,body)=>{calls.push({path,body});return {revision:path==='/intake/direct'?4:5,intake:{text:'literal'}};};
      const result=await saveScopeDraft({...common,mutate,read:async()=>assert.fail('no read'),onWritten:p=>written.push(p.revision)});
      assert.equal(result.revision,5);
      assert.deepEqual(calls.map(x=>x.path),['/intake/direct','/product-context']);
      assert.deepEqual(calls[0].body.values,{intent:'人工目标'});
      assert(!('scope_ids' in calls[0].body));
      assert.equal(calls[1].body.expected_revision,4);
      assert.deepEqual(calls[1].body.scope_ids,['REQ-1']);
      assert.deepEqual(calls[1].body.values,values);
      assert.deepEqual(written,[4,5]);
      calls=[];
      await saveScopeDraft({...common,p:{...p,intake:{text:'existing'}},mutate,read:async()=>assert.fail('no read')});
      assert.deepEqual(calls.map(x=>x.path),['/product-context']);
      assert.equal(calls[0].body.expected_revision,3);
      calls=[];written=[];
      await assert.rejects(()=>saveScopeDraft({...common,mutate:async(path,body)=>{calls.push(path);throw Error('direct failed');},read:async()=>assert.fail('no read'),onWritten:p=>written.push(p.revision)}),/direct failed/);
      assert.deepEqual(calls,['/intake/direct']);assert.deepEqual(written,[]);
      calls=[];written=[];
      await assert.rejects(()=>saveScopeDraft({...common,mutate:async(path)=>{calls.push(path);if(path==='/intake/direct')return {revision:4};throw Error('context failed');},read:async()=>assert.fail('no read'),onWritten:p=>written.push(p.revision)}),/context failed/);
      assert.deepEqual(calls,['/intake/direct','/product-context']);assert.deepEqual(written,[4]);
    ''')


def test_scope_save_checks_legacy_material_without_inventing_raw_text():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/scope-save.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {saveScopeDraft}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const p={revision:8,sources:[{id:'S1'}],product_flow:[{complete:false}],product_context:{}};
      let written=[];
      const args={p,revision:8,values:{users:'AI 建议'},ids:[],profile:{scenario:'new_module',dependencies:'independent'},states:{},editedKeys:[],all:true,proof:{},onWritten:p=>written.push(p.revision)};
      let calls=[];
      const mutate=async(path,body)=>{calls.push({path,body});return path==='/product-context'?{revision:9}:[{complete:true}];};
      const read=async()=>{calls.push({path:'read'});return calls.filter(x=>x.path==='read').length===1?{revision:9,product_flow:[{available:true,missing:[],content_hash:'fresh-hash'}]}:{revision:10,product_flow:[{complete:true}]};};
      assert.equal((await saveScopeDraft({...args,mutate,read})).revision,10);
      assert.deepEqual(calls.map(x=>x.path),['/product-context','read','/stage-checks/1','read']);
      assert.deepEqual(calls[0].body.values,{users:'AI 建议'});
      assert.deepEqual(calls[2].body,{expected_revision:9,expected_hash:'fresh-hash'});
      assert.deepEqual(written,[9,10]);
      calls=[];written=[];
      await assert.rejects(()=>saveScopeDraft({...args,mutate,read:async()=>{calls.push({path:'read'});return {revision:10,product_flow:[{missing:[],content_hash:'stale'}]};}}),/版本/);
      assert.deepEqual(calls.map(x=>x.path),['/product-context','read']);
      assert.deepEqual(written,[9]);
      calls=[];written=[];
      await assert.rejects(()=>saveScopeDraft({...args,mutate,read:async()=>{calls.push({path:'read'});return calls.filter(x=>x.path==='read').length===1?{revision:9,product_flow:[{missing:[],content_hash:'fresh-hash'}]}:{revision:11,product_flow:[{complete:true}]};}}),/版本/);
      assert.deepEqual(calls.map(x=>x.path),['/product-context','read','/stage-checks/1','read']);
      assert.deepEqual(written,[9]);
    ''')


def test_scope_save_then_checkpoint_confirms_fresh_scope_revision():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const load=async path=>{const built=await build({entryPoints:[path],bundle:true,write:false,format:'esm',platform:'node'});return import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));};
      const {saveScopeDraft}=await load('src/scope-save.ts');
      const {confirmCheckpoint}=await load('src/checkpoint-transition.ts');
      const p={revision:8,product_flow:[{complete:false}]},calls=[];
      const mutate=async(path,body)=>{calls.push([path,body.expected_revision]);return path==='/product-context'?{revision:9}:[{complete:true}];};
      let n=0;
      const read=async()=>{calls.push(['read']);return ++n===1?{revision:9,product_flow:[{missing:[],content_hash:'intake-hash'}]}:n===2?{revision:10,product_flow:[{complete:true}]}:{revision:10,product_flow:[{}, {available:true,missing:[],content_hash:'scope-hash'}]};};
      const save=()=>saveScopeDraft({p,revision:8,values:{users:'AI 建议'},ids:[],profile:{scenario:'new_module',dependencies:'independent'},states:{},editedKeys:[],all:true,proof:{},mutate,read});
      const next=await confirmCheckpoint({phase:1,revision:8,save,read,check:async(rev,hash)=>{calls.push(['check',rev,hash]);}});
      assert.equal(next,2);
      assert.deepEqual(calls,[['/product-context',8],['read'],['/stage-checks/1',9],['read'],['read'],['check',10,'scope-hash']]);
    ''')
