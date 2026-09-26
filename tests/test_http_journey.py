"""End-to-end API journey over a real HTTP Chat Completions fixture server.
The fixture is test-owned; normal application startup has no synthetic fallback.
"""
import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from fastapi.testclient import TestClient
from app.main import create_app
from app.provider import DEFAULT
from app.core import hashes
from app.contracts import PROFILES
from tests.helpers import EXAMPLES


def test_real_http_adapter_to_confirmation_and_export(tmp_path):
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            assert self.headers['Authorization']=='Bearer synthetic-test-credential'
            header=json.loads(body['messages'][0]['content'].split('可信任务头：')[-1])
            ctx=json.loads(body['messages'][1]['content'][0]['text'])
            stage=header['stage']
            value=dict(schema_version='1.1',stage=stage,summary='明确标记的 HTTP 合成测试响应',proposals=[],questions=[],findings=[],used_source_refs=[],limitations=[],result={})
            if stage=='ingest':
                from app.product_flow import INTAKE, SCOPE
                value['product_context_proposal']={k:'合成上下文 '+label for k,label in {**INTAKE,**SCOPE}.items()}
                ex=ctx['excerpts'][0]
                refs=[{'source_id':ex['source_id'],'excerpt_id':ex['id']}]
                value['used_source_refs']=refs
                for tid,kind,text in [('TMP-r','requirement','管理员维护时段。'),('TMP-rule','rule','重叠时拒绝保存，原数据不变。'),('TMP-ac','acceptance','输入重叠时段并保存，观察拒绝提示且原数据不变。')]:
                    value['proposals'].append(dict(temp_id=tid,action='add',target_item_id=None,kind=kind,title=text,statement=text,applies_to='to_be',epistemic_status='reported',source_refs=refs,related_refs=['TMP-r'] if kind!='requirement' else ['TMP-rule','TMP-ac']))
                value['result']={'understanding':'合成时段需求','material_limits':[]}
            elif stage=='ui':
                spec=copy.deepcopy(EXAMPLES['ui']['result']['spec'])
                req=next(i['id'] for i in ctx['items'] if i['kind']=='requirement')
                spec=json.loads(json.dumps(spec).replace('REQ-0001',req));spec['draft_revision']=header['current_revision']
                value['result']={'spec':spec}
            elif stage=='prd':
                chosen=[i for group in ctx['A_normative'].values() for i in group]
                value=dict(plan_version='2',sections=[dict(section_key='function',
                    context_refs=header['plan_contract']['context_ref_ids'],
                    normative_refs=[i['id'] for i in chosen],discussion_refs=[])])
            elif stage=='review':
                value['result']=dict(assessment='ready_for_human_review',reviewed_refs=[i['id'] for i in ctx['items']],perspectives=[{'role':'合成工程审查','considerations':['只验证程序链路，不代表真实业务审查']}],required_decisions=[])
            payload=json.dumps(dict(model='SYNTHETIC_HTTP_FIXTURE',choices=[dict(message={'content':json.dumps(value,ensure_ascii=False)},finish_reason='stop')],usage={'prompt_tokens':120,'completion_tokens':100,'total_tokens':220}),ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    app=create_app(tmp_path,access_token='test-login',env_path=tmp_path/'.env')
    try:
        with TestClient(app) as c:
            csrf=c.post('/api/session/login',json={'key':'test-login'}).json()['csrf']
            c.headers.update({'Origin':'http://testserver','X-CSRF-Token':csrf})
            config=dict(DEFAULT,name='明确合成 HTTP Provider',base_url=f'http://127.0.0.1:{server.server_port}',local_allowed=True,key_env='RA_SYNTHETIC_TEST_KEY')
            assert c.put('/api/models/model',json=config).status_code==200
            assert c.put('/api/models/model/key',json={'key':'synthetic-test-credential'}).status_code==200
            p=c.post('/api/projects',json={'name':'全链路工程合成'}).json();root='/api/projects/'+p['id']
            assert c.post(root+'/sources/text',json={'expected_revision':0,'text':'管理员维护时段。重叠时拒绝保存，原数据不变。'}).status_code==200
            p=c.get(root).json()
            # Sending before data authorization must not call the HTTP provider.
            c.post(root+'/runs',json={'expected_revision':p['revision'],'stage':'ingest'})
            time.sleep(.08)
            assert c.get(root+'/runs').json()[-1]['error']=='DATA_AUTH_REQUIRED' and not requests
            c.post(root+'/grants',json={'expected_revision':p['revision'],'source_ids':[s['id'] for s in p['sources']]})
            def stage(name,kind='prd'):
                p=c.get(root).json()
                response=c.post(root+'/runs',json={'expected_revision':p['revision'],'stage':name,'document_type':kind})
                assert response.status_code==200,response.text
                for _ in range(100):
                    r=c.get(root+'/runs').json()[-1]
                    if r['status'] not in ('queued','running'):break
                    time.sleep(.03)
                assert r['status']=='succeeded',r
                assert r['attempts'][0]['response_model']=='SYNTHETIC_HTTP_FIXTURE'
            stage('ingest')
            p=c.get(root).json()
            for i in p['items']:
                current=c.get(root).json()
                assert c.post(root+'/items/'+i['id'],json={'expected_revision':current['revision'],'selection_status':'selected'}).status_code==200
            from tests.product_flow_helpers import http_check
            http_check(c,root,through=3)
            p=c.get(root).json()
            assert p['ui'] is None and '4' not in p.get('stage_checks',{})
            assert p['product_flow'][4]['available']
            stage('prd');stage('prd','mrd');stage('review')
            p=c.get(root).json()
            for kind,doc in p['documents'].items():
                response=c.post(root+'/documents/'+kind+'/review',json=dict(expected_revision=c.get(root).json()['revision'],document_id=doc['id']))
                assert response.status_code==200,response.text
            p=c.get(root).json()
            assert c.post(root+'/stage-checks/5',json=dict(expected_revision=p['revision'],expected_hash=p['product_flow'][4]['content_hash'])).status_code==200
            p=c.get(root).json();assert p['confirmation_issues']==[]
            response=c.post(root+'/confirmations',json={'expected_revision':p['revision'],'expected_hashes':p['hashes'],'scope_ids':[i['id'] for i in p['items']],'idempotency_key':'http-journey'})
            assert response.status_code==200,response.text
            baseline=response.json()['baseline_id']
            response=c.post(root+'/exports',json={'baseline_id':baseline,'idempotency_key':'http-export'})
            assert response.status_code==200,response.text
            assert c.get(root+'/exports/'+response.json()['id']).content.startswith(b'PK')
            assert len(requests)==4  # Intake, PRD, MRD, review; no retired sketch request.
            assert all(d['sketch_policy']=='excluded' for d in p['documents'].values())
            assert len(app.state.store.records(p['id'],'confirmation'))==1
            artifacts=c.get(root+'/artifacts')
            assert artifacts.status_code==200 and len(artifacts.json()['document_artifact'])==2
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
