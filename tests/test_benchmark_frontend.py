"""Exercise the shipped benchmark helper and rendered intake controls."""
from tests.test_scope_frontend import run_ts


def test_benchmark_request_keeps_reference_identity_and_requires_real_content():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';
      const built=await build({entryPoints:['src/benchmark-intake.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const m=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      assert.deepEqual(m.extractPublicUrls('对标 https://a.example/x，及 http://b.example/y。重复 https://a.example/x'),['https://a.example/x','http://b.example/y']);
      const p={sources:[{id:'A',purpose:'reference',parse_status:'read',excerpts:[{text:'事实'}]},{id:'B',purpose:'current',parse_status:'partial',excerpts:[{text:'部分'}]},{id:'X',purpose:'template',parse_status:'read',excerpts:[{text:'模板'}]},{id:'F',purpose:'reference',parse_status:'failed'},{id:'E',purpose:'reference',parse_status:'read',excluded:true,excerpts:[{text:'旧'}]}]};
      assert.deepEqual(m.initialBenchmarkSourceIds(p),['A']);
      assert.equal(m.usableBenchmarkSource(p.sources[3]),false);
      assert.equal(m.usableBenchmarkSource(p.sources[0]),true);
      assert.deepEqual(m.restoredBenchmarkSourceIds(p,['F','A','E','B']),['A','B']);
      assert.deepEqual(m.prepareBenchmark(p,' 对标对象 ','范围',['A','B','A']),{target:'对标对象',focus:'范围',source_ids:['A','B']});
      for(const id of ['X','F','E'])assert.throws(()=>m.prepareBenchmark(p,'对象','',[id]));
      assert.throws(()=>m.prepareBenchmark(p,'','',['A']));
      assert.throws(()=>m.prepareBenchmark(p,'对象','',[]));
      assert.deepEqual(m.pastedWebpageReference(' https://example.com/page ','实际页面',' 真实正文 '),{
        title:'人工粘贴网页正文 · 实际页面',
        text:'来源网址：https://example.com/page\n页面标题：实际页面\n采集方式：用户粘贴网页正文\n\n 真实正文 ',purpose:'reference'});
      assert.throws(()=>m.pastedWebpageReference('https://example.com/page','实际页面','   '),/实际看到/);
    ''')


def test_benchmark_form_renders_explicit_entry_without_import_side_effect():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/benchmark-intake.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const html=renderToStaticMarkup(React.createElement(module.exports.BenchmarkIntake,{p:{id:'P',sources:[]},busy:false,text:'对标产品 https://example.com/',onSources:()=>assert.fail('SSR cannot open'),onImport:()=>assert.fail('SSR cannot import'),onPrepare:()=>assert.fail('SSR cannot plan')}));
      assert(html.includes('对标参考产品'));assert(html.includes('待审核草稿'));assert(html.includes('aria-expanded="false"'));
    ''')


def test_comparison_report_preserves_quote_identity_and_never_marks_suggestions_as_decided():
    run_ts(r'''
      import assert from 'node:assert/strict';import {build} from 'esbuild';import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const built=await build({entryPoints:['src/benchmark-report.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const p={sources:[{id:'S1',title:'参考页面',version:2,sha256:'changed'}],items:[{id:'REQ-1',title:'语音候选'}],benchmark_analysis:{summary:'根据参考分析',request:{target:'参考产品'},source_versions:[{id:'S1',version:1,sha256:'before'}],observations:[{title:'语音交互',reference_quote:'<script>引用内容只是原文</script>',source_ref:{source_id:'S1',excerpt_id:'E1'},difference:'',suggested_application:'建议增加语音入口',proposal_refs:['REQ-1']}],limitations:['仅能读取公开页']}};
      const before=structuredClone(p);
      const html=renderToStaticMarkup(React.createElement(module.exports.BenchmarkReport,{p,onItem:()=>assert.fail('SSR cannot adopt'),onSource:()=>assert.fail('SSR cannot open')}));
      for(const text of ['参考内容与出处','与当前需求的差异','可借鉴建议','核对候选：','语音候选','差异待核对','参考资料已变化','仅能读取公开页'])assert(html.includes(text),text);
      assert(html.includes('&lt;script&gt;'));assert(!html.includes('<script>引用'));
      assert.deepEqual(p,before);
    ''')
