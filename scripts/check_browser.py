"""Real browser engineering tests with an explicitly synthetic test project."""
import asyncio
import json
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import uvicorn
from playwright.async_api import async_playwright
from app.core import ROOT, dumps, digest
from app.main import create_app
from app.exports import document_files
from tests.helpers import prepared

async def main():
    run_dir=ROOT/'evidence/runtime/browser'
    run_dir.mkdir(parents=True,exist_ok=True)
    app=create_app(run_dir/'db',access_token='offline-browser-engineering-token',env_path=run_dir/'.env')
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
        try:
            await page.goto('http://127.0.0.1:8876')
            await page.get_by_label('本机访问令牌',exact=True).fill('offline-browser-engineering-token')
            await page.get_by_role('button',name='进入工作台').click()
            await page.get_by_role('button',name='合成工程验证项目').first.click()
            await page.get_by_role('button',name='页面与文档',exact=True).click()
            frame=page.frame_locator('iframe[title="低保真页面预览"]')
            await frame.get_by_role('cell',name='00:00—08:00',exact=True).wait_for()
            await frame.get_by_role('button',name='错误',exact=True).click()
            await frame.get_by_text('读取失败（模拟）',exact=False).wait_for()
            results.append({'case':'AT-13','result':'ENGINEERING_PASS','evidence':'browser/wireframe.png'})
            await frame.get_by_role('button',name='正常',exact=True).click()
            await page.screenshot(path=str(run_dir/'wireframe.png'),full_page=True)
            await page.get_by_role('button',name='确认与交接',exact=True).click()
            await page.get_by_role('button',name='确认此版本的 PRD 内容').click()
            await page.get_by_role('button',name='生成此基线的研发交接包').wait_for()
            await page.get_by_role('button',name='生成此基线的研发交接包').click()
            link=page.get_by_role('link',name='下载交接包',exact=False)
            await link.wait_for()
            async with page.expect_download() as dl:
                await link.click()
            download=await dl.value;await download.save_as(str(run_dir/'handoff.zip'))
            results.append({'case':'AT-16/18/19 UI positive','result':'ENGINEERING_PASS','note':'合成 UI 点击确认，仅验证机制，非真人业务批准'})
            await page.screenshot(path=str(run_dir/'confirmation.png'),full_page=True)
            # Untouched real runtime, no key, new project.
            await page.get_by_label('新项目名称').fill('无 Key 浏览器验证')
            await page.get_by_role('button',name='创建项目',exact=True).click()
            await page.get_by_role('button',name='开始分析 →').click()
            await page.get_by_text('请先在模型设置中配置此接收端的 Key',exact=False).wait_for()
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
    print(dumps(results))

if __name__=='__main__':asyncio.run(main())
