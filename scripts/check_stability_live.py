"""Fixed-fixture UI acceptance with the real configured DeepSeek provider.

Explicit --execute-real-model is mandatory. Writes only to a new isolated
session under evidence; never to the daily data directory. Each phase closes
its temporary listener. Business approvals are reviewed between phases.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import threading
import traceback
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


async def run(args):
    session = args.session.resolve()
    assert session.is_relative_to(REPO / 'evidence'), 'Session must be in ignored evidence'
    assert args.execute_real_model, 'Real model execution requires explicit flag'
    session.mkdir(parents=True, exist_ok=True)
    os.environ['RA_DATA_DIR'] = str(session / 'module-data')
    import app.config
    app.config.ROOT = session / 'module-data'
    import uvicorn
    from app.main import create_app
    from app.provider import Provider, DEFAULT, origin
    from starlette.staticfiles import StaticFiles
    from playwright.async_api import async_playwright, expect

    provider = Provider(REPO / '.env')
    app = create_app(session / 'data', access_token='off', provider=provider)
    # Read settings, never credentials, from the sole daily workbench.
    with urllib.request.urlopen('http://127.0.0.1:8765/api/models', timeout=5) as response:
        configured = json.load(response)
    for slot in ('model', 'vision'):
        config = {key: configured[slot].get(key, value) for key, value in DEFAULT.items()}
        assert origin(config) == 'https://api.deepseek.com', 'Unexpected provider; do not send materials'
        assert provider.key(config), 'Configured project key unavailable; no request sent'
        app.state.store.setting(slot, config)
    dist = args.dist.resolve()
    assert dist != REPO / 'web/dist' and (dist / 'index.html').is_file()
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'name', None) != 'web']
    app.mount('/', StaticFiles(directory=dist, html=True), name='isolated-web')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    base = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    result = {'phase': args.phase, 'status': 'RUNNING', 'mode': 'real provider, synthetic fixed business case', 'page_errors': [], 'writes': []}
    output = session / args.phase
    attempt = 1
    while output.exists():
        attempt += 1
        output = session / f'{args.phase}-{attempt}'
    output.mkdir()
    pid = None
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(.05)
        assert server.started
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': 1440, 'height': 1050})
            page.set_default_timeout(15000)
            page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
            page.on('response', lambda response: result['writes'].append({'path': response.url.split('/api/')[-1], 'status': response.status}) if response.request.method != 'GET' and '/api/projects' in response.url else None)
            try:
                await page.goto(base)
                await page.get_by_label('本机访问令牌').fill('off')
                await page.get_by_role('button', name='进入工作台', exact=True).click()
                if args.phase == 'ingest':
                    assert not (session / 'project-id.txt').exists(), 'Resume the existing case; do not recreate for a green result'
                    name = '稳定性验收 · 联系人备注（合成）'
                    await page.get_by_role('button', name='＋ 新建需求项目', exact=True).click()
                    await page.get_by_label('新项目名称', exact=True).fill(name)
                    await page.get_by_role('button', name='创建项目', exact=True).click()
                    await expect(page.get_by_role('heading', name=name, exact=True)).to_be_visible()
                    projects = await (await page.request.get(base + '/api/projects')).json()
                    pid = next(p['id'] for p in projects if p['name'] == name)
                    (session / 'project-id.txt').write_text(pid, encoding='utf8')
                else:
                    pid = (session / 'project-id.txt').read_text(encoding='utf8')
                    p = app.state.store.get(pid)
                    await page.get_by_role('button', name=p['name'], exact=True).click()
                work = page.locator('.project-workspace:not([hidden])')
                root = base + '/api/projects/' + pid

                async def snapshot(label):
                    (output / (label + '-project.json')).write_text(json.dumps(await (await page.request.get(root)).json(), ensure_ascii=False, indent=2), encoding='utf8')
                    await page.screenshot(path=str(output / (label + '.png')), full_page=True)
                    (output / (label + '-ui.txt')).write_text(await work.inner_text(), encoding='utf8')

                async def action(button):
                    await button.click()
                    dialog = page.get_by_role('dialog', name='核对本次任务')
                    await expect(dialog).to_be_visible()
                    await page.screenshot(path=str(output / 'authorization.png'), full_page=True)
                    submit = dialog.get_by_role('button', name='授权本次范围并运行', exact=True)
                    if not await submit.count():
                        submit = dialog.get_by_role('button', name='运行本次任务', exact=True)
                    async with page.expect_response(lambda r: r.request.method == 'POST' and r.url == root + '/actions') as receipt:
                        await submit.click()
                    response = await receipt.value
                    task = await response.json()
                    assert response.status == 200, task
                    result.setdefault('task_ids', []).append(task['id'])
                    prior = None
                    while True:
                        tasks = await (await page.request.get(root + '/actions')).json()
                        task = next(t for t in tasks if t['id'] == task['id'])
                        marker = (task['status'], task['completed_steps'], task['calls'], task['message'])
                        if marker != prior:
                            print(json.dumps({'task': task['id'], 'status': task['status'], 'steps': task['completed_steps'], 'calls': task['calls'], 'message': task['message']}, ensure_ascii=False), flush=True)
                            prior = marker
                        if task['status'] not in ('queued', 'running'):
                            break
                        await asyncio.sleep(1)
                    (output / (task['id'] + '.json')).write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding='utf8')
                    await asyncio.sleep(1)
                    await snapshot('after-task')
                    assert task['status'] in ('succeeded', 'partial'), task
                    return task

                if args.phase == 'ingest':
                    materials = args.materials.resolve()
                    fixture = json.loads((materials / 'intake.json').read_text(encoding='utf8'))
                    request = fixture['initial_request']
                    intake = {
                        'platform': fixture['label'] + '。现有联系人列表字段：' + '、'.join(fixture['current_state']['contact_list_fields']) + '。管理员可新增、编辑联系人，只读用户仅查看。',
                        'text': request['summary'] + '\n' + '\n'.join(request['rules'] + request['unresolved']),
                        'preserved': '现有查找、排序、分页和删除行为保持原状，其它功能不变。',
                    }
                    (output / 'submitted-intake.json').write_text(json.dumps(intake, ensure_ascii=False, indent=2), encoding='utf8')
                    fields = {'platform': work.get_by_placeholder('例如：目前使用的平台、希望改动的页面…'), 'text': work.get_by_label('核心诉求', exact=True), 'preserved': work.get_by_placeholder('哪些内容保持原状，哪些暂时不做…')}
                    for key, field in fields.items():
                        await field.fill(intake[key])
                    await work.get_by_role('button', name='添加资料', exact=True).click()
                    drawer = page.get_by_role('dialog', name='项目资料', exact=True)
                    for filename, purpose in [('current-state.docx', 'current'), ('prototype-reference.png', 'reference')]:
                        await drawer.get_by_label('材料用途').select_option(purpose)
                        async with page.expect_response(lambda r: r.request.method == 'POST' and r.url == root + '/sources/file', timeout=120000) as imported:
                            await drawer.locator('input[type=file]').set_input_files(str(materials / filename))
                        assert (await imported.value).status == 200
                        await expect(drawer.get_by_role('button', name='关闭项目资料', exact=True)).to_be_enabled()
                    await drawer.get_by_role('button', name='关闭项目资料', exact=True).click()
                    for key, field in fields.items():
                        await expect(field).to_have_value(intake[key])
                    await snapshot('before-analysis')
                    await action(work.get_by_role('button', name='✦ 分析现状与诉求 →', exact=True))
                    for key, field in fields.items():
                        await expect(field).to_have_value(intake[key])
                elif args.phase in ('scope', 'context'):
                    decisions = json.loads((session / 'operator-decisions.json').read_text(encoding='utf8'))
                    await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='核对现状、价值与改动范围', exact=False).click()
                    checkpoint = work.locator('.step-check-1')
                    await checkpoint.get_by_role('button', name='去补充', exact=True).click()
                    await checkpoint.get_by_label('优先级与依据', exact=False).fill(decisions['priority'])
                    await checkpoint.get_by_role('button', name='保存补充信息', exact=True).click()
                    for rid in decisions['scope_ids'] if args.phase == 'scope' else []:
                        item = next(i for i in app.state.store.get(pid)['items'] if i['id'] == rid)
                        await checkpoint.get_by_label('本期范围 ' + item['title'], exact=True).check()
                        await checkpoint.locator('.scope-requirement').filter(has_text=rid).locator('summary').click()
                        await checkpoint.get_by_label('采纳状态 ' + item['title'], exact=True).select_option('selected')
                    if args.phase == 'scope':
                        await checkpoint.get_by_role('button', name='保存范围并重新核对', exact=False).click()
                    await checkpoint.get_by_role('button', name='确认范围并进入下一步', exact=False).click()
                    await expect(work.get_by_role('heading', name='澄清流程与规则', exact=True)).to_be_visible()
                    await snapshot('scope-reviewed')
                elif args.phase in ('adopt', 'adopt-change'):
                    decisions = json.loads((session / 'operator-decisions.json').read_text(encoding='utf8'))
                    await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                    details = work.locator('.secondary-tools').filter(has_text='核对条目与业务行为')
                    await details.locator('summary').first.click()
                    candidates = details.locator('article').filter(has=page.get_by_role('heading', name='待采纳候选', exact=True))
                    for iid in decisions[args.phase]:
                        row = candidates.locator('.summary-item').filter(has=page.locator('strong').filter(has_text=iid))
                        async with page.expect_response(lambda r: r.request.method == 'POST' and r.url == root + '/items/' + iid) as updated:
                            await row.get_by_role('button', name='采纳', exact=True).click()
                        assert (await updated.value).status == 200
                        await expect(row).to_have_count(0)
                    if args.phase == 'adopt-change':
                        await details.get_by_text('查看全部底稿条目与状态', exact=True).click()
                        for edit in decisions.get('manual_clause_edits', []):
                            rows = details.locator('details').filter(has=page.locator('summary').filter(has_text='查看全部底稿条目与状态'))
                            row = rows.locator('.summary-item').filter(has=page.locator('strong').filter(has_text=edit['id']))
                            await row.get_by_role('button', name='编辑原文', exact=True).click()
                            dialog = page.get_by_role('dialog', name='编辑需求原文')
                            await expect(dialog.get_by_label('需求原文', exact=True)).to_have_value(edit['before'])
                            await dialog.get_by_label('需求原文', exact=True).fill(edit['after'])
                            await dialog.get_by_role('button', name='保存原文', exact=True).click()
                            await expect(dialog).to_have_count(0)
                    await snapshot('candidates-reviewed')
                elif args.phase in ('clarify', 'change', 'retry-clarify'):
                    decisions = json.loads((session / 'operator-decisions.json').read_text(encoding='utf8'))
                    await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                    p = app.state.store.get(pid)
                    if args.phase == 'change':
                        await work.get_by_text('已回答 ·', exact=False).click()
                    answers = {} if args.phase == 'retry-clarify' else decisions['answers' if args.phase == 'clarify' else 'change_answers']
                    for qid, answer in answers.items():
                        question = next(q for q in p['questions'] if q['id'] == qid)
                        await work.get_by_label(question['question'], exact=True).fill(answer)
                    if answers:
                        await work.get_by_role('button', name='保存本次回答', exact=True).click()
                        await expect(work.get_by_role('button', name='保存本次回答', exact=True)).to_be_disabled()
                    details = work.locator('.secondary-tools').filter(has_text='核对条目与业务行为')
                    await details.locator('summary').first.click()
                    await snapshot('answers-saved')
                    await action(work.get_by_role('button', name='核对后重试' if args.phase == 'retry-clarify' else '根据已保存回答更新理解', exact=True))
                elif args.phase == 'remediate':
                    decisions = json.loads((session / 'operator-decisions.json').read_text(encoding='utf8'))
                    await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                    details = work.locator('.secondary-tools').filter(has_text='核对条目与业务行为')
                    await details.locator('summary').first.click()
                    candidates = details.locator('article').filter(has=page.get_by_role('heading', name='待采纳候选', exact=True))
                    for iid in decisions.get('defer_after_review', []):
                        if next(i for i in app.state.store.get(pid)['items'] if i['id'] == iid)['selection_status'] == 'deferred':
                            continue
                        row = candidates.locator('.summary-item').filter(has=page.locator('strong').filter(has_text=iid))
                        await row.get_by_role('button', name='暂缓', exact=True).click()
                        await expect(row).to_have_count(0)
                    for edit in decisions.get('review_clause_edits', []):
                        if next(i for i in app.state.store.get(pid)['items'] if i['id'] == edit['id'])['statement'] == edit['after']:
                            continue
                        row = details.locator('.summary-item').filter(has=page.locator('strong').filter(has_text=edit['id'])).filter(visible=True)
                        await row.get_by_role('button', name='编辑原文', exact=True).click()
                        dialog = page.get_by_role('dialog', name='编辑需求原文')
                        await expect(dialog.get_by_label('需求原文', exact=True)).to_have_value(edit['before'])
                        await dialog.get_by_label('需求原文', exact=True).fill(edit['after'])
                        await dialog.get_by_role('button', name='保存原文', exact=True).click()
                        await expect(dialog).to_have_count(0)
                    for clause in decisions['review_acceptance']:
                        await work.get_by_role('button', name='＋ 补充关联验收条件', exact=True).click()
                        dialog = page.get_by_role('dialog', name='补充关联验收条件')
                        await dialog.get_by_role('combobox').select_option(decisions['scope_ids'][0])
                        await dialog.get_by_label('验收名称', exact=True).fill(clause['title'])
                        await dialog.get_by_label('验收原文', exact=True).fill(clause['statement'])
                        await dialog.get_by_role('button', name='保存验收候选', exact=True).click()
                        await expect(dialog).to_have_count(0)
                        row = candidates.locator('.summary-item').filter(has=page.locator('strong').filter(has_text=clause['title']))
                        await row.get_by_role('button', name='采纳', exact=True).click()
                        await expect(row).to_have_count(0)
                    await snapshot('review-remediated')
                elif args.phase in ('document', 'update-document'):
                    await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='澄清流程与规则', exact=False).click()
                    await work.get_by_role('button', name='核对规则并继续', exact=False).click()
                    await expect(work.get_by_role('heading', name='评审 MRD 与 PRD', exact=True)).to_be_visible()
                    for kind in ('MRD', 'PRD'):
                        await work.get_by_role('tab', name=kind + ' 评审稿', exact=True).click()
                        await action(work.get_by_role('button', name=('更新讨论稿 ' if args.phase == 'update-document' else '生成讨论稿 ') + kind, exact=True))
                        await snapshot(kind.lower() + '-generated')
                elif args.phase in ('review', 'retry-review'):
                    # Root must inspect both actual document artifacts before this phase.
                    decisions = json.loads((session / 'operator-decisions.json').read_text(encoding='utf8'))
                    assert decisions.get('documents_reviewed'), 'Independent document review required'
                    assert decisions['documents_reviewed']['document_ids'] == {k:d['id'] for k,d in app.state.store.get(pid)['documents'].items()}, 'Reviewed document versions changed'
                    if args.phase == 'review':
                        await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='评审 MRD 与 PRD', exact=False).click()
                        for kind in ('MRD', 'PRD'):
                            await work.get_by_role('tab', name=kind + ' 评审稿', exact=True).click()
                            await work.get_by_role('button', name='我已核对这份 ' + kind, exact=True).click()
                            await expect(work.get_by_role('button', name='我已核对这份 ' + kind, exact=True)).to_be_disabled()
                        await work.get_by_role('button', name='完成文档评审并进入交接', exact=False).click()
                    else:
                        await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='确认与交接', exact=False).click()
                    await action(work.get_by_role('button', name='核对后重试' if args.phase == 'retry-review' else '审查当前版本', exact=True))
                elif args.phase == 'export':
                    decisions = json.loads((session / 'operator-decisions.json').read_text(encoding='utf8'))
                    assert decisions.get('documents_reviewed'), 'Independent document review required'
                    assert decisions['documents_reviewed']['document_ids'] == {k:d['id'] for k,d in app.state.store.get(pid)['documents'].items()}, 'Reviewed document versions changed'
                    await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='评审 MRD 与 PRD', exact=False).click()
                    for kind in ('MRD', 'PRD'):
                        await work.get_by_role('tab', name=kind + ' 评审稿', exact=True).click()
                        for fmt in ('DOCX', 'MD', 'ZIP'):
                            async with page.expect_download(timeout=120000) as download:
                                await work.get_by_role('button', name=fmt + ' 下载', exact=True).click()
                            artifact = await download.value
                            assert await artifact.failure() is None
                            await artifact.save_as(output / (kind.lower() + '.' + fmt.lower()))
                        await snapshot(kind.lower() + '-downloaded')
                    await work.get_by_role('navigation', name='需求工作阶段').get_by_role('button', name='确认与交接', exact=False).click()
                    await work.get_by_role('button', name='确认此版本的需求与文档', exact=True).click()
                    dialog = page.get_by_role('dialog', name='确认内容复核')
                    async with page.expect_response(lambda r: r.request.method == 'POST' and r.url == root + '/confirmations') as confirmation:
                        await dialog.get_by_role('button', name='提交服务端确认', exact=True).click()
                    assert (await confirmation.value).status == 200
                    await expect(dialog).to_have_count(0)
                    await work.get_by_role('button', name='生成此基线的研发交接包', exact=True).click()
                    async with page.expect_download(timeout=120000) as download:
                        await work.get_by_role('button', name='下载交接包 ·', exact=False).click()
                    artifact = await download.value
                    assert await artifact.failure() is None
                    await artifact.save_as(output / 'handoff.zip')
                    await snapshot('baseline-exported')
                else:
                    raise AssertionError('Phase implementation pending independent result review')
                assert not result['page_errors'], result['page_errors']
                result['status'] = 'PHASE_EXECUTED_REQUIRES_SEMANTIC_REVIEW'
            finally:
                await page.screenshot(path=str(output / 'final-ui.png'), full_page=True)
                await browser.close()
    except Exception:
        result['status'] = 'FAILED'
        result['error'] = traceback.format_exc()
        raise
    finally:
        if pid:
            result['project_id'] = pid
            (output / 'project.json').write_text(json.dumps(app.state.store.get(pid), ensure_ascii=False, indent=2), encoding='utf8')
            (output / 'runs.json').write_text(json.dumps(app.state.store.records(pid, 'run'), ensure_ascii=False, indent=2), encoding='utf8')
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
        result['listener_closed'] = not thread.is_alive()
        sock.close()
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps({'phase': args.phase, 'status': result['status'], 'listener_closed': result['listener_closed']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-real-model', action='store_true')
    parser.add_argument('--phase', choices=['ingest', 'scope', 'context', 'clarify', 'adopt', 'change', 'retry-clarify', 'adopt-change', 'document', 'update-document', 'remediate', 'review', 'retry-review', 'export'], required=True)
    parser.add_argument('--session', type=Path, required=True)
    parser.add_argument('--materials', type=Path, required=True)
    parser.add_argument('--dist', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
