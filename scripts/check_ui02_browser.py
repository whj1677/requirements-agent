"""Two synthetic scenarios through normal UI + real loopback model HTTP."""
import asyncio
import io
import json
import socket
import sys
import threading
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import uvicorn
from PIL import Image
from playwright.async_api import async_playwright
from app.core import ROOT
from app.main import create_app
from app.provider import Provider, origin
from tests.ui02_fixture import model_server


async def main():
    evidence=ROOT/'evidence/runtime/ui02'/uuid.uuid4().hex[:8]
    evidence.mkdir(parents=True)
    with model_server() as model:
        provider=Provider(evidence/'absent.env')
        provider.keys[origin(model['config'])]='ui02-synthetic-key'
        app=create_app(evidence/'data',access_token='ui02-browser',provider=provider)
        for slot in ('model','vision'):app.state.store.setting(slot,model['config'])
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
                page=await browser.new_page(viewport={'width':1440,'height':1000})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                await page.goto(f'http://127.0.0.1:{port}')
                await page.get_by_label('本机访问令牌').fill('ui02-browser')
                await page.get_by_role('button',name='进入工作台').click()
                async def read_project(pid):return await (await page.request.get(f'http://127.0.0.1:{port}/api/projects/{pid}')).json()
                async def run_action(pid,button):
                    await button.click()
                    dialog=page.get_by_role('dialog',name='核对本次任务')
                    await dialog.wait_for()
                    await page.screenshot(path=str(evidence/'authorization.png'),full_page=True)
                    async with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/actions')) as response:
                        await dialog.get_by_role('button',name='授权本次范围并运行').click() if await dialog.get_by_role('button',name='授权本次范围并运行').count() else await dialog.get_by_role('button',name='运行本次任务').click()
                    task=await (await response.value).json()
                    await dialog.wait_for(state='hidden')
                    await page.screenshot(path=str(evidence/'task-running.png'),full_page=True)
                    for _ in range(250):
                        tasks=await (await page.request.get(f'http://127.0.0.1:{port}/api/projects/{pid}/actions')).json()
                        task=next(t for t in tasks if t['id']==task['id'])
                        if task['status'] not in ('queued','running'):break
                        await asyncio.sleep(.04)
                    await page.wait_for_timeout(950)
                    return task
                first_pid=None
                for topic in ('电价时段','联系人'):
                    await page.get_by_label('新项目名称',exact=True).fill('UI02 '+topic)
                    await page.get_by_role('button',name='创建项目',exact=True).click()
                    await page.get_by_role('heading',name='UI02 '+topic,exact=True).wait_for()
                    projects=await (await page.request.get(f'http://127.0.0.1:{port}/api/projects')).json()
                    pid=next(p['id'] for p in projects if p['name']=='UI02 '+topic)
                    first_pid=first_pid or pid
                    workspace=page.locator('.project-workspace:not([hidden])')
                    composer=workspace.get_by_label('本轮想讨论的内容')
                    await composer.fill(f'维护{topic}列表，只读角色不能修改。')
                    await workspace.get_by_role('button',name='添加资料',exact=True).first.click()
                    drawer=page.get_by_role('dialog',name='项目资料')
                    out=io.BytesIO();Image.new('RGB',(160,100),'white').save(out,format='PNG')
                    await drawer.locator('input[type=file]').set_input_files({'name':topic+'.png','mimeType':'image/png','buffer':out.getvalue()})
                    await drawer.get_by_text('待视觉分析',exact=True).wait_for()
                    await page.screenshot(path=str(evidence/(topic+'-sources.png')),full_page=True)
                    await drawer.get_by_role('button',name='关闭项目资料').click()
                    assert await composer.input_value()==f'维护{topic}列表，只读角色不能修改。'
                    before=len(model['requests'])
                    await workspace.locator('.task-guide .primary').click()
                    dialog=page.get_by_role('dialog',name='核对本次任务')
                    assert len(model['requests'])==before
                    await dialog.get_by_role('button',name='返回保留输入').click()
                    model['delay']=.15
                    task=await run_action(pid,workspace.locator('.task-guide .primary'))
                    assert task['status']=='succeeded' and task['calls']==2,task
                    p=await read_project(pid);assert len(p['items'])==3 and p['questions'][0]['status']=='open'
                    before=len(model['requests'])
                    await workspace.get_by_role('button',name='暂时不知道，先看方案').click()
                    assert await workspace.get_by_role('heading',name='方案对比',exact=True).is_visible()
                    assert len(model['requests'])==before and (await read_project(pid))['questions'][0]['status']=='open'
                    task=await run_action(pid,workspace.locator('.task-guide .primary'))
                    assert task['status']=='succeeded',task
                    options=workspace.locator('.artifact-panel:visible article').filter(has=page.get_by_role('button',name='按此方向继续讨论'))
                    await options.nth(1).get_by_role('button',name='查看对应原型').click()
                    await workspace.get_by_text('此方案尚无原型。',exact=False).wait_for()
                    await page.screenshot(path=str(evidence/(topic+'-preview-boundary.png')),full_page=True)
                    before_p=await read_project(pid)
                    await options.first.get_by_role('button',name='按此方向继续讨论').click()
                    await page.get_by_role('dialog',name='核对方案操作').get_by_role('button',name='确认提交').click()
                    await page.get_by_role('dialog',name='核对方案操作').wait_for(state='hidden')
                    after=await read_project(pid);assert after['items']==before_p['items'] and after['questions']==before_p['questions']
                    if topic=='电价时段':
                        task=await run_action(pid,workspace.get_by_role('button',name='生成讨论原型',exact=True))
                        assert task['status']=='succeeded',task
                        p=await read_project(pid);assert p['ui']['generation_target']['option_id']==p['options'][0]['id']
                        await options.nth(1).get_by_role('button',name='查看对应原型').click()
                        assert await workspace.get_by_text('此方案尚无原型。',exact=False).is_visible()
                    await workspace.get_by_role('button',name='明确需求',exact=False).click()
                    for _ in range(3):
                        await workspace.locator('.artifact-panel:visible').get_by_role('button',name='采纳',exact=True).first.click()
                        await page.wait_for_timeout(150)
                    p=await read_project(pid);assert sum(i['selection_status']=='selected' for i in p['items'])==3
                    await workspace.get_by_role('button',name='查看来源',exact=True).first.click()
                    await page.locator('.source-highlight').wait_for()
                    await page.screenshot(path=str(evidence/'source-location.png'),full_page=True)
                    await page.get_by_role('button',name='关闭项目资料').click()
                    await workspace.get_by_role('button',name='文档评审',exact=False).click()
                    task=await run_action(pid,workspace.get_by_role('button',name='生成讨论稿 PRD',exact=True))
                    assert task['status']=='succeeded',task
                    await workspace.get_by_role('heading',name=topic+'需求讨论稿',exact=True).wait_for()
                    assert await workspace.get_by_text('（尚未决定）',exact=False).count()>0
                    await page.screenshot(path=str(evidence/(topic+'-draft.png')),full_page=True)
                    if topic=='联系人':assert (await read_project(pid))['ui'] is None
                    async with page.expect_download() as download:
                        await workspace.get_by_role('link',name='DOCX 下载').click()
                    await (await download.value).save_as(str(evidence/(topic+'.docx')))
                    old=(await read_project(pid))['documents']['prd']
                    model['fail_next']=True
                    task=await run_action(pid,workspace.get_by_role('button',name='更新讨论稿 PRD',exact=True))
                    assert task['status']=='failed' and (await read_project(pid))['documents']['prd']==old
                    await page.screenshot(path=str(evidence/(topic+'-failure.png')),full_page=True)
                    await workspace.get_by_role('button',name='确认交接',exact=False).click()
                    assert await workspace.get_by_role('button',name='确认此版本的 PRD 内容').is_disabled()
                    await page.screenshot(path=str(evidence/(topic+'-confirmation-blocked.png')),full_page=True)
                    # A failed URL remains local and does not invalidate existing output content.
                    await workspace.get_by_role('button',name='添加资料',exact=True).first.click()
                    drawer=page.get_by_role('dialog',name='项目资料')
                    await drawer.get_by_label('公开网页 URL').fill('http://127.0.0.1:1/private')
                    await drawer.get_by_role('button',name='确认有权读取并添加').click()
                    bad=drawer.get_by_role('article').filter(has=page.get_by_role('heading',name='http://127.0.0.1:1/private'))
                    await bad.get_by_text('读取失败',exact=True).wait_for()
                    assert (await read_project(pid))['documents']['prd']==old
                    await page.screenshot(path=str(evidence/(topic+'-source-failure.png')),full_page=True)
                    await bad.get_by_role('button',name='排除材料').click()
                    await bad.get_by_text('已排除',exact=True).wait_for()
                    await drawer.get_by_role('button',name='关闭项目资料').click()
                    await composer.fill(topic+'未发送的草稿')
                    before=len(model['requests'])
                    for width in (1440,1280,1024,800):
                        await page.set_viewport_size({'width':width,'height':950})
                        if width==800:
                            await workspace.get_by_role('button',name='需求助手',exact=True).click()
                        assert await composer.input_value()==topic+'未发送的草稿'
                        assert await page.locator('body').evaluate('n=>n.scrollWidth<=innerWidth'),width
                        await page.screenshot(path=str(evidence/(topic+f'-{width}.png')),full_page=True)
                    assert len(model['requests'])==before
                    await page.set_viewport_size({'width':1440,'height':1000})
                    results.append({'scenario':topic,'status':'OFFLINE_HTTP_UI_VERIFIED','project_id':pid})
                await page.get_by_role('button',name='UI02 电价时段',exact=False).first.click()
                assert await page.locator('.project-workspace:not([hidden])').get_by_label('本轮想讨论的内容').input_value()=='电价时段未发送的草稿'
                # A slow response belongs to A while B remains visible with its own input.
                workspace=page.locator('.project-workspace:not([hidden])')
                model['delay']=.4
                await workspace.get_by_role('button',name='发送并整理').click()
                dialog=page.get_by_role('dialog',name='核对本次任务')
                await dialog.get_by_role('button',name='授权本次范围并运行').click()
                await dialog.wait_for(state='hidden')
                await page.get_by_role('button',name='UI02 联系人',exact=False).first.click()
                await workspace.get_by_role('heading',name='UI02 联系人',exact=True).wait_for()
                await page.wait_for_timeout(1000)
                assert await workspace.get_by_label('本轮想讨论的内容').input_value()=='联系人未发送的草稿'
                assert (await read_project(first_pid))['messages'][-1]['stage']=='clarify'
                assert await workspace.get_by_role('heading',name='UI02 联系人',exact=True).is_visible()
                assert not errors,errors
                (evidence/'results.json').write_text(json.dumps({'results':results,'model_calls':len(model['requests']),'console_errors':errors},ensure_ascii=False,indent=2),'utf-8')
                await browser.close()
                print(str(evidence))
        finally:
            server.should_exit=True;thread.join(timeout=5)


if __name__=='__main__':asyncio.run(main())
