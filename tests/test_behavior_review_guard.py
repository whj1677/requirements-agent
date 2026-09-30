"""Model expansion is a proposal until a version-bound human check."""
import copy

from app.core import brief_hash
from app.requirements import behavior_review_current
from app.workflow import apply_response
from tests.helpers import prepared
from tests.test_project_summary_repair import client
from tests.test_classification_recovery_20260930 import response, excerpts


def pending_case(tmp_path):
    browser, app = client(tmp_path)
    p = prepared(app.state.store)
    with app.state.store.edit(p['id'], p['revision'], '合成模型展开待核对') as (draft, _):
        item = draft['items'][0]
        item.update(selection_status='candidate', behavior_review_required=True,
                    change_type='new', behavior={'flow':'模型推演：提交与结果各写一条记录'})
        draft['options']=[dict(id='OPT-synthetic',proposed_item_refs=['REQ-0001','RULE-0001'])]
    return browser, app, '/api/projects/'+p['id']


def post(browser, root, path, **body):
    return browser.post(root+path, json=dict(expected_revision=browser.get(root).json()['revision'], **body))


def test_new_model_requirement_is_marked_pending_without_changing_response():
    value = response(excerpts())
    before = copy.deepcopy(value)
    p = dict(id='P-synthetic',revision=1,items=[],questions=[],sources=[],options=[],messages=[])
    apply_response(p,value)
    assert value == before
    item = p['items'][0]
    assert item['behavior_review_required'] is True
    assert not behavior_review_current(item)
    assert item['selection_status']=='candidate'
    assert 'behavior_review' not in item


def test_single_and_batch_adoption_cannot_bypass_pending_behavior(tmp_path):
    browser, app, root = pending_case(tmp_path)
    before = copy.deepcopy(app.state.store.get(root.split('/')[-1]))
    for path, body in [('/items/REQ-0001',dict(selection_status='selected')),
                       ('/options/OPT-synthetic/items',dict(item_ids=['RULE-0001','REQ-0001']))]:
        result = post(browser,root,path,**body)
        assert result.status_code==409 and result.json()['code']=='BEHAVIOR_REVIEW_REQUIRED'
        assert app.state.store.get(before['id'])==before  # Atomic: no partial rule adoption/revision bump.
    shown = browser.get(root).json()
    assert shown['behavior_review_statuses']['REQ-0001']=='required'


def test_saving_an_unreviewed_draft_preserves_proposal_and_stays_pending(tmp_path):
    browser, app, root = pending_case(tmp_path)
    result=post(browser,root,'/items/REQ-0001/behavior',values={'flow':''},change_type='new')
    assert result.status_code==200
    shown=browser.get(root).json()
    assert shown['items'][0]['behavior']['flow']==''
    assert shown['behavior_review_statuses']['REQ-0001']=='required'
    assert post(browser,root,'/items/REQ-0001',selection_status='selected').status_code==409


def test_explicit_review_accepts_exact_version_and_later_edits_require_recheck(tmp_path):
    browser, app, root = pending_case(tmp_path)
    original=browser.get(root).json()
    result=post(browser,root,'/items/REQ-0001/behavior',values={'flow':'核对审计字段；写入时点与粒度待定'},
                change_type='new',review_confirmed=True)
    assert result.status_code==200
    reviewed=browser.get(root).json()
    assert reviewed['behavior_review_statuses']['REQ-0001']=='reviewed'
    assert reviewed['items'][0]['behavior_review']['actor']=='authenticated_local_user'
    assert post(browser,root,'/items/REQ-0001',selection_status='selected').status_code==200
    before=app.state.store.get(original['id'])
    invalid=post(browser,root,'/items/REQ-0001',selection_status='selected',statement='变更为另一套未核对的业务')
    assert invalid.status_code==409 and app.state.store.get(original['id'])==before
    assert not any(s['title']=='人工修订.txt' for s in before['sources'])
    assert post(browser,root,'/items/REQ-0001/behavior',values={'flow':'另一段修改'},change_type='new').status_code==200
    assert browser.get(root).json()['behavior_review_statuses']['REQ-0001']=='required'
    stale=browser.post(root+'/items/REQ-0001/behavior',json=dict(expected_revision=original['revision'],
        values={},change_type='new',review_confirmed=True))
    assert stale.status_code==409


def test_legacy_selected_facts_are_not_migrated_by_read_projection(tmp_path):
    browser, app = client(tmp_path)
    p = prepared(app.state.store)
    before=copy.deepcopy(p)
    shown=browser.get('/api/projects/'+p['id']).json()
    assert shown['behavior_review_statuses']['REQ-0001']=='legacy'
    assert shown['items']==before['items']
    assert app.state.store.get(p['id'])==before
    assert brief_hash(app.state.store.get(p['id']))==brief_hash(before)
    assert brief_hash(shown)==brief_hash(before)==shown['hashes']['brief_hash']
