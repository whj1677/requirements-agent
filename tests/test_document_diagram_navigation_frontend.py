"""The reader starts with readable detail while retaining the complete overview."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const built=await build({entryPoints:['src/document-details.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom','react-dom/server']});
const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
const svg=text=>'data:image/svg+xml;base64,'+Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><text>'+text+'</text></svg>').toString('base64');
const node={title:'场站业务流程',detail_hash:'current',width:2400,height:3000,image_uri:svg('overview')};
const view=(id)=>({id,title:'分图 '+id,width:800,height:650,image_uri:svg(id)});
const render=node=>renderToStaticMarkup(React.createElement(module.exports.DetailDiagram,{node}));
'''


def test_readable_first_view_is_explicitly_partial_and_overview_remains_selectable():
    run_ts(LOAD + r'''
      const first=view('V1'),second=view('V2'),before=JSON.stringify(node);
      const markup=render({...node,views:[first,second]});
      assert(markup.includes('src="'+first.image_uri+'"'));
      assert(!markup.includes('src="'+node.image_uri+'"'));
      assert(markup.includes('分图 1 / 2'));
      assert(markup.includes('完整总览（索引）'));
      assert(/<button[^>]*disabled=""[^>]*>上一张<\/button>/.test(markup));
      assert(/<button[^>]*>下一张<\/button>/.test(markup));
      assert.equal(JSON.stringify(node),before);
    ''')


def test_invalid_views_never_fetch_remote_images_and_single_diagram_stays_available():
    run_ts(LOAD + r'''
      const invalid={...view('remote'),image_uri:'https://example.invalid/untrusted.svg'};
      const fallback=render({...node,views:[invalid]});
      assert(fallback.includes('src="'+node.image_uri+'"'));
      assert(!fallback.includes('https://example.invalid'));
      assert(!fallback.includes('分图 1 /'));
      const valid=view('valid'),detail=render({...node,views:[invalid,valid]});
      assert(detail.includes('src="'+valid.image_uri+'"'));
      assert(detail.includes('分图 1 / 1'));
      assert(!detail.includes('下一张'));
    ''')
