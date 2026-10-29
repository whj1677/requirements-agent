"""Real local file bytes through the upload/worker/download path; no model calls."""
import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app import sources
from tests.test_runtime import client
from tests.test_office_sources import fixtures


def pdf_bytes(pages=('Synthetic storage cloud requirement',)):
    writer=PdfWriter()
    for text in pages:
        page=writer.add_blank_page(width=400,height=300)
        if text:
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({
                NameObject('/F1'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),
                    NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})})
            stream=DecodedStreamObject()
            stream.set_data(('BT /F1 12 Tf 10 250 Td ('+text+') Tj ET').encode('ascii'))
            page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output)
    return output.getvalue()


HTML='''<!doctype html><meta charset="utf-8"><title>合成告警资料</title>
<style>.ignored{color:red}</style><h1>站点告警查看</h1><p>只读查看有权限的站点。</p>
<table><tr><th>字段</th><th>含义</th></tr><tr><td>告警等级</td><td>按原资料核对</td></tr></table>
<div hidden>HIDDEN_SECRET</div><span style="display: none">HIDDEN_STYLE</span>
<script>fetch('https://invalid.example/DO_NOT_FETCH');window.BAD_SCRIPT=true;</script>
<img src="https://invalid.example/no-fetch.png">'''


@pytest.mark.parametrize('purpose',['reference','chat_attachment'])
@pytest.mark.parametrize('name,content,needle,status',[
    ('合成.PDF',lambda:pdf_bytes(),'Synthetic storage cloud requirement','read'),
    ('合成.pptx',lambda:fixtures()['.pptx'],'合成演示：联系人备注','partial'),
    ('合成.HTML',lambda:HTML.encode(),'只读查看有权限的站点','partial'),
    ('合成.htm',lambda:'<meta charset="gb18030"><h1>合成中文静态页面</h1>'.encode('gb18030'),'合成中文静态页面','read'),
])
def test_upload_reads_real_contents_without_model_and_original_is_download_only(tmp_path,monkeypatch,purpose,name,content,needle,status):
    monkeypatch.setattr(sources,'fetch_public',lambda *_:pytest.fail('Local HTML must never fetch external resources'))
    monkeypatch.setattr(sources,'extract_office',lambda *_:pytest.fail('Ordinary PPTX must use the bundled reader'))
    c,app=client(tmp_path)
    with c:
        p=c.post('/api/projects',json={'name':'合成文件支持核对'}).json()
        root='/api/projects/'+p['id']
        raw=content()
        response=c.post(root+'/sources/file',data=dict(expected_revision=0,purpose=purpose),files={'file':(name,raw)})
        assert response.status_code==200,response.text
        source=response.json()
        assert source['parse_status']==status,source
        assert source['sha256']==hashlib.sha256(raw).hexdigest()
        text='\n'.join(row['text'] for row in source['excerpts'])
        assert needle in text
        assert all(row['locator'] and row['source_hash']==source['sha256'] for row in source['excerpts'])
        assert not any(token in text for token in ('DO_NOT_FETCH','BAD_SCRIPT','HIDDEN_SECRET','HIDDEN_STYLE'))
        original=c.get(root+'/sources/'+source['id']+'/file')
        assert original.content==raw
        assert original.headers['content-type']=='application/octet-stream'
        assert original.headers['content-disposition'].startswith('attachment;')
        project=c.get(root).json()
        assert set(('.pdf','.ppt','.pptx','.html','.htm'))<=set(project['source_capabilities']['extensions'])
        assert not project['items'] and not project['baselines'] and not c.get(root+'/runs').json()
        if purpose=='chat_attachment': assert source['origin']=='interview_attachment'


def test_html_retry_keeps_original_and_versioned_source(tmp_path):
    c,app=client(tmp_path)
    with c:
        p=c.post('/api/projects',json={'name':'HTML 重读合成案例'}).json();root='/api/projects/'+p['id']
        first=c.post(root+'/sources/file',data=dict(expected_revision=0,purpose='reference'),files={'file':('页面.html',HTML.encode())}).json()
        retry=c.post(root+'/sources/'+first['id']+'/retry',json={'expected_revision':1})
        assert retry.status_code==200,retry.text
        second=retry.json()
        assert second['parent_source_id']==first['id'] and second['version']==2
        assert second['sha256']==first['sha256'] and second['parse_status']=='partial'


def test_pdf_mixed_pages_and_scanned_or_password_files_report_limits(tmp_path):
    from app.store import Store
    store=Store(tmp_path)
    mixed=sources.save_source(store,'mixed.pdf',pdf_bytes(('Readable page',None)))
    assert mixed['parse_status']=='partial' and '第 2 页未提取文字' in mixed['failure_reason']
    scanned=sources.save_source(store,'scanned.pdf',pdf_bytes((None,)))
    assert scanned['parse_status']=='failed' and not scanned['excerpts'] and '未提取到文字' in scanned['failure_reason']
    writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('synthetic-password')
    output=io.BytesIO();writer.write(output)
    protected=sources.save_source(store,'protected.pdf',output.getvalue())
    assert protected['parse_status']=='failed' and not protected['excerpts'] and '密码或读取权限' in protected['failure_reason']


def test_script_only_html_does_not_claim_content_was_read(tmp_path):
    from app.store import Store
    source=sources.save_source(Store(tmp_path),'dynamic.html',b'<title>Title alone is not content</title><script>document.body.innerHTML="dynamic"</script>')
    assert source['parse_status']=='failed' and not source['excerpts']
    assert '未提取到静态正文' in source['failure_reason']


def test_legacy_ppt_stays_office_required_without_claiming_a_read(tmp_path,monkeypatch):
    from app.store import Store
    calls=[]
    def unavailable(title,data,folder):
        calls.append(title)
        return dict(status='office_required',reason='需要本机已授权 PowerPoint 读取',rows=[])
    monkeypatch.setattr(sources,'extract_office',unavailable)
    source=sources.save_source(Store(tmp_path),'legacy.ppt',b'Synthetic unavailable legacy container')
    assert calls==['legacy.ppt'] and source['parse_status']=='office_required' and not source['excerpts']
