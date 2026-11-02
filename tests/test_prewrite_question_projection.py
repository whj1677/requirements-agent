"""A reviewed dispute removes a stale technical question from drafting, not history."""
import copy

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.prewrite import apply_result, question_resolution_current
from app.refinement_completion import questions
from tests.test_detail_document import project_fixture
from tests.test_prewrite_consistency import review


def resolved_project(tmp_path):
    p = project_fixture(tmp_path)
    response = review(p, blocker=True)
    text = '请决定文本计数技术细节：' + '保留业务上限。' * 20
    response['findings'][0]['decision_question'] = text
    response['result']['required_decisions'] = [text]
    first = apply_result(p, response, 'RUN-question')
    p['prewrite_responses'] = [dict(finding_id=first['findings'][0]['id'], review_id=first['id'],
        mode='dispute', reason='无需新增技术业务决定，请按原业务来源复查。', created='synthetic')]
    apply_result(p, review(p), 'RUN-rechecked')
    return p


def test_resolved_question_is_projected_without_answer_and_not_reoffered(tmp_path):
    p = resolved_project(tmp_path)
    q = p['questions'][0]
    before = copy.deepcopy(p)
    assert question_resolution_current(p, q)
    assert questions(p, {'mode': 'required'}) == []
    assert questions(p, {'mode': 'question', 'question_id': q['id']}) == []
    assert p == before and q['status'] == 'open' and q['answer'] is None
    # Previously persisted reviewed-dispute records use their exact card/title,
    # related refs and source/content hash; no migration or invented answer.
    q['prewrite_resolution'].pop('question_hash')
    assert question_resolution_current(p, q)


@pytest.mark.parametrize('change', ['question', 'refs', 'key', 'source', 'item', 'open', 'hard_block'])
def test_changed_identity_or_unresolved_question_remains_in_drafting(tmp_path, change):
    p = resolved_project(tmp_path)
    q = p['questions'][0]
    if change == 'question': q['question'] += '新增业务决定？'
    elif change == 'refs': q['related_refs'].append('REQ-other')
    elif change == 'key': q['prewrite_key'] = 'PW-other'
    elif change == 'source': p['sources'][0]['excerpts'][0]['text'] += '新业务规则'
    elif change == 'item': p['items'][0]['statement'] += '业务规则变化'
    elif change == 'open': p['prewrite_review']['findings'][0]['status'] = 'open'
    else: p['prewrite_review']['findings'][0]['hard_block'] = True
    assert not question_resolution_current(p, q)
    assert q in questions(p, {'mode': 'required'})


def test_project_get_refreshes_resolution_projection_without_canonical_write(tmp_path):
    app = create_app(tmp_path, access_token='projection-test', env_path=tmp_path/'absent.env')
    p = resolved_project(tmp_path)
    with app.state.store.edit(p['id'], p['revision'], 'synthetic resolved question', bump=False) as (saved, _):
        saved.update(p)
    before = app.state.store.get(p['id'])
    with TestClient(app) as client:
        client.headers['Origin'] = 'http://testserver'
        client.headers['X-CSRF-Token'] = client.post('/api/session/login', json={'key':'projection-test'}).json()['csrf']
        result = client.get('/api/projects/'+p['id'])
    assert result.status_code == 200
    q = result.json()['questions'][0]
    assert q['prewrite_resolution_current'] is True and q['answer'] is None and q['status'] == 'open'
    assert app.state.store.get(p['id']) == before
