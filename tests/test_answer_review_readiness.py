"""The answer review GET reports existing guards without writing project state."""
import copy

import pytest
from fastapi.testclient import TestClient

from app.answer_review import _readiness
from app.main import create_app
from tests.helpers import prepared


def review_client(tmp_path, edit):
    app = create_app(tmp_path, access_token='synthetic-login', env_path=tmp_path / '.env')
    project = prepared(app.state.store)
    with app.state.store.edit(project['id'], project['revision'], '合成回答核对设置') as (draft, _):
        question = draft['questions'][0]
        question.update(status='answered', answer='合成回答：相接边界需要单独核对。',
                        related_refs=['REQ-0001', 'RULE-0001'])
        edit(draft)
    return app, project['id'], question['id']


def get_review(app, pid, qid):
    path = f'/api/projects/{pid}/questions/{qid}/application-review'
    before = copy.deepcopy(app.state.store.get(pid))
    with app.state.store.connect() as db:
        counts_before = tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                              for table in ('revisions', 'records'))
    with TestClient(app) as client:
        csrf = client.post('/api/session/login', json={'key': 'synthetic-login'}).json()['csrf']
        client.headers.update({'Origin': 'http://testserver', 'X-CSRF-Token': csrf})
        response = client.get(path)
    assert response.status_code == 200, response.text
    assert app.state.store.get(pid) == before
    with app.state.store.connect() as db:
        counts_after = tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                             for table in ('revisions', 'records'))
    assert counts_after == counts_before
    assert app.state.store.records(pid, 'run') == []
    return response.json(), before


def by_id(review, iid):
    return next(target for target in review['targets'] if target['item']['id'] == iid)


def test_unselected_constraint_and_selected_requirement_show_distinct_readiness(tmp_path):
    def edit(p):
        rule = next(i for i in p['items'] if i['id'] == 'RULE-0001')
        rule.update(kind='constraint', selection_status='candidate')
    app, pid, qid = review_client(tmp_path, edit)
    review, project = get_review(app, pid, qid)
    assert review['expected_revision'] == project['revision']
    assert review['question_id'] == qid
    assert review['question'] == project['questions'][0]['question']
    assert review['answer'] == project['questions'][0]['answer']
    assert len(review['answer_hash']) == 64
    req = by_id(review, 'REQ-0001')
    rule = by_id(review, 'RULE-0001')
    assert req['readiness'] == dict(can_adopt=False, can_confirm=True,
                                    behavior_review_required=False, issues=[])
    assert rule['item']['kind'] == 'constraint'
    assert rule['readiness'] == dict(can_adopt=True, can_confirm=False,
                                     behavior_review_required=False, issues=['请先采纳此条款'])
    assert len(rule['clause_hash']) == 64
    assert rule['sources'] == [dict(source_id='SRC-0001', excerpt_id='EX-0001',
                                    title='冻结工程材料', locator='第 1 段',
                                    text='管理员维护电价时段；重叠拒绝；相接边界待确认。')]


def test_pending_behavior_review_disables_adoption(tmp_path):
    def edit(p):
        req = next(i for i in p['items'] if i['id'] == 'REQ-0001')
        req.update(selection_status='candidate', behavior_review_required=True,
                   behavior_review={})
    app, pid, qid = review_client(tmp_path, edit)
    readiness = by_id(get_review(app, pid, qid)[0], 'REQ-0001')['readiness']
    assert readiness['behavior_review_required']
    assert not readiness['can_adopt'] and not readiness['can_confirm']
    assert '请先采纳此条款' in readiness['issues']
    assert any('行为说明及来源' in issue for issue in readiness['issues'])


@pytest.mark.parametrize('selected', [False, True])
@pytest.mark.parametrize('failure', ['source', 'result'])
def test_existing_source_or_result_guard_disables_readiness(tmp_path, selected, failure):
    def edit(p):
        req = next(i for i in p['items'] if i['id'] == 'REQ-0001')
        req['selection_status'] = 'selected' if selected else 'candidate'
        req['statement'] = ('只有管理员可查看电价拒绝反馈。' if failure == 'source'
                            else '结果未知时已创建新的申请。')
    app, pid, qid = review_client(tmp_path, edit)
    readiness = by_id(get_review(app, pid, qid)[0], 'REQ-0001')['readiness']
    assert not readiness['can_confirm'] and not readiness['can_adopt']
    assert any(('来源含义待确认' if failure == 'source' else '结果') in issue
               for issue in readiness['issues'])


def test_selected_targets_are_confirmable_and_projection_is_independent(tmp_path):
    app, pid, qid = review_client(tmp_path, lambda p: None)
    review, project = get_review(app, pid, qid)
    assert all(t['readiness']['can_confirm'] and not t['readiness']['can_adopt']
               for t in review['targets'])
    review['targets'][0]['item']['statement'] = '仅更改响应副本'
    assert app.state.store.get(pid) == project


def test_non_target_scope_and_revision_do_not_offer_single_adoption(tmp_path):
    def edit(p):
        rule = next(i for i in p['items'] if i['id'] == 'RULE-0001')
        rule.update(selection_status='candidate', applies_to='as_is')
    app, pid, qid = review_client(tmp_path, edit)
    review, project = get_review(app, pid, qid)
    readiness = by_id(review, 'RULE-0001')['readiness']
    assert not readiness['can_adopt'] and not readiness['can_confirm']
    assert any('适用范围' in issue for issue in readiness['issues'])
    item = copy.deepcopy(next(i for i in project['items'] if i['id'] == 'RULE-0001'))
    item.update(applies_to='to_be', target_item_id='REQ-0001')
    revision = _readiness(project, item)
    assert not revision['can_adopt']
    assert any('原条目' in issue for issue in revision['issues'])
