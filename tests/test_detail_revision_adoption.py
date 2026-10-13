"""A reviewed AI revision must carry its structured details to the stable item."""
import copy

from app.requirements import behavior_fingerprint
from tests.helpers import prepared
from tests.test_project_summary_repair import client


def test_adopting_revision_preserves_detail_and_invalidates_old_definition(tmp_path):
    browser, app = client(tmp_path)
    project = prepared(app.state.store)
    with app.state.store.edit(project['id'], project['revision'], 'isolated revision fixture') as (p, _):
        target = p['items'][0]
        target['detail_contract'] = {'schema_version': 1, 'fields': [{'id': 'F1', 'name': '旧名称'}]}
        target['detail_required'] = True
        revision = copy.deepcopy(target)
        revision.update(id='REQ-0099', target_item_id=target['id'], selection_status='candidate',
                        behavior_review_required=True)
        revision['detail_contract']['fields'][0]['name'] = '新名称'
        revision['behavior_review'] = {'content_hash': behavior_fingerprint(revision),
                                      'actor': 'synthetic_test_review'}
        p['items'].append(revision)
    before = app.state.store.get(project['id'])
    old_version = before['items'][0]['content_version']
    response = browser.post('/api/projects/' + project['id'] + '/items/REQ-0099',
                            json={'expected_revision': before['revision'], 'selection_status': 'selected'})
    assert response.status_code == 200, response.text
    after = app.state.store.get(project['id'])
    stable = after['items'][0]
    assert stable['id'] == before['items'][0]['id']
    assert stable['content_version'] > old_version
    assert stable['detail_contract'] == revision['detail_contract']
    assert stable['detail_required'] is True
    assert stable['behavior_review']['content_hash'] == behavior_fingerprint(stable)
    assert not stable.get('requirement_confirmation')
    assert next(i for i in after['items'] if i['id'] == 'REQ-0099')['selection_status'] == 'deferred'
