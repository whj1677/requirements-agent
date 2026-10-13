"""Exercise selection, draft-save and acknowledgement boundaries in the UI helpers."""
from tests.test_scope_frontend import run_ts


def test_batch_uses_checked_ids_and_reviewed_version_after_saving():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/bulk-adoption-review.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {prepareBulkAdoption,submitBulkAdoption}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const ids=['REQ-2','REQ-1'];
      const snapshot={expected_revision:9,items:ids.map(id=>({item:{id},content_hash:'hash-'+id,sources:[],review_required:true})),blockers:[]};
      let calls=[];
      const args={ids,save:async()=>{calls.push('save');},read:async()=>{calls.push('read');return {revision:9,items:[{id:'REQ-3'},...snapshot.items.map(r=>r.item)]};},review:async body=>{calls.push(['review',body]);return snapshot;}};
      const review=await prepareBulkAdoption(args);
      assert.deepEqual(calls,['save','read',['review',{item_ids:['REQ-2','REQ-1'],expected_revision:9}]]);
      calls=[];
      await submitBulkAdoption(review,true,async body=>{calls.push(body);return {revision:10};});
      assert.deepEqual(calls,[{expected_revision:9,item_ids:['REQ-2','REQ-1'],content_hashes:{'REQ-2':'hash-REQ-2','REQ-1':'hash-REQ-1'},review_confirmed:true}]);
      assert(!calls[0].item_ids.includes('REQ-3'));
      assert(!('scope_ids' in calls[0]));
    ''')


def test_failed_save_mismatched_snapshot_or_missing_review_cannot_submit():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      const built=await build({entryPoints:['src/bulk-adoption-review.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {prepareBulkAdoption,submitBulkAdoption}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const snapshot={expected_revision:9,items:[{item:{id:'REQ-1'},content_hash:'h',sources:[],review_required:true}],blockers:[]};
      let calls=[];
      const args={ids:['REQ-1'],save:async()=>{},read:async()=>({revision:9}),review:async()=>snapshot};
      await assert.rejects(()=>prepareBulkAdoption({...args,save:async()=>{calls.push('save');throw Error('save failed');},read:async()=>assert.fail('read after failed save')}),/save failed/);
      assert.deepEqual(calls,['save']);
      for(const changed of [{...snapshot,expected_revision:10},{...snapshot,items:[{...snapshot.items[0],item:{id:'REQ-2'}}]},{...snapshot,items:[]}]){
        await assert.rejects(()=>prepareBulkAdoption({...args,review:async()=>changed}),/变化/);
      }
      for(const ids of [[],['REQ-1','REQ-1']])await assert.rejects(()=>prepareBulkAdoption({...args,ids,save:async()=>assert.fail('invalid selection saved')}));
      const write=async()=>assert.fail('unreviewed or blocked adoption submitted');
      await assert.rejects(()=>submitBulkAdoption(snapshot,false,write),/勾选确认/);
      await assert.rejects(()=>submitBulkAdoption({...snapshot,blockers:[{id:'REQ-1',title:'一',message:'需要修订'}]},true,write),/先处理/);
      await assert.rejects(()=>submitBulkAdoption({...snapshot,items:[]},true,write));
      await assert.rejects(()=>submitBulkAdoption(snapshot,true,async()=>{throw Error('stale revision');}),/stale revision/);
    ''')


def test_saved_scope_does_not_preselect_batch_and_pending_behavior_can_be_selected():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/product-flow.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const p={id:'P-batch',revision:4,messages:[],sources:[],items:[{id:'REQ-1',kind:'requirement',title:'语音答疑',statement:'学生可以实时通话答疑',applies_to:'to_be',selection_status:'candidate',behavior_review_required:true}],questions:[],product_context:{scope_ids:['REQ-1']},behavior_review_statuses:{'REQ-1':'required'},product_flow:[{},{available:true,missing:[],complete:false}],documents:{}};
      const html=renderToStaticMarkup(React.createElement(module.exports.ProductCheckpoint,{p,phase:1,busy:false,mutate:()=>assert.fail('render wrote data'),go:()=>{}}));
      assert(html.includes('批量核对并采纳（0）'));
      assert(html.includes('全部纳入本期'));
      assert(html.includes('去核对此条行为'));
      assert(html.includes('id="behavior-review-P-batch-checkpoint-1-REQ-1-behavior"'));
      assert(/<option value="selected" disabled="">采纳此条<\/option>/.test(html));
      assert(/<input type="checkbox" aria-label="本次采纳 语音答疑"\/>/.test(html),'saved scope can be temporarily selected but must not already be checked');
      assert(/<button type="button" class="primary" disabled="">批量核对并采纳/.test(html),'batch review requires a new temporary selection');
      assert(!html.includes('aria-label="本期范围 语音答疑"'),'scope must not use the same checkbox');
      assert(html.includes('移出本期 语音答疑'));
      const empty=renderToStaticMarkup(React.createElement(module.exports.ProductCheckpoint,{p:{...p,product_context:{scope_ids:[]}},phase:1,busy:false,mutate:()=>{},go:()=>{}}));
      assert(/<button type="button" class="primary" disabled="">批量核对并采纳/.test(empty));
      assert(empty.includes('先将需要的条目纳入本期，再保存范围'));
      const revision=renderToStaticMarkup(React.createElement(module.exports.ProductCheckpoint,{p:{...p,product_context:{scope_ids:['REQ-1','REQ-revision']},items:[...p.items,{...p.items[0],id:'REQ-revision',target_item_id:'REQ-1',title:'修订候选'}]},phase:1,busy:false,mutate:()=>{},go:()=>{}}));
      assert(revision.includes('已保存范围 2 条'));
      assert(revision.includes('批量核对并采纳（0）'));
      assert(revision.includes('1 条修订候选需单独对照原条款采纳'));
    ''')


def test_batch_presave_excludes_unrelated_drafts_and_retains_failed_inputs():
    run_ts(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url);
      const built=await build({entryPoints:['src/editing.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const {saveRegisteredDrafts}=module.exports;
      let calls=[];
      const entry=(label,save)=>({label,save,discard:()=>assert.fail('discarded input')});
      const drafts=new Map([
        ['P-checkpoint-2-REQ-1-behavior',entry('隐藏阶段同一条目',async()=>assert.fail('hidden editor saved'))],
        ['source-url',entry('资料链接',async()=>assert.fail('unrelated URL fetched'))],
        ['P-checkpoint-1-REQ-other-behavior',entry('未勾选条目',async()=>assert.fail('unselected draft saved'))],
        ['P-checkpoint-1',entry('范围',async()=>assert.fail('own draft must save separately'))],
        ['P-checkpoint-1-REQ-1-behavior',entry('勾选条目',async()=>{calls.push('REQ-1');})],
      ]);
      await saveRegisteredDrafts(drafts,'P-checkpoint-1',['P-checkpoint-1-REQ-1-behavior']);
      assert.deepEqual(calls,['REQ-1']);
      assert.deepEqual([...drafts.keys()],['P-checkpoint-2-REQ-1-behavior','source-url','P-checkpoint-1-REQ-other-behavior','P-checkpoint-1']);
      drafts.set('P-checkpoint-1-REQ-2-behavior',entry('保存失败',async()=>{throw Error('save failed');}));
      await assert.rejects(()=>saveRegisteredDrafts(drafts,undefined,['P-checkpoint-1-REQ-2-behavior']),/save failed/);
      assert(drafts.has('P-checkpoint-1-REQ-2-behavior'),'failed draft remains available for retry');
      assert(drafts.has('source-url'));assert(drafts.has('P-checkpoint-1-REQ-other-behavior'));
    ''')
