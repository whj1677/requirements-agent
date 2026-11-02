"""Execute DSH settings callbacks and the existing draft guard without a server."""
import shutil
import subprocess
from pathlib import Path

import pytest


WEB = Path(__file__).resolve().parents[1] / "web"


def run_component_script(tmp_path: Path, scenario: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    script = tmp_path / "dsh-settings-component.mjs"
    script.write_text(HARNESS + scenario, encoding="utf-8")
    result = subprocess.run(
        [node, str(script)], cwd=WEB, capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


HARNESS = r'''
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
const realRequire=createRequire(resolve('package.json'));
const {build}=realRequire('esbuild'),React=realRequire('react');
const {renderToStaticMarkup}=realRequire('react-dom/server');
let active=null;
const contextValues=new Map();
const sameDeps=(a,b)=>a!==undefined&&b!==undefined&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
function controller(Component,initialProps){
  const slots=[];let cursor=0,props=initialProps,tree,changed=false,effects=[];
  const view={get tree(){return tree;},
    useState(initial){const at=cursor++;if(!slots[at])slots[at]={value:typeof initial==='function'?initial():initial};
      return [slots[at].value,next=>{const value=typeof next==='function'?next(slots[at].value):next;
        if(!Object.is(value,slots[at].value)){slots[at].value=value;changed=true;}}];},
    useRef(initial){const at=cursor++;if(!slots[at])slots[at]={value:{current:initial}};return slots[at].value;},
    useMemo(fn,deps){const at=cursor++;if(!slots[at]||!sameDeps(slots[at].deps,deps))slots[at]={deps,value:fn()};return slots[at].value;},
    useEffect(fn,deps){const at=cursor++;if(!slots[at]||!sameDeps(slots[at].deps,deps)){
      const old=slots[at];effects.push(()=>{old?.cleanup?.();slots[at]={deps,cleanup:fn()};});}},
    render(next=props){props=next;let loops=0;do{changed=false;cursor=0;effects=[];const old=active;active=view;
      try{tree=Component(props);}finally{active=old;}for(const effect of effects)effect();
      assert(++loops<20,'component hooks settle');}while(changed);return tree;},
  };return view;
}
const mockReact={...React,
  useState:(...a)=>active.useState(...a),useRef:(...a)=>active.useRef(...a),
  useMemo:(...a)=>active.useMemo(...a),useCallback:(fn,deps)=>active.useMemo(()=>fn,deps),
  useEffect:(...a)=>active.useEffect(...a),useLayoutEffect:(...a)=>active.useEffect(...a),
  useContext:context=>contextValues.get(context)??context._currentValue,
};
globalThis.window={addEventListener(){},removeEventListener(){}};
globalThis.fetch=()=>{throw Error('unexpected real HTTP');};
const mocks={
  './api':`export const api=(...args)=>globalThis.__dshSettingsApi(...args);`,
  './shell':`export const useDialog=()=>{};`,
  './activity':`export const hasPendingActivity=()=>false;export const activityNotice=()=>{throw Error('unexpected activity');};`,
};
const built=await build({
  stdin:{contents:"export {ModelSettings} from './src/model-settings'; export {DraftProvider} from './src/editing';",resolveDir:process.cwd(),sourcefile:'dsh-settings-test-entry.tsx'},
  bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react/jsx-runtime'],
  plugins:[{name:'isolated-settings-boundaries',setup(b){
    b.onResolve({filter:/^\.\//},args=>{
      const owner=args.importer.replaceAll('\\','/');
      return ((owner.endsWith('/model-settings.tsx')&&args.path==='./api')||
        (owner.endsWith('/editing.tsx')&&['./shell','./activity'].includes(args.path)))
        ?{path:args.path,namespace:'dsh-settings-mock'}:null;
    });
    b.onLoad({filter:/.*/,namespace:'dsh-settings-mock'},args=>({contents:mocks[args.path],loader:'js'}));
  }}],
});
const module={exports:{}};
new Function('require','module','exports',built.outputFiles[0].text)(id=>id==='react'?mockReact:realRequire(id),module,module.exports);
const {ModelSettings,DraftProvider}=module.exports;
function nodes(tree){const found=[];const walk=node=>{
  if(node==null||typeof node==='boolean')return;
  if(Array.isArray(node)){node.forEach(walk);return;}
  if(typeof node==='object'&&node.props){found.push(node);walk(node.props.children);}
};walk(tree);return found;}
const text=node=>node==null||typeof node==='boolean'?'':Array.isArray(node)?node.map(text).join(''):
  typeof node==='object'?text(node.props?.children):String(node);
function button(tree,label){const result=nodes(tree).filter(n=>n.type==='button'&&text(n)===label);
  assert.equal(result.length,1,'actual button: '+label);return result[0];}
function control(tree,label,type='select'){
  const matches=nodes(tree).filter(n=>n.type==='label'&&text(n).startsWith(label));
  assert.equal(matches.length,1,'actual label: '+label);
  const result=nodes(matches[0]).filter(n=>n.type===type);assert.equal(result.length,1);return result[0];
}
const change=(node,value)=>node.props.onChange({target:{value}});
async function click(node){assert(!node.props.disabled,'explicit action enabled');await node.props.onClick();}
function fixture(runtime={ready:true,code:'READY',message:'local ready',version:'0.1.5rc1'}){
  let saved={model:{name:'储能云平台主分析',base_url:'https://model.example.invalid/v1',model:'synthetic-analysis',key_env:'SYNTHETIC_MAIN',
    vision:'documented',json_mode:true,timeout:90,max_tokens:8192,review_max_tokens:6144,review_retry_max_tokens:7168,
    context_chars:40000,action_seconds:200,thinking_disabled:false,review_thinking:true,documented_at:'2026-11-01',local_allowed:false,
    input_price:1,output_price:2,key_configured:true,key_source:'protected_store',persistent_key_supported:true,
    ...(runtime===undefined?{}:{intake_runtime:runtime})},
    vision:{name:'储能云平台视觉',base_url:'https://vision.example.invalid/v1',model:'synthetic-vision',key_env:'SYNTHETIC_VISION',
      vision:'documented',json_mode:false,timeout:60,thinking_disabled:true,key_configured:true}};
  const requests=[];let actions=0,refreshes=0,fail=false;
  const props={models:structuredClone(saved),busy:false,act:fn=>{actions++;return Promise.resolve().then(fn);},
    refresh:async()=>{refreshes++;props.models=structuredClone(saved);}};
  globalThis.__dshSettingsApi=async(path,method,payload)=>{
    requests.push({path,method,payload:structuredClone(payload)});
    if(fail)throw Error('synthetic configuration save failure');
    assert.equal(method,'PUT');assert(['/models/model','/models/vision'].includes(path),'configuration save only');
    const slot=path.split('/').at(-1);saved={...saved,[slot]:{...saved[slot],...structuredClone(payload)}};return {};
  };
  const provider=controller(DraftProvider,{children:null}),view=controller(ModelSettings,props);
  const app={view,provider,props,requests,get actions(){return actions;},get refreshes(){return refreshes;},
    fail(value){fail=value;},get saved(){return saved;},
    render(){
      for(let n=0;n<3;n++){provider.render();contextValues.set(provider.tree.type,provider.tree.props.value);view.render(props);}
      provider.render();contextValues.set(provider.tree.type,provider.tree.props.value);return view.tree;
    },
    get dirtyKeys(){return provider.tree.props.value.dirtyKeys;},
  };app.render();return app;
}
'''


def test_engine_change_is_local_until_explicit_save_and_runtime_is_readonly(tmp_path):
    run_component_script(tmp_path, r'''
      const app=fixture(),original=structuredClone(app.saved.model);
      const intake=()=>control(app.view.tree,'资料整理方式');
      assert.equal(intake().props.value,'direct','legacy missing engine displays direct');
      assert.equal(button(app.view.tree,'保存高级参数').props.disabled,true);
      assert.deepEqual(app.dirtyKeys,[]);assert.equal(app.requests.length,0);
      change(intake(),'deepseek-harness');app.render();
      assert.equal(intake().props.value,'deepseek-harness');
      assert.deepEqual(app.dirtyKeys,['model-settings']);assert.equal(app.actions,0);assert.equal(app.requests.length,0);
      const notice=nodes(app.view.tree).find(n=>n.type==='p'&&text(n).includes('先保存配置'));
      assert.equal(text(notice),'配置已修改。先保存配置，再测试连接或临时使用密钥。','an intake-only edit uses the generic configuration notice');
      app.props.models=structuredClone(app.props.models);app.render();
      assert.equal(intake().props.value,'deepseek-harness','status refresh retains the dirty choice');
      await click(button(app.view.tree,'保存高级参数'));app.render();
      const {intake_runtime,key_configured,key_source,persistent_key_supported,...existing}=original;
      assert.deepEqual(app.requests,[{path:'/models/model',method:'PUT',payload:{...existing,
        intake_engine:'deepseek-harness',budget_mode:'unlimited',max_calls:null}}]);
      assert.equal(app.refreshes,1);assert.equal(app.actions,1);assert.deepEqual(app.dirtyKeys,[]);
      assert.equal(app.saved.model.intake_runtime.version,'0.1.5rc1');
      assert.equal(button(app.view.tree,'保存高级参数').props.disabled,true);
    ''')


def test_existing_draft_guard_preserves_model_edits_before_vision_switch(tmp_path):
    run_component_script(tmp_path, r'''
      const app=fixture();
      change(control(app.view.tree,'资料整理方式'),'deepseek-harness');app.render();
      change(control(app.view.tree,'服务方名称','input'),'主分析未保存编辑');app.render();
      const requestVision=()=>change(control(app.view.tree,'配置用途'),'vision');
      requestVision();app.render();
      assert(nodes(app.provider.tree).some(n=>n.props.role==='dialog'&&n.props['aria-label']==='未保存的修改'));
      assert.equal(control(app.view.tree,'配置用途').props.value,'model');
      assert.equal(control(app.view.tree,'服务方名称','input').props.value,'主分析未保存编辑');
      assert.equal(app.requests.length,0);
      await click(button(app.provider.tree,'留在当前页'));app.render();
      assert.equal(control(app.view.tree,'资料整理方式').props.value,'deepseek-harness');
      requestVision();app.render();await click(button(app.provider.tree,'保存后离开'));app.render();
      assert.equal(app.requests.length,1);assert.equal(app.requests[0].path,'/models/model');
      assert.equal(app.requests[0].payload.name,'主分析未保存编辑');
      assert.equal(app.requests[0].payload.intake_engine,'deepseek-harness');
      assert.equal(control(app.view.tree,'配置用途').props.value,'vision');
      assert(!nodes(app.view.tree).some(n=>n.type==='label'&&text(n).startsWith('资料整理方式')));
      assert(!text(app.view.tree).includes('DSH 运行状态'));assert.deepEqual(app.dirtyKeys,[]);
      change(control(app.view.tree,'服务方名称','input'),'视觉配置单独编辑');app.render();
      await click(button(app.view.tree,'保存高级参数'));app.render();
      assert.equal(app.requests.length,2);assert.equal(app.requests[1].path,'/models/vision');
      assert(!('intake_engine' in app.requests[1].payload));assert(!('intake_runtime' in app.requests[1].payload));
      assert.equal(app.saved.model.name,'主分析未保存编辑');assert.equal(app.saved.model.intake_engine,'deepseek-harness');
      change(control(app.view.tree,'配置用途'),'model');app.render();
      assert.equal(control(app.view.tree,'资料整理方式').props.value,'deepseek-harness');
      assert.equal(control(app.view.tree,'服务方名称','input').props.value,'主分析未保存编辑');
    ''')


def test_rendered_runtime_states_are_conservative_and_do_not_start_any_action(tmp_path):
    run_component_script(tmp_path, r'''
      const cases=[
        [null,'尚未取得运行状态'],
        [{ready:true,code:'READY',message:'local ready',version:'0.1.5rc1'},'本机隔离组件可用。运行环境可用不代表生成结果已核对。'],
        [{ready:false,code:'SDK_MISSING',message:'隔离组件缺失，请由管理员处理。',version:'0.1.5rc1'},'隔离组件缺失，请由管理员处理。'],
        [{ready:false,code:'NOT_READY',message:'',version:'0.1.5rc1'},'本机隔离组件尚未就绪。'],
      ];
      for(const [runtime,expected] of cases){
        const app=fixture(runtime);if(runtime===null){delete app.props.models.model.intake_runtime;app.render();}
        const status=nodes(app.view.tree).find(n=>n.type==='p'&&text(n).startsWith('DSH 运行状态：'));
        assert.equal(text(status),'DSH 运行状态：'+expected);
        const html=renderToStaticMarkup(app.view.tree);
        assert(html.includes(expected));assert(!html.includes('模型验证通过'));assert(!html.includes('生成质量通过'));
        assert(html.includes('详细定义、成文和审查等保持现有流程'));
        assert(html.includes('选择后需保存，不会自动安装、发送资料或开始任务'));
        assert.equal(app.requests.length,0);assert.equal(app.actions,0);assert.equal(app.refreshes,0);
      }
    ''')


def test_failed_explicit_save_keeps_engine_edit_and_uses_existing_error_path(tmp_path):
    run_component_script(tmp_path, r'''
      const app=fixture();change(control(app.view.tree,'资料整理方式'),'deepseek-harness');app.render();app.fail(true);
      await assert.rejects(()=>click(button(app.view.tree,'保存高级参数')),/synthetic configuration save failure/);app.render();
      assert.equal(control(app.view.tree,'资料整理方式').props.value,'deepseek-harness');
      assert.equal(app.requests.length,1);assert.equal(app.refreshes,0);assert.deepEqual(app.dirtyKeys,['model-settings']);
      assert(!text(app.view.tree).includes('高级参数已保存。'));
      app.fail(false);await click(button(app.view.tree,'保存高级参数'));app.render();
      assert.equal(app.requests.length,2);assert.equal(app.actions,2);
      assert.equal(app.saved.model.intake_engine,'deepseek-harness');assert.deepEqual(app.dirtyKeys,[]);
    ''')
