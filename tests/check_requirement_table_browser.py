"""Table confirmation usability: isolated real browser/API, never a model claim.

The first row and its acceptance start as unadopted synthetic AI recommendations.
Only actor/flow/result have source-backed text. Four unused behavior dimensions
are genuinely empty. A separate, explicitly disclosed second row is already
confirmed before the browser starts, to detect accidental cross-row invalidation.
"""
import argparse
import asyncio
import copy
import json
import mimetypes
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMPORT_SANDBOX = tempfile.TemporaryDirectory(prefix='ra-table-import-')
os.environ['RA_DATA_DIR'] = str((Path(IMPORT_SANDBOX.name) / 'data').resolve())

from app.core import now
from app.main import create_app
from app.product_flow import checkpoint, status
from app.requirement_confirmation import confirmation_current
from tests.check_refine_workspace_browser import NoModelProvider, require, wait_store
from tests.check_refine_human_flow_browser import ANNOTATION, Recorder

ORIGIN = 'http://127.0.0.1:8765'
NAME = '合成储能云：需求表格确认'
TITLE = '场站告警处理记录'
RID = 'REQ-ALARM'
ACID = 'AC-ALARM'
OTHER_RID = 'REQ-HISTORY'
OTHER_TITLE = '历史处理记录查询'
BEFORE = '场站值班人员在告警详情补充处理记录，保存后显示记录内容。'
AFTER = BEFORE + '两名值班人员都可以查看处理记录，只能修改自己填写的处理记录。'
AC_BEFORE = '值班人员填写处理记录并保存，告警详情应显示保存的处理内容。'
AC_AFTER = AC_BEFORE + '用户甲可修改自己填写的记录；用户乙可查看该记录，但不能修改。'
QUESTION = '两名值班人员是否可以修改对方填写的告警处理记录？'
RECOMMENDED_ANSWER = '不能相互修改，但都能查看。'
OPTIONAL = '以后是否需要导出告警处理日报？'
FIELDS = ('actor', 'entry', 'flow', 'data', 'permissions', 'result', 'exceptions')


def item(project, identity):
    return next(row for row in project['items'] if row['id'] == identity)


async def seed(store, client, *, unknown=False, versions=False):
    session = (await client.get('/api/session')).json()
    client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': session['csrf']})
    project = store.create(NAME + ('（关键决定待回答）' if unknown else ''))
    pid = project['id']
    with store.edit(pid, project['revision'], '冻结合成表格验收前置材料') as (p, _):
        p['workflow_mode'] = 'shared-draft'
        p['workspace_draft'] = dict(text='新增独立的场站告警记录模块。', entry_mode='direct', saved_at=now())
        p['intake'] = dict(mode='direct', text='新增独立的场站告警记录模块。', saved_at=now())
        p['scope_profile'] = dict(scenario='new_module', dependencies='independent')
        p['product_context'] = dict(product='大型储能云平台', module='场站告警记录',
            intent='云端记录和查询场站告警处理过程。', users='场站值班人员。',
            change_scope='新增独立的告警处理记录和历史查询。', scope_ids=[RID, OTHER_RID])
        p['context_field_states'] = {key: dict(status='known') for key in ('intent', 'users', 'change_scope')}
        source_text = BEFORE + AC_BEFORE + '值班人员按场站查询历史处理记录，查询结果显示对应场站的已保存记录。'
        p['sources'] = [dict(id='SRC-TABLE', title='冻结合成材料：告警处理记录', purpose='goal', uri=None,
            sha256='synthetic-table-source-v1', created=now(), version=1, excluded=False,
            parse_status='read', failure_reason='', image_mime=None,
            excerpts=[dict(id='EX-TABLE', source_id='SRC-TABLE', source_hash='synthetic-table-source-v1',
                locator='合成材料第 1 段', text=source_text, method='synthetic-fixture')])]

        def row(iid, kind, title, statement, related):
            return dict(id=iid, kind=kind, title=title, statement=statement, applies_to='to_be',
                epistemic_status='reported', source_refs=[dict(source_id='SRC-TABLE', excerpt_id='EX-TABLE')],
                related_refs=related, selection_status='candidate', revision=1, content_version=1,
                change_type='new', behavior_review_required=kind == 'requirement')

        first = row(RID, 'requirement', TITLE, BEFORE, [ACID])
        first['behavior'] = dict(actor='场站值班人员。', flow='填写告警处理记录并保存。', result='显示已保存的处理记录。')
        second = row(OTHER_RID, 'requirement', OTHER_TITLE, '值班人员按场站查询已保存的历史处理记录。', ['AC-HISTORY'])
        second['behavior'] = dict(actor='场站值班人员。', flow='选择场站并查询历史处理记录。', result='显示对应场站的已保存记录。')
        p['items'] = [first, row(ACID, 'acceptance', '告警处理记录验收建议', AC_BEFORE, [RID]),
            second, row('AC-HISTORY', 'acceptance', '历史记录查询验收',
                '值班人员选择场站并查询，结果应显示该场站已保存的处理记录。', [OTHER_RID])]
        if versions:
            for number, statement in enumerate((BEFORE + '可按处理人筛选记录。', BEFORE + '可按处理时间查询记录。'), 1):
                candidate = copy.deepcopy(first)
                candidate.update(id='REQ-ALARM-V' + str(number), target_item_id=RID,
                    statement=statement, epistemic_status='inferred', answer_refs=[])
                p['items'].append(candidate)
        p['questions'] = [dict(id='Q-OPTIONAL', topic_key='daily-report', question=OPTIONAL,
            why='后续建议，非本期必要决定。', options=[], blocking=False, related_refs=[RID], source_refs=[],
            status='open', answer=None, decision_coverage='pending')]
        if unknown:
            p['questions'].insert(0, dict(id='Q-PERMISSION', topic_key='record-permission', question=QUESTION,
                why='跨人员修改权限必须由产品经理决定。',
                options=[RECOMMENDED_ANSWER, '可以相互修改，并记录修改人。', '暂不确定。'], blocking=True, blocking_stage=3,
                related_refs=[RID], source_refs=[], status='open', answer=None, decision_coverage='pending'))
        p['stage_checks'] = {}
        checkpoint(p, 1, status(p)[0]['content_hash'])

    # Explicitly disclosed prior reviewed row. No hidden preparation of test row.
    current = store.get(pid)
    other = item(current, OTHER_RID)
    preview = await client.post(f'/api/projects/{pid}/requirement-confirmation/preview', json=dict(
        expected_revision=current['revision'], requirement_id=OTHER_RID, statement=other['statement'],
        behavior={key: other.get('behavior', {}).get(key, '') for key in FIELDS},
        acceptance_items=[dict(id='AC-HISTORY', statement=item(current, 'AC-HISTORY')['statement'])]))
    require(preview.status_code == 200, 'Fixture prior-row preview failed: ' + preview.text)
    token = preview.json()
    commit = await client.post(f'/api/projects/{pid}/requirement-confirmation/commit', json=dict(
        expected_revision=current['revision'], preview_id=token['preview_id'], preview_hash=token['preview_hash'],
        idempotency_key='fixture-prior-row-confirmed', review_confirmed=True))
    require(commit.status_code == 200, 'Fixture prior-row confirmation failed: ' + commit.text)
    frozen = copy.deepcopy(store.get(pid))
    require(item(frozen, RID)['selection_status'] == 'candidate', 'Test row was already adopted')
    require(item(frozen, ACID)['selection_status'] == 'candidate', 'Test acceptance was already adopted')
    require(not confirmation_current(frozen, item(frozen, RID)), 'Test row was already confirmed')
    require(confirmation_current(frozen, item(frozen, OTHER_RID)), 'Control row lacks real confirmation')
    require(set(item(frozen, RID)['behavior']) == {'actor', 'flow', 'result'}, 'Fixture filled unused dimensions')
    return pid, frozen


async def open_table(page, rec, pid, *, unknown=False):
    await rec.click(page.get_by_role('button', name=NAME + ('（关键决定待回答）' if unknown else '')).first)
    work = page.locator('.connected-workspace:visible')
    await rec.click(work.locator('.connected-steps').get_by_role('button', name='完善需求'))
    table = work.get_by_role('table', name='需求确认表', exact=True)
    await expect(table).to_be_visible()
    return work, table


async def open_row(work, table, rec):
    row = table.locator('tr[data-requirement-id="' + RID + '"]')
    await expect(row).to_have_count(1)
    await rec.click(row.get_by_role('button', name='查看与确认', exact=True))
    editor = work.get_by_role('region', name=TITLE + ' 需求确认', exact=True)
    await expect(editor).to_be_visible()
    return row, editor


async def journey(page, rec, store, pid, frozen, trace, client):
    await rec.note('隔离合成储能云项目。第一条及验收是未采纳的模拟 AI 建议；第二条已在测试前确认。仅验证交互，不冒充真实模型结果。', 6)
    work, table = await open_table(page, rec, pid)
    first = table.locator('tr[data-requirement-id="' + RID + '"]')
    other = table.locator('tr[data-requirement-id="' + OTHER_RID + '"]')
    await expect(first).to_contain_text('待确认')
    await expect(other).to_contain_text('已确认')
    await expect(table).to_contain_text(AC_BEFORE)
    require(store.get(pid) == frozen, 'Viewing the table wrote project content')
    require(await table.locator('textarea:visible').count() == 0, 'Overview starts with editors open')
    trace['checks'].append('candidate_requirement_and_acceptance_visible_before_confirmation_with_other_row_already_confirmed')
    await page.screenshot(path=str(rec.output / '01-table-overview.png'))
    await rec.note('表格同时显示需求、验收建议和确认状态。先查看第一条；不必逐个补满七项表格字段。', 5)
    first, editor = await open_row(work, table, rec)
    statement = editor.get_by_role('textbox', name=TITLE + ' 需求内容', exact=True)
    acceptance = editor.get_by_role('textbox', name=TITLE + ' 验收建议 1', exact=True)
    await expect(statement).to_have_value(BEFORE)
    await expect(acceptance).to_have_value(AC_BEFORE)
    await rec.fill(statement, AFTER)
    await rec.fill(acceptance, AC_AFTER)
    await rec.note('直接改需求和验收：双方可查看，只有填写者可修改。人工写下的业务决定仍需要明确确认才生效。', 6)
    await rec.click(editor.get_by_role('button', name='查看确认内容', exact=True))
    preview = editor.get_by_role('region', name='本条确认预览', exact=True)
    await expect(preview).to_be_visible()
    await expect(preview.get_by_role('heading', name='本次确认内容', exact=True)).to_be_in_viewport(ratio=1)
    await expect(preview).to_contain_text(AFTER)
    await expect(preview).to_contain_text(AC_AFTER)
    await expect(preview).to_contain_text('可选')
    require(store.get(pid) == frozen, 'Preview modified business content before confirmation')
    trace['checks'].append('direct_statement_and_acceptance_edits_preview_together_without_business_writes')
    await page.screenshot(path=str(rec.output / '02-whole-row-preview.png'))
    await rec.note('同屏核对本条完整内容和验收建议。确认前，后台仍保留原候选；点击一次“确认这条需求”才一起生效。', 7)
    if rec.enabled:
        await preview.get_by_role('heading', name='同时确认的验收', exact=True).scroll_into_view_if_needed()
        await rec.note('继续核对验收内容：谁能修改、谁只能查看。可选日报问题仍待定，不会被本次确认变成已知规则。', 6)
        await page.screenshot(path=str(rec.output / '02b-acceptance-preview.png'))
    await rec.click(preview.get_by_role('button', name='确认这条需求', exact=True))
    saved = await wait_store(store, pid, lambda p: confirmation_current(p, item(p, RID)), 'Whole row did not confirm')
    await expect(first).to_contain_text('已确认')
    await expect(editor).not_to_be_visible()
    await expect(first).to_be_in_viewport(ratio=.6)
    await expect(first.get_by_role('rowheader')).to_be_in_viewport(ratio=.8)
    await expect(work.locator('.connected-steps').get_by_role('button', name='完善需求')).to_have_attribute('aria-current', 'step')
    require(item(saved, RID)['statement'] == AFTER and item(saved, ACID)['statement'] == AC_AFTER, 'Displayed changes were not committed')
    require(item(saved, RID)['selection_status'] == item(saved, ACID)['selection_status'] == 'selected', 'Requirement and acceptance did not adopt atomically')
    require(item(saved, OTHER_RID) == item(frozen, OTHER_RID) and item(saved, 'AC-HISTORY') == item(frozen, 'AC-HISTORY'), 'Confirming one row rewrote control row')
    require(confirmation_current(saved, item(saved, OTHER_RID)), 'Unchanged row lost confirmation')
    require(all(not item(saved, RID)['behavior'].get(key) for key in ('entry', 'data', 'permissions', 'exceptions')), 'Empty dimensions were invented')
    require(not [b for stage in status(saved)[:3] for b in stage['blockers']], 'Confirmed rows still trigger fixed-field blockers')
    require(saved['questions'][0]['status'] == 'open', 'Optional question was silently answered')
    commits = [r for r in trace['api_requests'] if r['path'].endswith('/requirement-confirmation/commit')]
    require(len(commits) == 1 and commits[0]['status'] == 200, 'Row needed more than one confirmation write')
    trace['checks'].append('one_commit_confirms_requirement_and_acceptance_preserves_other_row_and_does_not_invent_empty_fields')
    await page.screenshot(path=str(rec.output / '03-row-confirmed-other-row-preserved.png'))
    await rec.note('两条均已确认；未改动的第二条保持确认。可选日报问题仍未回答，不影响本期进入交付。', 6)
    ready = work.get_by_role('region', name='需求核对准备', exact=True)
    await rec.click(ready.get_by_role('button', name='确认需求核对并进入交付', exact=True))
    await expect(work.locator('.connected-steps').get_by_role('button', name='确认交付')).to_have_attribute('aria-current', 'step')
    final = store.get(pid)
    require(all(stage['complete'] for stage in status(final)[:3]), 'Clicking delivery did not record required stage checks')
    require(not final.get('active_baseline_id'), 'Entering delivery silently confirmed formal baseline')
    trace['checks'].append('necessary_rows_complete_optional_unknown_retained_and_explicit_stage_confirmation_navigates_to_delivery')
    await rec.note('已自动进入确认交付。这里尚未确认正式交付或建立基线，本段只验证需求确认操作。', 6)
    await page.screenshot(path=str(rec.output / '04-enter-delivery.png'))


async def guards(page, rec, store, pid, frozen, trace, client):
    work, table = await open_table(page, rec, pid)
    first, editor = await open_row(work, table, rec)
    statement = editor.get_by_role('textbox', name=TITLE + ' 需求内容', exact=True)
    await statement.fill(AFTER)
    await work.locator('.connected-steps').get_by_role('button', name='提供信息').click()
    guard = page.get_by_role('dialog', name='未保存的修改', exact=True)
    await expect(guard).to_be_visible()
    await guard.get_by_role('button', name='留在当前页', exact=True).click()
    await expect(statement).to_have_value(AFTER)
    require(store.get(pid) == frozen, 'Cancelled navigation wrote or lost input')
    trace['checks'].append('unsaved_navigation_cancel_retains_row_edits_without_writing')
    await editor.get_by_role('button', name='查看确认内容', exact=True).click()
    preview = editor.get_by_role('region', name='本条确认预览', exact=True)
    await expect(preview).to_be_visible()
    # Real second client changes a different row using a real API, never store.edit.
    response = await client.post(f'/api/projects/{pid}/items/{OTHER_RID}/behavior', json=dict(
        expected_revision=store.get(pid)['revision'], values={'flow': '选择场站并查询历史处理记录，结果按最新记录排列。'},
        change_type='new', review_confirmed=False))
    require(response.status_code == 200, 'Second-client real mutation failed: ' + response.text)
    conflict_state = copy.deepcopy(store.get(pid))
    await preview.get_by_role('button', name='确认这条需求', exact=True).click()
    await expect(editor.get_by_role('alert')).to_be_visible()
    require(store.get(pid) == conflict_state, 'Stale confirmation wrote content')
    require(item(store.get(pid), RID) == item(frozen, RID), 'Version conflict adopted stale proposal')
    require(any(r['path'].endswith('/requirement-confirmation/commit') and r['status'] == 409 for r in trace['api_requests']), 'Conflict was not enforced by backend')
    trace['checks'].append('real_concurrent_revision_rejects_stale_confirmation_preserving_user_draft')
    await editor.get_by_role('button', name='核对最新内容', exact=True).click()
    await expect(statement).to_have_value(AFTER)
    await editor.get_by_role('button', name='已核对，保留草稿重新预览', exact=True).click()
    await expect(preview).to_contain_text(AFTER)
    require(store.get(pid) == conflict_state, 'Recovering a fresh preview silently wrote content')
    await preview.get_by_role('button', name='确认这条需求', exact=True).click()
    recovered = await wait_store(store, pid, lambda p: confirmation_current(p, item(p, RID)), 'Fresh explicit confirmation did not recover')
    require(item(recovered, OTHER_RID) == item(conflict_state, OTHER_RID), 'Recovery overwrote the concurrent unrelated row')
    trace['checks'].append('explicit_latest_content_review_recovers_preserved_edits_and_keeps_concurrent_other_row')
    await page.screenshot(path=str(rec.output / 'conflict-preserves-draft.png'))
    first, editor = await open_row(work, table, rec)
    statement = editor.get_by_role('textbox', name=TITLE + ' 需求内容', exact=True)
    await expect(statement).to_have_value(AFTER)
    revised = AFTER + '保存后的处理记录可在历史查询中查看。'
    await statement.fill(revised)
    await editor.get_by_role('button', name='查看确认内容', exact=True).click()
    preview = editor.get_by_role('region', name='本条确认预览', exact=True)
    await expect(preview).to_contain_text(revised)
    await preview.get_by_role('button', name='确认这条需求', exact=True).click()
    reopened = await wait_store(store, pid, lambda p: item(p, RID)['statement'] == revised, 'Revisiting confirmed row used stale revision')
    require(confirmation_current(reopened, item(reopened, RID)), 'Revisited row did not confirm current edited content')
    trace['checks'].append('immediate_revisit_of_confirmed_row_can_edit_and_confirm_without_false_stale_revision')


async def unknown(page, rec, store, pid, frozen, trace, client):
    work, table = await open_table(page, rec, pid, unknown=True)
    first, editor = await open_row(work, table, rec)
    await expect(editor).to_contain_text(QUESTION)
    require(item(store.get(pid), RID)['selection_status'] == 'candidate', 'Viewing unknown marked row accepted')
    await editor.get_by_role('button', name=RECOMMENDED_ANSWER, exact=True).click()
    answer = editor.get_by_role('textbox', name=QUESTION + ' 我的回答', exact=True)
    await expect(answer).to_have_value(RECOMMENDED_ANSWER)
    require(store.get(pid) == frozen, 'Selecting an AI recommended answer silently saved or confirmed it')
    trace['checks'].append('recommended_answer_fills_editable_draft_only_and_never_decides_business_without_user_confirmation')
    await answer.fill('')
    await editor.get_by_role('button', name='查看确认内容', exact=True).click()
    preview = editor.get_by_role('region', name='本条确认预览', exact=True)
    await expect(preview).to_be_visible()
    await preview.get_by_role('button', name='确认这条需求', exact=True).click()
    saved = await wait_store(store, pid, lambda p: confirmation_current(p, item(p, RID)), 'Displayed content did not confirm')
    blockers = [b for stage in status(saved)[:3] for b in stage['blockers']]
    require(any(b['code'] == 'question_open' and QUESTION in b['message'] for b in blockers), 'Human row confirmation silently decided missing business rule')
    require(not any(b['code'] == 'behavior_fields_missing' for b in blockers), 'Remaining unknown is presented as mandatory seven fields')
    await expect(work.get_by_role('button', name='确认需求核对并进入交付', exact=True)).to_have_count(0)
    await expect(first).to_contain_text('决定')
    require(next(q for q in saved['questions'] if q['id'] == 'Q-PERMISSION')['status'] == 'open', 'Unknown answered without user decision')
    trace['checks'].append('unanswered_critical_decision_remains_specific_visible_blocker_after_content_confirmation_without_seven_field_checklist')
    await page.screenshot(path=str(rec.output / 'critical-decision-remains.png'))
    ready = work.get_by_role('region', name='需求核对准备', exact=True)
    await ready.get_by_role('button', name='处理第一项', exact=True).click()
    await expect(editor).to_be_visible()
    trace['blocker_focus_observation'] = await page.evaluate("""() => ({
      active: document.activeElement?.getAttribute('aria-label'),
      controls: [...document.querySelectorAll('[data-question-id="Q-PERMISSION"]')].map(e => ({
        top:e.getBoundingClientRect().top,height:e.getBoundingClientRect().height,
        inputTop:e.querySelector('textarea')?.getBoundingClientRect().top,
        parentOpen:e.closest('details')?.open}))})""")
    await expect(editor.get_by_role('textbox', name=QUESTION + ' 我的回答', exact=True)).to_be_in_viewport(ratio=1)
    trace['checks'].append('critical_blocker_action_opens_actual_answer_control_in_current_viewport')
    decision = editor.locator('[data-question-id="Q-PERMISSION"]')
    await decision.get_by_role('button', name=RECOMMENDED_ANSWER, exact=True).click()
    await decision.get_by_role('button', name='保存回答', exact=True).click()
    answered = await wait_store(store, pid, lambda p: next(q for q in p['questions'] if q['id'] == 'Q-PERMISSION').get('answer') == RECOMMENDED_ANSWER,
        'Explicitly saving recommended answer did not preserve the original answer')
    require(item(answered, RID)['statement'] == BEFORE, 'Saving recommended answer silently rewrote requirement')
    await editor.get_by_role('textbox', name=TITLE + ' 需求内容', exact=True).fill(AFTER)
    await editor.get_by_role('textbox', name=TITLE + ' 验收建议 1', exact=True).fill(AC_AFTER)
    await editor.get_by_role('button', name='查看确认内容', exact=True).click()
    preview = editor.get_by_role('region', name='本条确认预览', exact=True)
    await expect(preview).to_contain_text(RECOMMENDED_ANSWER)
    await expect(preview).to_contain_text('本次同时确认')
    await preview.get_by_role('button', name='确认这条需求', exact=True).click()
    resolved = await wait_store(store, pid, lambda p: next(q for q in p['questions'] if q['id'] == 'Q-PERMISSION').get('understanding_status') == 'applied',
        'Final displayed row confirmation did not resolve the answered critical question')
    require(not [b for stage in status(resolved)[:3] for b in stage['blockers']], 'Resolved permission still blocks completion')
    await expect(first).to_contain_text('已确认')
    await expect(work.get_by_role('button', name='确认需求核对并进入交付', exact=True)).to_be_visible()
    require(next(q for q in resolved['questions'] if q['id'] == 'Q-OPTIONAL')['status'] == 'open', 'Optional question accidentally resolved with critical one')
    trace['checks'].append('recommended_answer_then_displayed_clause_acceptance_confirmation_resolves_actual_critical_blocker_once')
    await page.screenshot(path=str(rec.output / 'critical-decision-resolved.png'))


async def versions(page, rec, store, pid, frozen, trace, client):
    work, table = await open_table(page, rec, pid)
    first, editor = await open_row(work, table, rec)
    choice = editor.get_by_role('combobox', name=TITLE + ' 建议版本', exact=True)
    await expect(choice).to_have_value('REQ-ALARM-V2')
    statement = editor.get_by_role('textbox', name=TITLE + ' 需求内容', exact=True)
    manual_draft = BEFORE + '人工草稿：处理记录保留原始填写人的署名。'
    await statement.fill(manual_draft)
    await choice.select_option('REQ-ALARM-V1')
    await editor.get_by_role('button', name='恢复上一次草稿', exact=True).click()
    await expect(statement).to_have_value(manual_draft)
    require(store.get(pid) == frozen, 'Switching or restoring a suggestion draft wrote project content')
    trace['checks'].append('switching_suggestion_versions_preserves_and_restores_exact_manual_draft_without_implicit_adoption')
    await choice.select_option('REQ-ALARM-V1')
    expected = item(frozen, 'REQ-ALARM-V1')['statement']
    await expect(editor.get_by_role('textbox', name=TITLE + ' 需求内容', exact=True)).to_have_value(expected)
    await editor.get_by_role('button', name='查看确认内容', exact=True).click()
    preview = editor.get_by_role('region', name='本条确认预览', exact=True)
    await expect(preview).to_contain_text(expected)
    require(store.get(pid) == frozen, 'Selecting an alternate suggestion wrote or approved it before confirmation')
    await preview.get_by_role('button', name='确认这条需求', exact=True).click()
    saved = await wait_store(store, pid, lambda p: confirmation_current(p, item(p, RID)), 'Alternate suggestion did not confirm')
    await expect(first).to_contain_text('已确认')
    await expect(first).to_contain_text(expected)
    await expect(first).not_to_contain_text(item(frozen, 'REQ-ALARM-V2')['statement'])
    require(item(saved, RID)['statement'] == expected, 'Committed a different suggestion version')
    require(not [b for stage in status(saved)[:3] for b in stage['blockers']], 'Old alternative suggestion continues to block after explicit row confirmation')
    require(item(saved, OTHER_RID) == item(frozen, OTHER_RID), 'Alternative suggestion confirmation changed unrelated row')
    trace['checks'].append('explicitly_chosen_suggestion_version_confirms_row_without_endless_old_alternative_blockers')
    await page.screenshot(path=str(rec.output / 'older-suggestion-confirmed.png'))


async def run(args):
    build, output = args.static_build.resolve(), args.output.resolve()
    require((build / 'index.html').is_file(), 'Validated static build missing')
    output.mkdir(parents=True, exist_ok=True)
    trace = dict(scope='isolated_synthetic_storage_cloud_table_confirmation', static_build=str(build),
        fixture_preconditions=['两条有来源的合成建议及验收；非真实模型输出',
            '测试前通过真实确认接口明确确认第二条，仅作未改行保持对照',
            '第一条及其验收均未采纳，只有actor/flow/result有内容，四个其他字段保持空',
            '一个可选问题未答；unknown场景另有一个关键权限问题未答'],
        checks=[], api_requests=[], page_errors=[], provider_calls=0, daily_data_access=False,
        post_browser_fixture_mutations=0, not_run=['真实模型生成质量', '正式交付、文档审核与基线'])
    with tempfile.TemporaryDirectory(prefix='ra-table-browser-') as folder:
        provider = NoModelProvider(Path(folder))
        app = create_app(Path(folder) / 'data', provider=provider)
        store = app.state.store
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, timeout=30) as client:
            pid, frozen = await seed(store, client, unknown=args.case == 'unknown', versions=args.case == 'versions')
            trace.update(project_id=pid, initial_revision=frozen['revision'])
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                options = dict(viewport=dict(width=1440, height=900))
                if args.record:
                    options.update(record_video_dir=str(output / 'raw'), record_video_size=dict(width=1440, height=900))
                context = await browser.new_context(**options)
                if args.record:
                    await context.add_init_script('(' + ANNOTATION + ')()')

                async def dispatch(route):
                    req = route.request
                    parsed = urlsplit(req.url)
                    require(parsed.netloc == '127.0.0.1:8765', 'External request forbidden: ' + req.url)
                    if parsed.path.startswith('/api/'):
                        require(not (req.method != 'GET' and (parsed.path.endswith('/actions') or
                            '/confirm' in parsed.path or '/runs' in parsed.path or parsed.path.endswith('/recommend'))),
                            'Model invocation or formal confirmation forbidden: ' + parsed.path)
                        headers = {k: v for k, v in req.headers.items() if k.lower() not in ('host', 'content-length', 'connection', 'accept-encoding')}
                        headers['host'] = '127.0.0.1:8765'
                        response = await client.request(req.method, parsed.path + ('?' + parsed.query if parsed.query else ''),
                            content=req.post_data_buffer, headers=headers)
                        row = dict(method=req.method, path=parsed.path, status=response.status_code)
                        if req.method != 'GET':
                            row.update(body=req.post_data_json, response=response.json())
                        trace['api_requests'].append(row)
                        await route.fulfill(status=response.status_code, body=response.content,
                            headers={'content-type': response.headers.get('content-type', 'application/json')})
                    else:
                        target = (build / (parsed.path.lstrip('/') or 'index.html')).resolve()
                        require(target.is_relative_to(build) and target.is_file(), 'Missing static asset: ' + parsed.path)
                        await route.fulfill(status=200, body=target.read_bytes(), content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')

                await context.route('**/*', dispatch)
                page = await context.new_page()
                page.set_default_timeout(12000)
                page.on('pageerror', lambda error: trace['page_errors'].append(str(error)))
                rec = Recorder(page, output, args.record)
                video = page.video
                try:
                    await page.goto(ORIGIN, wait_until='networkidle')
                    await {'journey': journey, 'guards': guards, 'unknown': unknown, 'versions': versions}[args.case](page, rec, store, pid, frozen, trace, client)
                    require(not trace['page_errors'], 'Browser runtime errors occurred')
                    require(provider.calls == 0, 'Unexpected provider invocation')
                    trace.update(status='passed', final_revision=store.get(pid)['revision'])
                except Exception as error:
                    trace.update(status='failed', failure=str(error))
                    await page.screenshot(path=str(output / 'failure.png'), full_page=True)
                    (output / 'failure.html').write_text(await page.content(), encoding='utf-8')
                    raise
                finally:
                    trace['provider_calls'] = provider.calls
                    rec.save()
                    (output / 'result.json').write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding='utf-8')
                    await context.close()
                    if video:
                        await video.save_as(str(output / '需求表格确认操作.webm'))
                    await browser.close()
    print(json.dumps(dict(status=trace['status'], checks=trace['checks'], output=str(output)), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--static-build', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--case', choices=('journey', 'guards', 'unknown', 'versions'), default='journey')
    parser.add_argument('--record', action='store_true')
    asyncio.run(run(parser.parse_args()))
