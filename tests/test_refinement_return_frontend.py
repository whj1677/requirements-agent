"""Returning to delivery reuses exact human confirmation, never browser save state."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const load=async entry=>{const built=await build({entryPoints:[entry],bundle:true,write:false,format:'esm',platform:'node'});return import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));};
const {canReturnToDelivery,refinementContent}=await load('src/pm-prototype/refinement-return.ts');
const {initial,restoreSheet}=await load('src/pm-prototype/refinement-sheet-state.ts');
const project={id:'P-return',revision:9,items:[
  {id:'REQ-1',kind:'requirement',title:'查看储能站告警',statement:'值班人员查看储能站告警。',change_type:'new',applies_to:'to_be',selection_status:'selected',behavior:{actor:'值班人员'},related_refs:[]},
  {id:'AC-1',kind:'acceptance',statement:'打开列表后显示所选储能站告警。',applies_to:'to_be',selection_status:'selected',related_refs:['REQ-1']},
  {id:'RULE-1',kind:'rule',statement:'允许查看所负责储能站。',applies_to:'to_be',selection_status:'selected',behavior:{},related_refs:[]},
],questions:[{id:'Q-1',question:'查看哪个储能站？',answer:'所负责储能站',status:'answered',related_refs:['RULE-1']}],
product_context:{intent:'查看告警',users:'值班人员',change_scope:'新增告警列表',scope_ids:['REQ-1']},
scope_profile:{scenario:'new_product',dependencies:'independent'},
stage_checks:Object.fromEntries([1,2,3].map(step=>[step,{content_hash:'current-'+step,actor:'authenticated_local_user'}])),
product_flow:[1,2,3].map(step=>({step,content_hash:'current-'+step,complete:true}))};
const states=[{requirement_id:'REQ-1',current:true}];
const sheet=initial(project,states);
const allowed=(p=project,s=sheet,r=states,loaded=p.revision,stale=false)=>canReturnToDelivery(p,s,r,loaded,stale);
'''


def test_unchanged_confirmation_returns_without_mutating_or_requiring_other_delivery_gates():
    run_ts(LOAD + r'''
      const before=JSON.stringify({project,sheet,states});
      assert.equal(allowed(),true);
      const blocked=structuredClone(project);
      blocked.confirmation_blockers=[{code:'document_missing',message:'尚未生成 PRD'}];
      blocked.product_flow.forEach(step=>step.complete=false);
      assert.equal(allowed(blocked),true,'other blockers belong to the delivery screen');
      assert.equal(JSON.stringify({project,sheet,states}),before);
    ''')


def test_current_content_and_original_review_proof_are_both_required():
    run_ts(LOAD + r'''
      for(const change of [
        p=>delete p.stage_checks,
        p=>delete p.stage_checks['2'],
        p=>p.stage_checks['3'].content_hash='old',
        p=>p.stage_checks['2'].actor='model',
        p=>delete p.product_flow,
      ]){const p=structuredClone(project);change(p);assert.equal(allowed(p),false);}
      assert.equal(allowed(project,sheet,[]),false);
      assert.equal(allowed(project,sheet,[{requirement_id:'REQ-1',current:false}]),false,'source/content fingerprint expired');
      assert.equal(allowed(project,sheet,states,8),false);
      assert.equal(allowed(project,sheet,states,9,true),false);
      assert.equal(allowed(project,{...sheet,revision:8}),false);
      assert.equal(allowed(project,{...sheet,merge_conflicts:['内容已变化']}),false);
    ''')


def test_edits_saved_locally_and_restored_after_reload_still_require_confirmation():
    run_ts(LOAD + r'''
      for(const change of [
        s=>s.values.users='运维管理员',
        s=>s.states.users.status='unknown',
        s=>s.states.users.reason='仅说明当前值班角色',
        s=>s.states.users.note='人工补充的适用说明',
        s=>s.profile.scenario='new_module',
        s=>s.scope=[],
        s=>s.rows['REQ-1'].statement+='支持筛选。',
        s=>s.rows['REQ-1'].behavior.actor='运维管理员',
        s=>s.rows['REQ-1'].acceptance_items[0].statement+='可按告警等级筛选。',
        s=>s.answers['Q-1']='所有储能站',
        s=>s.linked_clauses['RULE-1'].statement='允许查看所有储能站。',
      ]){
        const edited=structuredClone(sheet);change(edited);
        assert.equal(allowed(project,edited),false);
        const saved=JSON.parse(JSON.stringify(edited));
        assert.equal(allowed(project,saved),false,'saving must not approve');
        const restored=restoreSheet(saved,sheet,initial(project,states));
        assert.equal(allowed(project,restored),false,'reload must not approve');
      }
    ''')


def test_new_ai_cells_require_confirmation_even_when_requirement_fingerprint_is_current():
    run_ts(LOAD + r'''
      for(const change of [
        p=>p.product_context_proposal={value:'AI 补充的价值建议'},
        p=>p.items.push({id:'AC-NEW',kind:'acceptance',statement:'AI 新验收内容',applies_to:'to_be',selection_status:'candidate',related_refs:['REQ-1']}),
        p=>p.items.find(i=>i.id==='RULE-1').selection_status='candidate',
      ]){const p=structuredClone(project);change(p);assert.equal(allowed(p,initial(p,states)),false);}
      const p=structuredClone(project);p.items[0].selection_status='candidate';
      assert.equal(allowed(p,initial(p,states)),false);
    ''')


def test_unapplied_suggestion_does_not_invalidate_confirmation_but_using_it_does():
    run_ts(LOAD + r'''
      const p=structuredClone(project);
      p.items.push({id:'AC-REV',kind:'acceptance',action:'revise',target_item_id:'AC-1',statement:'AI 修订验收',applies_to:'to_be',selection_status:'candidate'});
      const shown=initial(p,states);
      assert.equal(shown.rows['REQ-1'].acceptance_items[0].suggestions.length,1);
      assert.equal(allowed(p,shown),true,'advice beside a saved cell does not modify that cell');
      const used=structuredClone(shown);used.rows['REQ-1'].acceptance_items[0].candidate_id='AC-REV';
      assert.equal(allowed(p,used),false,'even equal candidate text still requires explicit review');
      assert.notEqual(refinementContent(used),refinementContent(shown));
      const reordered=structuredClone(shown);
      reordered.profile={dependencies:shown.profile.dependencies,scenario:shown.profile.scenario};
      assert.equal(refinementContent(reordered),refinementContent(shown),'object key order is not an edit');
    ''')


def test_unsubmitted_initial_table_cannot_return_even_if_all_required_cells_are_filled():
    run_ts(LOAD + r'''
      const p=structuredClone(project);p.stage_checks={};
      assert.equal(allowed(p,initial(p,states)),false);
      const unconfirmed=states.map(row=>({...row,current:false}));
      assert.equal(allowed(project,initial(project,unconfirmed),unconfirmed),false);
    ''')


def test_real_backend_confirmation_can_return_using_its_read_only_fingerprints(tmp_path):
    import json
    from app.product_flow import status
    from app.requirement_confirmation import confirmation_state
    from app.store import Store
    from tests.test_refinement_reconfirmation import confirmed_project

    store = Store(tmp_path)
    project, _ = confirmed_project(store)
    project['product_flow'] = status(project)
    state = confirmation_state(project)
    before = store.get(project['id'])
    run_ts(LOAD + '\nconst actual=' + json.dumps(project, ensure_ascii=False) + ';\n'
           + 'const actualStates=' + json.dumps(state['rows'], ensure_ascii=False) + ';\n'
           + '''assert.equal(canReturnToDelivery(actual,initial(actual,actualStates),actualStates,actual.revision),true);''')
    assert store.get(project['id']) == before
