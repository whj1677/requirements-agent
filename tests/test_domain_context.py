"""Optional project background and source-selection invariants."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core import brief_hash
from app.domain_context import (active_source_ids, bound_input_source_ids,
                                filter_excerpts, model_context,
                                source_snapshot, update, view)
from app.domain_context_routes import register
from app.core import Problem
from app.store import Store


def source(sid, text='原始材料'):
    excerpt = {'id': 'EX-' + sid, 'source_id': sid, 'text': text, 'locator': 'p1'}
    return {'id': sid, 'title': sid, 'sha256': 'hash-' + sid, 'version': 1,
            'excluded': False, 'parse_status': 'read', 'excerpts': [excerpt], 'image_mime': None}


def test_legacy_sources_and_project_isolation(tmp_path):
    store = Store(tmp_path)
    a = store.create('站点 A')
    b = store.create('站点 B')
    with store.edit(a['id'], a['revision'], 'fixture') as (p, _):
        p['sources'] = [source('A1'), source('A2')]
    with store.edit(b['id'], b['revision'], 'fixture') as (p, _):
        p['sources'] = [source('B1')]
    a = store.get(a['id'])
    b = store.get(b['id'])
    assert active_source_ids(a) == {'A1', 'A2'}
    assert view(a)['background'] == ''
    assert model_context(a) is None
    old_hash = brief_hash(a)
    update(a, background='只做本次资料管理需求',
           terms=[{'term': '站点', 'meaning': 'A 项目的客户站点', 'status': 'confirmed'}],
           selected_source_ids=['A1'])
    assert brief_hash(a) == old_hash
    assert active_source_ids(a) == {'A1'}
    assert filter_excerpts(a, a['sources'][1], a['sources'][1]['excerpts']) == []
    assert model_context(a)['terms'][0]['status'] == 'confirmed'
    assert active_source_ids(b) == {'B1'} and model_context(b) is None


def test_explicit_empty_selection_and_exclusion_do_not_change_saved_choice(tmp_path):
    p = Store(tmp_path).create('项目')
    p['sources'] = [source('S1'), source('S2')]
    update(p, selected_source_ids=[])
    assert active_source_ids(p) == set()
    update(p, selected_source_ids=['S1', 'S2'])
    p['sources'][1]['excluded'] = True
    assert active_source_ids(p) == {'S1'}
    assert view(p)['selected_source_ids'] == ['S1', 'S2']
    assert view(p)['unavailable_selected_source_ids'] == ['S2']
    update(p, selected_source_ids=None)
    assert active_source_ids(p) == {'S1'}


def test_snapshot_only_binds_sent_excerpt_and_rejects_other_project(tmp_path):
    p = Store(tmp_path).create('项目')
    p['sources'] = [source('S1'), source('S2')]
    actual = source_snapshot(p, [p['sources'][0]['excerpts'][0]])
    assert actual['project_id'] == p['id']
    assert [(r['source_id'], r['excerpt_id'], r['source_sha256'])
            for r in actual['references']] == [('S1', 'EX-S1', 'hash-S1')]
    assert len(actual['references'][0]['excerpt_sha256']) == 64
    from app.core import Problem
    try:
        source_snapshot(p, [source('X')['excerpts'][0]])
    except Problem as error:
        assert error.code == 'REFERENCE_INVALID'
    else:
        assert False, 'other-project excerpt must be rejected'
    p['sources'][0]['excluded'] = True
    try:
        source_snapshot(p, [p['sources'][0]['excerpts'][0]])
    except Problem as error:
        assert error.code == 'REFERENCE_INVALID'
    else:
        assert False, 'excluded excerpt must not be reported as sent'


def test_routes_are_optional_partial_and_revision_guarded(tmp_path):
    store = Store(tmp_path)
    app = FastAPI()
    @app.exception_handler(Problem)
    async def problem_response(_, error):
        return JSONResponse({'code': error.code, 'message': error.message}, status_code=error.status)
    register(app, store)
    client = TestClient(app)
    p = store.create('客户资料')
    root = '/api/projects/' + p['id'] + '/domain-context'
    initial = client.get(root)
    assert initial.status_code == 200 and initial.json()['selected_source_ids'] is None
    changed = client.post(root, json={'expected_revision': p['revision'],
                                      'background': '只用于理解术语，不扩模块',
                                      'terms': [{'term': '站点', 'meaning': '本项目站点',
                                                 'status': 'reference'}],
                                      'selected_source_ids': []})
    assert changed.status_code == 200, changed.text
    value = changed.json()
    assert value['background'] == '只用于理解术语，不扩模块'
    assert value['effective_source_ids'] == []
    assert value['terms'][0]['status'] == 'reference'
    stale = client.post(root, json={'expected_revision': p['revision'], 'background': '旧页面覆盖'})
    assert stale.status_code == 409 and stale.json()['code'] == 'STALE_REVISION'
    retained = client.post(root, json={'expected_revision': value['revision'],
                                       'selected_source_ids': None})
    assert retained.status_code == 200 and retained.json()['terms'] == value['terms']


def test_cross_project_source_selection_is_rejected(tmp_path):
    p = Store(tmp_path).create('项目')
    from app.core import Problem
    try:
        update(p, selected_source_ids=['SRC-from-another-project'])
    except Problem as error:
        assert error.code == 'REFERENCE_INVALID'
    else:
        assert False, 'foreign source ID must be rejected'


def test_current_answer_survives_empty_reference_selection_but_old_answer_does_not(tmp_path):
    p = Store(tmp_path).create('项目 A')
    p['sources'] = [source('old'), source('current'), source('reference')]
    p['questions'] = [{
        'id': 'Q1', 'status': 'answered', 'answer': '当前回答',
        'answer_source_refs': [{'source_id': 'current', 'excerpt_id': 'EX-current'}],
        'answer_history': [{'answer': '旧回答', 'answer_source_refs': [
            {'source_id': 'old', 'excerpt_id': 'EX-old'}]}],
    }]
    update(p, selected_source_ids=[])
    assert active_source_ids(p) == {'current'}
    assert view(p)['bound_input_source_ids'] == ['current']
    assert filter_excerpts(p, p['sources'][0], p['sources'][0]['excerpts']) == []
    p['sources'][1]['excluded'] = True
    assert active_source_ids(p) == set()
    assert bound_input_source_ids(p) == set()


def test_new_decision_answer_and_workspace_draft_bindings_are_current_and_local(tmp_path):
    store = Store(tmp_path)
    a = store.create('项目 A')
    b = store.create('项目 B')
    a['sources'] = [source('A-old'), source('A-now'), source('A-draft')]
    a['questions'] = [{'id': 'Q1', 'status': 'open', 'answer': None,
                       'decision_answer_id': 'DA-now'}]
    a['decision_answers'] = [
        {'id': 'DA-old', 'source_refs': [{'source_id': 'A-old', 'excerpt_id': 'EX-A-old'}]},
        {'id': 'DA-now', 'source_refs': [{'source_id': 'A-now', 'excerpt_id': 'EX-A-now'}]},
    ]
    a['workspace_draft'] = {'source_id': 'A-draft'}
    b['sources'] = [source('B-only')]
    b['questions'] = [{'id': 'Q1', 'status': 'open', 'answer': None,
                       'decision_answer_id': 'DA-now'}]
    b['decision_answers'] = a['decision_answers']
    update(a, selected_source_ids=[])
    update(b, selected_source_ids=[])
    assert active_source_ids(a) == {'A-now', 'A-draft'}
    assert active_source_ids(b) == set()
    a['questions'][0]['decision_answer_id'] = 'DA-old'
    assert active_source_ids(a) == {'A-old', 'A-draft'}
    a['sources'][0]['excluded'] = True
    assert active_source_ids(a) == {'A-draft'}


def test_only_current_task_message_source_survives_empty_selection(tmp_path):
    p = Store(tmp_path).create('项目 A')
    p['sources'] = [source('old-task'), source('current-task')]
    p['current_task_source_id'] = 'current-task'
    update(p, selected_source_ids=[])
    assert active_source_ids(p) == {'current-task'}
    assert filter_excerpts(p, p['sources'][0], p['sources'][0]['excerpts']) == []
    p['current_task_source_id'] = 'old-task'
    assert active_source_ids(p) == {'old-task'}
    p['sources'][0]['excluded'] = True
    assert active_source_ids(p) == set()
