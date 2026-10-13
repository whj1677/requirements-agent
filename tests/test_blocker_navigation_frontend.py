"""Exercise actual identity-based routes and the rendered actionable blocker list."""
from tests.test_scope_frontend import run_ts


def test_destinations_use_actual_identities_and_never_change_business_state():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/blocker-navigation.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {blockerDestination:route,acceptanceTargets,blockerGroups}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const p={product_context:{scope_ids:['REQ-1']},items:[
        {id:'REQ-1',kind:'requirement',title:'学习档案',applies_to:'to_be',related_refs:['AC-2']},
        {id:'REQ-2',kind:'requirement',title:'其他需求'},
        {id:'AC-1',kind:'acceptance',applies_to:'to_be',selection_status:'candidate',related_refs:['REQ-1']},
        {id:'AC-2',kind:'acceptance',applies_to:'to_be',selection_status:'candidate',related_refs:[]},
        {id:'AC-OTHER',kind:'acceptance',applies_to:'to_be',selection_status:'candidate',related_refs:['REQ-2']},
        {id:'AC-REJECTED',kind:'acceptance',applies_to:'to_be',selection_status:'rejected',related_refs:['REQ-1']},
        {id:'AC-OLD',kind:'acceptance',applies_to:'as_is',selection_status:'candidate',related_refs:['REQ-1']},
        {id:'RULE-1',kind:'constraint'}],questions:[{id:'Q-1',status:'answered'},{id:'Q-OLD',superseded_by:['Q-1']}],documents:{}};
      const before=structuredClone(p);
      assert.deepEqual(acceptanceTargets(p,'REQ-1').map(i=>i.id),['AC-1','AC-2']);
      assert.deepEqual(route(p,{kind:'acceptance',item_id:'REQ-1'}),{phase:2,itemIds:['AC-1','AC-2']});
      for(const [action,phase] of [[{kind:'behavior',item_id:'REQ-1',fields:['entry']},2],[{kind:'add_acceptance',item_id:'REQ-1'},2],[{kind:'add_acceptance'},2],[{kind:'requirement',item_id:'REQ-1'},1],[{kind:'requirement',item_id:'RULE-1'},2],[{kind:'context',field:'users'},1],[{kind:'question',question_id:'Q-1'},2],[{kind:'answer',question_id:'Q-1'},2],[{kind:'stage',step:5},4],[{kind:'document',document:'mrd'},4],[{kind:'review'},4],[{kind:'source'},0]])assert.equal(route(p,action).phase,phase);
      for(const action of [{kind:'behavior',item_id:'REQ-2'},{kind:'behavior',item_id:'MISSING'},{kind:'answer',question_id:'Q-OLD'},{kind:'question',question_id:'MISSING'},{kind:'context',field:'invented'},{kind:'stage',step:4},{kind:'document',document:'invented'},{kind:'invented'}])assert.throws(()=>route(p,action),/位置已变化/);
      p.items=p.items.filter(i=>!['AC-1','AC-2'].includes(i.id));
      assert.throws(()=>route(p,{kind:'acceptance',item_id:'REQ-1'}),/位置已变化/);
      p.items=before.items;assert.deepEqual(p,before,'navigation never adopts, edits answers, or invokes tasks');
      const historic={...p,items:[{...p.items[0],applies_to:'as_is',selection_status:'selected'}]};
      assert.equal(route(historic,{kind:'add_acceptance'}).phase,1);
      assert.throws(()=>route(historic,{kind:'add_acceptance',item_id:'REQ-1'}),/位置已变化/);
      assert.equal(route({...p,items:[{...p.items[0],selection_status:'candidate'}]},{kind:'add_acceptance'}).phase,2,'candidate requirements may receive acceptance candidates before adoption');
      const rows=[{key:'a',title:'学习档案',actions:[{kind:'behavior',item_id:'REQ-1'}]},{key:'b',title:'学习档案',actions:[{kind:'acceptance',item_id:'REQ-1'}]},{key:'c',title:'学习档案',actions:[{kind:'behavior',item_id:'REQ-2'}]}];
      const groups=blockerGroups(rows);assert.equal(groups.length,2);assert.deepEqual(groups[0].rows,rows.slice(0,2));
    ''')


def test_rendered_blockers_expose_name_reason_and_each_real_action():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/blockers.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const {BlockerPanel}=module.exports;
      const rows=[{key:'a',code:'behavior_fields_missing',title:'各学科学习档案',message:'REQ-1 尚缺字段',detail:'尚缺：入口及前置条件、权限约束',actions:[{kind:'behavior',label:'补全业务行为',item_id:'REQ-1',fields:['entry','permissions']}]},{key:'b',code:'acceptance_missing',title:'各学科学习档案',message:'REQ-1 缺验收',detail:'缺少已采纳的关联验收条件。',actions:[{kind:'add_acceptance',label:'补充关联验收条件',item_id:'REQ-1'}]}];
      const render=(busy=false)=>renderToStaticMarkup(React.createElement(BlockerPanel,{id:'blockers-P-2',rows,busy,onResolve:()=>assert.fail('render must not write')}));
      const html=render();assert(html.includes('当前阶段阻塞清单'));assert(html.includes('各学科学习档案'));assert(html.includes('入口及前置条件'));assert(html.includes('补全业务行为'));assert(html.includes('补充关联验收条件'));assert(!html.includes('REQ-1 尚缺字段'));assert.equal((html.match(/class="blocker-group"/g)||[]).length,1);
      assert.equal((render(true).match(/disabled=""/g)||[]).length,3);
      assert.equal(renderToStaticMarkup(React.createElement(BlockerPanel,{id:'none',rows:[],busy:false,onResolve:()=>{}})),'');
    ''')


def test_blocked_continue_reports_count_and_returns_latest_list_without_submission():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/checkpoint-transition.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {confirmCheckpoint}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const p={revision:9,product_flow:{2:{available:true,missing:['REQ-SECRET 缺字段','REQ-SECRET 缺验收'],blockers:[{actions:[{kind:'behavior',item_id:'REQ-SECRET'}]}],content_hash:'hash'}}};let received;
      await assert.rejects(()=>confirmCheckpoint({phase:2,revision:9,read:async()=>p,blocked:latest=>received=latest,check:async()=>assert.fail('blocked must not confirm stage')}),error=>error.message.includes('2 项')&&error.message.includes('阻塞清单')&&!error.message.includes('REQ-SECRET'));
      assert.equal(received,p);
    ''')
