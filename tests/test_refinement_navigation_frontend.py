"""Task navigation and matching action copy without business mutations."""
from tests.test_scope_frontend import run_ts


def test_pending_queue_skips_reviewed_answers_and_preserves_input_order():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/pm-prototype/refinement-navigation.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {nextPendingAnswer}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const pending=['Q1','Q3'],order=['Q1','Q2','Q3'];
      assert.equal(nextPendingAnswer(pending,'',1,order),'Q1');
      assert.equal(nextPendingAnswer(pending,'Q1',1,order),'Q3');
      assert.equal(nextPendingAnswer(pending,'Q3',1,order),'Q1');
      assert.equal(nextPendingAnswer(pending,'Q2',1,order),'Q3','a just-reviewed answer moves forward');
      assert.equal(nextPendingAnswer(pending,'Q2',-1,order),'Q1');
      assert.equal(nextPendingAnswer([], 'Q2',1,order),null);
      assert.deepEqual(pending,['Q1','Q3']);assert.deepEqual(order,['Q1','Q2','Q3']);
    ''')


def test_navigation_uses_the_same_action_label_and_distinguishes_local_draft():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/pm-prototype/refinement-navigation.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {refinementActionCopy}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const pending=refinementActionCopy(false,4,0);
      assert.equal(pending.label,'保存并继续核对');
      assert(pending.navigation.includes(pending.label)&&pending.navigation.includes('4 项回答待落实'));
      assert(pending.detail.includes('仍待落实的回答保留'));
      const missing=refinementActionCopy(false,2,1);
      assert(missing.detail.includes('先补齐必填')&&missing.detail.includes('当前浏览器'));
      const complete=refinementActionCopy(false,0,0);
      assert.equal(complete.label,'确认并进入交付');assert(complete.navigation.includes(complete.label));
      assert.equal(refinementActionCopy(true,0,0).label,'返回交付');
    ''')
