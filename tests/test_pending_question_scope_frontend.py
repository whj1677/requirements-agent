"""Question labels must distinguish pending, excluded and included table scope."""
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_detail_bulk_adoption_frontend import HARNESS


WEB = Path(__file__).resolve().parents[1] / "web"


FIXTURE = r'''
async function scopeFixture(link='direct',answer=''){
  const requirement=id=>({id,kind:'requirement',applies_to:'to_be',selection_status:'candidate',
    title:id==='REQ-scope'?'查看告警':'其他需求',statement:'合成需求原文 '+id,change_type:'new',behavior:{actor:'值班员'},related_refs:[]});
  const p={id:'P-question-scope',name:'合成储能云平台告警查询',revision:1,sources:[],messages:[],documents:{},
    product_context:{intent:'查询告警',users:'值班员'},scope_profile:{scenario:'new_module',dependencies:'unknown'},
    items:[requirement('REQ-scope')],questions:[{id:'Q-scope',question:'是否只查询当前站点告警？',status:'open',blocking:true,
      related_refs:['REQ-scope'],options:['仅当前站点','跨站点']}],confirmation_issues:[]};
  if(link==='acceptance'){
    p.items.push({id:'AC-scope',kind:'acceptance',applies_to:'to_be',selection_status:'candidate',
      title:'告警验收',statement:'合成验收原文',related_refs:['REQ-scope']});
    p.questions[0].related_refs=['AC-scope'];
  }
  if(link==='mixed'){
    p.items.push(requirement('REQ-other'));p.questions[0].related_refs.push('REQ-other');
  }
  const base=initial(p,[]),mine=structuredClone(base);mine.answers['Q-scope']=answer;
  if(link==='mixed')mine.scope_excluded_ids=['REQ-other'];
  const view=await tableFixture({p,base,mine});
  const question=()=>{const row=nodes(view.view.tree).find(n=>n.type==='tr'&&n.props['data-cell-key']==='question-Q-scope');assert(row);return row;};
  const category=()=>text(nodes(question()).find(n=>n.type==='small'));
  const range=()=>nodes(view.view.tree).find(n=>n.type==='select'&&n.props['aria-label']==='查看告警 本期范围');
  return {...view,question,category,range,setRange:value=>{range().props.onChange({target:{value}});view.view.render();}};
}
'''


def run_scope_script(tmp_path: Path, scenario: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    harness = HARNESS.replace(
        "import {build} from 'esbuild';", "import {resolve} from 'node:path';", 1,
    ).replace(
        "const realRequire=createRequire(import.meta.url),React=realRequire('react');",
        "const realRequire=createRequire(resolve('package.json')),React=realRequire('react'),{build}=realRequire('esbuild');", 1,
    )
    script = tmp_path / "question-scope.mjs"
    script.write_text(harness + FIXTURE + scenario, encoding="utf-8")
    result = subprocess.run([node, str(script)], cwd=WEB, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pending_candidate_question_is_not_presented_as_user_exclusion(tmp_path):
    run_scope_script(tmp_path, r'''
      for(const link of ['direct','acceptance','mixed']){
        const f=await scopeFixture(link),before=structuredClone(f.view.sheet()),projectBefore=structuredClone(f.p);
        assert.equal(f.range().props.value,'pending');
        assert.equal(f.category(),'范围待核对','an undecided candidate cannot be displayed as excluded: '+link);
        assert(text(f.question()).includes('关联需求范围尚未核对'));
        assert(!text(f.question()).includes('已暂不纳入本期'));
        assert(text(f.question()).includes('尚未回答'));
        assert(!nodes(f.question()).some(n=>n.type==='textarea'),'pending scope does not turn into an included answer');
        assert.equal(f.view.sheet().answers['Q-scope'],'');assert.equal(f.p.items[0].selection_status,'candidate');
        assert.deepEqual(f.view.sheet(),before);assert.deepEqual(f.p,projectBefore,'render cannot include, answer or confirm');
      }
    ''')


def test_explicit_exclusion_keeps_original_outside_scope_label_and_words(tmp_path):
    run_scope_script(tmp_path, r'''
      for(const link of ['direct','acceptance']){
        const original='人工尚未确认的原话。',f=await scopeFixture(link,original),projectBefore=structuredClone(f.p);
        f.setRange('excluded');const afterChoice=structuredClone(f.view.sheet());f.view.render();
        assert.equal(f.range().props.value,'excluded');assert.equal(f.category(),'本期以外');
        assert(text(f.question()).includes('关联需求已暂不纳入本期，本次保留原话，不确认回答。'));
        assert(text(f.question()).includes(original));assert(!nodes(f.question()).some(n=>n.type==='textarea'));
        assert.deepEqual(f.view.sheet(),afterChoice);assert.deepEqual(f.p,projectBefore,'local range choice cannot persist or confirm');
      }
    ''')


def test_included_scope_keeps_answer_editing_and_explicit_recommendation_callback(tmp_path):
    run_scope_script(tmp_path, r'''
      for(const link of ['direct','acceptance']){
        const f=await scopeFixture(link),projectBefore=structuredClone(f.p);
        f.setRange('included');assert.equal(f.category(),'业务决定');
        assert(!text(f.question()).includes('本期以外'));assert(!text(f.question()).includes('范围尚未核对'));
        let input=nodes(f.question()).find(n=>n.type==='textarea'&&n.props['aria-label']==='是否只查询当前站点告警？ 我的回答');
        assert(input&&!input.props.disabled);assert.equal(input.props.value,'');
        click(button(f.question(),'仅当前站点'));f.view.render();
        assert.equal(f.view.sheet().answers['Q-scope'],'仅当前站点','existing explicit recommendation fills only the browser answer');
        input=nodes(f.question()).find(n=>n.type==='textarea');input.props.onChange({target:{value:'人工修改的回答。'}});f.view.render();
        assert.equal(f.view.sheet().answers['Q-scope'],'人工修改的回答。');
        assert.deepEqual(f.view.sheet().scope,['REQ-scope']);assert.deepEqual(f.p,projectBefore,'editing never confirms or persists');
      }
    ''')
