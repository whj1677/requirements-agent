"""The compact scope editor shows explicit inclusion without approving content."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
  import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
  const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
  const built=await build({entryPoints:['src/pm-prototype/RequirementScope.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
  const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
  const {RequirementScope,scopeRequirementOptions}=module.exports;
  const item=(id,extra={})=>({id,title:id,kind:'requirement',applies_to:'to_be',selection_status:'candidate',...extra});
  const project=(items,scope_ids=[])=>({id:'P-SCOPE',revision:3,items,product_context:{scope_ids},sources:[],questions:[],messages:[]});
  const render=p=>renderToStaticMarkup(React.createElement(RequirementScope,{project:p,busy:false,mutate:()=>assert.fail('render must not save or invoke a model')}));
'''


def test_scope_candidates_are_not_preselected_or_approved_and_editor_starts_collapsed():
    run_ts(LOAD + r'''
      const p=project([item('已纳入'),item('待确认'),item('已采纳但未纳入',{selection_status:'selected'})],['已纳入']);
      const before=JSON.stringify(p),html=render(p);
      assert.match(html,/aria-label="纳入本期 已纳入" checked=""/);
      assert(!/aria-label="纳入本期 待确认" checked/.test(html));
      assert(!/aria-label="纳入本期 已采纳但未纳入" checked/.test(html));
      assert.match(html,/<details class="requirement-scope">/);
      assert(html.includes('调整本期范围')&&html.includes('本期需求范围'));
      assert(!html.includes('补充业务说明')&&!html.includes('确认本条')&&!html.includes('保存本期范围'));
      assert.equal(JSON.stringify(p),before);
    ''')


def test_only_visible_canonical_requirements_have_checkboxes_and_other_scope_is_preserved():
    run_ts(LOAD + r'''
      const p=project([item('REQ'),item('候选修订',{target_item_id:'REQ'}),item('历史',{selection_status:'deferred'}),
        item('被拒绝',{selection_status:'rejected'}),item('已排除',{selection_status:'excluded'}),item('现状',{applies_to:'as_is'}),
        item('限制',{kind:'constraint'}),item('旧版本',{superseded_by:'REQ'})],['REQ','限制','历史','unknown-original-id']);
      const before=JSON.stringify(p);assert.deepEqual(scopeRequirementOptions(p).map(x=>x.id),['REQ']);
      const html=render(p);
      assert.equal((html.match(/type="checkbox"/g)||[]).length,1);
      assert(html.includes('另有 3 项已保存范围内容，本次保留：限制、历史、unknown-original-id'));
      assert.equal(JSON.stringify(p),before,'hidden scope entries must remain in the saved snapshot');
    ''')


def test_empty_scope_editor_does_not_infer_requirements_from_other_items():
    run_ts(LOAD + r'''
      const html=render(project([item('AC',{kind:'acceptance'})]));
      assert(!html.includes('type="checkbox"'));assert(html.includes('还没有可选择的需求'));
    ''')
