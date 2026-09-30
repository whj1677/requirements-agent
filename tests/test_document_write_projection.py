"""Actual rendered reader and project-write boundaries, without browser mocks."""
import shutil
import subprocess
from pathlib import Path

import pytest

WEB=Path(__file__).resolve().parents[1]/'web'


def node_script(script):
    node=shutil.which('node')
    if not node:pytest.skip('Node.js unavailable; frontend check not executed')
    result=subprocess.run([node,'--input-type=module','-e',script],cwd=WEB,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr


def test_project_write_keeps_only_the_matching_immutable_document_reader():
    node_script(r'''
      import assert from 'node:assert/strict';
      import fs from 'node:fs';
      import ts from 'typescript';
      const js=ts.transpileModule(fs.readFileSync('src/project-update.ts','utf8'),
        {compilerOptions:{module:ts.ModuleKind.ES2022}}).outputText;
      const {mergeProjectWrite}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
      const reader={title:'产品正文',sections:[{text:'可交付正文'}]};
      const current={id:'P-test',revision:1,documents:{prd:{id:'DOC-a',brief_hash:'a',reader},mrd:{id:'DOC-old',reader}}};
      const written={id:'P-test',revision:2,items:[{statement:'已保存'}],documents:{prd:{id:'DOC-a',brief_hash:'a',user_review:{checked:true}}}};
      const before=JSON.stringify({current,written});
      const result=mergeProjectWrite(current,written);
      assert.equal(result.revision,2);
      assert.equal(result.documents.prd.reader,reader);
      assert.equal(result.documents.prd.user_review.checked,true);
      assert.equal(result.documents.mrd,undefined,'deleted documents must stay deleted');
      assert.equal(JSON.stringify({current,written}),before,'merge must not mutate its inputs');
      for(const next of [{id:'DOC-new',brief_hash:'a'},{id:'DOC-a',brief_hash:'changed'}]){
        assert.equal(mergeProjectWrite(current,{documents:{prd:next}}).documents.prd.reader,undefined,
          'a new or different artifact cannot reuse another document reader');
      }
    ''')


def test_rendered_document_never_exposes_canonical_discussion_during_write_refresh():
    node_script(r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url);
      const bundled=await build({entryPoints:['src/workbench.tsx'],bundle:true,write:false,
        format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
      const module={exports:{}};
      new Function('require','module','exports',bundled.outputFiles[0].text)(require,module,module.exports);
      const React=require('react');
      const {renderToStaticMarkup}=require('react-dom/server');
      const {DocumentViewer}=module.exports;
      const document={id:'DOC-test',brief_hash:'a',draft_revision:2,content:{title:'需求',document_type:'prd',
        sections:[{section_id:'audit',title:'历史分析提示（非当前事实）',level:1,blocks:[{kind:'text',text:'禁止进入产品正文的讨论秘密'}]}]}};
      const props={document,items:[],currentRevision:2,currentBriefHash:'a',downloadRoot:'/synthetic',readiness:{ready:true}};
      const pending=renderToStaticMarkup(React.createElement(DocumentViewer,props));
      assert(!pending.includes('讨论秘密')&&!pending.includes('历史分析提示'));
      assert(pending.includes('正在取得正文阅读视图'));
      assert(!pending.includes('ZIP 下载'),'unloaded projection must not offer a premature download');
      const reader={title:'需求｜PRD',document_type:'prd',outline:[],sections:[{section_id:'product',title:'产品要求',level:1,
        reading_nodes:[{kind:'paragraph',text:'核对后的产品正文'}]}]};
      const ready=renderToStaticMarkup(React.createElement(DocumentViewer,{...props,document:{...document,reader}}));
      assert(ready.includes('核对后的产品正文')&&!ready.includes('讨论秘密'));
      assert(ready.includes('ZIP 下载'));
    ''')
