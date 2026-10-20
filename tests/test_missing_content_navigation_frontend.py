"""Identity routing and file-selection compatibility, independent of page rendering."""
from tests.test_scope_frontend import run_ts


def test_continue_resolves_stage_to_actual_missing_field_without_mutating_project():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/blocker-navigation.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {firstActionableBlocker}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const field={key:'intent',actions:[{kind:'context',field:'intent'}]};
      const document={key:'prd',actions:[{kind:'document',document:'prd',mode:'generate'}]};
      const p={confirmation_blockers:[{key:'stage',actions:[{kind:'stage',step:2}]}],product_flow:[{}, {blockers:[field]}]};
      const before=structuredClone(p);
      assert.deepEqual(firstActionableBlocker(p),{action:field.actions[0],row:field});
      assert.deepEqual(firstActionableBlocker(p,[{key:'no-actions',actions:[]},document]),{action:document.actions[0],row:document});
      assert.equal(firstActionableBlocker(p,[]),undefined);
      assert.deepEqual(p,before,'jumping to a gap must not confirm, fill or change a stage');
      p.product_flow[1].blockers=p.confirmation_blockers;
      assert.equal(firstActionableBlocker(p).action.kind,'stage','a cyclic prerequisite must stop without recursion');
    ''')


def test_chat_file_selection_accepts_html_and_powerpoint_alongside_pdf():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/pasted-images.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {extractChatAttachments}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const names=['方案.HTML','原型.htm','介绍.ppt','介绍.pptx','资料.pdf'];
      const result=extractChatAttachments(names.map(name=>new File(['synthetic selection only'],name)));
      assert.equal(result.rejected.length,0);
      assert.deepEqual(result.files.map(file=>file.name),['方案.html','原型.htm','介绍.ppt','介绍.pptx','资料.pdf']);
    ''')
