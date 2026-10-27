"""The formal workspace renders the shared draft, not the historical prototype."""

from tests.test_scope_frontend import run_ts


ENTRY_HARNESS = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),React=require('react');
const built=await build({entryPoints:['src/pm-prototype/ConnectedWorkspace.tsx'],bundle:true,write:false,
  format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react/jsx-runtime']});
const same=(left,right)=>!!left&&!!right&&left.length===right.length&&left.every((value,index)=>Object.is(value,right[index]));
const memory=new Map();
globalThis.sessionStorage={getItem:key=>memory.get(key)??null,setItem:(key,value)=>memory.set(key,String(value)),removeItem:key=>memory.delete(key)};
globalThis.requestAnimationFrame=fn=>{fn();return 1;};
const original='已保存的储能云平台电价模板需求原文。';
const project={id:'P-entry',name:'已有需求和文档的项目',revision:17,sources:[],items:[],questions:[],
  documents:{prd:{id:'DOC-kept'}},messages:[{role:'user',text:'已有讨论'}],confirmation_issues:[]};
function createHarness({nonce=1,savedStep='refine',savedEntry='auto',deferDraft=false,storedMode='direct'}={}) {
  memory.clear();memory.set('ra-connected-step-P-entry',savedStep);memory.set('ra-refinement-entry-P-entry',savedEntry);
  const slots=[],effects=[],registry=new Map(),events=[];
  let cursor=0,changed=true,view,releaseDraft;
  const draftResponse={text:original,entry_mode:storedMode,revision:17,saved_at:'fixture-saved'};
  const delayed=deferDraft?new Promise(resolve=>{releaseDraft=()=>resolve(draftResponse);}):Promise.resolve(draftResponse);
  globalThis.fetch=async(path,options)=>{
    assert.equal(options.method,'GET','entering/restoring views must never issue writes or task requests');
    events.push(['read',path]);
    const data=path.endsWith('/workspace-draft')?await delayed:{revision:17,background:'',terms:[],
      selected_source_ids:null,effective_source_ids:[],unavailable_selected_source_ids:[]};
    return {ok:true,text:async()=>JSON.stringify(data)};
  };
  const entry={register:(key,value)=>value?registry.set(key,value):registry.delete(key),navigate:next=>next(),saveDrafts:async()=>{}};
  function effect(callback,deps){const index=cursor++,prior=slots[index];
    if(!prior||!same(prior.deps,deps)){slots[index]={deps};effects.push(()=>{prior?.cleanup?.();slots[index].cleanup=callback();});}}
  const hooks={...React,
    useState:initial=>{const index=cursor++;if(!slots[index])slots[index]={value:typeof initial==='function'?initial():initial};
      return [slots[index].value,value=>{const next=typeof value==='function'?value(slots[index].value):value;
        if(!Object.is(next,slots[index].value)){slots[index].value=next;changed=true;}}];},
    useRef:initial=>{const index=cursor++;return slots[index]||(slots[index]={current:initial});},
    useMemo:(fn,deps)=>{const index=cursor++;if(!slots[index]||!same(slots[index].deps,deps))slots[index]={deps,value:fn()};return slots[index].value;},
    useEffect:effect,useLayoutEffect:effect,useContext:()=>{cursor++;return entry;},
    useSyncExternalStore:(_subscribe,get)=>{cursor++;return get();}};
  const module={exports:{}};
  new Function('require','module','exports',built.outputFiles[0].text)(name=>name==='react'?hooks:require(name),module,module.exports);
  const fail=()=>assert.fail('normal project entry cannot organize or cancel a task');
  let chatBeforeSend;
  let props={id:project.id,project,tasks:[],assistantEntryNonce:nonce,active:true,busy:false,running:false,
    actionBlockReason:'',mutate:async(path,body)=>{events.push(['write',path,body]);return {...draftResponse,...body,project};},
    onOrganize:fail,onClarify:fail,onSources:()=>{},onCreateRequirement:()=>{},onCreateAcceptance:()=>{},
    onDelivery:()=>{},onDocuments:()=>{},onRefresh:()=>{},onRecommendRequirement:fail,onResolveBlocker:fail,
    onPrepareRefinement:async()=>{events.push(['prepare']);},onCancelPreparation:()=>events.push(['cancel-preparation']),
    chatPanel:before=>{chatBeforeSend=before;return React.createElement('div',{},'已保存的讨论仍在聊天入口');}};
  function render(){cursor=0;changed=false;view=module.exports.ConnectedWorkspace(props);
    while(effects.length)effects.shift()();}
  async function flush(){for(let index=0;index<40;index++){if(changed)render();await new Promise(setImmediate);
    if(!changed&&!effects.length)return;}assert.fail('entry effects did not settle');}
  const walk=(node,predicate)=>{if(node==null||typeof node==='boolean')return [];if(Array.isArray(node))return node.flatMap(value=>walk(value,predicate));
    if(typeof node!=='object')return [];return [...(predicate(node)?[node]:[]),...walk(node.props?.children,predicate)];};
  const text=node=>Array.isArray(node)?node.map(text).join(''):node==null||typeof node==='boolean'?'':
    typeof node==='object'?text(node.props?.children):String(node);
  function button(label){const values=walk(view,node=>node.type==='button'&&text(node.props.children)===label);
    assert.equal(values.length,1,'find actual shipped button '+label);return values[0];}
  return {flush,events,registry,releaseDraft,update:async extra=>{props={...props,...extra};changed=true;await flush();},
    click:async label=>{button(label).props.onClick({currentTarget:{closest:()=>null}});await flush();},
    input:async value=>{const input=walk(view,node=>node.type==='textarea'&&node.props.id==='connected-draft-P-entry')[0];
      input.props.onChange({target:{value}});await flush();},
    get step(){return view.props['data-step'];},get mode(){return button('和助手聊需求').props['aria-pressed']?'chat':'direct';},
    get draft(){return walk(view,node=>node.type==='textarea'&&node.props.id==='connected-draft-P-entry')[0]?.props.value;},
    get gate(){return walk(view,node=>node.props?.['aria-label']==='进入完善需求的准备状态').length>0;},
    beforeChatSend:()=>chatBeforeSend()};
}
'''


def test_shared_workspace_exposes_equal_entry_and_real_save_review_actions():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react');
      const {renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/pm-prototype/ConnectedWorkspace.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};
      new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const project={id:'P-one',name:'合成储能项目',revision:3,sources:[],items:[],questions:[],confirmation_issues:[],documents:{},messages:[]};
      const html=renderToStaticMarkup(React.createElement(module.exports.ConnectedWorkspace,{
        id:'P-one',project,tasks:[],busy:false,running:false,actionBlockReason:'',
        mutate:()=>assert.fail('render must not mutate'),onOrganize:()=>assert.fail('render must not plan'),
        onClarify:()=>{},onSources:()=>{},chatPanel:()=>React.createElement('div',{},'真实会话入口'),onCreateRequirement:()=>{},onCreateAcceptance:()=>{},onChooseScope:()=>{},
        onReviewItem:()=>{},onDelivery:()=>{},onDocuments:()=>{},onRefresh:()=>{},onTask:()=>{}
      }));
      assert(html.includes('提供信息')&&html.includes('完善需求')&&html.includes('确认交付'));
      assert(html.includes('和助手聊需求')&&html.includes('直接填写需求'));
      assert(html.includes('保存工作底稿')&&html.includes('整理并进入完善需求'));
      assert(html.includes('不使用 AI，直接完善'));
      assert(html.includes('助手先根据聊天和资料起草整张需求确认表，包含验收建议；你再确认或修改'));
      assert(!html.includes('数据已模拟保存')&&!html.includes('模拟完成'));
    ''')


def test_normal_entry_ignores_old_refinement_delivery_and_direct_mode_without_dirty_or_ai():
    run_ts(ENTRY_HARNESS + r'''
      const before=structuredClone(project);
      for(const [savedStep,savedEntry] of [['refine','auto'],['refine','manual'],['delivery','auto'],['information','manual']]) {
        const h=createHarness({savedStep,savedEntry,nonce:0});await h.flush();
        assert.equal(h.step,'information');assert.equal(h.mode,'chat');assert.equal(h.gate,false);
        assert.equal(h.draft,original,'loading chat does not erase a stored direct draft');
        assert(!h.registry.has('P-entry-connected-draft'),'view reset must not register an unsaved business change');
        assert.equal(memory.get('ra-connected-step-P-entry'),'information');
        assert.equal(memory.get('ra-refinement-entry-P-entry'),undefined);
        assert.deepEqual(h.events.filter(event=>event[0]!=='read'),[],'normal entry cannot prepare/cancel/save/run anything');
        assert.equal(await h.beforeChatSend(),project,'saved direct text is reusable without rewriting entry_mode');
        assert(!h.events.some(event=>event[0]==='write'));
      }
      assert.deepEqual(project,before,'existing requirements, documents and discussion stay untouched');
    ''')


def test_refinement_navigation_never_prepares_models_even_when_inputs_or_tasks_change():
    run_ts(ENTRY_HARNESS + r'''
      for(const label of ['2完善需求','进入完善需求']) {
        const h=createHarness();await h.flush();
        await h.update({project:{...project,revision:18,sources:[{id:'SRC-single-instruction'}]},
          tasks:[{id:'TASK-single',action:'clarify',status:'partial',label:'完善这条需求建议'}]});
        await h.click(label);assert.equal(h.step,'refine');assert.equal(h.gate,false);
        assert(!h.events.some(event=>event[0]==='prepare'||event[0]==='write'),
          'ordinary navigation must not prepare an AI task or rewrite saved input');
        await h.click('1提供信息');await h.click(label);assert.equal(h.step,'refine');
        assert(!h.events.some(event=>event[0]==='prepare'));
        await h.click('让 AI 先起草整表');
        assert.equal(h.events.filter(event=>event[0]==='prepare').length,1,
          'the explicit AI action remains available');
      }
    ''')


def test_legacy_refinement_view_restore_cannot_prepare_or_resume_model_work():
    run_ts(ENTRY_HARNESS + r'''
      for(const savedEntry of ['auto','manual']) {
        const h=createHarness({nonce:null,savedStep:'refine',savedEntry});await h.flush();
        assert.equal(h.step,'refine');assert.equal(h.gate,false);
        assert(!h.events.some(event=>event[0]==='prepare'||event[0]==='write'),
          'legacy restoration must only read the saved draft');
      }
    ''')


def test_entry_nonce_resets_mounted_view_but_preserves_user_navigation_and_written_draft():
    run_ts(ENTRY_HARNESS + r'''
      const h=createHarness({nonce:1});await h.flush();
      await h.click('直接填写需求');assert.equal(h.mode,'direct');assert.equal(h.draft,original);
      await h.update({assistantEntryNonce:1});assert.equal(h.mode,'direct','unchanged entry signal cannot override a user choice');
      await h.update({destination:{step:'delivery',nonce:10}});assert.equal(h.step,'delivery','explicit navigation still works');
      await h.update({assistantEntryNonce:2});assert.equal(h.step,'information');assert.equal(h.mode,'chat');assert.equal(h.gate,false);
      assert.equal(h.draft,original);assert(!h.registry.has('P-entry-connected-draft'));
      await h.update({destination:{step:'refine',nonce:11}});assert.equal(h.step,'refine','a later explicit destination may open saved refinement');
      await h.update({assistantEntryNonce:3,destination:{step:'delivery',nonce:12}});
      assert.equal(h.step,'information','a normal project entry wins over stale destination in the same render');
      await h.click('直接填写需求');await h.input(original+' 尚未保存的人工补充。');
      await h.update({assistantEntryNonce:4});assert.equal(h.mode,'chat');
      assert.equal(h.draft,original+' 尚未保存的人工补充。','view reset keeps actual unsaved text as well');
      assert(h.registry.has('P-entry-connected-draft'),'an existing real text edit remains protected');
      assert(!h.events.some(event=>event[0]==='write'||event[0]==='prepare'));
    ''')


def test_late_stored_direct_response_cannot_replace_a_new_assistant_entry():
    run_ts(ENTRY_HARNESS + r'''
      const h=createHarness({deferDraft:true,nonce:1});await h.flush();
      await h.click('直接填写需求');assert.equal(h.mode,'direct');
      await h.update({assistantEntryNonce:2});assert.equal(h.mode,'chat');
      h.releaseDraft();await h.flush();
      assert.equal(h.mode,'chat');assert.equal(h.step,'information');assert.equal(h.draft,original);
      assert(!h.registry.has('P-entry-connected-draft'));
      assert.equal(await h.beforeChatSend(),project);
      assert(!h.events.some(event=>event[0]==='write'||event[0]==='prepare'));
      await h.click('不使用 AI，直接完善');
      assert.equal(h.step,'refine','manual refinement remains an explicit available path');
      assert(!h.events.some(event=>event[0]==='prepare'),'manual navigation cannot start AI');
    ''')


def test_chat_direct_view_switches_and_restored_text_do_not_create_phantom_draft_edits():
    run_ts(ENTRY_HARNESS + r'''
      const h=createHarness({nonce:1,storedMode:'direct'});await h.flush();
      assert.equal(h.mode,'chat');assert.equal(h.draft,original);
      await h.click('直接填写需求');assert(!h.registry.has('P-entry-connected-draft'));
      await h.input(original+' 临时填写内容。');
      assert(h.registry.has('P-entry-connected-draft'),'actual text edits must remain protected');
      await h.click('和助手聊需求');assert(h.registry.has('P-entry-connected-draft'));
      await h.click('直接填写需求');assert.equal(h.draft,original+' 临时填写内容。');
      await h.input(original);await h.click('和助手聊需求');
      assert(!h.registry.has('P-entry-connected-draft'),
        'returning to saved words must remove the draft guard even if the visible input mode differs');
      assert.equal(await h.beforeChatSend(),project);
      assert(!h.events.some(event=>event[0]==='write'||event[0]==='prepare'),
        'mode switching and reuse of saved text cannot silently rewrite source entry_mode');
      await h.click('直接填写需求');await h.input(original+' 人工确定的追加。');
      assert(h.registry.has('P-entry-connected-draft'));
      await h.click('保存工作底稿');
      const write=h.events.find(event=>event[0]==='write');
      assert.equal(write[1],'/workspace-draft');
      assert.deepEqual(write[2],{text:original+' 人工确定的追加。',entry_mode:'direct'},
        'explicit saving still records the currently chosen input mode');
      assert(!h.registry.has('P-entry-connected-draft'));
    ''')
