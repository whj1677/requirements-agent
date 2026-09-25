"""Test-only loopback HTTP model. Never imported by application runtime."""
import copy
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.provider import DEFAULT
from tests.helpers import EXAMPLES


@contextmanager
def model_server():
    state={'requests':[], 'fail_next':False, 'invalid_next':False, 'delay':0}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state['requests'].append(body)
            time.sleep(state['delay'])
            if state['fail_next']:
                state['fail_next']=False
                self.send_response(401); self.end_headers(); return
            header=json.loads(body['messages'][0]['content'].split('可信任务头：')[-1])
            ctx=json.loads(body['messages'][1]['content'][0]['text'])
            stage=header['stage']
            material=' '.join(x['text'] for x in ctx['excerpts'])
            contact='联系人' in material
            topic='联系人' if contact else '电价时段'
            ref=[dict(source_id=x['source_id'],excerpt_id=x['id']) for x in ctx['excerpts'][:1]]
            value=dict(schema_version='1.1',stage=stage,summary=f'合成 HTTP：{topic}内容待人工核对',proposals=[],questions=[],findings=[],used_source_refs=ref,limitations=[],result={})
            if stage=='vision':
                image_ids={s['id'] for s in ctx['source_status'] if s['parse_status']=='awaiting_vision'}
                image=next(x for x in ctx['excerpts'] if x['source_id'] in image_ids)
                image_ref=dict(source_id=image['source_id'],excerpt_id=image['id'])
                value['used_source_refs']=[image_ref]
                value['result']=dict(observations=[dict(source_ref=image_ref,region='中央',observation='合成图中可见列表和新增按钮',uncertain=False)],unobservable=['保存效果未知'],unreadable=[])
            elif stage=='ingest':
                from app.product_flow import INTAKE, SCOPE
                value['product_context_proposal']={k:'合成上下文 '+label for k,label in {**INTAKE,**SCOPE}.items()}
                for tid,kind,text in [('TMP-R','requirement',f'管理员维护{topic}列表。'),('TMP-RULE','rule','只读角色不能修改。'),('TMP-AC','acceptance','只读角色进入列表，不允许修改。')]:
                    value['proposals'].append(dict(temp_id=tid,action='add',target_item_id=None,kind=kind,title=text,statement=text,applies_to='to_be',epistemic_status='reported',source_refs=ref,related_refs=['TMP-R'] if kind!='requirement' else ['TMP-RULE','TMP-AC']))
                question='联系人重复姓名如何处理？' if contact else '时段端点相接如何处理？'
                value['questions']=[dict(temp_id='QTMP-1',topic_key='undecided-rule',question=question,why='关键业务规则未知',options=['由业务负责人决定'],blocking=True,related_refs=['TMP-R'],source_refs=ref)]
                value['result']=dict(understanding=f'{topic}维护，规则仍有未知。',material_limits=[])
            elif stage=='clarify':
                value['result']=dict(scope_summary=f'{topic}已有回答与未知继续保留',outstanding_decisions=[q['question'] for q in ctx['questions'] if q['status']=='open'],next_focus='核对本期内容')
            elif stage=='brainstorm':
                value['proposals']=[dict(temp_id='TMP-A',action='add',target_item_id=None,kind='assumption',title='界面建议',statement='建议使用侧栏编辑，业务规则仍待决定。',applies_to='to_be',epistemic_status='proposed',source_refs=ref,related_refs=[])]
                options=[]
                for n,label in [('A','侧栏'),('B','弹窗')]:
                    option=copy.deepcopy(EXAMPLES['brainstorm']['result']['options'][0])
                    option.update(option_id='OPT-TMP-'+n,name=f'{topic}方案{n}：{label}',proposed_item_refs=['TMP-A'],source_refs=ref)
                    options.append(option)
                value['result']=dict(options=options,recommended_option_id=None,recommendation_reason='合成方案，不自动替用户选择')
            elif stage=='ui':
                spec=copy.deepcopy(EXAMPLES['ui']['result']['spec'])
                req=next(i['id'] for i in ctx['items'] if i['kind']=='requirement')
                spec=json.loads(json.dumps(spec).replace('REQ-0001',req))
                spec['draft_revision']=header['current_revision'];spec['title']=topic+'讨论原型'
                if contact:
                    for page in spec['pages']:
                        page['title']='联系人列表'
                        for region in page['regions']:
                            for c in region['components']:
                                c['label']='联系人列表'; c['description']='合成联系人展示'
                                if c['columns']:
                                    c['columns']=[dict(key='name',label='姓名',ref_ids=[req])];c['rows']=[['小李']]
                value['result']={'spec':spec}
            elif stage=='prd':
                chosen=[i for group in ctx['A_normative'].values() for i in group]
                value=dict(plan_version='1',title=topic+'需求讨论稿',sections=[dict(title='本期内容与未决规则',normative_refs=[i['id'] for i in chosen],discussion_refs=[],narration=f'{topic}讨论稿；未采纳建议不是确定规则。')],limitations=[])
            elif stage=='review':
                value['result']=dict(assessment='ready_for_human_review',reviewed_refs=[i['id'] for i in ctx['items']],perspectives=[dict(role='合成工程审查',considerations=['不代表真人审查'])],required_decisions=[q['question'] for q in ctx['questions'] if q['status']=='open'])
            if state.get('transform'):
                value=state['transform'](value,body,ctx)
            content=json.dumps(value,ensure_ascii=False)
            if state['invalid_next']:
                state['invalid_next']=False;content='{invalid first response'
            finish='length' if state.get('truncate') else 'stop'
            payload=json.dumps(dict(model='UI02_SYNTHETIC_HTTP',choices=[dict(message={'content':content},finish_reason=finish)],usage=dict(prompt_tokens=10,completion_tokens=10,total_tokens=20)),ensure_ascii=False).encode()
            self.send_response(200); self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    state['config']=dict(DEFAULT,base_url=f'http://127.0.0.1:{server.server_port}',local_allowed=True,key_env='RA_UI02_FIXTURE_KEY',name='UI-02 本机合成测试',model='UI02_SYNTHETIC_HTTP')
    try: yield state
    finally: server.shutdown();server.server_close();thread.join(timeout=3)
