"""Task lifecycle and user-facing progress use the same shipped modules."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';import {build} from 'esbuild';
const built=await build({entryPoints:['src/task-progress-state.ts'],bundle:true,write:false,format:'esm',platform:'node'});
const m=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
const task=(id,status='running')=>({id,action:'organize',label:'整理资料',status,message:'已提交',created:'2026-10-04T14:00:00Z',completed_steps:0,stages:['ingest'],step_states:[{stage:'ingest',status:status==='running'?'running':'NOT_RUN'}]});
const snapshot=(tasks,extra={})=>({projectId:'P1',projectName:'合成项目',tasks,busy:false,uncertain:false,...extra});
'''


def test_failed_inline_prewrite_draft_does_not_block_its_retry_controls():
    run_ts(LOAD+r'''
      const running={...task('draft'),retry_input:{draft_request:{mode:'prewrite'}}};
      const failed={...running,status:'failed'};
      let s=m.reconcileProgress(m.emptyProgress,snapshot([running],{inlineDraft:true}),'draft');
      s=m.reconcileProgress(s,snapshot([failed],{inlineDraft:true}));
      assert.equal(s.expanded,false);assert.equal(s.entries[0].task.status,'failed');
      assert.equal(m.inlinePrewriteFailure(failed),true);
      assert.equal(m.taskGenerated(failed),false);
      const other=m.reconcileProgress(m.reconcileProgress(m.emptyProgress,snapshot([running]),'draft'),snapshot([failed]));
      assert.equal(other.expanded,true,'failures outside the active table must remain visible');
    ''')


def test_inline_business_decisions_do_not_reopen_a_panel_over_the_confirmation_table():
    run_ts(LOAD + r'''
      let s=m.reconcileProgress(m.emptyProgress,snapshot([{...task('check'),action:'prewrite'}],{inlineDraft:true}),'check');
      s=m.reconcileProgress(s,snapshot([{...task('check','awaiting_user'),action:'prewrite'}],{inlineDraft:true}));
      assert.equal(s.expanded,false,'the inline table already displays the decisions');
      assert.equal(s.entries[0].task.status,'awaiting_user','presentation must not mark a decision resolved');
      s={...s,expanded:true};assert.equal(m.reconcileProgress(s,snapshot([{...task('check','awaiting_user'),action:'prewrite'}],{inlineDraft:true})).expanded,true,'an explicit user expansion stays open');
      let other=m.reconcileProgress(m.emptyProgress,snapshot([{...task('else'),action:'prewrite'}]));
      other=m.reconcileProgress(other,snapshot([{...task('else','awaiting_user'),action:'prewrite'}]));
      assert.equal(other.expanded,true,'outside the active inline table, keep attention visible');
    ''')


def test_running_opens_once_and_historical_failures_do_not_interrupt():
    run_ts(LOAD + r'''
      const history=m.reconcileProgress(m.emptyProgress,snapshot([task('old','failed'),task('done','succeeded')]));
      assert.equal(history.entries.length,0);assert.equal(history.expanded,false);
      let s=m.reconcileProgress(history,snapshot([task('new')]));
      assert.equal(s.selected,'new');assert.equal(s.expanded,true);
      s={...s,expanded:false};
      s=m.reconcileProgress(s,snapshot([{...task('new'),message:'仍在处理中'}]));
      assert.equal(s.expanded,false,'polling must not undo minimize');
      s=m.reconcileProgress(s,snapshot([task('new','failed')]));
      assert.equal(s.expanded,true,'new failure makes the recovery controls visible');
      s={...s,expanded:false};
      assert.equal(m.reconcileProgress(s,snapshot([task('new','failed')])).expanded,false);
    ''')


def test_success_and_partial_keep_reviewable_result_without_reopening():
    run_ts(LOAD + r'''
      for(const status of ['succeeded','partial']){
        let s=m.reconcileProgress(m.emptyProgress,snapshot([task('T')]));
        s=m.reconcileProgress(s,snapshot([task('T',status)]));
        assert.equal(s.expanded,false);assert.equal(s.entries[0].task.status,status);
        s=m.reconcileProgress(s,snapshot([task('T')]));
        assert.equal(s.entries[0].task.status,status,'late running snapshot must not roll back completion');
        s=m.dismissProgress(s,'T');
        assert.equal(s.entries.length,0);
        assert.equal(m.reconcileProgress(s,snapshot([task('T')])).entries.length,0,'dismissed terminal task must not resurrect');
      }
      const fast=m.reconcileProgress(m.emptyProgress,snapshot([task('fast','succeeded')]),'fast');
      assert.equal(fast.entries.length,1,'fast submission still exposes its result');
      assert.equal(fast.expanded,false);
      const stopped=m.reconcileProgress(m.emptyProgress,snapshot([task('failed-fast','failed')]),'failed-fast');
      assert.equal(stopped.expanded,true);
    ''')


def test_projects_keep_independent_tasks_and_uncertain_state_does_not_finish_work():
    run_ts(LOAD + r'''
      let s=m.reconcileProgress(m.emptyProgress,snapshot([task('A')]));
      s=m.reconcileProgress(s,snapshot([task('B')],{projectId:'P2',projectName:'另一项目'}));
      assert.equal(s.entries.length,2);assert.equal(s.selected,'B');
      s=m.reconcileProgress(s,snapshot([task('A')],{uncertain:true}));
      assert.equal(s.entries.find(x=>x.task.id==='A').uncertain,true);
      assert.equal(s.entries.find(x=>x.task.id==='A').task.status,'running');
      assert.equal(s.selected,'B');
      assert.equal(m.dismissProgress(s,'A'),s,'running tasks cannot be dismissed');
      s=m.reconcileProgress(s,snapshot([task('A','cancelled')]));
      assert.equal(s.entries.find(x=>x.task.id==='B').task.status,'running');
      s=m.reconcileProgress(s,snapshot([task('A','cancelled'),task('C')]));
      assert.deepEqual(s.entries.map(x=>x.task.id).sort(),['B','C']);
    ''')


def test_elapsed_time_uses_server_timestamps_and_freezes_only_with_known_end():
    run_ts(LOAD + r'''
      const running=task('T');
      assert.equal(m.taskElapsed(running,Date.parse('2026-10-04T14:02:03Z')),'2 分 3 秒');
      assert.equal(m.taskElapsed({...running,created:undefined}),'耗时暂未取得');
      assert.equal(m.taskElapsed({...running,created:'bad'}),'耗时暂未取得');
      const done={...running,status:'succeeded',completed:'2026-10-04T14:00:42Z'};
      assert.equal(m.taskElapsed(done,Date.parse('2026-10-05T00:00:00Z')),'42 秒');
      assert.equal(m.taskElapsed({...done,completed:undefined}),'耗时暂未取得');
      assert.equal(m.taskCurrentStage({...running,step_states:[]}),'等待最新步骤状态');
      assert.equal(m.taskCurrentStage(running),'整理材料和目标');
    ''')


def test_rendered_progress_explains_real_steps_and_preserves_failure_details():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/task-progress.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const task={id:'T',label:'整理资料',status:'running',message:'正在整理资料',created:'2026-10-04T14:00:00Z',stages:['vision','ingest'],step_states:[{stage:'vision',status:'succeeded'},{stage:'ingest',status:'running'}],completed_steps:1};
      const entry={projectId:'P',projectName:'合成项目',task,busy:false,uncertain:false};
      const render=e=>renderToStaticMarkup(React.createElement(module.exports.ProgressContents,{entry:e,now:Date.parse('2026-10-04T14:00:35Z')}));
      const html=render(entry);
      for(const text of ['35 秒','整理材料和目标','分析待处理图片','aria-current="step"','aria-live="off"'])assert(html.includes(text),text);
      assert(!html.includes('50%'));assert(!html.includes('role="progressbar"'));
      const failed=render({...entry,task:{...task,status:'failed',message:'<script>来源无法读取</script>'},uncertain:true});
      assert(failed.includes('最新状态暂未取得'));assert(failed.includes('核对原任务范围'));assert(failed.includes('&lt;script&gt;'));
      assert(!failed.includes('<script>'));
      const cancelled=render({...entry,task:{...task,status:'cancelled'}});
      assert(cancelled.includes('已发送的请求可能仍在结束处理中并计费'));
    ''')
