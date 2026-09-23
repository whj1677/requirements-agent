import asyncio
import copy
import hmac
import os
import secrets
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from .core import DATA, ROOT, Problem, brief_hash, digest, hashes, ident, now, require
from .store import Store
from .contracts import gate, PROFILES
from .provider import Provider, DEFAULT, origin
from .workflow import Workflow, confirm
from .sources import MAX_BYTES, save_source, webpage
from .preview import prototype
from .exports import document_files, handoff, zip_files


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Revision(Strict):
    expected_revision: int

class Name(Strict):
    name: str = Field(min_length=1,max_length=100)

class ProjectEdit(Revision):
    name: str | None = Field(default=None,min_length=1,max_length=100)
    mode: Literal['explore','converge'] | None = None
    reference_mode: Literal['user','builtin'] | None = None

class TextSource(Revision):
    title: str = Field(default='粘贴材料',max_length=200)
    text: str = Field(min_length=1,max_length=300000)
    purpose: Literal['current','reference','goal','template'] = 'current'

class URLSource(Revision):
    url: str = Field(max_length=2000)
    dynamic: bool = False
    authorized_public: bool = False

class RunInput(Revision):
    stage: str
    message: str = Field(default='',max_length=12000)
    document_type: Literal['prd','mrd'] = 'prd'

class Decision(Revision):
    selection_status: Literal['candidate','selected','rejected','deferred']
    statement: str | None = Field(default=None,min_length=1,max_length=6000)

class Answer(Revision):
    answer: str = Field(min_length=1,max_length=6000)

class Confirmation(Revision):
    expected_hashes: dict
    scope_ids: list[str]
    idempotency_key: str = Field(min_length=8,max_length=100)

class ExportInput(Strict):
    baseline_id: str
    idempotency_key: str = Field(min_length=8,max_length=100)

class ModelConfig(Strict):
    name: str = Field(min_length=1,max_length=100)
    base_url: str = Field(max_length=2000)
    model: str = Field(min_length=1,max_length=200)
    key_env: str = Field(default='RA_DEEPSEEK_API_KEY',pattern=r'^RA_[A-Z0-9_]+$')
    vision: Literal['documented','verified','unsupported','unknown'] = 'unknown'
    json_mode: bool = True
    timeout: int = Field(default=120,ge=1,le=600)
    max_calls: int = Field(default=8,ge=1,le=30)
    max_tokens: int = Field(default=6000,ge=100,le=50000)
    context_chars: int = Field(default=180000,ge=10000,le=2000000)
    action_seconds: int = Field(default=300,ge=5,le=1800)
    thinking_disabled: bool = True
    documented_at: str | None = None
    local_allowed: bool = False
    proxy: str = Field(default='',max_length=2000)
    input_price: float | None = Field(default=None,ge=0)
    output_price: float | None = Field(default=None,ge=0)

class KeyInput(Strict):
    key: str = Field(max_length=2000)

class Grant(Revision):
    slot: Literal['model','vision'] = 'model'
    source_ids: list[str]


def create_app(folder=DATA, access_token=None, provider=None):
    store = Store(folder)
    provider = provider or Provider()
    workflow = Workflow(store, provider)
    token = access_token or os.environ.get('RA_ACCESS_TOKEN') or secrets.token_urlsafe(32)
    sessions = {}
    app = FastAPI(title='需求 Agent', version='1.1', docs_url=None, redoc_url=None)
    app.state.store, app.state.provider, app.state.workflow = store, provider, workflow
    app.state.access_token = token

    @app.exception_handler(Problem)
    async def error_handler(request, error):
        return JSONResponse({'code':error.code,'message':error.message}, status_code=error.status)

    @app.middleware('http')
    async def local_security(request, call_next):
        host = request.headers.get('host','')
        parsed_host = urlsplit('http://' + host).hostname
        if parsed_host not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'code':'HOST_DENIED','message':'只允许本机访问'},status_code=403)
        if request.headers.get('content-length','0').isdigit() and int(request.headers.get('content-length','0')) > MAX_BYTES + 100000:
            return JSONResponse({'code':'UPLOAD_TOO_LARGE','message':'请求过大'},status_code=413)
        if request.url.path.startswith('/api/') or request.url.path == '/openapi.json':
            remote_origin = request.headers.get('origin')
            expected_origin = str(request.base_url).rstrip('/')
            if remote_origin and remote_origin != expected_origin:
                return JSONResponse({'code':'ORIGIN_DENIED','message':'拒绝跨站访问'},status_code=403)
            if request.url.path != '/api/session/login':
                session = sessions.get(request.cookies.get('ra_session',''))
                if not session:
                    return JSONResponse({'code':'SESSION_REQUIRED','message':'请输入本机访问令牌'},status_code=401)
                if request.method not in ('GET','HEAD','OPTIONS'):
                    if not remote_origin or not hmac.compare_digest(request.headers.get('x-csrf-token',''),session):
                        return JSONResponse({'code':'CSRF_DENIED','message':'会话校验失败'},status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        if not request.url.path.endswith('/prototype'):
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.post('/api/session/login')
    async def login(body: KeyInput):
        require(hmac.compare_digest(body.key,token), 'AUTH_FAILED', '本机访问令牌错误', 401)
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[sid] = csrf
        res = JSONResponse({'csrf':csrf})
        res.set_cookie('ra_session',sid,httponly=True,samesite='strict',path='/')
        return res

    @app.get('/api/session')
    async def session(request:Request):
        return {'csrf':sessions[request.cookies['ra_session']], 'mode':'live', 'version':'1.1'}

    @app.get('/api/projects')
    async def projects():
        return [{k:p[k] for k in ('id','name','revision','created')} for p in store.list()]

    @app.post('/api/projects')
    async def new_project(body:Name):
        return store.create(body.name)

    @app.get('/api/projects/{pid}')
    async def project(pid:str):
        p=store.get(pid)
        try:
            issues=gate(p)
        except Problem as e:
            issues=[e.message]
        return dict(p, hashes=hashes(p), confirmation_issues=issues, baselines=store.records(pid,'baseline'), exports=store.records(pid,'export'))

    @app.patch('/api/projects/{pid}')
    async def edit_project(pid:str, body:ProjectEdit):
        with store.edit(pid,body.expected_revision,'修改项目信息') as (p,db):
            if body.name: p['name']=body.name
            if body.mode: p['mode']=body.mode
            if body.reference_mode:p['reference_mode']=body.reference_mode
        return p

    @app.get('/api/projects/{pid}/history')
    async def history(pid:str):
        store.get(pid)
        with store.connect() as db:
            return [dict(r) for r in db.execute('SELECT revision,reason,created,payload FROM revisions WHERE project_id=? ORDER BY revision DESC',(pid,))]

    @app.get('/api/projects/{pid}/artifacts')
    async def artifact_history(pid:str):
        store.get(pid)
        return {kind:store.records(pid,kind) for kind in ('document_artifact','ui_artifact','review','document_export')}

    @app.post('/api/projects/{pid}/sources/text')
    async def text_source(pid:str,body:TextSource):
        with store.edit(pid,body.expected_revision,'添加文字材料') as (p,db):
            s=save_source(store,body.title+'.txt',body.text.encode('utf-8'),body.purpose)
            p['sources'].append(s)
        return s

    @app.post('/api/projects/{pid}/sources/file')
    async def file_source(pid:str,expected_revision:int=Form(...),purpose:str=Form('current'),file:UploadFile=File(...)):
        data=await file.read(MAX_BYTES+1)
        require(len(data)<=MAX_BYTES,'SOURCE_FAILED','文件超过 12 MiB')
        require(purpose in ('current','goal','reference','template'),'SOURCE_FAILED','材料用途无效')
        p=store.get(pid)
        require(p['revision']==expected_revision,'STALE_REVISION','页面已过期',409)
        s=await asyncio.to_thread(save_source,store,Path(file.filename or 'unknown').name,data,purpose)
        with store.edit(pid,expected_revision,'添加文件材料') as (p,db):
            p['sources'].append(s)
        return s

    @app.post('/api/projects/{pid}/sources/url')
    async def url_source(pid:str,body:URLSource):
        require(body.authorized_public,'SOURCE_FAILED','请确认有权读取该公开网页')
        store.get(pid)
        try:
            raw,text,method,final=await asyncio.wait_for(webpage(body.url,body.dynamic),30)
            s=save_source(store,body.url,raw,'reference',final,([(method,text)],'partial' if 'partial' in method else 'read','部分子资源被阻止' if 'partial' in method else '',None))
        except Exception as e:
            s=dict(id=ident('SRC'),title=body.url,purpose='reference',uri=body.url,sha256=None,created=now(),version=1,excluded=False,excerpts=[],parse_status='failed',failure_reason=e.message if isinstance(e,Problem) else '网页读取失败：'+type(e).__name__,image_mime=None)
        with store.edit(pid,body.expected_revision,'添加网页材料') as (p,db):
            p['sources'].append(s)
        return s

    @app.post('/api/projects/{pid}/sources/{sid}/exclude')
    async def exclude(pid:str,sid:str,body:Revision):
        with store.edit(pid,body.expected_revision,'切换材料排除状态') as (p,db):
            s=next((x for x in p['sources'] if x['id']==sid),None)
            require(s is not None,'NOT_FOUND','材料不存在',404)
            s['excluded']=not s['excluded']
        return s

    @app.get('/api/projects/{pid}/sources/{sid}/image')
    async def source_image(pid:str,sid:str):
        p=store.get(pid)
        s=next((x for x in p['sources'] if x['id']==sid and x['image_mime']),None)
        require(s is not None,'NOT_FOUND','图片不存在',404)
        return FileResponse(store.folder/'sources'/s['id'],media_type=s['image_mime'])

    @app.post('/api/projects/{pid}/sources/{sid}/retry')
    async def retry_source(pid:str,sid:str,body:Revision):
        p=store.get(pid)
        old=next((x for x in p['sources'] if x['id']==sid),None)
        require(old is not None,'NOT_FOUND','材料不存在',404)
        if old.get('uri'):
            try:
                raw,text,method,final=await asyncio.wait_for(webpage(old['uri']),30)
                s=save_source(store,old['title'],raw,old['purpose'],final,([(method,text)],'read','',None))
            except Exception:
                raise Problem('SOURCE_FAILED','网页重试失败；原失败记录保留')
        else:
            s=save_source(store,old['title'],(store.folder/'sources'/old['id']).read_bytes(),old['purpose'])
        s['version']=old['version']+1
        s['parent_source_id']=old['id']
        with store.edit(pid,body.expected_revision,'重新读取材料，保留原记录') as (p,db):p['sources'].append(s)
        return s

    @app.post('/api/projects/{pid}/items/{iid}')
    async def decide(pid:str,iid:str,body:Decision):
        with store.edit(pid,body.expected_revision,'人工编辑或采纳条目') as (p,db):
            i=next((x for x in p['items'] if x['id']==iid),None)
            require(i is not None,'NOT_FOUND','条目不存在',404)
            if body.statement:
                i['statement']=body.statement
            if i.get('target_item_id') and body.selection_status=='selected':
                target=next(x for x in p['items'] if x['id']==i['target_item_id'])
                # Preserve stable business identity for a selected revision.
                for key in ('title','statement','applies_to','epistemic_status','source_refs','related_refs'):
                    target[key]=copy.deepcopy(i[key])
                target['selection_status']='selected'
                i['selection_status']='deferred'
                for linked in p['items']+p['questions']:
                    linked['related_refs']=[target['id'] if ref==i['id'] else ref for ref in linked['related_refs']]
                for option in p['options']:
                    option['proposed_item_refs']=[target['id'] if ref==i['id'] else ref for ref in option['proposed_item_refs']]
            else:
                i['selection_status']=body.selection_status
        return p

    @app.post('/api/projects/{pid}/options/{oid}')
    async def choose(pid:str,oid:str,body:Decision):
        with store.edit(pid,body.expected_revision,'人工选择方案') as (p,db):
            option=next((x for x in p['options'] if x['id']==oid),None)
            require(option is not None,'NOT_FOUND','方案不存在',404)
            option['selection_status']=body.selection_status
            for i in p['items']:
                if i['id'] in option['proposed_item_refs']:
                    i['selection_status']=body.selection_status
        return p

    @app.post('/api/projects/{pid}/questions/{qid}')
    async def answer(pid:str,qid:str,body:Answer):
        with store.edit(pid,body.expected_revision,'人工回答问题') as (p,db):
            q=next((x for x in p['questions'] if x['id']==qid),None)
            require(q is not None,'NOT_FOUND','问题不存在',404)
            q.update(answer=body.answer,status='answered',answered_at=now())
            p['sources'].append(save_source(store,'问题回答.txt',body.answer.encode(),'goal'))
        return p

    @app.get('/api/models')
    async def get_models():
        result={}
        for slot in ('model','vision'):
            c=store.setting(slot) or dict(DEFAULT)
            result[slot]=dict(c,key_configured=bool(provider.key(c)),proxy='已配置' if c.get('proxy') else '')
        return result

    @app.put('/api/models/{slot}')
    async def set_model(slot:str,body:ModelConfig):
        require(slot in ('model','vision'),'CONFIG_INVALID','未知配置槽')
        c=body.model_dump(); origin(c)
        require(body.vision!='verified','CONFIG_INVALID','已验证状态只能由实际能力测试记录，不能手工声明')
        bound=provider.env_origins.get(c['key_env'])
        require(bound is None or bound==origin(c),'KEY_ORIGIN_CONFLICT','该环境变量已绑定另一接收端，请使用独立的项目环境变量或会话 Key')
        provider.env_origins[c['key_env']]=origin(c)
        store.setting(slot,c)
        return {'saved':True}

    @app.put('/api/models/{slot}/key')
    async def set_key(slot:str,body:KeyInput):
        require(slot in ('model','vision'),'CONFIG_INVALID','未知配置槽')
        config=store.setting(slot) or dict(DEFAULT)
        provider.keys[origin(config)]=body.key
        return {'key_configured':bool(body.key),'storage':'server_process_memory'}

    @app.post('/api/models/{slot}/test')
    async def test_model(slot:str):
        require(slot in ('model','vision'),'CONFIG_INVALID','未知配置槽')
        c=store.setting(slot) or dict(DEFAULT)
        content='Return {"connection":"ok"}. This is a connection test with no project material.'
        if slot=='vision':
            import base64,io
            from PIL import Image,ImageDraw
            image=Image.new('RGB',(160,120),'white');draw=ImageDraw.Draw(image)
            draw.rectangle((10,10,70,70),fill='red');draw.rectangle((90,10,150,70),fill='blue')
            buffer=io.BytesIO();image.save(buffer,format='PNG')
            content=[{'type':'text','text':'Describe the left and right rectangle colors as JSON {"left":"English color","right":"English color"}.'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode()}}]
        value,meta=await provider.request(c,[{'role':'system','content':'Return a JSON object only.'},{'role':'user','content':content}])
        if slot=='vision':
            require(value.get('left','').lower()=='red' and value.get('right','').lower()=='blue','VISION_UNAVAILABLE','视觉探针未正确识别；不标记已验证')
            c['vision']='verified';c['documented_at']=now();store.setting(slot,c)
        return {'connection':'succeeded','task_semantics':'NOT_TESTED','vision':'verified_synthetic_color_probe' if slot=='vision' else 'NOT_TESTED','meta':meta}

    @app.post('/api/projects/{pid}/grants')
    async def grant(pid:str,body:Grant):
        c=store.setting(body.slot) or dict(DEFAULT)
        with store.edit(pid,body.expected_revision,'授权向指定接收端发送选定项目材料',bump=False) as (p,db):
            require(set(body.source_ids)<={s['id'] for s in p['sources']},'REFERENCE_INVALID','授权来源不属于当前项目')
            p['grants'][origin(c)]={'source_ids':body.source_ids,'created':now(),'actor':'authenticated_local_user'}
        return {'origin':origin(c),'source_ids':body.source_ids}

    @app.post('/api/projects/{pid}/runs')
    async def run(pid:str,body:RunInput):
        return workflow.start(pid,body.expected_revision,body.stage,body.message,body.document_type)

    @app.get('/api/projects/{pid}/runs')
    async def runs(pid:str):
        store.get(pid)
        return store.records(pid,'run')

    @app.post('/api/projects/{pid}/runs/{rid}/cancel')
    async def cancel(pid:str,rid:str):
        r=store.get_record(pid,rid,'run')
        require(r['status'] in ('queued','running'),'RUN_FINISHED','此任务已结束')
        store.update_run(pid,rid,status='cancelled',message='已取消；已发请求仍可能计费')
        return {'status':'cancelled'}

    @app.post('/api/projects/{pid}/runs/{rid}/resume')
    async def resume(pid:str,rid:str,body:Revision):
        r=store.get_record(pid,rid,'run')
        require(r['status'] in ('failed','cancelled','paused_budget'),'RUN_FINISHED','无需重跑已成功阶段')
        return workflow.start(pid,body.expected_revision,r['stage'],r['message'],r['document_type'],rid)

    @app.get('/api/projects/{pid}/prototype')
    async def preview(pid:str):
        p=store.get(pid)
        require(p['ui'],'NOT_FOUND','尚未生成页面方案',404)
        return HTMLResponse(prototype(p['ui']['spec']))

    @app.get('/api/projects/{pid}/documents/{kind}/{fmt}')
    async def download_document(pid:str,kind:str,fmt:str):
        require(kind in ('prd','mrd') and fmt in ('md','docx','zip'),'NOT_FOUND','文件类型不存在',404)
        p=store.get(pid)
        status='草稿／待产品经理内容确认'
        if p['active_baseline_id']:
            baseline=store.get_record(pid,p['active_baseline_id'],'baseline')
            if baseline['hashes']==hashes(p):
                status='产品经理 PRD 内容确认 · '+baseline['confirmation_id'] if kind=='prd' else '同底稿 PRD 内容已确认；MRD 为派生文档，未单独签署审批'
        files=await document_files(p,kind,status)
        import hashlib
        store.record(pid,'document_export',dict(document_type=kind,style_version='1',generator_version='1.1',artifact_hashes={k:hashlib.sha256(v).hexdigest() for k,v in files.items()},asset_bindings=files['document_asset_bindings.json'].decode('utf8')))
        body=zip_files(files) if fmt=='zip' else files[kind.upper()+'.'+fmt]
        return Response(body,media_type='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="{kind.upper()}.{fmt}"'})

    @app.post('/api/projects/{pid}/confirmations')
    async def confirmation(pid:str,body:Confirmation):
        return confirm(store,pid,body.model_dump())

    @app.post('/api/projects/{pid}/exports')
    async def export(pid:str,body:ExportInput):
        return handoff(store,pid,body.baseline_id,body.idempotency_key)

    @app.get('/api/projects/{pid}/exports/{eid}')
    async def download_export(pid:str,eid:str):
        store.get_record(pid,eid,'export')
        return FileResponse(store.folder/'exports'/(eid+'.zip'),filename=eid+'.zip')

    if (ROOT/'web/dist').exists():
        app.mount('/',StaticFiles(directory=ROOT/'web/dist',html=True),name='web')
    return app


app = create_app()
