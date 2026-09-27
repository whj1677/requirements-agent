"""Scope inclusion follows the same bidirectional links as delivery selection."""
import pytest

from tests.test_export_responsiveness import run_isolated_program


@pytest.mark.parametrize('kind', ['rule', 'acceptance'])
def test_reference_scope_inclusion_accepts_requirement_to_clause_link(tmp_path, kind):
    run_isolated_program(tmp_path, r'''
import asyncio
import copy
import httpx
import app.main as main
from app.requirements import delivery_items
from tests.helpers import prepared
app = main.create_app(Path(os.environ['RA_DATA_DIR']) / 'case', env_path=Path(os.environ['RA_DATA_DIR']) / 'absent.env')
store = app.state.store
p = prepared(store)
with store.edit(p['id'], p['revision'], 'synthetic reverse reference') as (p, _):
    p['product_context'] = {'scope_ids': ['REQ-0001']}
    item = next(item for item in p['items'] if item['kind'] == os.environ['RA_TEST_CLAUSE_KIND'])
    item.update(applies_to='reference', selection_status='candidate', related_refs=[])
    item_id = item['id']
before = copy.deepcopy(item)
assert item_id in p['items'][0]['related_refs'] and not item['related_refs']
assert item_id not in {item['id'] for item in delivery_items(p)}

async def exercise():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
        response = await client.post('/api/projects/' + p['id'] + '/items/' + item_id + '/include-scope',
            json={'expected_revision': p['revision'], 'reason': 'Explicit synthetic business scope decision'})
        assert response.status_code == 200, response.text
    current = store.get(p['id'])
    item = next(item for item in current['items'] if item['id'] == item_id)
    assert item['applies_to'] == 'to_be' and item['selection_status'] == 'selected'
    assert item['id'] == before['id'] and item['statement'] == before['statement']
    assert item['related_refs'] == []
    assert all(ref in item['source_refs'] for ref in before['source_refs'])
    assert item_id in {item['id'] for item in delivery_items(current)}
    assert item['scope_decision']['reason'] == 'Explicit synthetic business scope decision'
asyncio.run(exercise())
''', RA_TEST_CLAUSE_KIND=kind)
