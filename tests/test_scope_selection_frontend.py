"""Persistent scope and transient batch selection must have separate lifetimes."""
from tests.test_scope_frontend import run_ts


def test_range_save_is_a_prerequisite_without_selecting_or_adopting_anything():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/scope-selection.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const m=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const item=(id,extra={})=>({id,kind:'requirement',applies_to:'to_be',selection_status:'candidate',...extra});
      const p={product_context:{scope_ids:['R1','R2','R3','R4']},items:[item('R1'),item('R2',{selection_status:'selected'}),item('R3',{selection_status:'deferred'}),item('R4',{target_item_id:'R1'}),item('outside')]};
      const before=JSON.stringify(p),ids=[...p.product_context.scope_ids];
      const s=m.scopeSelection(p,ids,false);
      assert.equal(s.ready,true);assert.equal(s.adopted,1);assert.deepEqual(s.eligible,['R1']);
      assert.deepEqual(m.retainBatchSelection([],s.eligible,true,s.ready),[],'saved business scope is not a temporary selection');
      assert.equal(m.scopeSelection(p,ids,true).ready,false,'unsaved edits block adoption');
      assert.equal(m.scopeSelection(p,['R1'],false).ready,false,'new range must first be saved');
      assert.equal(m.scopeSelection({...p,product_context:{scope_ids:[]}},[],false).ready,false);
      assert.equal(m.scopeSelection({...p,product_context:{scope_ids:['gone']}},['gone'],false).ready,false);
      assert.equal(JSON.stringify(p),before,'UI readiness never mutates saved scope or adoption');
    ''')


def test_batch_selection_clears_on_leave_and_prunes_adopted_removed_or_changed_items():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/scope-selection.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const m=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      let selected=['R1','R2'];
      assert.deepEqual(m.retainBatchSelection(selected,['R1'],true,true),['R1']);
      assert.deepEqual(m.retainBatchSelection(selected,['R1','R2'],true,false),[]);
      selected=m.retainBatchSelection(selected,['R1','R2'],false,true);
      assert.deepEqual(selected,[]);
      assert.deepEqual(m.retainBatchSelection(selected,['R1','R2'],true,true),[],'returning does not restore old selections');
    ''')


def test_failed_generation_explains_recovery_with_technical_details_collapsed():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/task-failure.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const task={status:'failed',error:'SEMANTIC_BLOCKED',message:"格式修复改变了已有业务内容；[['proposals','TMP-example','statement']] <script>"};
      const failure=module.exports.taskFailure(task);
      assert(failure.summary.includes('改写了需求文本'));assert(failure.next.includes('现有需求保留'));
      const html=renderToStaticMarkup(React.createElement(module.exports.TaskFailureNotice,{task}));
      assert(html.includes('<details><summary>查看本次失败详情</summary>'));
      assert(!html.includes('<details open'));assert(!html.includes('<script>'));assert(html.includes('&lt;script&gt;'));
      assert(html.indexOf('TMP-example')>html.indexOf('<details>'),'technical path belongs inside the collapsed details');
      assert.equal(module.exports.taskFailure({...task,status:'succeeded'}),null);
      assert.equal(module.exports.taskFailure({...task,error:'HTTP_ERROR'}),null);
      const statusBuilt=await build({entryPoints:['src/guided.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
      const statusModule={exports:{}};new Function('require','module','exports',statusBuilt.outputFiles[0].text)(require,statusModule,statusModule.exports);
      const stale=renderToStaticMarkup(React.createElement(statusModule.exports.TaskStatus,{task:{...task,label:'整理当前信息',calls:2,run_ids:[]},uncertain:true,onRetry:()=>{},onCancel:()=>{},onRefresh:()=>{}}));
      assert(stale.includes('下方为此前记录'));
      assert(stale.includes('刷新任务状态'));
      assert(stale.includes('<button disabled="">核对后重试</button>'),'stale failure cannot be retried before refreshing');
      for(const message of ['本次没有对标任务，不能写入对标结果','普通整理包含未请求的对标内容，本次结果未写入；无需补充竞品资料。']){
        const mismatch={...task,action:'organize',error:'SCHEMA_INVALID',message,label:'整理当前信息',calls:3,run_ids:[]};
        const notice=module.exports.taskFailure(mismatch);
        assert(notice.summary.includes('本次草稿未生成'));
        assert(notice.next.includes('无需补充竞品信息')&&notice.next.includes('重新生成会再次调用模型'));
        const view=renderToStaticMarkup(React.createElement(statusModule.exports.TaskStatus,{task:mismatch,onOpenDraft:()=>assert.fail('render must not mutate'),onRetry:()=>{},onCancel:()=>{}}));
        assert(view.includes('前往需求草稿'));
        assert(!view.includes('查看分析结果'),'failure does not imply a newly generated draft');
      }
    ''')


def test_duplicate_recovery_and_historical_failure_are_distinct_in_ui():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      async function load(path){const built=await build({entryPoints:[path],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});const m={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,m,m.exports);return m.exports;}
      const failure=await load('src/task-failure.tsx'),status=await load('src/guided.tsx'),progress=await load('src/task-progress-state.ts');
      const task={id:'T',action:'organize',status:'failed',error:'SEMANTIC_BLOCKED',message:'模型 JSON 有重复键，无法证明格式修复保持原义',label:'整理当前信息',calls:1,run_ids:['R']};
      assert(failure.canRecoverDraft(task));
      assert(!failure.canRecoverDraft({...task,retry_input:{benchmark:{target:'竞品'}}}));
      assert(!failure.canRecoverDraft({...task,status:'cancelled'}));
      const callbacks={onRecover:()=>assert.fail('render never runs recovery'),onCancel:()=>{},onRetry:()=>{}};
      const html=renderToStaticMarkup(React.createElement(status.TaskStatus,{task,...callbacks}));
      assert(html.includes('恢复草稿（不调用 AI）'));assert(html.includes('不用重新描述'));
      const restored={...task,local_recovery:{id:'L',candidate_ids:['I'],external_model_calls:0}};
      assert(!failure.canRecoverDraft(restored));assert.equal(failure.taskFailure(restored),null);
      const view=renderToStaticMarkup(React.createElement(status.TaskStatus,{task:restored,...callbacks}));
      assert(view.includes('已恢复待核对草稿')&&view.includes('原 AI 请求仍记录为失败'));
      assert(!view.includes('核对后重试'));assert(!view.includes('分析未成功'));
      assert(progress.taskGenerated(restored)&&!progress.taskNeedsAttention(restored));
      assert(progress.taskCurrentStage(restored).includes('未重新调用 AI'));
    ''')
