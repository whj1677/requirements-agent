"""Opt-in, isolated end-to-end Actions/Workflow validation with the real provider.

Generate candidates first. A separately reviewed, hash-bound synthetic review
file is required before composing the PRD. No daily project is ever opened by
Store and no web service is started. An exported review draft is not approval.
"""
import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.actions import Actions
from app.bulk_adoption import adopt, review as adoption_review
from app.core import Problem, digest, now, require
from app.document_reader import export_readiness, reader_document
from app.exports import document_files
from app.provider import DEFAULT, Provider, origin
from app.requirement_details import detail_issues
from app.requirements import item_content, require_behavior_review
from app.sources import save_source
from app.store import Store
from app.workflow import Workflow

TITLE = '合成验证：多站点告警批量派单'
# Keep the preceding real-model smoke's input unchanged for comparison. This
# script adds no business defaults merely to make confirmation gates pass.
from scripts.run_detail_contract_validation import CASES
BRIEF = CASES['complex'][1]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load_settings_readonly():
    """Never instantiate Store on the daily database: its constructor mutates runs."""
    with sqlite3.connect((ROOT / 'data/requirements.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT payload FROM settings WHERE id='model'").fetchone()
    settings = {**DEFAULT, **(json.loads(row[0]) if row else {})}
    settings.update(business_draft_protocol='business-draft-v1', max_tokens=24000,
                    timeout=240, action_seconds=600)
    return settings


def prepare_project(store, brief=BRIEF):
    project = store.create(TITLE)
    source = save_source(store, '合成测试业务说明.txt', brief.encode('utf-8'), 'goal')
    require(source['parse_status'] == 'read', 'SOURCE_UNREAD', '合成说明未完整读取')
    with store.edit(project['id'], project['revision'], '建立隔离合成验证输入') as (project, _):
        project.update(workflow_mode='shared-draft', reference_mode='builtin',
                       product_context={'product': '储能云平台'},
                       test_provenance={'type': 'synthetic', 'user_business_acceptance': 'NOT_RUN'})
        project['sources'].append(source)
    return store.get(project['id'])


async def run_action(store, actions, pid, action, message, max_calls, output, target=None):
    project = store.get(pid)
    body = dict(expected_revision=project['revision'], action=action, message=message,
                document_type='prd', max_calls=max_calls)
    if target:
        body['target'] = dict(kind='item', id=target)
    label = action
    sequence = 1
    while (output / (label + '-plan.json')).exists():
        sequence += 1
        label = action + '-' + str(sequence)
    plan = actions.plan(pid, body)
    write_json(output / (label + '-plan.json'), plan)
    require(not plan['missing'], 'ACTION_BLOCKED', '；'.join(plan['missing']))
    task = actions.start(pid, body, plan['plan_hash'], 'synthetic-' + action + '-' + digest(body)[:24], True)
    print(json.dumps(dict(event='action_started', action=action, task_id=task['id']), ensure_ascii=False), flush=True)
    pending = actions.jobs.get(task['id'])
    if pending:
        await pending
    task = store.get_record(pid, task['id'], 'user_task')
    write_json(output / (label + '-task.json'), task)
    write_json(output / (label + '-project.json'), store.get(pid))
    print(json.dumps({k: task[k] for k in ('id', 'status', 'calls', 'message') if k in task}, ensure_ascii=False), flush=True)
    require(task['status'] in ('succeeded', 'partial', 'awaiting_user'), 'VALIDATION_ACTION_FAILED',
            task.get('error', '') + '：' + task.get('message', '任务未成功'))
    return task


def review_template(project):
    return dict(schema='synthetic-draft-review-1', test_actor='SIMULATED_TEST_REVIEW',
                project_id=project['id'], input_revision=project['revision'],
                notice='仅合成验证；请逐条核对模型输出后填写 decision 和 reason。纳入评审稿不等于确认完整或业务批准。',
                items=[dict(id=item['id'], title=item['title'], kind=item['kind'],
                            target_item_id=item.get('target_item_id'),
                            content_hash=digest(item_content(item)), decision='PENDING_REVIEW', reason='')
                       for item in project['items'] if item['kind'] in ('requirement', 'rule', 'acceptance')
                       and item['selection_status'] == 'candidate'])


def validated_review(project, review, *, allow_revisions=False):
    pid = project['id']
    require(review.get('schema') == 'synthetic-draft-review-1'
            and review.get('test_actor') == 'SIMULATED_TEST_REVIEW'
            and review.get('project_id') == pid
            and review.get('input_revision') == project['revision'],
            'TEST_REVIEW_INVALID', '合成评审清单身份或版本不匹配')
    rows = review.get('items', [])
    require(isinstance(rows, list) and rows and len({r['id'] for r in rows}) == len(rows),
            'TEST_REVIEW_INVALID', '请提供不重复的逐条评审清单')
    selected = [r for r in rows if r.get('decision') == 'include_in_review_draft']
    require(selected and all(r.get('decision') in ('include_in_review_draft', 'exclude_from_review_draft')
                            and str(r.get('reason', '')).strip() for r in rows),
            'TEST_REVIEW_REQUIRED', '所有条目均须明确评审决定与理由')
    by_id = {item['id']: item for item in project['items']}
    for row in rows:
        require(row['id'] in by_id and (allow_revisions or not by_id[row['id']].get('target_item_id'))
                and row['content_hash'] == digest(item_content(by_id[row['id']])),
                'STALE_CONTENT', '评审条目已变化或引用不支持：' + row['id'])
    return by_id, selected


def select_reviewed_draft(store, pid, review):
    """Use scope-adoption validation; never sign detailed row confirmation."""
    project = store.get(pid)
    by_id, selected = validated_review(project, review, allow_revisions=True)
    require(all(not by_id[row['id']].get('target_item_id') for row in selected),
            'TEST_REVIEW_INVALID', '已选修订须先通过正常修订采纳接口落实到原条目')
    req_ids = [r['id'] for r in selected if by_id[r['id']]['kind'] == 'requirement']
    require(req_ids, 'TEST_REVIEW_REQUIRED', '评审稿至少需要一个已核对合成需求')
    preview = adoption_review(project, req_ids, project['revision'])
    require(not preview['blockers'], 'TEST_ADOPTION_BLOCKED', json.dumps(preview['blockers'], ensure_ascii=False))
    with store.edit(pid, project['revision'], 'SIMULATED_TEST_REVIEW：仅将核对条目纳入合成评审稿') as (current, db):
        adopt(current, req_ids, {r['id']: r['content_hash'] for r in selected if r['id'] in req_ids}, True)
        current_items = {i['id']: i for i in current['items']}
        for row in selected:
            item = current_items[row['id']]
            if item['kind'] != 'requirement':
                require(item['kind'] in ('rule', 'acceptance'), 'TEST_REVIEW_INVALID', '只可纳入规范条目')
                require_behavior_review(item, current)
                item['selection_status'] = 'selected'
            item['test_review'] = dict(actor='SIMULATED_TEST_REVIEW', decision=row['decision'],
                                       reason=row['reason'], reviewed_content_hash=row['content_hash'],
                                       business_approval=False)
        current.setdefault('product_context', {})['scope_ids'] = req_ids
        store.record(pid, 'synthetic_test_review', copy.deepcopy(review), db=db)
    return store.get(pid)


async def select_reviewed_with_revisions(store, provider, pid, review, output):
    """Apply reviewed model revisions through the existing production endpoints.

    No endpoint changes model wording and no contract confirmation is created.
    Both original and post-identity-remapping hashes remain in local evidence.
    """
    project = store.get(pid)
    by_id, selected = validated_review(project, review, allow_revisions=True)
    revisions = [row for row in selected if by_id[row['id']].get('target_item_id')]
    if not revisions:
        return select_reviewed_draft(store, pid, review)
    targets = [by_id[row['id']].get('target_item_id') or row['id'] for row in selected]
    require(len(targets) == len(set(targets)), 'TEST_REVIEW_INVALID',
            '同一个目标只能选择原条款或一条已审修订，不能同时纳入')
    # main.py builds a global app on first import. Bind that side effect to this
    # already idle isolated Store; never let its default open the daily Store.
    import app.core as core
    daily_default = core.DATA
    try:
        core.DATA = store.folder
        from app.main import create_app
    finally:
        core.DATA = daily_default
    app = create_app(folder=store.folder, provider=provider, access_token='synthetic-test-only')
    import httpx
    api_events = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8765') as client:
        login = await client.post('/api/session/login', json={'key': 'synthetic-test-only'})
        login.raise_for_status()
        client.headers.update({'Origin': 'http://127.0.0.1:8765', 'X-CSRF-Token': login.json()['csrf']})
        for row in sorted(revisions, key=lambda r: by_id[r['id']]['kind'] != 'requirement'):
            current = store.get(pid)
            item = next(i for i in current['items'] if i['id'] == row['id'])
            # A preceding accepted requirement revision may only remap this
            # candidate's related_refs, preserving the model-authored content.
            before = copy.deepcopy(by_id[row['id']])
            current_content = item_content(item)
            before_content = item_content(before)
            current_content.pop('related_refs', None); before_content.pop('related_refs', None)
            require(current_content == before_content, 'STALE_CONTENT', '修订内容在核对后发生变化')
            if item['kind'] == 'requirement':
                body = dict(expected_revision=current['revision'], values=item.get('behavior', {}),
                            change_type=item.get('change_type', 'unspecified'), review_confirmed=True)
                response = await client.post(f'/api/projects/{pid}/items/{item["id"]}/behavior', json=body)
                require(response.status_code == 200, 'TEST_API_REVIEW_FAILED', response.text)
                current = store.get(pid)
            response = await client.post(f'/api/projects/{pid}/items/{item["id"]}',
                json=dict(expected_revision=current['revision'], selection_status='selected'))
            require(response.status_code == 200, 'TEST_API_ADOPTION_FAILED', response.text)
            api_events.append(dict(candidate_id=item['id'], target_item_id=item['target_item_id'],
                                   reviewed_candidate_hash=row['content_hash'], response_revision=response.json()['revision']))
    current = store.get(pid)
    current_items = {i['id']: i for i in current['items']}
    projected = dict(schema=review['schema'], test_actor=review['test_actor'], project_id=pid,
                     input_revision=current['revision'], derived_from_review_hash=digest(review),
                     notice='仅将已审核修订映射回稳定条目身份；模型业务正文没有人工代写。', items=[])
    for row in selected:
        iid = by_id[row['id']].get('target_item_id') or row['id']
        projected['items'].append(dict(id=iid, title=current_items[iid]['title'], kind=current_items[iid]['kind'],
            content_hash=digest(item_content(current_items[iid])), decision=row['decision'], reason=row['reason']))
    write_json(output / 'reviewed-revision-api-evidence.json', dict(original_review=review, calls=api_events,
                                                                 mapped_scope_review=projected))
    return select_reviewed_draft(store, pid, projected)


def evidence_summary(store, pid):
    calls = []
    for path in sorted((store.folder / 'evidence/model-calls').glob('*.json')):
        call = json.loads(path.read_text('utf-8'))
        raw = call.get('final_output')
        calls.append({**{key: call.get(key) for key in ('call_id', 'run_id', 'stage', 'model', 'origin',
                       'repair_of', 'response_received', 'validation_result', 'usage', 'actual_input_hash')},
                      'raw_output_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest() if isinstance(raw, str) else None,
                      'evidence_file': str(path)})
    project = store.get(pid)
    artifact = project.get('documents', {}).get('prd')
    runs = store.records(pid, 'run')
    document_runs = [r for r in runs if r['stage'] == 'prd' and r.get('result_applied')]
    selected = [i for i in project['items'] if i['selection_status'] == 'selected']
    return dict(schema='real-agent-prd-validation-1', created=now(), project_id=pid,
                actual_pipeline='Actions.plan/start -> Workflow.execute -> compile_plan -> document_files(mode=review)',
                source_type='synthetic_test_material', daily_data_mutation=False,
                user_business_acceptance='NOT_RUN', real_model_cost='unknown',
                model_call_count=sum(r.get('calls', 0) for r in runs), model_evidence=calls,
                tasks=[{k: t.get(k) for k in ('id', 'action', 'status', 'run_ids', 'calls', 'error')}
                       for t in store.records(pid, 'user_task')],
                sources=[{k: s.get(k) for k in ('id', 'title', 'sha256', 'version', 'parse_status')}
                         for s in project['sources']],
                detail_issues=[dict(item_id=i['id'], issues=detail_issues(i, project, require_confirmed=True))
                               for i in selected if i['kind'] == 'requirement'],
                row_confirmations=sum(bool(i.get('requirement_confirmation')) for i in project['items']),
                document=dict(id=artifact['id'], content_hash=digest(artifact['content']),
                              item_snapshot_hash=digest(artifact['item_snapshot']),
                              generation_run_ids=[r['id'] for r in document_runs],
                              source_ids=[s['id'] for s in artifact['source_snapshot']]) if artifact else None,
                review_readiness=export_readiness(project, 'prd') if not artifact else export_readiness(project, 'prd', 'review'),
                formal_readiness=export_readiness(project, 'prd'))


async def export_review(store, pid, output):
    project = store.get(pid)
    files = await document_files(project, 'prd', mode='review')
    export_dir = output / 'deliverables'
    export_dir.mkdir(exist_ok=True)
    manifest = {}
    for name, content in files.items():
        path = export_dir / name
        require(path.resolve().is_relative_to(export_dir.resolve()), 'EXPORT_PATH_INVALID', '导出路径越界')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        manifest[name] = dict(sha256=hashlib.sha256(content).hexdigest(), size=len(content))
    write_json(export_dir / 'PRD.reader.json', reader_document(project['documents']['prd']))
    write_json(output / 'deliverables-manifest.json', manifest)
    return manifest


async def run(args):
    output = args.output.resolve()
    require(output.is_relative_to((ROOT / 'work').resolve()), 'OUTPUT_INVALID', '请使用仓库 work 下的隔离目录')
    fresh = args.phase == 'generate'
    require(not output.exists() if fresh else output.is_dir(), 'OUTPUT_INVALID', '生成阶段须用新目录；成文阶段须使用已有验证目录')
    output.mkdir(parents=True, exist_ok=True)
    provider = Provider(ROOT / '.env', ROOT / 'data/.credentials')
    settings = load_settings_readonly()
    require(provider.key_status(settings)['key_configured'], 'KEY_REQUIRED', '未配置真实模型密钥，未发送请求')
    require(origin(settings) == 'https://api.deepseek.com', 'PROVIDER_CHANGED', '当前验证限定已配置的官方 DeepSeek 接收端')
    store = Store(output / 'isolated-store')
    store.setting('model', settings)
    workflow = Workflow(store, provider)
    actions = Actions(store, workflow)
    if fresh:
        brief = args.brief.read_text('utf-8') if args.brief else BRIEF
        (output / 'synthetic-brief.txt').write_text(brief, encoding='utf-8')
        project = prepare_project(store, brief)
        write_json(output / 'input-project.json', project)
        write_json(output / 'validation-state.json', dict(project_id=project['id'], phase='prepared'))
    else:
        project = store.get(json.loads((output / 'validation-state.json').read_text('utf-8'))['project_id'])
    pid = project['id']
    try:
        if fresh or args.phase == 'retry-generate':
            message = ('根据合成说明整理一个独立需求。展开字段属性、交互、完整正常/异常流程和状态变化；'
                       '所有未决仍待确认。不得标记已确认或擅自补数值。')
            if not fresh:
                latest = store.records(pid, 'user_task')
                require(not store.get(pid)['items'] and latest and latest[-1]['status'] == 'failed'
                        and latest[-1]['action'] == 'organize', 'TEST_RETRY_INVALID',
                        '仅可在尚无候选且最近整理失败时重试；保留原失败记录')
                message += ('前次响应因JSON括号未完整闭合而被拒绝。请重新依据原始合成说明生成合法的单一JSON对象。'
                            '结束前逐层检查对象与数组的括号配对，不得省略任何结束括号，禁止Markdown代码围栏；'
                            '业务含义与原始未决不变，不能用删内容或补未知数值换取格式通过。')
            await run_action(store, actions, pid, 'organize',
                message,
                args.max_calls, output)
            write_json(output / 'synthetic-review-template.json', review_template(store.get(pid)))
        elif args.phase == 'clarify':
            require(args.target_id and args.message_file, 'TEST_CLARIFY_REQUIRED',
                    '局部修订须提供真实目标ID和已评审的反馈文件')
            await run_action(store, actions, pid, 'clarify', args.message_file.read_text('utf-8'),
                             args.max_calls, output, args.target_id)
            write_json(output / 'synthetic-review-template-after-clarify.json', review_template(store.get(pid)))
        else:
            require(args.review_file is not None, 'TEST_REVIEW_REQUIRED', '成文前必须提供已逐条核对的合成评审清单')
            reviewed = json.loads(args.review_file.read_text('utf-8'))
            await select_reviewed_with_revisions(store, provider, pid, reviewed, output)
            write_json(output / 'synthetic-review-applied.json', reviewed)
            await run_action(store, actions, pid, 'document',
                '请生成多站点告警批量派单的PRD评审稿。正文依据已纳入合成评审范围的模型条目；保留所有未决，不能冒充业务确认。字段、交互、流程和状态从同一详细定义生成图文表，避免重复解释。',
                args.max_calls, output)
            await export_review(store, pid, output)
    finally:
        write_json(output / 'validation-summary.json', evidence_summary(store, pid))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-model', action='store_true', required=True,
                        help='Explicitly authorize using configured provider for synthetic validation')
    parser.add_argument('--phase', choices=('generate', 'retry-generate', 'clarify', 'document'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-calls', type=int, default=3)
    parser.add_argument('--brief', type=Path)
    parser.add_argument('--review-file', type=Path)
    parser.add_argument('--target-id')
    parser.add_argument('--message-file', type=Path)
    asyncio.run(run(parser.parse_args()))
