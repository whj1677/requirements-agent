"""Review existing MRD/PRD through the UI and confirm/export an isolated copy.

No model actions are allowed. --data-dir is copied to a disposable directory
before any write. --fixture uses synthetic engineering data to verify the script.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import traceback
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument('--data-dir', type=Path, help='quiescent isolated project data copy; never modified')
group.add_argument('--fixture', action='store_true', help='synthetic prepared project, no model output')
parser.add_argument('--project-id', help='required with --data-dir')
parser.add_argument('--web-dist', type=Path, required=True)
parser.add_argument('--output', type=Path, default=ROOT / 'evidence/release-20260925/release-journey-browser.json')
parser.add_argument('--artifacts-dir', type=Path, help='new directory for UI-downloaded ZIP and DOCX files')
parser.add_argument('--final-data-dir', type=Path, help='new directory for completed temporary data after service stops')
args = parser.parse_args()


def validate_destinations():
    daily_data = (ROOT / 'data').resolve()
    daily_dist = (ROOT / 'web/dist').resolve()
    source = args.data_dir.resolve() if args.data_dir else None
    destinations = [p.resolve() for p in (args.artifacts_dir, args.final_data_dir) if p]
    for target in destinations:
        assert not target.exists(), '输出目录必须是尚不存在的新目录：' + str(target)
        assert target != daily_data and not target.is_relative_to(daily_data), '输出目录不得位于日常 data：' + str(target)
        assert target != daily_dist and not target.is_relative_to(daily_dist), '输出目录不得位于日常 web/dist：' + str(target)
        assert not source or not target.is_relative_to(source), '输出目录不得写入传入的数据副本：' + str(target)
        assert not args.output.resolve().is_relative_to(target), '结果 JSON 请放在输出目录之外：' + str(target)
    if len(destinations) == 2:
        left, right = destinations
        assert not (left == right or left.is_relative_to(right) or right.is_relative_to(left)), '两个输出目录不能重合或嵌套'


def persist_outputs(folder: Path, result: dict):
    if args.artifacts_dir:
        target = args.artifacts_dir.resolve()
        target.mkdir(parents=True, exist_ok=False)
        files = {'handoff': folder / 'handoff.zip', 'mrd': folder / 'mrd.docx', 'prd': folder / 'prd.docx'}
        baseline_id = next(case['baseline_id'] for case in result['cases'] if case['case'] == 'ui-confirmation-created-baseline')
        saved = {}
        for kind, source in files.items():
            assert source.is_file(), 'UI 下载文件缺失：' + kind
            name = ('handoff-' + baseline_id + '.zip') if kind == 'handoff' else kind + '-current.docx'
            destination = target / name
            data = source.read_bytes()
            with destination.open('xb') as output:
                output.write(data)
            saved[kind] = {'path': str(destination), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
        result['artifacts'] = saved
    if args.final_data_dir:
        target = args.final_data_dir.resolve()
        source = folder / 'data'
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('requirements.sqlite3', 'requirements.sqlite3-wal', 'requirements.sqlite3-shm'))
        database = target / 'requirements.sqlite3'
        with closing(sqlite3.connect(source / 'requirements.sqlite3')) as existing, closing(sqlite3.connect(database)) as backup:
            existing.backup(backup)
            assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', '最终数据 SQLite 备份一致性检查失败'
        result['final_data'] = {'path': str(target), 'sqlite_sha256': hashlib.sha256(database.read_bytes()).hexdigest(),
                                'sqlite_integrity': 'ok'}


def prepare_fixture(store):
    from app.core import brief_hash, now
    from app.contracts import review_target
    from app.product_flow import BEHAVIOR, INTAKE, SCOPE, checkpoint, status
    from tests.helpers import complete_document, prepared

    project = prepared(store)
    with store.edit(project['id'], project['revision'], '合成发布路径预置', bump=False) as (draft, _):
        draft['product_context'] = {key: '合成核对：' + label for key, label in {**INTAKE, **SCOPE}.items()}
        draft['product_context']['scope_ids'] = ['REQ-0001']
        draft['items'][0]['change_type'] = 'new'
        draft['items'][0]['behavior'] = {key: '合成已明确：' + label for key, label in BEHAVIOR.items()}
        draft['questions'][0]['out_of_scope_reason'] = '合成夹具明确移出本期，原问题仍未知。'
    project = store.get(project['id'])
    with store.edit(project['id'], project['revision'], '合成发布门禁与文档预置', bump=False) as (draft, _):
        for step in (1, 2, 3):
            checkpoint(draft, step, status(draft)[step - 1]['content_hash'])
        for kind in ('mrd', 'prd'):
            artifact = draft['documents'][kind]
            artifact['content'] = complete_document(draft, kind)
            artifact['brief_hash'] = brief_hash(draft)
            artifact['draft_revision'] = draft['revision']
            artifact['item_snapshot'] = copy.deepcopy(draft['items'])
            artifact['question_snapshot'] = copy.deepcopy(draft['questions'])
            artifact['source_snapshot'] = [{k: source.get(k) for k in ('id', 'title', 'parse_status', 'failure_reason')}
                                           for source in draft['sources']]
        draft['review']['target_hash'] = review_target(draft)
        draft['review']['created'] = now()
        draft['document_reviews'] = {}
        draft['stage_checks'].pop('5', None)
    return project['id']


async def run(folder: Path, result: dict):
    dist = args.web_dist.resolve()
    assert dist != (ROOT / 'web/dist').resolve(), '请传入隔离构建目录，不使用日常 web/dist'
    assert (dist / 'index.html').is_file(), dist
    data = folder / 'data'
    if args.data_dir:
        source = args.data_dir.resolve()
        assert args.project_id, '--data-dir 需要 --project-id'
        assert source != (ROOT / 'data').resolve(), '拒绝读取日常活动 data；请先提供隔离副本'
        assert source.is_dir(), source
        shutil.copytree(source, data)
        result['data_provenance'] = 'provided_isolated_data_copied_to_temp'
    else:
        data.mkdir()
        result['data_provenance'] = 'synthetic_fixture'

    os.environ['RA_DATA_DIR'] = str(data)
    sys.path.insert(0, str(ROOT))
    import uvicorn
    from playwright.async_api import async_playwright
    from starlette.staticfiles import StaticFiles
    from app.contracts import gate, review_target
    from app.core import brief_hash, ident
    from app.document_reader import export_readiness
    from app.main import create_app
    from app.product_flow import status

    app = create_app(data, access_token='isolated-journey-token', env_path=folder / 'absent.env')
    store = app.state.store
    pid = prepare_fixture(store) if args.fixture else args.project_id
    project = store.get(pid)
    result['project_id'] = pid
    result['input_revision'] = project['revision']
    before_runs = len(store.records(pid, 'run'))
    assert not (project.get('active_baseline_id') and project.get('confirmed_hashes')), '请提供当前尚未确认的项目副本'
    assert all(kind in project['documents'] for kind in ('mrd', 'prd')), '需要已生成 MRD 和 PRD'
    first_three = status(project)[:3]
    assert all(s['complete'] for s in first_three), '第一至第三步应已完成：' + repr(first_three)
    assert all(project['documents'][kind]['brief_hash'] == brief_hash(project)
               and kind not in project.get('stale_document_kinds', []) for kind in ('mrd', 'prd')), '文档不是当前底稿版本'
    assert project.get('review') and project['review']['target_hash'] == review_target(project), '需要已有当前语义审查；脚本不会调用模型'
    review = project['review']['response']
    assert review['result']['assessment'] == 'ready_for_human_review', '已有语义审查未达到人工核对就绪状态'
    assert all(export_readiness(project, kind)['ready'] for kind in ('mrd', 'prd')), '当前文档不能下载 DOCX；请先核对未决问题/版本'
    # The supplied copy may already have human document reviews. Reset only the
    # disposable copy to exercise the negative then the ordinary UI review path.
    with store.edit(pid, project['revision'], '隔离浏览器：回到文档未核对状态', bump=False) as (draft, _):
        draft['document_reviews'] = {}
        draft.get('stage_checks', {}).pop('5', None)
    project = store.get(pid)
    gate_issues = gate(project)
    assert any('文档' in issue for issue in gate_issues), '未审阅负例未成立'
    unrelated = [issue for issue in gate_issues if not (
        issue.startswith('请完成第4步「评审 MRD 与 PRD」')
        or issue.startswith('请评审并核对当前 MRD 文档')
        or issue.startswith('请评审并核对当前 PRD 文档'))]
    assert not unrelated, '除文档人工核对外仍有确认阻塞：' + '；'.join(unrelated)
    historical = copy.deepcopy(project['documents']['prd'])
    historical['id'] = ident('DOC')
    store.record(pid, 'document_artifact', historical, rid=historical['id'])
    result['injections'] = ['cleared_document_reviews_in_disposable_copy',
                            'added_read_only_historical_document_artifact',
                            'revision_bump_before_first_confirmation_submit']

    @app.middleware('http')
    async def reject_model_actions(request, call_next):
        from starlette.responses import JSONResponse
        if request.method == 'POST' and request.url.path.startswith('/api/projects/') and (
                request.url.path.endswith('/actions') or request.url.path.endswith('/actions/plan')
                or request.url.path.endswith('/runs')):
            return JSONResponse({'code': 'MODEL_DISABLED_FOR_CHECK', 'message': '隔离浏览器检查禁止模型任务'}, status_code=403)
        return await call_next(request)

    app.router.routes[:] = [route for route in app.router.routes if getattr(route, 'name', None) != 'web']
    app.mount('/', StaticFiles(directory=dist, html=True), name='isolated-web')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(.05)
        assert server.started, '临时服务未启动'
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 900}, accept_downloads=True)
                page.on('pageerror', lambda error: result['page_errors'].append(str(error)))
                page.on('response', lambda response: result['http_writes'].append({
                    'path': response.url.split('/api/')[-1].split('?')[0], 'status': response.status})
                    if response.request.method == 'POST' and '/api/projects/' in response.url else None)
                await page.goto(f'http://127.0.0.1:{port}/')
                await page.get_by_label('本机访问令牌').fill('isolated-journey-token')
                await page.get_by_role('button', name='进入工作台').click()
                await page.get_by_role('button', name=project['name'], exact=True).first.click()
                work = page.locator('.project-workspace:not([hidden])')
                nav = work.get_by_role('navigation', name='需求工作阶段')
                await nav.get_by_role('button', name='确认与交接', exact=False).click()
                confirm = work.get_by_role('button', name='确认此版本的需求与文档')
                assert await confirm.is_disabled(), '未审阅文档却允许确认'
                assert not store.records(pid, 'confirmation')
                result['cases'].append({'case': 'unreviewed-documents-block-confirmation', 'status': 'VERIFIED'})

                await nav.get_by_role('button', name='评审 MRD 与 PRD', exact=False).click()
                await work.locator('.document-options summary').first.click()
                await work.get_by_label('文档版本').select_option(historical['id'])
                assert await work.get_by_role('button', name='我已核对这份 PRD').count() == 0
                assert await work.get_by_role('button', name='DOCX 下载').count() == 0
                await work.get_by_label('文档版本').select_option('')
                result['cases'].append({'case': 'historical-document-is-read-only', 'status': 'VERIFIED',
                                        'state': 'injected_historical_artifact'})

                for kind in ('MRD', 'PRD'):
                    await work.get_by_role('tab', name=kind + ' 评审稿').click()
                    await work.get_by_role('article', name=kind + ' 正文').wait_for()
                    async with page.expect_response(lambda response, k=kind.lower(): response.request.method == 'POST'
                                                    and response.url.endswith('/documents/' + k + '/review') and response.status == 200):
                        await work.get_by_role('button', name='我已核对这份 ' + kind).click()
                    assert store.get(pid)['document_reviews'][kind.lower()]['document_id'] == store.get(pid)['documents'][kind.lower()]['id']
                    result['cases'].append({'case': kind.lower() + '-current-reader-review', 'status': 'VERIFIED'})
                await work.get_by_role('button', name='完成文档评审并进入交接', exact=False).click()
                await work.get_by_role('heading', name='核对指定版本').wait_for()
                assert not await confirm.is_disabled(), '两份文档核对后确认仍不可用'
                assert store.get(pid)['review']['target_hash'] == review_target(store.get(pid)), '语义审查版本变化'
                result['cases'].append({'case': 'existing-semantic-review-current-and-stage-five-checked', 'status': 'VERIFIED',
                                        'review_origin': 'synthetic_fixture' if args.fixture else 'copied_preexisting_review_origin_unverified'})

                await confirm.click()
                modal = page.get_by_role('dialog', name='确认内容复核')
                current = store.get(pid)
                with store.edit(pid, current['revision'], '隔离浏览器：模拟外部版本变化'):
                    pass
                async with page.expect_response(lambda response: response.request.method == 'POST'
                                                and response.url.endswith('/confirmations') and response.status == 409):
                    await modal.get_by_role('button', name='提交服务端确认').click()
                await modal.get_by_text('版本已变化', exact=False).wait_for()
                assert not store.records(pid, 'confirmation'), '错误版本不应建立基线'
                result['cases'].append({'case': 'stale-confirmation-rejected-without-baseline', 'status': 'VERIFIED',
                                        'state': 'injected_revision_bump'})
                await modal.get_by_role('button', name='返回核对').click()
                await modal.wait_for(state='hidden')
                await confirm.click()
                modal = page.get_by_role('dialog', name='确认内容复核')
                async with page.expect_response(lambda response: response.request.method == 'POST'
                                                and response.url.endswith('/confirmations') and response.status == 200):
                    await modal.get_by_role('button', name='提交服务端确认').click()
                await modal.wait_for(state='hidden')
                baselines = store.records(pid, 'baseline')
                assert len(baselines) == 1
                baseline_id = baselines[0]['id']
                result['cases'].append({'case': 'ui-confirmation-created-baseline', 'status': 'VERIFIED',
                                        'baseline_id': baseline_id})

                async with page.expect_response(lambda response: response.request.method == 'POST'
                                                and response.url.endswith('/exports') and response.status == 200):
                    await work.get_by_role('button', name='生成此基线的研发交接包').click()
                async with page.expect_download() as pending:
                    await work.get_by_role('link', name='下载交接包', exact=False).click()
                download = await pending.value
                handoff_path = folder / 'handoff.zip'
                await download.save_as(str(handoff_path))
                with zipfile.ZipFile(handoff_path) as archive:
                    names = set(archive.namelist())
                    assert {'manifest.json', 'requirements.md', 'PRD.md', 'MRD.md'} <= names
                    manifest = json.loads(archive.read('manifest.json'))
                    assert manifest['baseline_id'] == baseline_id
                result['cases'].append({'case': 'ui-handoff-download', 'status': 'VERIFIED',
                                        'bytes': handoff_path.stat().st_size})

                await nav.get_by_role('button', name='评审 MRD 与 PRD', exact=False).click()
                for kind in ('MRD', 'PRD'):
                    await work.get_by_role('tab', name=kind + ' 评审稿').click()
                    async with page.expect_download() as pending:
                        await work.get_by_role('button', name='DOCX 下载').click()
                    docx = await pending.value
                    docx_path = folder / (kind.lower() + '.docx')
                    await docx.save_as(str(docx_path))
                    with zipfile.ZipFile(docx_path) as archive:
                        assert 'word/document.xml' in archive.namelist()
                    result['cases'].append({'case': kind.lower() + '-docx-download', 'status': 'VERIFIED',
                                            'bytes': docx_path.stat().st_size,
                                            'sha256': hashlib.sha256(docx_path.read_bytes()).hexdigest()})
                assert len(store.records(pid, 'run')) == before_runs, '测试期间意外创建模型运行记录'
                assert not result['page_errors'], result['page_errors']
                result['status'] = 'VERIFIED'
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        result['temporary_service_stopped'] = not thread.is_alive()
        assert result['temporary_service_stopped'], '临时服务未停止'


if __name__ == '__main__':
    payload = {'status': 'FAILED', 'cases': [], 'http_writes': [], 'page_errors': [], 'model_calls': 0,
               'temporary_service_stopped': None, 'time_utc': datetime.now(timezone.utc).isoformat()}
    with tempfile.TemporaryDirectory(prefix='ra-release-journey-') as temporary:
        try:
            validate_destinations()
            asyncio.run(run(Path(temporary), payload))
            persist_outputs(Path(temporary), payload)
        except Exception:
            payload['status'] = 'FAILED'
            payload['error'] = traceback.format_exc()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(0 if payload['status'] == 'VERIFIED' else 1)
