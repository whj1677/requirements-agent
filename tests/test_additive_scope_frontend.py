"""Unknown existing-state summaries do not force fabricated facts for pure additions."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const built=await build({stdin:{contents:"export {requiredIssues} from './src/pm-prototype/RequirementTable'; export {initial} from './src/pm-prototype/refinement-sheet-state'; export {isScopeFieldRequired} from './src/scope-draft';",resolveDir:process.cwd()},bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom','react-dom/server']});
const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
const {requiredIssues,initial,isScopeFieldRequired}=module.exports;
const req=id=>({id,kind:'requirement',applies_to:'to_be',selection_status:'candidate',change_type:'new',statement:'新增备注能力',title:id,behavior:{}});
const project=()=>({id:'P-additive',revision:1,items:[req('R1'),req('R2')],questions:[],sources:[],options:[],
  product_context:{intent:'新增记录',users:'本站值班员',change_scope:'新增两项能力',preserve_scope:'保持既有控制与告警逻辑',current_state:'',scope_ids:['R1','R2']},
  scope_profile:{scenario:'new_module',dependencies:'shared'},context_field_states:{current_state:{status:'unknown',note:'原资料未说明既有页面流程'}}});
const contextIssue=(issues,field)=>issues.some(i=>i.kind==='context'&&i.field===field);
'''


def test_additive_draft_keeps_unknown_without_waiving_preservation_or_real_questions():
    run_ts(LOAD + r'''
      const p=project(),sheet=initial(p,[]),before=JSON.stringify({p,sheet});
      assert(!contextIssue(requiredIssues(sheet,[],p),'current_state'));
      assert.equal(sheet.states.current_state.status,'unknown');
      assert.equal(sheet.states.current_state.note,'原资料未说明既有页面流程');
      assert.equal(JSON.stringify({p,sheet}),before);
      sheet.states.current_state.status='not_applicable';
      assert(contextIssue(requiredIssues(sheet,[],p),'current_state'),'unknown exemption never means not applicable');
      sheet.states.current_state.status='unknown';
      sheet.values.preserve_scope='';sheet.states.preserve_scope={status:'unknown'};
      const q={id:'Q-real',question:'真实业务冲突',blocking:true};
      const issues=requiredIssues(sheet,[q],p);
      assert(contextIssue(issues,'preserve_scope'));
      assert(issues.some(i=>i.kind==='question'&&i.question_id===q.id));
    ''')


def test_current_draft_changes_restore_required_state_without_trusting_stale_saved_scope():
    run_ts(LOAD + r'''
      const p=project(),sheet=initial(p,[]);
      assert(isScopeFieldRequired('current_state',sheet.profile),'legacy callers without scope remain conservative');
      sheet.rows.R2.change_type='modified';
      assert(contextIssue(requiredIssues(sheet,[],p),'current_state'));
      sheet.rows.R2.change_type='new';sheet.profile={scenario:'modify_existing',dependencies:'shared'};
      assert(contextIssue(requiredIssues(sheet,[],p),'current_state'));
      sheet.profile=p.scope_profile;sheet.scope=[];
      assert(contextIssue(requiredIssues(sheet,[],p),'current_state'));
      sheet.scope=['R1','R2'];p.items[1].selection_status='deferred';
      assert(contextIssue(requiredIssues(sheet,[],p),'current_state'));
    ''')
