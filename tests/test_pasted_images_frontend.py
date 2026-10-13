"""Execute the actual paste helper under a Node DOM-compatible File shim."""
import shutil
import subprocess
from pathlib import Path

import pytest


WEB = Path(__file__).resolve().parents[1] / 'web'


def test_paste_images_filters_and_names_actual_files():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable; paste helper not executed')
    script = r'''
      import assert from 'node:assert/strict';
      import {File as NodeFile} from 'node:buffer';
      import {build} from 'esbuild';
      globalThis.File = NodeFile;
      const built = await build({entryPoints:['src/pasted-images.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {extractPastedImages} = await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const make=(name,type,size=3)=>new File([Buffer.alloc(size)],name,{type,lastModified:123});
      const transfer=(entries, fallback=[])=>({items:entries.map(x=>x.kind==='string'?{kind:'string',type:'text/plain',getAsFile:()=>null}:{kind:'file',type:x.type,getAsFile:()=>x}),files:fallback});
      assert.deepEqual(extractPastedImages(transfer([{kind:'string'}])),{files:[],rejected:[]},'plain text must remain untouched');
      const png=make('image.png','image/png'),jpg=make('现场照片.jpeg','image/jpeg'),webp=make('','image/webp');
      const gif=make('动画.gif','image/gif'),oversize=make('巨大.png','image/png',12*1024*1024+1);
      const result=extractPastedImages(transfer([{kind:'string'},png,jpg,webp,gif,oversize]));
      assert.equal(result.files.length,3);
      assert.deepEqual(result.files.map(f=>f.type),['image/png','image/jpeg','image/webp']);
      assert.equal(result.files[1].name,'现场照片.jpg');
      assert(result.files[0].name.startsWith('粘贴图片-') && result.files[0].name.endsWith('.png'));
      assert(result.files[2].name.startsWith('粘贴图片-') && result.files[2].name.endsWith('.webp'));
      assert.notEqual(result.files[0].name,result.files[2].name);
      assert.equal(result.files[0].size,3);
      assert.equal(result.files[0].lastModified,123);
      assert.equal(result.rejected.length,2);
      assert(result.rejected.some(s=>s.includes('GIF')),'GIF must be visibly rejected');
      assert(result.rejected.some(s=>s.includes('12 MiB')),'oversize must be visibly rejected');
      const another=extractPastedImages(transfer([png]));
      assert.notEqual(another.files[0].name,result.files[0].name,'generic names must be unique across calls');
      const duplicate=extractPastedImages(transfer([make('a.png','image/png'),make('a.png','image/png')]));
      assert.deepEqual(duplicate.files.map(f=>f.name),['a.png','a-2.png']);
      const boundary=extractPastedImages(transfer([make('最大.png','image/png',12*1024*1024)]));
      assert.equal(boundary.files.length,1,'exactly 12 MiB is allowed');
      const unsafe=extractPastedImages(transfer([make('../folder/../../x?.png','image/png')]));
      assert.equal(unsafe.files[0].name,'x_.png','clipboard names cannot retain paths or unsafe characters');
      const unsupported=extractPastedImages(transfer([make('图.svg','image/svg+xml')]));
      assert.equal(unsupported.files.length,0);
      assert(unsupported.rejected[0].includes('image/svg+xml'));
      const filesOnly=extractPastedImages({items:[],files:[make('fallback.webp','image/webp')]});
      assert.equal(filesOnly.files[0].name,'fallback.webp');
      assert.deepEqual(extractPastedImages(transfer([make('notes.txt','text/plain')])),{files:[],rejected:[]});
    '''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=WEB,
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_chat_attachment_files_from_picker_drop_and_clipboard():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable; attachment helper not executed')
    script = r'''
      import assert from 'node:assert/strict';
      import {File as NodeFile} from 'node:buffer';
      import {build} from 'esbuild';
      globalThis.File = NodeFile;
      const built=await build({entryPoints:['src/pasted-images.ts'],bundle:true,write:false,format:'esm',platform:'node'});
      const {extractChatAttachments}=await import('data:text/javascript;base64,'+Buffer.from(built.outputFiles[0].text).toString('base64'));
      const file=(name,type='application/octet-stream',size=4)=>new File([Buffer.alloc(size)],name,{type});
      const doc=file('需求说明.docx'),pdf=file('验收范围.pdf','application/pdf');
      const png=file('image.png','image/png'),gif=file('动图.gif','image/gif');
      const selected=extractChatAttachments([doc,pdf,png,gif,file('程序.exe'),file('空白.txt','text/plain',0)]);
      assert.deepEqual(selected.files.map(f=>f.name),['需求说明.docx','验收范围.pdf',selected.files[2].name]);
      assert(selected.files[2].name.startsWith('粘贴图片-')&&selected.files[2].name.endsWith('.png'));
      assert.equal(selected.rejected.length,3);
      assert(selected.rejected.some(x=>x.includes('GIF')));
      assert(selected.rejected.some(x=>x.includes('程序.exe')));
      assert(selected.rejected.some(x=>x.includes('空白.txt')));
      const transfer={items:[{kind:'string',type:'text/plain',getAsFile:()=>null},
        ...[doc,png,pdf].map(f=>({kind:'file',type:f.type,getAsFile:()=>f}))],files:[doc,png,pdf]};
      assert.deepEqual(extractChatAttachments(transfer).files.map(f=>f.type),[doc.type,png.type,pdf.type],
        'drag/drop must take each actual file item once in order');
      assert.deepEqual(extractChatAttachments({items:[{kind:'string',type:'text/plain',getAsFile:()=>null}],files:[]}),
        {files:[],rejected:[]},'plain text paste remains native textarea behavior');
      const filesOnly=extractChatAttachments({items:[],files:[file('笔记.md','text/markdown')]});
      assert.equal(filesOnly.files[0].name,'笔记.md');
      const nullItem=extractChatAttachments({items:[{kind:'file',type:'image/png',getAsFile:()=>null}],files:[png]});
      assert.equal(nullItem.files.length,1);
      assert.deepEqual(nullItem.rejected,[],'files fallback must not report a recovered image as unreadable');
      const oversize=extractChatAttachments([file('大文件.pdf','application/pdf',12*1024*1024+1)]);
      assert.equal(oversize.files.length,0);
      assert(oversize.rejected[0].includes('12 MiB'));
      assert.equal(extractChatAttachments([file('上限.txt','text/plain',12*1024*1024)]).files.length,1);
      assert.equal(extractChatAttachments([file('unknown.png','application/octet-stream')]).files[0].type,'image/png',
        'supported image extensions remain usable when the browser supplies a generic MIME');
    '''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=WEB,
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_actual_chat_render_shows_direct_file_picker_and_document_history_without_thumbnail():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable; chat render not executed')
    script = r'''
      import assert from 'node:assert/strict';
      import {build} from 'esbuild';
      import {createRequire} from 'node:module';
      const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
      const values=new Map();globalThis.sessionStorage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
      const built=await build({entryPoints:['src/intake-chat.tsx'],bundle:true,write:false,format:'cjs',platform:'node',jsx:'automatic',loader:{'.css':'empty'},external:['react','react-dom/server']});
      const module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
      const p={id:'P-chat',name:'合成项目',revision:1,sources:[{id:'SRC-doc',title:'方案说明.pdf',origin:'interview_attachment',parse_status:'read'}],
        messages:[{role:'user',stage:'interview',text:'请看附件',attachment_source_ids:['SRC-doc']}],items:[],questions:[],product_context:{},product_flow:[],documents:{}};
      sessionStorage.setItem('ra-chat-images-P-chat',JSON.stringify([{id:'local-doc',sourceId:'SRC-doc',name:'方案说明.pdf',type:'application/pdf'}]));
      const noop=()=>{},promise=async()=>{};
      const html=renderToStaticMarkup(React.createElement(module.exports.IntakeChat,{p,model:{},busy:false,blocked:'',tasks:[],status:null,
        onStarted:promise,onRefresh:noop,onSources:noop,onOrganize:noop,go:noop,onReferenceSources:noop,
        onReferenceImport:promise,onBenchmark:promise,onDirectSave:promise}));
      for(const value of ['选择图片或文件','查看项目资料','方案说明.pdf','本轮附件','已读取','Enter 发送 · Shift+Enter 换行','chat-history-images','type="file"','.docx','.webp'])assert(html.includes(value),value);
      assert(!html.includes('/sources/SRC-doc/image'),'document history must not invent an image thumbnail');
      assert(!html.includes('＋ 添加资料'),'file picker must be directly in composer');
      const imageProject={...p,id:'P-image',sources:[{id:'SRC-image',title:'截图.png',origin:'interview_attachment',image_mime:'image/png',parse_status:'awaiting_vision'}],
        active_source_ids:['SRC-image'],messages:[]};
      sessionStorage.setItem('ra-chat-images-P-image',JSON.stringify([{id:'local-image',sourceId:'SRC-image',name:'截图.png',type:'image/png'}]));
      const imageHtml=renderToStaticMarkup(React.createElement(module.exports.IntakeChat,{p:imageProject,model:{},busy:false,blocked:'',tasks:[],status:null,
        onStarted:promise,onRefresh:noop,onSources:noop,onOrganize:noop,go:noop,onReferenceSources:noop,
        onReferenceImport:promise,onBenchmark:promise,onDirectSave:promise}));
      assert(imageHtml.includes('识别图片并发送'));
      assert(imageHtml.includes('图片待识别'));
    '''
    result = subprocess.run([node, '--input-type=module', '-e', script], cwd=WEB,
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
