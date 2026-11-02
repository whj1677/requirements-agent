"""Execute shipped detail-editor callbacks; bulk adoption must be local and reversible."""
from tests.test_scope_frontend import run_ts


HARNESS = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const realRequire=createRequire(import.meta.url),React=realRequire('react');
let active=null;
const sameDeps=(a,b)=>a!==undefined&&b!==undefined&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
function controller(Component,initialProps){
  const slots=[];let cursor=0,props=initialProps,tree,dirty=false,effects=[];
  const api={slots,get tree(){return tree;},get props(){return props;},
    useState(init){const at=cursor++;if(!slots[at])slots[at]={kind:'state',value:typeof init==='function'?init():init};
      return [slots[at].value,next=>{const value=typeof next==='function'?next(slots[at].value):next;if(!Object.is(value,slots[at].value)){slots[at].value=value;dirty=true;}}];},
    useRef(init){const at=cursor++;if(!slots[at])slots[at]={kind:'ref',value:{current:init}};return slots[at].value;},
    useMemo(fn,deps){const at=cursor++;if(!slots[at]||!sameDeps(slots[at].deps,deps))slots[at]={kind:'memo',deps,value:fn()};return slots[at].value;},
    useEffect(fn,deps){const at=cursor++;if(!slots[at]||!sameDeps(slots[at].deps,deps)){const old=slots[at];effects.push(()=>{old?.cleanup?.();slots[at]={kind:'effect',deps,cleanup:fn()};});}},
    render(next=props,commit=true){props=next;let loops=0;do{dirty=false;cursor=0;effects=[];const old=active;active=api;try{tree=Component(props);}finally{active=old;}
      if(commit)for(const effect of effects)effect();assert(++loops<15,'hook render must settle');}while(commit&&dirty);return tree;},
    async settle(){for(let n=0;n<5;n++){await new Promise(resolve=>setImmediate(resolve));api.render();}return tree;},
    sheet(){return slots.find(slot=>slot?.kind==='state'&&slot.value?.rows&&slot.value?.answers)?.value;},
  };return api;
}
const mockReact={...React,useState:(...a)=>active.useState(...a),useRef:(...a)=>active.useRef(...a),
  useMemo:(...a)=>active.useMemo(...a),useCallback:(fn,deps)=>active.useMemo(()=>fn,deps),
  useEffect:(...a)=>active.useEffect(...a),useLayoutEffect:(...a)=>active.useEffect(...a)};
globalThis.__bulkSession=new Map();globalThis.__bulkScheduled=null;globalThis.__bulkApi=()=>{throw Error('unexpected API');};
const mocks={
  '../draft-storage':`export const session={getItem:k=>globalThis.__bulkSession.get(k)??null,setItem:(k,v)=>{globalThis.__bulkSession.set(k,String(v));return true;},removeItem:k=>globalThis.__bulkSession.delete(k)};`,
  '../use-draft-writer':`export const useDraftWriter=()=>({schedule:fn=>{globalThis.__bulkScheduled=fn;},flush:()=>true,cancel:()=>{}});`,
  '../editing':`export const useDirty=()=>()=>{};export const useNavigation=()=>fn=>fn();`,
  '../api':`export const api=(path)=>Promise.resolve(globalThis.__bulkApi(path));`,
};
async function load(entry,mockTable=false){
  const plugins=mockTable?[{name:'test-local-boundaries',setup(b){
    b.onResolve({filter:/^\.\.\//},args=>args.importer.replaceAll('\\','/').endsWith('/RequirementTable.tsx')&&Object.hasOwn(mocks,args.path)?{path:args.path,namespace:'bulk-mock'}:null);
    b.onLoad({filter:/.*/,namespace:'bulk-mock'},args=>({contents:mocks[args.path],loader:'js'}));
  }}]:[];
  const result=await build({entryPoints:[entry],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react'],plugins});
  const module={exports:{}};const require=id=>id==='react'?mockReact:realRequire(id);
  new Function('require','module','exports',result.outputFiles[0].text)(require,module,module.exports);return module.exports;
}
const {DetailContractEditor}=await load('src/pm-prototype/DetailContractEditor.tsx');
const {emptyDetailContract}=await load('src/pm-prototype/detail-contract.ts');
const {initial}=await load('src/pm-prototype/refinement-sheet-state.ts');
function nodes(tree){const found=[];const walk=node=>{if(node==null||typeof node==='boolean')return;if(Array.isArray(node)){node.forEach(walk);return;}
  if(typeof node==='object'&&node.props){found.push(node);walk(node.props.children);}};walk(tree);return found;}
const text=node=>node==null||typeof node==='boolean'?'':Array.isArray(node)?node.map(text).join(''):typeof node==='object'?text(node.props?.children):String(node);
const buttons=tree=>nodes(tree).filter(node=>node.type==='button');
function button(tree,label){const matches=buttons(tree).filter(node=>text(node)===label);assert.equal(matches.length,1,'actual button: '+label);return matches[0];}
function bulkButton(tree){const matches=buttons(tree).filter(node=>/整版|整份/.test(text(node))&&/AI/.test(text(node))&&/详细定义/.test(text(node))&&!/撤销/.test(text(node)));
  assert.equal(matches.length,1,'one explicit whole AI detail adoption action');return matches[0];}
function undoButton(tree,required=true){const matches=buttons(tree).filter(node=>/撤销/.test(text(node))&&/AI|采用|整版|整份|详细定义/.test(text(node)));
  if(required)assert.equal(matches.length,1,'one immediate undo action');return matches[0];}
function click(node){assert(node,'click target exists');if(!node.props.disabled)node.props.onClick?.();}
function detail(label,count=1){const d=emptyDetailContract();for(const k of Object.keys(d.applicability))d.applicability[k]={applicable:k==='fields',reason:k==='fields'?'本期字段':'本期不涉及'};
  d.fields=Array.from({length:count},(_,n)=>({id:'FD-'+n,name:'字段'+n,meaning:label+'-'+n,data_type:'string',source:'现有记录',required:'必填',default_value:'无',constraints:'沿用既有约束',visibility:'两名值班员',editability:'只读',
    evidence:{meaning:{status:label==='AI'?'source':'proposal',source_refs:[{source_id:'SRC-synthetic',excerpt_id:'EX-synthetic'}]}}}));return d;}
function editorFixture(count=121){const mine=detail('人工',count),base=detail('原版',count),contract=detail('AI',count);
  const conflicts=mine.fields.map((f,n)=>({path:'detail_contract.fields.'+f.id+'.meaning',local:f.meaning,remote:contract.fields[n].meaning}));
  const calls=[];let props={requirementId:'REQ-1',title:'告警归属',value:mine,base,conflicts,candidate:{candidate_id:'C-new',contract},onChange:(value,conflicts)=>{calls.push({value,conflicts});props={...props,value,conflicts:conflicts??props.conflicts};}};
  const view=controller(DetailContractEditor,props);view.render();return {mine,base,contract,conflicts,calls,view,get props(){return props;},render:()=>view.render(props),set:patch=>{props={...props,...patch};return props;}};}
function projectFixture(){const d=detail('已保存'),r=id=>({id,kind:'requirement',title:id==='REQ-1'?'告警归属':'其他需求',statement:'已保存正文'+id,applies_to:'to_be',selection_status:'selected',change_type:'modified',behavior:{actor:'两名值班员'},related_refs:[],detail_contract:structuredClone(d)});
  const p={id:'P-bulk-synthetic',revision:1,product_context:{intent:'核对告警归属',users:'两名值班员',scope_ids:['REQ-1','REQ-2']},scope_profile:{scenario:'modify_existing',dependencies:'unknown'},items:[r('REQ-1'),r('REQ-2'),{id:'AC-1',kind:'acceptance',statement:'原验收',applies_to:'to_be',selection_status:'selected',related_refs:['REQ-1']}],questions:[{id:'Q1',question:'本期操作者范围',status:'open',blocking:true,related_refs:['REQ-1']}]};
  const revision=(id,target,label='AI')=>({id,kind:'requirement',action:'revise',target_item_id:target,recommendation_requirement_id:target,applies_to:'to_be',selection_status:'candidate',statement:'AI正文'+id,change_type:'modified',behavior:{actor:'AI候选角色'},detail_contract:detail(label)});
  p.items.push(revision('C-old','REQ-1','旧AI'),revision('C-new','REQ-1'),revision('C-other','REQ-2','其他AI'));
  const base=initial(p,[]),mine=structuredClone(base);mine.answers.Q1='本期只核对已说明的两名值班员。';mine.profile={scenario:'modify_existing',dependencies:'unknown'};mine.values.priority='';
  mine.rows['REQ-1'].statement='人工正文';mine.rows['REQ-1'].behavior.actor='两名值班员';mine.rows['REQ-1'].candidate_id='C-old';mine.rows['REQ-1'].detail_contract=detail('人工',121);
  mine.rows['REQ-1'].detail_conflicts=Array.from({length:121},(_,n)=>({path:'detail_contract.fields.FD-'+n+'.meaning',local:'人工-'+n,remote:'AI-'+n}));delete mine.rows['REQ-1'].detail_suggestion;
  mine.merge_conflicts=['原有正文冲突保留'];mine.answer_implementations={Q1:[{item_id:'REQ-1',field:'behavior.actor',quote:'两名值班员'}]};return {p,base,mine,revision};}
async function tableFixture(fixture=projectFixture()){
  const {p,base,mine}=fixture,key=p.id+'-refinement-sheet';globalThis.__bulkSession.clear();globalThis.__bulkSession.set(key,JSON.stringify(mine));globalThis.__bulkSession.set(key+'-base',JSON.stringify(base));
  globalThis.__bulkApi=path=>path.endsWith('/state')?{revision:p.revision,rows:[]}:{status:'ready'};
  const {RequirementTable}=await load('src/pm-prototype/RequirementTable.tsx',true);
  const props={project:p,busy:false,active:false,mutate:()=>{throw Error('unexpected mutation API');},onSuggest:()=>{throw Error('unexpected AI');},onRefresh:()=>{},onSources:()=>{},onRecommend:()=>{throw Error('unexpected AI');},onCreate:()=>{},onResolve:()=>{},onQuestionReview:()=>{},onStageConfirmed:()=>{}};
  const view=controller(RequirementTable,props);view.render();await view.settle();const child=id=>nodes(view.tree).find(n=>typeof n.type==='function'&&n.type.name==='DetailContractEditor'&&n.props.requirementId===id);
  assert(view.sheet(),'actual effect-loaded table sheet');return {...fixture,view,props,child};}
'''


def test_existing_merge_button_still_keeps_local_values_and_reviewable_conflicts():
    run_ts(HARNESS + r'''
      const f=editorFixture(2),before=JSON.stringify({mine:f.mine,base:f.base,contract:f.contract,conflicts:f.conflicts});
      click(button(f.view.tree,'合并 AI 详细定义建议'));
      assert.equal(f.calls.length,1);assert.equal(f.calls[0].value.fields[0].meaning,'人工-0');
      assert(f.calls[0].conflicts.length>=2);assert.equal(JSON.stringify({mine:f.mine,base:f.base,contract:f.contract,conflicts:f.conflicts}),before);
    ''')


def test_bulk_uses_deep_candidate_clone_clears_121_conflicts_and_undo_restores_exactly():
    run_ts(HARNESS + r'''
      const f=editorFixture(),before=JSON.stringify({mine:f.mine,base:f.base,contract:f.contract,conflicts:f.conflicts});
      click(bulkButton(f.view.tree));assert.equal(f.calls.length,1);
      const adopted=f.calls[0];assert.deepEqual(adopted.value,f.contract);assert.deepEqual(adopted.conflicts,[]);
      assert.notEqual(adopted.value,f.contract);assert.notEqual(adopted.value.fields,f.contract.fields);
      assert.notEqual(adopted.value.fields[0].evidence.meaning.source_refs,f.contract.fields[0].evidence.meaning.source_refs);
      f.render();click(undoButton(f.view.tree));assert.equal(f.calls.length,2);
      assert.deepEqual(f.calls[1].value,f.mine);assert.deepEqual(f.calls[1].conflicts,f.conflicts);assert.equal(f.calls[1].conflicts.length,121);
      assert.equal(JSON.stringify({mine:f.mine,base:f.base,contract:f.contract,conflicts:f.conflicts}),before);
      f.render();assert(!undoButton(f.view.tree,false),'undo is single-use');
    ''')


def test_undo_cannot_overwrite_later_edits_rows_or_candidate_versions():
    run_ts(HARNESS + r'''
      for(const change of ['edit','row','candidate-id','candidate-contract','candidate-in-place','candidate-removed','conflicts']){
        const f=editorFixture(1);click(bulkButton(f.view.tree));f.render();assert(undoButton(f.view.tree));
        if(change==='edit'){
          const control=nodes(f.view.tree).find(n=>n.type==='textarea'&&n.props.value==='AI-0');assert(control);
          control.props.onChange({target:{value:'后续人工修改'}});
        }else if(change==='row')f.set({requirementId:'REQ-2'});
        else if(change==='candidate-id')f.set({candidate:{candidate_id:'C-next',contract:f.contract}});
        else if(change==='candidate-contract')f.set({candidate:{candidate_id:'C-new',contract:detail('AI新版')}});
        else if(change==='candidate-in-place')f.contract.fields[0].meaning='外部更新的同编号候选';
        else if(change==='conflicts')f.set({conflicts:[{path:'detail_contract.fields.FD-0.meaning',local:'新本地',remote:'新冲突'}]});
        else f.set({candidate:undefined});
        const count=f.calls.length;
        f.view.render(f.props,false);const undo=undoButton(f.view.tree,false);if(undo)click(undo);
        assert.equal(f.calls.length,count,'rendered stale undo must not write: '+change);
        f.render();const settledUndo=undoButton(f.view.tree,false);assert(!settledUndo||settledUndo.props.disabled,'old undo unavailable: '+change);
        if(change!=='candidate-in-place')assert.equal(f.contract.fields[0].meaning,'AI-0');
      }
      const disabled=editorFixture(1);disabled.set({disabled:true});disabled.render();click(bulkButton(disabled.view.tree));assert.equal(disabled.calls.length,0);
      const disabledUndo=editorFixture(1);click(bulkButton(disabledUndo.view.tree));disabledUndo.set({disabled:true});disabledUndo.render();
      const disabledUndoButton=undoButton(disabledUndo.view.tree,false);if(disabledUndoButton)click(disabledUndoButton);assert.equal(disabledUndo.calls.length,1);
      const absent=editorFixture(1);absent.set({candidate:undefined});absent.render();assert(!buttons(absent.view.tree).some(b=>/整版|整份/.test(text(b))));
    ''')


def test_table_passes_latest_valid_detail_even_when_suggestion_is_omitted_or_body_choice_is_old():
    run_ts(HARNESS + r'''
      for(const offeredLatest of [false,true]){
        const f=projectFixture();if(offeredLatest){f.mine.rows['REQ-1'].candidate_id='C-new';f.mine.rows['REQ-1'].detail_contract=structuredClone(f.p.items.find(x=>x.id==='C-new').detail_contract);}
        delete f.mine.rows['REQ-1'].detail_suggestion;
        f.p.items.push({...f.revision('C-wrong-root','REQ-1'),recommendation_requirement_id:'REQ-2'});
        f.p.items.push({...f.revision('C-wrong-action','REQ-1'),action:'add'});
        f.p.items.push({...f.revision('C-as-is','REQ-1'),applies_to:'as_is'});
        f.p.items.push({...f.revision('C-deferred','REQ-1'),selection_status:'deferred'});
        f.p.items.push({...f.revision('C-superseded','REQ-1'),superseded_by:'C-new'});
        const t=await tableFixture(f),child=t.child('REQ-1');assert(child,'actual editor element');
        assert.equal(child.props.candidate?.candidate_id,'C-new');assert.deepEqual(child.props.candidate.contract,f.p.items.find(x=>x.id==='C-new').detail_contract);
        assert.equal(t.child('REQ-2').props.candidate?.candidate_id,'C-other','other row keeps its own candidate');
      }
    ''')


def test_actual_table_callback_changes_only_detail_and_preserves_actor_witness_and_business_draft():
    run_ts(HARNESS + r'''
      const t=await tableFixture(),before=structuredClone(t.view.sheet()),child=t.child('REQ-1');assert(child?.props.candidate,'table must offer latest detail');
      let editorProps=child.props;const editor=controller(child.type,editorProps);editor.render();click(bulkButton(editor.tree));t.view.render();
      const after=t.view.sheet(),withoutDetail=sheet=>{const x=structuredClone(sheet);delete x.rows['REQ-1'].detail_contract;delete x.rows['REQ-1'].detail_conflicts;return x;};
      assert.deepEqual(withoutDetail(after),withoutDetail(before));assert.deepEqual(after.rows['REQ-1'].detail_conflicts,[]);
      assert.deepEqual(after.rows['REQ-1'].detail_contract,child.props.candidate.contract);assert.equal(after.rows['REQ-1'].candidate_id,'C-old');
      assert.deepEqual(after.answer_implementations.Q1,[{item_id:'REQ-1',field:'behavior.actor',quote:'两名值班员'}]);
      assert.deepEqual(after.merge_conflicts,before.merge_conflicts);assert.equal(after.answers.Q1,before.answers.Q1);
      editorProps=t.child('REQ-1').props;editor.render(editorProps);click(undoButton(editor.tree));t.view.render();
      assert.deepEqual(t.view.sheet(),before,'actual table undo restores exact detail/conflicts without unrelated edits');
    ''')


def test_bulk_can_clear_residual_121_conflicts_after_actual_merge_equals_candidate():
    run_ts(HARNESS + r'''
      const f=editorFixture();f.set({base:structuredClone(f.mine)});f.render();
      click(button(f.view.tree,'合并 AI 详细定义建议'));f.render();
      assert.deepEqual(f.props.value,f.contract,'old merge legitimately reaches the candidate value');
      assert.equal(f.props.conflicts.length,121,'the existing merge retains earlier conflicts for review');
      const previous=structuredClone(f.props.value),previousConflicts=structuredClone(f.props.conflicts);
      click(bulkButton(f.view.tree));assert.deepEqual(f.calls.at(-1).conflicts,[]);assert.deepEqual(f.calls.at(-1).value,f.contract);
      assert.notEqual(f.calls.at(-1).value,f.contract);f.render();click(undoButton(f.view.tree));
      assert.deepEqual(f.calls.at(-1).value,previous);assert.deepEqual(f.calls.at(-1).conflicts,previousConflicts);
      assert.equal(f.calls.at(-1).conflicts.length,121);
    ''')
