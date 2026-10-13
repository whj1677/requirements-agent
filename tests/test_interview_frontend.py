"""Execute the real TypeScript sender, verifying send consent and recovery."""
import shutil
import subprocess
from pathlib import Path

import pytest

WEB=Path(__file__).resolve().parents[1]/'web'

def test_actual_sender_checks_receiver_sources_and_reuses_receipt():
    node=shutil.which('node')
    if not node:pytest.skip('Node.js unavailable; sender not executed')
    script=r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const bundle=await build({entryPoints:['src/interview-request.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {sendInterview,interviewSourceIds}=await import('data:text/javascript;base64,'+Buffer.from(bundle.outputFiles[0].text).toString('base64'));
      const input={action:'interview',expected_revision:3,message:'我还不知道',document_type:'prd'};
      const receiver={origin:'https://example.test',model:'paid'};
      const plan={expected_revision:3,stages:['interview'],missing:[],recipients:[receiver],sources:[{id:'S1'}],plan_hash:'approved'};
      let calls=[],receipt;
      const request=async(path,method,body)=>{calls.push({path,body});if(path.endsWith('/plan'))return plan;return {id:'TASK1'};};
      const projected={sources:[{id:'S1'},{id:'S2'}],active_source_ids:['S1'],domain_context:{selected_source_ids:['S1']}};
      assert.equal((await sendInterview(request,'/p',input,receiver,interviewSourceIds(projected),r=>receipt=r)).id,'TASK1');
      assert.deepEqual(interviewSourceIds({...projected,active_source_ids:[]}),[],'empty selection must not fall back to all sources');
      assert.throws(()=>interviewSourceIds({...projected,active_source_ids:undefined}),/资料范围尚未取得/);
      assert.deepEqual(interviewSourceIds({sources:[{id:'legacy'},{id:'excluded',excluded:true}]}),['legacy']);
      assert.equal(calls[1].body.authorize,true);
      assert.equal(calls[1].body.message,input.message);
      const original=calls[1].body;
      calls=[];
      await sendInterview(request,'/p',{...input,expected_revision:9},receiver,[],()=>assert.fail('no replan'),receipt);
      assert.equal(calls.length,1);assert.deepEqual(calls[0].body,original);
      for(const change of [p=>p.stages=['vision','interview'],p=>p.recipients=[{...receiver,model:'other'}],p=>p.sources=[{id:'new'}],p=>p.expected_revision=4]){
        const backup=structuredClone(plan);change(plan);calls=[];
        await assert.rejects(()=>sendInterview(request,'/p',input,receiver,['S1'],()=>assert.fail('must not prepare')));
        assert.equal(calls.length,1);Object.assign(plan,backup);
      }
      calls=[];
      const broken=async(path,method,body)=>{calls.push({path,body});if(path.endsWith('/plan'))return plan;throw Error('ack lost');};
      receipt=undefined;
      await assert.rejects(()=>sendInterview(broken,'/p',input,receiver,['S1'],r=>receipt=r));
      assert(receipt?.idempotency_key);
      const first=calls[1].body;calls=[];
      await sendInterview(request,'/p',input,receiver,['S1'],()=>assert.fail('no new key'),receipt);
      assert.deepEqual(calls[0].body,first);
    '''
    result=subprocess.run([node,'--input-type=module','-e',script],cwd=WEB,capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr


def test_rendered_draft_separates_goal_change_and_new_proposal():
    node=shutil.which('node')
    if not node:pytest.skip('Node.js unavailable; rendering not executed')
    script=r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/product-flow.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const p={id:'P-draft',revision:3,messages:[],sources:[],items:[],questions:[],product_context:{intent:'减少重复整理',change_scope:'新增备注',preserve_scope:'导出保持原状'},product_context_proposal:{preserve_scope:'建议改变导出'},product_flow:[{}, {missing:[],complete:false}],documents:{}};
      const html=renderToStaticMarkup(React.createElement(module.exports.ProductCheckpoint,{p,phase:1,busy:false,mutate:()=>assert.fail('render must not write'),go:()=>{}}));
      for(const text of ['希望达到的目标','本次改动','减少重复整理','新增备注','导出保持原状','建议改变导出','最新分析提出了不同建议，待核对','放入编辑框核对'])assert(html.includes(text),text);
      assert(html.includes('已保存的核对内容'));
      assert.equal([...html.matchAll(/已保存的核对内容/g)].length,3,'blank fields are not saved evidence');
      assert.equal(p.product_context.preserve_scope,'导出保持原状');
    '''
    result=subprocess.run([node,'--input-type=module','-e',script],cwd=WEB,capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr
