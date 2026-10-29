"""Exercise saved-project reads through the extracted hook, without a browser server."""
from tests.test_scope_frontend import run_ts


HARNESS = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
const built=await build({entryPoints:['src/use-workspace-data.ts'],bundle:true,write:false,
  format:'cjs',platform:'node',external:['react']});
const tick=()=>new Promise(setImmediate);
const reply=(value,status=200)=>({ok:status===200,status,text:async()=>JSON.stringify(value)});
const project=revision=>({id:'P-read',name:'合成读取项目',revision,sources:[],items:[],questions:[],documents:{}});
const task=(id,status='succeeded')=>({id,status,run_ids:[],step_states:[]});
function harness(handler) {
  const slots=[],effects=[],snapshots=[],requests=[];
  let cursor=0,view;
  const react={
    useState(initial){const index=cursor++;if(!(index in slots))slots[index]=typeof initial==='function'?initial():initial;
      return [slots[index],value=>{slots[index]=typeof value==='function'?value(slots[index]):value;}];},
    useRef(initial){const index=cursor++;return slots[index]||(slots[index]={current:initial});},
    useEffect(callback){cursor++;effects.push(callback);},
  };
  const module={exports:{}};
  new Function('require','module','exports',built.outputFiles[0].text)(name=>{
    assert.equal(name,'react');return react;
  },module,module.exports);
  globalThis.fetch=async(path,options)=>{assert.equal(options.method,'GET','reading must never mutate or start a model');
    requests.push(path);return handler(path);};
  function render(){cursor=0;effects.length=0;view=module.exports.useWorkspaceData('P-read',false,p=>snapshots.push(p.revision));return view;}
  render();
  return {render,requests,snapshots,effects,get view(){return view;}};
}
'''


def test_refresh_is_serial_and_cannot_overwrite_a_newer_confirmed_write():
    run_ts(HARNESS + r'''
      let reads=0,releaseFirst,releaseSecond;
      const h=harness(path=>{
        if(path==='/api/projects/P-read')return new Promise(resolve=>{if(++reads===1)releaseFirst=resolve;else releaseSecond=resolve;});
        return reply(path.endsWith('/artifacts')?{document_artifact:[]}:[task('T-read')]);
      });
      const first=h.view.refresh(),second=h.view.refresh();await tick();
      assert.equal(h.requests.length,4,'the second batch must wait for the first batch');
      const saved=project(9);h.view.projectRef.current=saved;h.view.setP(saved);
      releaseFirst(reply(project(8)));await first;await tick();
      assert.equal(h.render().p.revision,9,'an old GET must not undo the confirmed write');
      assert.deepEqual(h.snapshots,[],'stale snapshots must not update the project list');
      assert.equal(h.requests.length,8);
      releaseSecond(reply(project(10)));await second;
      assert.equal(h.render().p.revision,10);
      assert.equal(h.view.projectRef.current.revision,10);
      assert.deepEqual(h.snapshots,[10]);
    ''')


def test_failed_reads_preserve_saved_state_and_do_not_poison_the_refresh_queue():
    run_ts(HARNESS + r'''
      let mode='good';
      const h=harness(path=>{
        if(path==='/api/projects/P-read')return reply(mode==='project-error'?{code:'FAILED',message:'synthetic failure'}:project(mode==='good'?1:2),mode==='project-error'?500:200);
        if(path.endsWith('/actions'))return reply(mode==='partial'?{code:'FAILED',message:'synthetic task failure'}:[task('T-kept')],mode==='partial'?500:200);
        return reply(path.endsWith('/artifacts')?{document_artifact:[]}:[]);
      });
      await h.view.refresh();h.render();
      mode='project-error';await assert.rejects(h.view.refresh(),/synthetic failure/);h.render();
      assert.equal(h.view.p.revision,1);assert(h.view.fatal);
      assert.deepEqual(h.view.tasks.map(t=>t.id),['T-kept']);
      mode='partial';await h.view.refresh();h.render();
      assert.equal(h.view.p.revision,2);assert.equal(h.view.fatal,null);
      assert.deepEqual(h.view.stale,['任务列表']);assert.equal(h.view.tasksLoaded,true);
      assert.deepEqual(h.view.tasks.map(t=>t.id),['T-kept']);
      mode='recovered';await h.view.refresh();h.render();
      assert.deepEqual(h.view.stale,[]);
    ''')
