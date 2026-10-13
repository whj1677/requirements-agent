"""The refinement queue groups real backend blockers without creating obligations."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
  import assert from 'node:assert/strict'; import {build} from 'esbuild';
  const built=await build({entryPoints:['src/pm-prototype/refine-tasks.ts'],bundle:true,write:false,format:'esm',platform:'node'});
  const {buildRefineTasks,taskForAction,nextRefineTask}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
  const row=(key,actions)=>({key,code:key,title:key,message:key,actions});
  const action=(kind,extra={})=>({kind,label:kind,...extra});
  const project=(rows)=>({items:[],questions:[],product_flow:rows.map(blockers=>({blockers}))});
'''


def test_queue_preserves_all_real_actions_grouped_by_identity_without_mutation():
    run_ts(LOAD + r'''
      const p=project([
        [row('context-users',[action('context',{field:'users'})]),row('scope-adopt',[action('requirement',{item_id:'REQ-A'}),action('requirement',{item_id:'REQ-B'})])],
        [row('question',[action('question',{question_id:'Q-1'})]),row('scope-empty',[action('scope')])],
        [row('answer',[action('answer',{question_id:'Q-2'})]),row('behavior',[action('behavior',{item_id:'REQ-A',fields:['actor']})]),row('acceptance',[action('add_acceptance',{item_id:'REQ-A'})])],
      ]);
      p.items=[{id:'REQ-A',title:'云平台告警'},{id:'REQ-B',title:'收益统计'}];
      p.questions=[{id:'Q-1',question:'告警依据？',decision_group_id:'G-1'},{id:'Q-2',question:'告警边界？',decision_group_id:'G-1'}, {id:'Q-optional',question:'可选追问',blocking:false}];
      const before=JSON.stringify(p),tasks=buildRefineTasks(p);
      assert.deepEqual(tasks.map(t=>t.id),['question-group:G-1','context:users','scope','item:REQ-A','item:REQ-B']);
      assert.deepEqual(tasks[0].actions.map(a=>a.question_id),['Q-1','Q-2']);
      assert.equal(tasks[0].blockers.length,2);
      assert.deepEqual(tasks.find(t=>t.itemId==='REQ-A').actions.map(a=>a.kind),['requirement','behavior','add_acceptance']);
      assert.equal(tasks.find(t=>t.itemId==='REQ-A').title,'云平台告警');
      assert.equal(JSON.stringify(p),before,'queue creation must not change approvals, answers or navigation');
      assert.equal(taskForAction(p,action('answer',{question_id:'Q-2'})).id,tasks[0].id);
      assert.equal(taskForAction(p,action('item',{item_id:'REQ-A'})).id,'item:REQ-A');
    ''')


def test_acceptance_targets_actual_candidates_and_retains_multiple_navigation_choices():
    run_ts(LOAD + r'''
      const acceptance=action('acceptance',{item_id:'REQ-A'});
      const p=project([[],[],[row('acceptance',[acceptance])]]);
      p.items=[{id:'REQ-A',kind:'requirement',title:'云平台告警'},
        {id:'AC-A',kind:'acceptance',title:'告警来源可追溯',applies_to:'to_be',selection_status:'candidate',related_refs:['REQ-A']},
        {id:'AC-B',kind:'acceptance',title:'告警边界可验证',applies_to:'to_be',selection_status:'candidate',related_refs:['REQ-A']},
        {id:'AC-other',kind:'acceptance',applies_to:'to_be',selection_status:'candidate',related_refs:['REQ-B']}];
      const tasks=buildRefineTasks(p);
      assert.deepEqual(tasks.map(t=>t.itemId),['AC-A','AC-B']);
      assert.deepEqual(tasks.map(t=>t.action.item_id),['AC-A','AC-B']);
      assert(tasks.every(t=>t.actions.some(a=>a.kind==='acceptance'&&a.item_id==='REQ-A')));
      assert.equal(taskForAction(p,acceptance).itemId,'AC-A');
      assert.equal(taskForAction(p,action('item',{item_id:'AC-B'})).id,tasks[1].id);
      const stale=taskForAction(p,action('acceptance',{item_id:'missing'}));
      assert.equal(stale.itemId,undefined,'do not mistake a requirement ID for an acceptance candidate');
    ''')


def test_optional_questions_do_not_become_required_and_later_document_gates_stay_out():
    run_ts(LOAD + r'''
      const p=project([[],[],[],[row('document',[action('document',{document:'prd'})])]]);
      p.questions=[{id:'Q-opt',status:'open',blocking:false},{id:'Q-old',status:'answered',blocking:true}];
      p.product_context={};
      assert.deepEqual(buildRefineTasks(p),[],'empty required queue permits review without demanding every optional question or blank field');
      p.product_flow[0].blockers=[row('sources',[action('source')])];
      assert.deepEqual(buildRefineTasks(p).map(t=>t.area),['source']);
    ''')


def test_repeated_blockers_keep_additional_links_and_actionless_blockers_remain_visible():
    run_ts(LOAD + r'''
      const p=project([
        [row('same',[action('context',{field:'users'})])],
        [row('same',[action('source'),action('context',{field:'users'})])],
        [row('no-route',[]),row('unknown',[action('new-server-action')])],
      ]);
      const tasks=buildRefineTasks(p);
      assert(tasks.some(t=>t.field==='users'));
      assert(tasks.some(t=>t.area==='source'));
      assert.equal(tasks.find(t=>t.field==='users').actions.length,1,'do not duplicate repeated links');
      assert(tasks.some(t=>t.id==='blocker:no-route'&&t.blockers[0].key==='no-route'));
      assert(tasks.some(t=>t.action.kind==='new-server-action'),'unrecognized blockers must never disappear silently');
    ''')


def test_next_task_follows_visible_order_without_looping_to_previous_blockers():
    run_ts(LOAD + r'''
      const before=['first','middle','last'].map(id=>({id}));
      assert.equal(nextRefineTask(before,'middle').id,'last');
      assert.equal(nextRefineTask(before,'last'),undefined,'last task goes to checklist even when earlier blockers remain');
      assert.equal(nextRefineTask(before,'middle',[before[0],before[2]]).id,'last','removed current task retains original successor');
      assert.equal(nextRefineTask(before,'last',[before[0],before[1]]),undefined,'removed final task must not restart at first blocker');
      assert.equal(nextRefineTask(before,'middle',[before[0]]),undefined,'removed successor leads to checklist');
      assert.equal(nextRefineTask(before,'middle',[]),undefined,'fully resolved queue goes to stage confirmation');
    ''')


def test_next_task_preserves_current_order_and_handles_new_blockers_without_mutation():
    run_ts(LOAD + r'''
      const before=['first','middle','last'].map(id=>({id}));
      const after=[{id:'new'},before[0],before[2]];
      const original=JSON.stringify({before,after});
      assert.equal(nextRefineTask(before,'middle',after).id,'last','existing successor stays ahead of newly introduced task');
      assert.equal(nextRefineTask(before,'last',after).id,'new','new blocker remains reachable after former last task');
      assert.equal(nextRefineTask(before,'missing',after).id,'new','manual task can rejoin current queue');
      assert.equal(JSON.stringify({before,after}),original,'continuation only chooses a destination');
    ''')
