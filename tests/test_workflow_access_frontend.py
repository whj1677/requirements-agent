"""Saved prerequisites control the actual navigation and blocker destination."""
from tests.test_scope_frontend import run_ts


def test_stage_locks_follow_saved_predecessors_and_do_not_trust_old_completion():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/workflow-access.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const {stageAccess,StageNavigation,StageLock,intakeStatusLabel,canStartDirect}=module.exports;
      const p={messages:[],product_flow:Array.from({length:6},(_,n)=>({available:n===0,complete:false}))};
      const render=()=>renderToStaticMarkup(React.createElement(StageNavigation,{p,phase:0,busy:false,intakeLabel:'',onGo:()=>assert.fail('render must not navigate')}));
      assert.equal((render().match(/disabled=""/g)||[]).length,3);
      assert(render().includes('可选 · 可跳过对话')&&render().includes('可直接填写'));
      assert.equal(stageAccess(p,0).allowed,true);
      assert.equal(canStartDirect(p),true);
      assert.equal(stageAccess(p,1).allowed,true,'experts can start the manual form without an interview');
      assert.equal(stageAccess(p,2).allowed,false);
      assert.equal(stageAccess(p,2).prerequisite,1,'missing manual input is resolved on the manual page');
      assert.equal(p.product_flow[0].complete,false,'entering the form does not complete any checkpoint');
      p.product_flow[0].needs_recheck=true;
      assert.equal(stageAccess(p,1).allowed,false,'skip cannot override invalidated input');
      p.product_flow[0].needs_recheck=false;
      p.product_flow[0].complete=true;p.product_flow[1].available=true;
      assert.equal(stageAccess(p,1).allowed,true);assert.equal(stageAccess(p,2).allowed,false);
      p.product_flow[1].complete=true;p.product_flow[2].available=true;
      assert.equal(stageAccess(p,2).allowed,true);
      // Even stale later completion flags cannot override an invalid predecessor.
      p.intake={text:'previously saved input'};
      p.product_flow[0].complete=false;p.product_flow[2].complete=true;
      const before=structuredClone(p);
      assert.equal(stageAccess(p,2).allowed,false);
      assert.equal(stageAccess(p,2).prerequisite,0);
      const nav=renderToStaticMarkup(React.createElement(StageNavigation,{p,phase:2,busy:false,intakeLabel:'',onGo:()=>{}}));
      assert(!nav.includes('aria-current="step"'),'a locked remembered page must not look like the active step');
      const lock=renderToStaticMarkup(React.createElement(StageLock,{p,phase:2,busy:false,onGo:()=>{},onRefresh:()=>{}}));
      assert(lock.includes('完善需求尚有前置内容待核对')&&lock.includes('前往')&&lock.includes('提供信息')&&lock.includes('草稿保留'));
      assert(!lock.includes('第 3 步暂未解锁'),'internal checkpoints must not reintroduce the old five-step flow');
      assert.deepEqual(p,before);
      assert.equal(stageAccess({messages:[]},1).allowed,false);
      assert(stageAccess({messages:[]},1).reason.includes('刷新'));
      assert.equal(intakeStatusLabel({...p,intake:{mode:'direct'}}),'输入已保存 · 直接核对草稿');
      assert.equal(intakeStatusLabel({...p,intake:{}},{status:'failed'}),'输入已保存 · 本次整理未完成');
    ''')


def test_scope_blocking_questions_are_reachable_before_the_rules_stage_unlocks():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/blocker-navigation.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {blockerDestination,stageBlockers}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const p={items:[],questions:[{id:'Q-scope',blocking:true,blocking_stage:2},{id:'Q-rules',blocking:true,blocking_stage:3}]};
      assert.equal(blockerDestination(p,{kind:'question',question_id:'Q-scope'}).phase,1);
      assert.equal(blockerDestination(p,{kind:'question',question_id:'Q-rules'}).phase,2);
      assert.equal(blockerDestination(p,{kind:'answer',question_id:'Q-scope'}).phase,2,'answer-to-clause review belongs after adoption');
      const fresh={...p,product_flow:[{complete:false,available:true,missing:[]},{complete:false,available:false,missing:['本次需求待填写'],blockers:[]}]};
      assert.deepEqual(stageBlockers(fresh,0),[],'optional interview must not be a blocker');
      assert(!stageBlockers(fresh,1).some(b=>b.code==='prerequisite_stage'),'direct editor must not send user back to the interview');
      const intake=stageBlockers({...fresh,intake:{text:'previous input'}},0);
      assert.equal(intake.length,1,'direct save replaces misleading legacy product/module chores');
      assert.equal(intake[0].actions[0].field,'intent');
      assert(intake[0].message.includes('直接填写'));
    ''')


def test_scope_question_editor_offers_answers_and_explicit_scope_decisions_in_place():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/product-flow.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const p={id:'P-scope',revision:1,items:[],questions:[
        {id:'Q-1',question:'是否纳入本期？',blocking:true,blocking_stage:2,status:'open'},
        {id:'Q-2',question:'具体规则是什么？',blocking:true,blocking_stage:3,status:'open'}]};
      const html=renderToStaticMarkup(React.createElement(module.exports.QuestionList,{p,scopeOnly:true,active:true,busy:false,mutate:()=>assert.fail('render must not save'),onHelp:()=>{}}));
      assert(html.includes('先明确影响本期范围的问题'));
      assert(html.includes('id="question-scope-Q-1"')&&html.includes('保存本次回答'));
      assert(html.includes('本期范围决定：')&&html.includes('明确移出本期的理由'));
      assert(!html.includes('具体规则是什么？'),'rules-stage questions do not force premature scope answers');
      assert(!html.includes('人工核对回答与当前条款'),'scope questions must not demand later clause application');
    ''')
