"""Synthetic fault-injection scenarios for version-compat and API error handling.

Uses a synthetic loopback model server, an isolated data directory and synthetic
credentials. No real model requests are made; the script asserts they stay zero.
"""
import asyncio
import json
import re
import socket
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import uvicorn
from fastapi.responses import HTMLResponse, JSONResponse, Response
from playwright.async_api import async_playwright
from app.core import ROOT
from app.main import create_app
from app.provider import Provider
from tests.ui02_fixture import model_server

TOKEN='compat-synthetic-token'
PROJECT_GET=re.compile(r'^/api/projects/[^/]+$')


async def main():
    evidence=ROOT/'evidence/runtime/compat'/uuid.uuid4().hex[:8]
    evidence.mkdir(parents=True)
    with model_server() as model:
        provider=Provider(evidence/'absent.env')
        app=create_app(evidence/'data',access_token=TOKEN,provider=provider)
        faults={'legacy_actions':False,'project_not_found':False,'html500':False,'empty500':False,'session_patch':None}
        @app.middleware('http')
        async def fault_injection(request,call_next):
            path=request.url.path
            if faults['legacy_actions'] and re.match(r'^/api/projects/[^/]+/(actions|artifacts)$',path):
                return JSONResponse({'detail':'Not Found'},status_code=404)
            if faults['session_patch'] and path=='/api/session' and request.method=='GET':
                real=await call_next(request)
                raw=b''.join([chunk async for chunk in real.body_iterator])
                data=json.loads(raw)
                if faults['session_patch']=='drop_backend':data.pop('backend',None)
                elif faults['session_patch']=='drop_actions':data['backend']['capabilities']=[c for c in data['backend']['capabilities'] if c!='actions']
                return JSONResponse(data)
            if request.method=='GET' and PROJECT_GET.match(path):
                if faults['project_not_found']:
                    return JSONResponse({'code':'NOT_FOUND','message':'合成故障：项目不存在'},status_code=404)
                if faults['html500']:
                    return HTMLResponse('<html><body><h1>Synthetic Proxy Failure</h1></body></html>',status_code=500)
                if faults['empty500']:
                    return Response(status_code=500)
            return await call_next(request)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        for _ in range(100):
            if server.started:break
            await asyncio.sleep(.05)
        results=[]
        try:
            async with async_playwright() as pw:
                browser=await pw.chromium.launch(headless=True)
                context=await browser.new_context(viewport={'width':1440,'height':1000})
                page=await context.new_page()
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                base=f'http://127.0.0.1:{port}'
                await page.goto(base)
                await page.get_by_label('本机访问令牌').fill(TOKEN)
                await page.get_by_role('button',name='进入工作台').click()
                await page.get_by_role('heading',name='选择或新建需求项目').wait_for()

                async def body_text():return await page.locator('body').inner_text()
                async def project_pid(name):
                    projects=await (await page.request.get(base+'/api/projects')).json()
                    return next(p['id'] for p in projects if p['name']==name)
                async def create_and_enter(name):
                    await page.get_by_label('新项目名称',exact=True).fill(name)
                    await page.get_by_role('button',name='创建项目',exact=True).click()
                    workspace=page.locator('.project-workspace:not([hidden])')
                    await workspace.get_by_role('heading',name=name,exact=True).wait_for()
                    return workspace

                # e. 正常兼容：能力声明完整、无兼容性横幅、runtime_id 稳定
                first=await (await page.request.get(base+'/api/session')).json()
                second=await (await page.request.get(base+'/api/session')).json()
                assert 'actions' in first['backend']['capabilities']
                assert first['backend']['runtime_id']==second['backend']['runtime_id']
                assert first['backend']['started_at']==second['backend']['started_at']
                text=await body_text()
                assert '缺少必需接口能力' not in text and '兼容性未核实' not in text
                workspace=await create_and_enter('兼容检查A')
                pid_a=await project_pid('兼容检查A')
                assert await workspace.locator('.task-guide .primary').is_enabled()
                await page.screenshot(path=str(evidence/'e-normal.png'),full_page=True)
                results.append({'scenario':'e-normal-compat','status':'VERIFIED','runtime_id':first['backend']['runtime_id']})

                # a. 模拟旧后端：/actions 与 /artifacts 为 FastAPI 风格 404
                faults['legacy_actions']=True
                workspace_b=await create_and_enter('兼容检查B')
                text=await body_text()
                assert 'Not Found' not in text,'裸 Not Found 仍被渲染'
                await workspace_b.get_by_text('部分状态未能更新',exact=False).wait_for()
                await workspace_b.get_by_text('任务状态尚未取得，发起动作已暂停',exact=False).wait_for()
                assert await workspace_b.locator('.task-guide .primary').is_disabled()
                assert len(model['requests'])==0
                await page.screenshot(path=str(evidence/'a-legacy-fault.png'),full_page=True)
                faults['legacy_actions']=False
                await workspace_b.get_by_role('button',name='重试',exact=True).click()
                await workspace_b.get_by_text('部分状态未能更新',exact=False).wait_for(state='detached')
                text=await body_text()
                assert '部分状态未能更新' not in text
                assert await workspace_b.locator('.task-guide .primary').is_enabled()
                projects=await (await page.request.get(base+'/api/projects')).json()
                assert len(projects)==2,'恢复过程不得重建项目'
                await page.screenshot(path=str(evidence/'a-recovered.png'),full_page=True)
                results.append({'scenario':'a-legacy-backend-404','status':'VERIFIED','model_requests':len(model['requests'])})
                assert len(model['requests'])==0

                # b. 后端风格 404（{'code':'NOT_FOUND'}）：显示项目不存在，不误报版本错配
                faults['project_not_found']=True
                await page.reload()
                await page.get_by_role('button',name='兼容检查A',exact=False).first.click()
                await page.get_by_role('heading',name='项目不存在或已被删除').wait_for()
                text=await body_text()
                assert '前后端版本可能不兼容' not in text
                await page.screenshot(path=str(evidence/'b-project-not-found.png'),full_page=True)
                await page.get_by_role('button',name='返回项目列表').click()
                await page.get_by_role('heading',name='选择或新建需求项目').wait_for()
                faults['project_not_found']=False
                results.append({'scenario':'b-backend-not-found','status':'VERIFIED'})

                # c1/c2. HTML 500 与空错误体：不显示裸解析错误
                faults['html500']=True
                await page.get_by_role('button',name='兼容检查A',exact=False).first.click()
                await page.get_by_role('heading',name='服务返回了无法识别的内容').wait_for()
                text=await body_text()
                assert 'Synthetic Proxy Failure' not in text and 'Unexpected token' not in text
                await page.screenshot(path=str(evidence/'c-html-500.png'),full_page=True)
                faults['html500']=False;faults['empty500']=True
                await page.get_by_role('button',name='重新加载').click()
                await page.get_by_role('heading',name='服务返回了无法识别的内容').wait_for()
                await page.screenshot(path=str(evidence/'c-empty-500.png'),full_page=True)
                faults['empty500']=False
                await page.get_by_role('button',name='重新加载').click()
                workspace=page.locator('.project-workspace:not([hidden])')
                await workspace.get_by_role('heading',name='兼容检查A',exact=True).wait_for()
                results.append({'scenario':'c-non-json-error-bodies','status':'VERIFIED'})

                # c3. 网络失败：已有内容与未发送输入保留，出现“最新状态尚未取得”
                composer=workspace.get_by_label('本轮想讨论的内容')
                await composer.fill('网络故障前未发送的草稿')
                async def abort(route):await route.abort()
                await page.route('**/api/projects/**',abort)
                await page.get_by_role('button',name='兼容检查B',exact=False).first.click()
                await page.get_by_role('button',name='兼容检查A',exact=False).first.click()
                await workspace.get_by_text('最新状态尚未取得：无法连接服务',exact=False).wait_for()
                assert await workspace.get_by_role('heading',name='兼容检查A',exact=True).is_visible()
                assert await composer.input_value()=='网络故障前未发送的草稿'
                text=await body_text()
                assert 'Unexpected token' not in text and 'SyntaxError' not in text
                await page.screenshot(path=str(evidence/'c-network-preserved.png'),full_page=True)
                await page.unroute('**/api/projects/**')
                results.append({'scenario':'c-network-failure-preserves-content','status':'VERIFIED'})

                # d. 401/403/409 不回归，不自动重放
                session=await (await page.request.get(base+'/api/session')).json()
                stale_resp=await page.request.patch(base+'/api/projects/'+pid_a,data={'expected_revision':999,'name':'不应生效'},headers={'Origin':base,'X-CSRF-Token':session['csrf']})
                assert stale_resp.status==409 and (await stale_resp.json())['code']=='STALE_REVISION'
                forbidden=await page.request.post(base+'/api/projects',data={'name':'x'},headers={'Origin':base,'X-CSRF-Token':'wrong'})
                assert forbidden.status==403 and (await forbidden.json())['code']=='CSRF_DENIED'
                assert (await project_pid('兼容检查A'))==pid_a
                await context.clear_cookies()
                await page.reload()
                await page.get_by_role('heading',name='需求工作区').wait_for()
                await page.get_by_label('本机访问令牌').fill(TOKEN)
                await page.get_by_role('button',name='进入工作台').click()
                await page.get_by_role('heading',name='选择或新建需求项目').wait_for()
                await context.clear_cookies()
                await page.get_by_role('button',name='兼容检查A',exact=False).first.click()
                await page.get_by_role('heading',name='登录已失效，请重新登录').wait_for()
                await page.screenshot(path=str(evidence/'d-session-expired.png'),full_page=True)
                await page.get_by_role('button',name='重新登录').click()
                await page.get_by_label('本机访问令牌').wait_for()
                await page.screenshot(path=str(evidence/'d-back-to-login.png'),full_page=True)
                results.append({'scenario':'d-401-403-409-preserved','status':'VERIFIED'})

                # f. 能力信息缺失→一次性“兼容性未核实”提示；能力不含 actions→持续横幅并暂停动作
                faults['session_patch']='drop_backend'
                await page.get_by_label('本机访问令牌').fill(TOKEN)
                await page.get_by_role('button',name='进入工作台').click()
                await page.get_by_role('heading',name='选择或新建需求项目').wait_for()
                await page.get_by_role('button',name='兼容检查A',exact=False).first.click()
                workspace=page.locator('.project-workspace:not([hidden])')
                await workspace.get_by_text('兼容性未核实',exact=False).wait_for()
                assert await workspace.locator('.task-guide .primary').is_enabled()
                await page.screenshot(path=str(evidence/'f-unverified.png'),full_page=True)
                await workspace.get_by_role('button',name='知道了').click()
                assert '兼容性未核实' not in await body_text()
                faults['session_patch']='drop_actions'
                await page.reload()
                await page.get_by_role('button',name='兼容检查A',exact=False).first.click()
                workspace=page.locator('.project-workspace:not([hidden])')
                await workspace.get_by_text('前后端版本可能不兼容',exact=False).wait_for()
                assert await workspace.locator('.task-guide .primary').is_disabled()
                await page.screenshot(path=str(evidence/'f-incompatible.png'),full_page=True)
                faults['session_patch']=None
                results.append({'scenario':'f-capability-missing-and-unverified','status':'VERIFIED'})
                assert len(model['requests'])==0
                assert not errors,errors
                (evidence/'results.json').write_text(json.dumps({'results':results,'model_calls':len(model['requests']),'console_errors':errors},ensure_ascii=False,indent=2),'utf-8')
                await browser.close()
                print(str(evidence))
        finally:
            server.should_exit=True;thread.join(timeout=5)


if __name__=='__main__':asyncio.run(main())
