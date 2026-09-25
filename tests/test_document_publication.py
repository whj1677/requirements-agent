"""Reader/export contracts, using isolated synthetic inputs, not live acceptance."""
import copy
import io
from urllib.parse import unquote

from docx import Document
from fastapi.testclient import TestClient

from app.main import create_app
from app.prd import compile_plan
from app.workflow import apply_response
from tests.helpers import prepared


def setup(tmp_path, monkeypatch):
    async def no_images(_):
        return []
    monkeypatch.setattr('app.exports.capture', no_images)
    app = create_app(tmp_path, access_token='synthetic', env_path=tmp_path/'.env')
    p = prepared(app.state.store)
    return app, p


def generate(store, pid):
    p = store.get(pid)
    plan = dict(plan_version='1', title='需求讨论稿', limitations=[], sections=[dict(
        title='时段配置与保存', normative_refs=[i['id'] for i in p['items']],
        discussion_refs=[], narration='管理员在现有平台中配置时段，保存后查看结果。')])
    with store.edit(pid, p['revision'], '合成章节组装', bump=False) as (state, db):
        apply_response(state, compile_plan(plan, state, 'prd'))
        store.record(pid, 'document_artifact', copy.deepcopy(state['documents']['prd']), db=db)
    return store.get(pid)['documents']['prd']


def login(client):
    csrf = client.post('/api/session/login', json={'key':'synthetic'}).json()['csrf']
    client.headers.update({'Origin':'http://testserver', 'X-CSRF-Token':csrf})


def test_reader_preserves_business_text_without_internal_audit_dump(tmp_path, monkeypatch):
    app, p = setup(tmp_path, monkeypatch)
    artifact = generate(app.state.store, p['id'])
    with TestClient(app) as client:
        login(client)
        current = client.get('/api/projects/'+p['id']).json()
        reader = current['documents']['prd']['reader']
        assert p['name'] in reader['title']
        text = '\n'.join(b['text'] for s in reader['sections'] for b in s['blocks'])
        for item in p['items']:
            assert item['statement'] in text
        assert p['questions'][0]['question'] in text
        # PRODUCT-01 explicitly restores stable requirement identities in human documents.
        assert 'REQ-0001｜' in text and 'v1' in text
        for technical in ('SRC-0001', 'reported', '模型讨论说明', '参考维度', '历史分析提示'):
            assert technical not in text
        assert artifact['content'] == app.state.store.get(p['id'])['documents']['prd']['content']


def test_all_download_formats_require_clarification_even_nonblocking_question(tmp_path, monkeypatch):
    app, p = setup(tmp_path, monkeypatch)
    generate(app.state.store, p['id'])
    assert p['questions'][0]['blocking'] is False
    with TestClient(app) as client:
        login(client)
        root = '/api/projects/'+p['id']
        for fmt in ('md', 'docx', 'zip'):
            response = client.get(root+'/documents/prd/'+fmt)
            assert response.status_code == 409
            assert response.json()['code'] == 'CLARIFICATION_REQUIRED'
            assert p['questions'][0]['question'] in response.json()['message']
        assert not app.state.store.records(p['id'], 'document_export')


def test_named_export_after_answers_and_regeneration_matches_reader_and_snapshot(tmp_path, monkeypatch):
    app, p = setup(tmp_path, monkeypatch)
    old = generate(app.state.store, p['id'])
    with app.state.store.edit(p['id'], p['revision'], '合成预定答案') as (state, _):
        state['questions'][0].update(status='answered', answer='相接边界允许。')
    with TestClient(app) as client:
        login(client)
        root = '/api/projects/'+p['id']
        assert client.get(root+'/documents/prd/md').status_code == 409
        new = generate(app.state.store, p['id'])
        assert client.get(root+'/documents/prd/md?document_id='+old['id']).status_code == 409
        md = client.get(root+'/documents/prd/md?document_id='+new['id'])
        assert md.status_code == 200
        assert p['name'] in unquote(md.headers['content-disposition'])
        assert "filename*=UTF-8''" in md.headers['content-disposition']
        assert f'_PRD_v{new["document_version"]}_{new["id"]}.md' in unquote(md.headers['content-disposition'])
        word = client.get(root+'/documents/prd/docx?document_id='+new['id'])
        assert word.status_code == 200
        paragraphs = '\n'.join(x.text for x in Document(io.BytesIO(word.content)).paragraphs)
        reader = client.get(root).json()['documents']['prd']['reader']
        for s in reader['sections']:
            # The reader now exposes semantic headings and metadata separately.
            # Check every rendered node in both exports, not the retired flat
            # concatenation of title, ID and statement in block.text.
            for node in s['reading_nodes']:
                text = node['number']+' '+node['text'] if node['kind']=='heading' else node['text']
                assert text in md.text and text in paragraphs
                if node['kind']=='heading':
                    assert '#'*(node['level']+1)+' '+text in md.text
        assert 'REQ-0001 · v1' in paragraphs and 'REQ-0001 · v1' in md.text
        assert any(n['text']=='电价时段维护' and n['kind']=='heading' for s in reader['sections'] for n in s['reading_nodes'])
        for noise in ('内容哈希', '参考维度处置', 'reported', 'SRC-0001'):
            assert noise not in paragraphs and noise not in md.text
        assert '相接边界允许。' in paragraphs
        assert not app.state.store.records(p['id'], 'baseline')
        current = app.state.store.get(p['id'])
        with app.state.store.edit(p['id'], current['revision'], '合成项目重命名') as (state, _):
            state['name'] = '另一个需求名称'
        history = client.get(root+'/artifacts').json()['document_artifact']
        assert all(p['name'] in d['reader']['title'] for d in history)


def test_reader_keeps_candidate_and_inference_identity_and_original_evidence(tmp_path, monkeypatch):
    from app.document_reader import reader_document
    app, p = setup(tmp_path, monkeypatch)
    candidate = copy.deepcopy(p['items'][0])
    candidate.update(id='REQ-CANDIDATE', selection_status='candidate', epistemic_status='inferred',
                     statement='建议增加联系人备注；是否必填尚未决定。')
    p['items'].append(candidate)
    plan = dict(plan_version='1', title='讨论稿', limitations=['Q-0001 仍待澄清。'], sections=[dict(
        title='新增建议', normative_refs=[], discussion_refs=[candidate['id']], narration='')])
    apply_response(p, compile_plan(plan, p, 'prd'))
    artifact = copy.deepcopy(p['documents']['prd'])
    content = reader_document(artifact)
    text = '\n'.join(b['text'] for s in content['sections'] for b in s['blocks'])
    assert '尚未采纳：待核实的推断：'+candidate['statement'] in text
    assert '澄清记录 1 仍待澄清。' in text and 'Q-0001' not in text
    assert artifact == p['documents']['prd']
    assert p['items'][-1]['selection_status']=='candidate'


def test_source_diagnostics_use_artifact_snapshot_names_without_changing_original(tmp_path, monkeypatch):
    from app.document_reader import reader_document
    app,p=setup(tmp_path,monkeypatch)
    artifact=generate(app.state.store,p['id'])
    source=artifact['source_snapshot'][0]
    artifact['content']['sections'][-1]['blocks'].append(dict(kind='narrative',ref_ids=[],text='材料限制：'+source['id']+' partial 图像有不可辨认部分。'))
    original=copy.deepcopy(artifact)
    text='\n'.join(b['text'] for s in reader_document(artifact)['sections'] for b in s['blocks'])
    assert '材料「'+source['title']+'」（部分读取） 图像有不可辨认部分。' in text
    assert source['id'] not in text and 'partial' not in text
    assert artifact==original
