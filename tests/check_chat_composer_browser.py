"""Isolated long-chat composer reachability on the formal 8765 origin.

No daily project is loaded: every /api request is routed to a temporary
create_app instance, and its provider has no network request path.
"""

import argparse
import asyncio
import base64
import io
import json
import mimetypes
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.main import create_app
from app.provider import DEFAULT, Provider, origin

ORIGIN = 'http://127.0.0.1:8765'
VIEWPORTS = [(1440, 900), (1280, 720), (2552, 1308), (390, 844)]
LONG_REPLY = ('这是合成的需求访谈长回复。当前只记录提问和边界，不设定实际电价规则。' * 38)


class OfflineInterview(Provider):
    def __init__(self, folder):
        super().__init__(env_path=folder / 'absent.env', credential_dir=folder / 'credentials')
        self.keys[origin(DEFAULT)] = 'OFFLINE_CHAT_COMPOSER_FIXTURE_ONLY'
        self.calls = 0
        self.stages = []

    async def request(self, config, messages, evidence=None):
        self.calls += 1
        header = json.loads(messages[0]['content'].split('可信任务头：')[-1])
        stage = header['stage']
        self.stages.append(stage)
        if not evidence:
            raise AssertionError('model evidence was not supplied')
        value = dict(schema_version='1.1', stage=stage, summary=LONG_REPLY,
                     proposals=[], questions=[], findings=[], used_source_refs=[],
                     limitations=[], result={'can_organize': False})
        if stage == 'vision':
            context = json.loads(messages[1]['content'][0]['text'])
            image_ids = {row['id'] for row in context['source_status'] if row['parse_status'] == 'awaiting_vision'}
            excerpt = next(row for row in context['excerpts'] if row['source_id'] in image_ids)
            ref = dict(source_id=excerpt['source_id'], excerpt_id=excerpt['id'])
            value['summary'] = '离线合成图片中可见绿色方块，其他内容不可推断。'
            value['used_source_refs'] = [ref]
            value['result'] = dict(observations=[dict(source_ref=ref, region='全部', observation='绿色方块', uncertain=False)],
                                   unobservable=['业务含义未知'], unreadable=[])
        evidence.before_send(config)
        evidence.response(content=json.dumps(value, ensure_ascii=False), http_status=200,
                          finish_reason='stop', usage={'prompt_tokens': 20, 'completion_tokens': 20, 'total_tokens': 40},
                          response_model='OFFLINE_CHAT_COMPOSER')
        return value, dict(request_model=config['model'], response_model='OFFLINE_CHAT_COMPOSER',
                           usage={'prompt_tokens': 20, 'completion_tokens': 20, 'total_tokens': 40},
                           origin=origin(config), finish_reason='stop')


def seed(store, name):
    p = store.create(name)
    with store.edit(p['id'], p['revision'], '合成长聊天历史') as (p, _):
        for round_index in range(5):
            p['messages'].append(dict(role='user', stage='interview', text=f'第{round_index + 1}轮合成提问',
                                      created='2026-10-09T00:00:00Z', user_task_id=f'SYN-{round_index}'))
            p['messages'].append(dict(role='assistant', stage='interview', text=LONG_REPLY,
                                      created='2026-10-09T00:00:01Z', user_task_id=f'SYN-{round_index}'))
    return p['id']


def seed_pending_image(store):
    p = store.create('图片待识别隔离项目')
    with store.edit(p['id'], p['revision'], '合成图片来源') as (project, _):
        base = dict(purpose='reference', uri=None, sha256='synthetic-only',
                    created='2026-10-09T00:00:00Z', version=1, excerpts=[], failure_reason='')
        project['sources'].extend([
            dict(base, id='SYN-PENDING', title='合成待识别图片', excluded=False,
                 parse_status='awaiting_vision', image_mime='image/png', vision_run_id=None),
            dict(base, id='SYN-EXCLUDED', title='已排除图片', excluded=True,
                 parse_status='awaiting_vision', image_mime='image/png', vision_run_id=None),
            dict(base, id='SYN-READ', title='已识别图片', excluded=False,
                 parse_status='read', image_mime='image/png', vision_run_id='RUN-SYN'),
        ])
    return p['id']


async def composer_geometry(page, button_text='发送'):
    return await page.evaluate('''buttonText => {
      const root=document.querySelector('.project-workspace:not([hidden])');
      const textarea=root?.querySelector('.connected-chat textarea[aria-label="你的想法"]');
      const button=[...(root?.querySelectorAll('.connected-chat button')||[])].find(x=>x.textContent.trim()===buttonText);
      const area=textarea?.getBoundingClientRect(), send=button?.getBoundingClientRect();
      const hit=r=>r&&r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth;
      const clickable=(el,r)=>!!(el&&hit(r)&&el.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)));
      const boxes={};
      for(const selector of ['.workspace','.project-workspace','.connected-workspace','.connected-chat','.intake-chat','.chat-messages']){
        const el=root?.querySelector(selector)||document.querySelector(selector);
        if(el){const r=el.getBoundingClientRect();boxes[selector]={top:r.top,bottom:r.bottom,height:r.height,clientHeight:el.clientHeight,scrollHeight:el.scrollHeight,overflowY:getComputedStyle(el).overflowY,scrollTop:el.scrollTop};}
      }
      return {viewport:{width:innerWidth,height:innerHeight},textarea:area&&{top:area.top,bottom:area.bottom},button:send&&{top:send.top,bottom:send.bottom},textarea_visible:clickable(textarea,area),button_visible:clickable(button,send),boxes};
    }''', button_text)


async def wait_assistant(page, count):
    await page.wait_for_function('count => document.querySelectorAll(".project-workspace:not([hidden]) .chat-message.assistant").length >= count', arg=count, timeout=20000)
    await page.wait_for_timeout(100)


async def run(args):
    static = args.static_build.resolve() if args.static_build else None
    if static and not (static / 'index.html').is_file():
        raise AssertionError('static build index.html missing')
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
    report = {'surface': 'formal_8765', 'static_build': str(static) if static else None,
              'viewports': [], 'provider_calls': 0, 'page_errors': []}
    with tempfile.TemporaryDirectory(prefix='ra-chat-composer-') as scratch:
        folder = Path(scratch)
        provider = OfflineInterview(folder)
        app = create_app(folder / 'data', provider=provider)
        store = app.state.store
        names = [(f'长回复隔离项目 {width}×{height}', seed(store, f'长回复隔离项目 {width}×{height}')) for width, height in VIEWPORTS]
        image_pid = seed_pending_image(store)
        image_save_pid = store.create('图片保存恢复隔离项目')['id']
        image_send_pid = store.create('图片连续发送隔离项目')['id']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                for (width, height), (name, pid) in zip(VIEWPORTS, names):
                    context = await browser.new_context(viewport={'width': width, 'height': height})

                    async def dispatch(route):
                        request = route.request
                        path = urlsplit(request.url).path
                        if path.startswith('/api/'):
                            headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                            headers['host'] = '127.0.0.1:8765'
                            response = await client.request(request.method, path, content=request.post_data_buffer, headers=headers)
                            await route.fulfill(status=response.status_code,
                                                headers={'content-type': response.headers.get('content-type', 'application/json')},
                                                body=response.content)
                        elif static and (path == '/' or path.startswith('/assets/')):
                            target = (static / (path.lstrip('/') or 'index.html')).resolve()
                            if not target.is_relative_to(static) or not target.is_file():
                                raise AssertionError('static asset unavailable: ' + path)
                            await route.fulfill(status=200, content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream', body=target.read_bytes())
                        else:
                            await route.continue_()

                    await context.route('**/*', dispatch)
                    page = await context.new_page()
                    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                    await page.goto(ORIGIN + '/', wait_until='networkidle')
                    if width <= 700:
                        await page.get_by_label('切换项目').select_option(pid)
                    else:
                        await page.get_by_role('button', name=name).click()
                    await page.locator('.connected-workspace:visible').wait_for()
                    await page.locator('.connected-workspace:visible').get_by_role('button', name='和助手聊需求').click()
                    await page.locator('.connected-workspace:visible .intake-chat').wait_for()
                    await page.wait_for_timeout(150)
                    row = {'size': [width, height], 'project_id': pid, 'checks': []}
                    for round_index in range(3):
                        geometry = await composer_geometry(page)
                        row['checks'].append({'round': round_index + 1, 'before': geometry})
                        if args.output:
                            await page.screenshot(path=str(args.output / f'chat-{width}x{height}-round-{round_index + 1}.png'))
                        if not geometry['textarea_visible'] or not geometry['button_visible']:
                            break
                        textarea = page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]')
                        await textarea.fill(f'第 {round_index + 1} 轮离线访谈：请继续。')
                        fresh = await composer_geometry(page)
                        if not fresh['button_visible']:
                            row['checks'][-1]['after_fill'] = fresh
                            break
                        box = page.locator('.connected-workspace:visible .chat-composer-actions button').filter(has_text='发送')
                        rect = await box.bounding_box()
                        await page.mouse.click(rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2)
                        await wait_assistant(page, 6 + round_index)
                    report['viewports'].append(row)
                    await context.close()
                context = await browser.new_context(viewport={'width': 1280, 'height': 720})
                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                await page.goto(ORIGIN + '/', wait_until='networkidle')
                await page.get_by_role('button', name='图片待识别隔离项目').click()
                await page.locator('.connected-workspace:visible').get_by_role('button', name='和助手聊需求').click()
                await page.get_by_text('还有 1 张本次有效资料图片待识别').wait_for()
                image_message = '请结合这张合成图片继续讨论，暂不确定业务规则。'
                await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').fill(image_message)
                calls_before = provider.calls
                await page.get_by_role('button', name='识别图片并发送').click()
                dialog = page.get_by_role('dialog', name='核对本次任务')
                await dialog.wait_for()
                planned = await dialog.inner_text()
                assert image_message in planned and provider.calls == calls_before, 'image send skipped task review or called provider'
                await dialog.get_by_role('button', name='返回保留输入').click()
                preserved = await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').input_value()
                assert preserved == image_message, 'cancelled image plan discarded the unsent input'
                pixels = io.BytesIO()
                Image.new('RGB', (3, 3), '#2f795a').save(pixels, format='PNG')
                encoded = base64.b64encode(pixels.getvalue()).decode('ascii')
                await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').evaluate('''(element, encoded) => {
                  const raw=atob(encoded), bytes=new Uint8Array(raw.length);
                  for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
                  const file=new File([bytes],'image.png',{type:'image/png'});
                  const data=new DataTransfer();data.items.add(file);
                  element.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
                }''', encoded)
                await page.locator('.connected-chat .chat-pasted-image').wait_for()
                assert await page.locator('.connected-chat .chat-pasted-image img').count() == 1, 'pasted thumbnail absent'
                await page.get_by_role('button', name='识别图片并发送').click()
                dialog = page.get_by_role('dialog', name='核对本次任务')
                await dialog.wait_for()
                after_upload = store.get(image_pid)
                attached = [s for s in after_upload['sources'] if s.get('origin') == 'interview_attachment']
                assert len(attached) == 1 and attached[0]['id'] in await dialog.inner_text(), 'pasted image not bound to reviewed plan'
                await dialog.get_by_role('button', name='返回保留输入').click()
                assert await page.locator('.connected-chat .chat-pasted-image').count() == 1, 'cancel discarded attachment'
                await page.get_by_role('button', name='识别图片并发送').click()
                await page.get_by_role('dialog', name='核对本次任务').wait_for()
                assert len([s for s in store.get(image_pid)['sources'] if s.get('origin') == 'interview_attachment']) == 1, 'retry duplicated uploaded image'
                await page.get_by_role('dialog', name='核对本次任务').get_by_role('button', name='返回保留输入').click()
                await page.get_by_role('button', name='移除本轮附件 ' + attached[0]['title']).click()
                assert await page.locator('.connected-chat .chat-pasted-image').count() == 0, 'remove kept pasted image in composer'
                report['image_plan'] = {'pending_notice': 1, 'review_opened': True,
                                        'no_provider_call_before_confirmation': provider.calls == calls_before,
                                        'cancel_kept_input': preserved == image_message,
                                        'pasted_uploaded_once': len(attached) == 1,
                                        'remove_kept_source_record': any(s['id'] == attached[0]['id'] for s in store.get(image_pid)['sources'])}
                if args.output:
                    await page.screenshot(path=str(args.output / 'image-plan-cancel-keeps-input.png'))
                await context.close()
                context = await browser.new_context(viewport={'width': 1280, 'height': 720})
                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                await page.goto(ORIGIN + '/', wait_until='networkidle')
                await page.get_by_role('button', name='图片保存恢复隔离项目').click()
                await page.locator('.connected-workspace:visible').get_by_role('button', name='和助手聊需求').click()
                await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').evaluate('''(element, encoded) => {
                  const raw=atob(encoded), bytes=new Uint8Array(raw.length);
                  for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
                  const data=new DataTransfer();data.items.add(new File([bytes],'image.png',{type:'image/png'}));
                  element.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
                }''', encoded)
                await page.locator('.connected-workspace:visible .connected-steps button').nth(1).click()
                await page.locator('.connected-workspace:visible .connected-entry-error').get_by_text('聊天框还有未发送的文字或附件。请先发送，或移除本轮输入，再进入完善需求。', exact=True).wait_for()
                assert not store.get(image_save_pid)['sources'], 'entering refinement silently saved an unsent attachment'
                # Entering refinement preserves unsent chat input. Leaving for a
                # different project still exercises the actual save-before-leave dialog.
                await page.get_by_role('button', name='图片待识别隔离项目').click()
                leave_dialog = page.get_by_role('dialog', name='未保存的修改')
                await leave_dialog.wait_for()
                await leave_dialog.get_by_role('button', name='保存后离开').click()
                await leave_dialog.wait_for(state='hidden')
                saved_sources = [s for s in store.get(image_save_pid)['sources'] if s.get('origin') == 'interview_attachment']
                assert len(saved_sources) == 1, 'save-before-leave did not upload pasted image'
                assert provider.calls == 12, 'save-before-leave called model'
                await page.reload(wait_until='networkidle')
                await page.get_by_role('button', name='图片保存恢复隔离项目').click()
                await page.locator('.connected-workspace:visible .connected-steps').get_by_role('button', name='提供信息').click()
                await page.locator('.connected-workspace:visible').get_by_role('button', name='和助手聊需求').click()
                await page.locator('.connected-chat .chat-pasted-image').wait_for()
                assert await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').input_value() == '请结合这些图片继续梳理需求。', 'save/reload lost the image message'
                assert await page.locator('.connected-chat .chat-pasted-image img').get_attribute('src') == '/api/projects/'+image_save_pid+'/sources/'+saved_sources[0]['id']+'/image', 'saved image did not restore from local source'
                await page.get_by_role('button', name='识别图片并发送').click()
                await page.get_by_role('dialog', name='核对本次任务').wait_for()
                assert len([s for s in store.get(image_save_pid)['sources'] if s.get('origin') == 'interview_attachment']) == 1, 'restored image was uploaded again'
                await page.get_by_role('dialog', name='核对本次任务').get_by_role('button', name='返回保留输入').click()
                assert await page.locator('.connected-chat .chat-pasted-image').count() == 1, 'cancel lost restored image'
                report['image_save'] = {'saved_locally_without_model': True, 'restored_after_reload': True,
                                        'no_duplicate_upload': True, 'cancel_kept_restored_image': True}
                if args.output:
                    await page.screenshot(path=str(args.output / 'image-save-reload-cancel.png'))
                await context.close()
                context = await browser.new_context(viewport={'width': 390, 'height': 844})
                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
                await page.goto(ORIGIN + '/', wait_until='networkidle')
                await page.get_by_label('切换项目').select_option(image_send_pid)
                await page.locator('.connected-workspace:visible').get_by_role('button', name='和助手聊需求').click()
                await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').evaluate('''(element, encoded) => {
                  const raw=atob(encoded), bytes=new Uint8Array(raw.length);
                  for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
                  const data=new DataTransfer();data.items.add(new File([bytes],'image.png',{type:'image/png'}));
                  element.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
                }''', encoded)
                assert await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').input_value() == '请结合这些图片继续梳理需求。', 'image-only default was not visible'
                await page.wait_for_timeout(150)
                image_geometry = await composer_geometry(page, '识别图片并发送')
                assert image_geometry['textarea_visible'] and image_geometry['button_visible'], 'mobile image composer was clipped'
                button_rect = await page.get_by_role('button', name='识别图片并发送').bounding_box()
                await page.mouse.click(button_rect['x'] + button_rect['width'] / 2, button_rect['y'] + button_rect['height'] / 2)
                dialog = page.get_by_role('dialog', name='核对本次任务')
                await dialog.wait_for()
                plan_text = await dialog.inner_text()
                assert '分析待处理图片' in plan_text and '聊清需求想法' in plan_text, 'vision/interview stages absent: '+plan_text
                await dialog.get_by_role('button', name='授权本次范围并运行').click()
                try:
                    await wait_assistant(page, 1)
                except Exception:
                    print(json.dumps({'tasks': store.records(image_send_pid, 'user_task'),
                                      'runs': store.records(image_send_pid, 'run'),
                                      'messages': store.get(image_send_pid)['messages'],
                                      'page_text': (await page.locator('.project-workspace:visible').inner_text())[-2400:]}, ensure_ascii=False, default=str))
                    if args.output:
                        await page.screenshot(path=str(args.output / 'image-send-timeout.png'))
                    raise
                await page.wait_for_function('() => !document.querySelector(".project-workspace:not([hidden]) .chat-pasted-image")')
                assert await page.locator('.connected-chat .chat-history-images img').count() == 1, 'submitted attachment thumbnail missing from history'
                assert await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').input_value() == '', 'confirmed attachment submission left composer text'
                stages_after_image = provider.stages[:]
                assert stages_after_image[-2:] == ['vision', 'interview'], stages_after_image
                for turn in range(2):
                    await page.locator('.connected-workspace:visible textarea[aria-label="你的想法"]').fill(f'后续第 {turn + 1} 轮离线文字追问。')
                    await page.get_by_role('button', name='发送', exact=True).click()
                    await wait_assistant(page, 2 + turn)
                assert provider.stages[-2:] == ['interview', 'interview'], provider.stages
                report['image_send'] = {'stages': provider.stages[-4:],
                                        'mobile_composer_visible_before_send': image_geometry['textarea_visible'] and image_geometry['button_visible'],
                                        'history_thumbnail': True,
                                        'composer_cleared_after_confirmed_task': True,
                                        'followup_interviews': 2,
                                        'source_count': len(store.get(image_send_pid)['sources'])}
                if args.output:
                    await page.screenshot(path=str(args.output / 'image-sent-and-continued.png'))
                await context.close()
                report['provider_calls'] = provider.calls
                await browser.close()
    if args.output:
        (args.output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    failures = [row['size'] for row in report['viewports'] if len(row['checks']) != 3 or
                any(not check['before']['textarea_visible'] or not check['before']['button_visible'] or
                    (check.get('after_fill') and not check['after_fill']['button_visible']) for check in row['checks'])]
    if failures or report['provider_calls'] != 16 or report['page_errors'] or not report.get('image_plan', {}).get('cancel_kept_input') or not report.get('image_save', {}).get('restored_after_reload') or not report.get('image_send', {}).get('history_thumbnail'):
        raise AssertionError(f'composer not reachable in {failures}; offline calls={report["provider_calls"]}; page errors={report["page_errors"]}')
    print(json.dumps({'viewports': [row['size'] for row in report['viewports']], 'provider_calls': report['provider_calls'], 'page_errors': report['page_errors']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-build', type=Path)
    parser.add_argument('--output', type=Path)
    asyncio.run(run(parser.parse_args()))
