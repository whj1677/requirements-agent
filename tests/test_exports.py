import asyncio
import io
import zipfile
from docx import Document
from app.store import Store
from app.core import hashes
from app.exports import document_files,handoff
from app.workflow import confirm
from tests.helpers import prepared

def test_md_docx_canonical_and_actual_embedded_image(tmp_path):
    store=Store(tmp_path);p=prepared(store)
    for kind in ('prd','mrd'):
        files=asyncio.run(document_files(p,kind))
        text='\n'.join(x.text for x in Document(io.BytesIO(files[kind.upper()+'.docx'])).paragraphs)
        md=files[kind.upper()+'.md'].decode()
        for i in p['items']:
            assert i['statement'] in text and i['statement'] in md
        assert '待确认' in text and '低保真模拟' in text
        with zipfile.ZipFile(io.BytesIO(files[kind.upper()+'.docx'])) as z:
            assert any(name.startswith('word/media/') for name in z.namelist())
        assert '800X600' not in text and '撤销订单' not in text

def test_handoff_canonical_and_manifest(tmp_path):
    import hashlib,json
    store=Store(tmp_path);p=prepared(store)
    c=confirm(store,p['id'],dict(expected_revision=p['revision'],expected_hashes=hashes(p),scope_ids=[i['id'] for i in p['items']],idempotency_key='export-test'))
    e=handoff(store,p['id'],c['baseline_id'],'export-test')
    with zipfile.ZipFile(store.folder/'exports'/(e['id']+'.zip')) as z:
        manifest=json.loads(z.read('manifest.json'))
        for path,record in manifest['artifacts'].items():assert hashlib.sha256(z.read(path)).hexdigest()==record['sha256']
        for path in ('requirements.md','frontend_spec.md','backend_spec.md'):
            text=z.read(path).decode()
            assert all(i['statement'] in text for i in p['items'])
