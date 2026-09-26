"""Historical UI02 browser script for the former layout.

Use check_five_step_browser.py with an isolated --web-dist for the current UI.
This file remains to preserve the historical test and its evidence boundary.
"""
import asyncio
import json
import sys
if __name__ == '__main__':
    raise SystemExit('Historical UI selectors; run scripts/check_five_step_browser.py --web-dist evidence/repair-20260925/dist-frontend')
import threading
import time
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import uvicorn
from playwright.async_api import async_playwright
from app.core import ROOT, dumps, digest
from app.main import create_app
from app.exports import document_files
from tests.helpers import prepared

async def main():
    run_dir=ROOT/'evidence/runtime/ui02-regression'/('browser-'+uuid.uuid4().hex[:8])
    run_dir.mkdir(parents=True,exist_ok=True)
    app=create_app(run_dir/('db-'+uuid.uuid4().hex),access_token='offline-browser-engineering-token',env_path=run_dir/'.env')
    p=prepared(app.state.store)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8876,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        await asyncio.sleep(.1)
    results=[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        page=await browser.new_page(viewport={'width':1440,'height':1000})
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        run_posts=[];page.on('request',lambda request:run_posts.append(request.url) if request.method=='POST' and request.url.endswith(('/runs','/actions')) else None)
        try:
            await page.goto('http://127.0.0.1:8876')
            await page.get_by_label('本机访问令牌',exact=True).fill('offline-browser-engineering-token')
            await page.get_by_role('button',name='进入工作台').click()
            await page.get_by_role('button',name='合成工程验证项目').first.click()
            work=page.locator('.project-workspace:not([hidden])')
            composer=work.get_by_label('本轮想讨论的内容')
            await composer.fill('保留的讨论草稿')
            await composer.evaluate("node=>{node.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true}));node.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true}));}")
            assert await composer.input_value()=='保留的讨论草稿' and not run_posts
            for width in (1440,1280,1024):
                await page.set_viewport_size({'width':width,'height':900})
                assert await page.locator('body').evaluate('(node)=>node.scrollWidth<=window.innerWidth'),f'discussion overflow at {width}'
                await page.screenshot(path=str(run_dir/f'discussion-{width}.png'),full_page=True)
            await page.set_viewport_size({'width':800,'height':900})
            await page.get_by_role('button',name='任务内容',exact=True).click()
            await page.get_by_role('button',name='明确需求',exact=False).click()
            await work.get_by_role('heading',name='需求摘要').first.wait_for()
            assert await page.locator('body').evaluate('(node)=>node.scrollWidth<=window.innerWidth')
            await page.screenshot(path=str(run_dir/'discussion-800-artifacts.png'),full_page=True)
            await page.set_viewport_size({'width':1440,'height':1000})
            await page.get_by_label('新项目名称',exact=True).fill('草稿切换验证')
            await page.get_by_role('button',name='创建项目',exact=True).click()
            await page.get_by_role('heading',name='草稿切换验证').wait_for()
            await work.get_by_label('本轮想讨论的内容').fill('第二项目草稿')
            await page.get_by_role('button',name='合成工程验证项目').click()
            await page.get_by_role('heading',name='合成工程验证项目').wait_for()
            assert await work.get_by_label('本轮想讨论的内容').input_value()=='保留的讨论草稿'
            await page.get_by_role('button',name='草稿切换验证').click()
            await page.get_by_role('heading',name='草稿切换验证').wait_for()
            assert await work.get_by_label('本轮想讨论的内容').input_value()=='第二项目草稿'
            await page.get_by_role('button',name='合成工程验证项目').click()
            await page.get_by_role('heading',name='合成工程验证项目').wait_for()
            await page.get_by_role('button',name='推演方案',exact=False).click()
            await page.get_by_role('button',name='理解需求',exact=False).click()
            assert await composer.input_value()=='保留的讨论草稿'
            await page.get_by_role('button',name='推演方案',exact=False).click()
            await page.get_by_role('button',name='查看项目当前原型').click()
            frame=work.frame_locator('iframe[title="低保真页面预览"]')
            await frame.get_by_role('cell',name='00:00—08:00',exact=True).wait_for()
            await frame.get_by_role('button',name='错误',exact=True).click()
            await frame.get_by_text('读取失败（模拟）',exact=False).wait_for()
            results.append({'case':'AT-13','result':'ENGINEERING_PASS','evidence':'browser/workbench-1440.png'})
            await frame.get_by_role('button',name='正常',exact=True).click()
            await page.screenshot(path=str(run_dir/'workbench-1440.png'),full_page=True)
            for width in (1280,1024):
                await page.set_viewport_size({'width':width,'height':900})
                assert await page.locator('body').evaluate('(node)=>node.scrollWidth<=window.innerWidth'),f'horizontal clipping at {width}'
                await page.screenshot(path=str(run_dir/f'workbench-{width}.png'),full_page=True)
            await page.set_viewport_size({'width':1440,'height':1000})
            await page.get_by_role('button',name='文档评审',exact=False).click()
            await work.locator('.document-layout').get_by_text('本期范围 规则 验收与未知事项').first.wait_for()
            await page.screenshot(path=str(run_dir/'document.png'),full_page=True)
            await page.get_by_role('button',name='确认交接',exact=False).click()
            await page.get_by_role('button',name='确认此版本的 PRD 内容').click()
            async def invalid_csrf(route):
                await route.continue_(headers={**route.request.headers,'x-csrf-token':'synthetic-invalid-csrf'})
            await page.route('**/confirmations',invalid_csrf)
            await page.get_by_role('dialog',name='确认内容复核').get_by_role('button',name='提交服务端确认').click()
            await page.get_by_role('dialog',name='确认内容复核').get_by_role('alert').wait_for()
            assert await page.get_by_role('dialog',name='确认内容复核').is_visible()
            assert not app.state.store.records(p['id'],'confirmation')
            await page.unroute('**/confirmations',invalid_csrf)
            results.append({'case':'confirmation 403 preserves context without success','result':'ENGINEERING_PASS'})
            await page.get_by_role('dialog',name='确认内容复核').get_by_role('button',name='提交服务端确认').click()
            await page.get_by_role('button',name='生成此基线的研发交接包').wait_for()
            await page.get_by_role('button',name='生成此基线的研发交接包').click()
            link=page.get_by_role('link',name='下载交接包',exact=False)
            await link.wait_for()
            async with page.expect_download() as dl:
                await link.click()
            download=await dl.value;await download.save_as(str(run_dir/'handoff.zip'))
            results.append({'case':'AT-16/18/19 UI positive','result':'ENGINEERING_PASS','note':'合成 UI 点击确认，仅验证机制，非真人业务批准'})
            await page.screenshot(path=str(run_dir/'confirmation.png'),full_page=True)
            before=len(app.state.store.records(p['id'],'confirmation'))
            await page.get_by_role('button',name='确认此版本的 PRD 内容').click()
            current=app.state.store.get(p['id'])
            with app.state.store.edit(p['id'],current['revision'],'合成并发版本变化') as (changed,_):
                changed['items'][0]['statement']='并发合成修改，旧确认请求必须拒绝。'
            await page.get_by_role('dialog',name='确认内容复核').get_by_role('button',name='提交服务端确认').click()
            await page.get_by_text('版本已变化：',exact=False).wait_for()
            assert await page.get_by_role('dialog',name='确认内容复核').is_visible()
            assert len(app.state.store.records(p['id'],'confirmation'))==before
            await page.get_by_role('dialog',name='确认内容复核').get_by_role('button',name='返回核对').click()
            await page.wait_for_function("document.activeElement?.textContent?.includes('确认此版本的 PRD 内容')")
            results.append({'case':'confirmation 409 preserves dialog and no replay','result':'ENGINEERING_PASS'})
            await page.reload()
            await page.get_by_role('button',name='合成工程验证项目').click()
            await page.get_by_role('button',name='文档评审',exact=False).click()
            await page.get_by_text('基于旧版本',exact=False).wait_for()
            assert await work.locator('.document-layout').get_by_text('管理员能够维护电价时段。',exact=False).count()>0
            assert await work.locator('.document-layout').get_by_text('并发合成修改，旧确认请求必须拒绝。',exact=False).count()==0
            results.append({'case':'stale document uses own item snapshot','result':'ENGINEERING_PASS'})
            # Untouched real runtime, no key, new project.
            await page.get_by_label('新项目名称',exact=True).fill('无 Key 浏览器验证')
            await page.get_by_role('button',name='创建项目',exact=True).click()
            await page.get_by_role('heading',name='无 Key 浏览器验证',exact=True).wait_for()
            await work.get_by_role('button',name='重命名',exact=True).click()
            rename_dialog=page.get_by_role('dialog',name='重命名项目')
            name_input=rename_dialog.get_by_label('项目名称')
            await name_input.fill('临时名称')
            await name_input.fill('')
            assert await rename_dialog.is_visible()
            assert not await rename_dialog.get_by_role('button',name='保存',exact=True).is_enabled()
            await name_input.fill('无 Key 项目（已重命名）')
            await page.get_by_role('dialog').get_by_role('button',name='保存').click()
            await page.get_by_role('heading',name='无 Key 项目（已重命名）').wait_for()
            await work.get_by_label('本轮想讨论的内容').fill('模型失败后应保留这段输入')
            await page.get_by_role('button',name='发送并整理').click()
            await page.get_by_text('主分析尚未配置可用 Key',exact=False).wait_for()
            assert await page.get_by_role('dialog').get_by_role('button',name='授权本次范围并运行').is_disabled()
            assert not run_posts
            assert await work.get_by_label('本轮想讨论的内容').input_value()=='模型失败后应保留这段输入'
            await page.screenshot(path=str(run_dir/'no-key.png'),full_page=True)
            results.append({'case':'AT-01','result':'ENGINEERING_PASS','evidence':'browser/no-key.png'})
            assert not errors,errors
            results.append({'case':'browser-console','result':'ENGINEERING_PASS','errors':errors})
            files={}
            for kind in ('prd','mrd'):
                for filename,data in (await document_files(p,kind)).items():
                    target=run_dir/kind/filename;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
                    files[kind+'/'+filename]=len(data)
            results.append({'case':'AT-15/41 export generation','result':'GENERATED_RENDER_PENDING','files':files})
        finally:
            await browser.close()
            server.should_exit=True
            thread.join(timeout=10)
    (run_dir/'results.json').write_text(dumps({'mode':'OFFLINE_ENGINEERING_FIXTURE','results':results}),encoding='utf8')
    print(dumps(dict(evidence=str(run_dir),results=results)))

if __name__=='__main__':asyncio.run(main())
