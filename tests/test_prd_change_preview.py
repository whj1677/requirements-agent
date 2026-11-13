"""Read-only PRD scope/diff previews; no model or production data involved."""
import copy

from app.document_preview import build_prd_preview, text_diff
from tests.test_prd01 import state


def test_first_preview_counts_delivery_scope_and_preserves_project(tmp_path):
    _, project = state(tmp_path)
    before = copy.deepcopy(project)

    preview = build_prd_preview(project)

    assert preview['first_generation'] is True
    assert preview['counts']['added'] == 3
    assert preview['counts']['modified'] == preview['counts']['removed'] == 0
    assert preview['counts']['pending'] >= 1
    assert any(row['id'] == 'REQ-CANDIDATE' for row in preview['excluded'])
    assert any(row['id'] == 'REQ-CANDIDATE' for row in preview['ai_suggestions'])
    assert project == before


def test_incremental_preview_has_exact_character_diff_removals_and_pending(tmp_path):
    _, project = state(tmp_path)
    previous_items = copy.deepcopy(project['items'][:3])
    project['documents']['prd'] = dict(id='DOC-OLD', document_version=2, draft_revision=3,
        item_snapshot=previous_items, delivery_item_ids=[item['id'] for item in previous_items])
    project['items'][0]['statement'] = '管理员可以编辑联系人姓名和所属部门。'
    project['items'][1]['selection_status'] = 'rejected'
    project['sources'].append(dict(project['sources'][0], id='SRC-EXCLUDED', title='排除资料', excluded=True))
    before = copy.deepcopy(project)

    preview = build_prd_preview(project)

    assert preview['baseline']['document_id'] == 'DOC-OLD'
    assert preview['counts']['added'] == 0
    assert preview['counts']['modified'] == preview['counts']['removed'] == 1
    modified = next(row for row in preview['changes'] if row['change_type'] == 'modified')
    statement = next(field for field in modified['fields'] if field['field'] == 'statement')
    assert ''.join(part['text'] for part in statement['segments'] if part['type'] != 'removed') == project['items'][0]['statement']
    assert any(part['type'] == 'added' and '所属部门' in part['text'] for part in statement['segments'])
    removed = next(row for row in preview['changes'] if row['change_type'] == 'removed')
    assert removed['item_id'] == 'RULE-0001'
    assert any(row['id'] == 'SRC-EXCLUDED' for row in preview['excluded'])
    assert any(row['kind'] == 'question' for row in preview['pending_confirmations'])
    assert project == before


def test_action_plan_contains_same_preview_without_calling_model(tmp_path):
    from app.actions import Actions
    from app.workflow import Workflow
    from tests.test_runtime import FakeProvider

    store, project = state(tmp_path)
    provider = FakeProvider([])
    actions = Actions(store, Workflow(store, provider))
    body = dict(expected_revision=project['revision'], action='document', message='',
        document_type='prd', max_calls=None, allow_review_draft=True)

    plan = actions.plan(project['id'], body)

    assert plan['document_preview']['preview_hash'] == build_prd_preview(project)['preview_hash']
    assert plan['document_preview']['counts']['added'] == 3
    assert provider.received == []


def test_text_diff_round_trips_both_versions():
    before, after = '解绑后保留历史数据。', '解绑后保留历史数据，但禁止重复绑定。'
    segments = text_diff(before, after)
    assert ''.join(part['text'] for part in segments if part['type'] != 'added') == before
    assert ''.join(part['text'] for part in segments if part['type'] != 'removed') == after
    assert any(part['type'] == 'added' and '重复绑定' in part['text'] for part in segments)
