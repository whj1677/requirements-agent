from tests.test_scope_frontend import run_ts


def test_public_checkpoint_labels_preserve_action_identity_and_business_messages():
    run_ts(r'''
      import assert from 'node:assert/strict'; import {build} from 'esbuild';
      const built=await build({entryPoints:['src/workflow-labels.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {publicBlocker,publicStep}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      assert.deepEqual([1,2,3,4,5,6].map(step=>publicStep(step).number),[1,2,2,2,3,3]);
      for(const code of ['stage_incomplete','prerequisite_stage']) {
        const row={key:'stage-3',code,title:'澄清流程与规则',message:'请完成第3步',actions:[{kind:'stage',step:3,label:'前往本步骤核对'}]};
        const before=JSON.stringify(row), result=publicBlocker(row);
        assert.equal(result.title,'完善需求'); assert(!result.message.includes('第3步'));
        assert.equal(result.actions[0].step,3,'backend checkpoint must remain unchanged');
        assert.equal(result.actions[0].label,'前往完善需求');assert.equal(JSON.stringify(row),before);
      }
      for(const code of ['document_discussion','question','legacy']) {
        const row={key:'Q',code,title:'本篇文档的讨论补充',message:'真实业务说明',actions:[{kind:'stage',step:3,label:'处理本次文档讨论'}]};
        assert.equal(publicBlocker(row),row,'do not replace specific discussion or business content');
      }
    ''')
