import copy
import hashlib
import pytest
from app.core import KIT, Problem, hashes, digest
from app.store import Store
from app.contracts import VALIDATOR, validate_response, validate_document, document_gaps, gate, profile
from app.workflow import confirm
from app.exports import handoff
from tests.helpers import EXAMPLES, prepared

@pytest.fixture
def state(tmp_path):
    store=Store(tmp_path);return store,prepared(store)

def confirmation(p,key='test-idempotency'):
    return dict(expected_revision=p['revision'],expected_hashes=hashes(p),scope_ids=[i['id'] for i in p['items'] if i['selection_status']=='selected'],idempotency_key=key)

@pytest.mark.parametrize('name',list(EXAMPLES))
def test_supplied_protocol_examples_are_schema_valid(name):
    assert not list(VALIDATOR.iter_errors(EXAMPLES[name]))

@pytest.mark.parametrize('field',['confirmed','approved','actor','active_baseline'])
def test_model_cannot_add_authority(field):
    value=copy.deepcopy(EXAMPLES['clarify']);value[field]=True
    assert list(VALIDATOR.iter_errors(value))

def test_cross_project_reference(state):
    _,p=state;r=copy.deepcopy(EXAMPLES['clarify'])
    r['used_source_refs']=[{'source_id':'OTHER','excerpt_id':'EX-0001'}]
    with pytest.raises(Problem,match='来源'):validate_response(r,'clarify',p,p['sources'][0]['excerpts'])

@pytest.mark.parametrize('kind',['prd','mrd'])
def test_profiles_and_complete_mapping(state,kind):
    _,p=state;doc=p['documents'][kind]['content'];validate_document(doc,p,kind)
    assert document_gaps(doc,p)==[]

@pytest.mark.parametrize('mutation',['wrong-profile','dangling-parent','cycle','false-coverage','unknown-mapping'])
def test_document_reference_rejection(state,mutation):
    _,p=state;doc=copy.deepcopy(p['documents']['prd']['content'])
    if mutation=='wrong-profile':doc['content_profile_id']='user-mrd-reference'
    if mutation=='dangling-parent':doc['sections'][0]['parent_section_id']='PRD-404'
    if mutation=='cycle':doc['sections'][0]['parent_section_id']=doc['sections'][0]['section_id']
    if mutation=='false-coverage':doc['coverage'][0]['section_ids']=['PRD-404']
    if mutation=='unknown-mapping':doc['reference_mapping'][0]['profile_section_id']='UNKNOWN'
    with pytest.raises(Problem):validate_document(doc,p,'prd')

def test_partial_mapping_never_ready(state):
    _,p=state;p['documents']['prd']['content']['reference_mapping']=[]
    assert any('参考维度' in x for x in gate(p))

def test_canonical_block_rewrite_refused():
    r=copy.deepcopy(EXAMPLES['prd']);r['result']['sections'][0]['blocks'][0]['text']='允许只读用户修改'
    assert list(VALIDATOR.iter_errors(r))

def test_gate_mrd_not_prd(state):
    _,p=state;del p['documents']['prd'];assert any('缺少 PRD' in issue for issue in gate(p))

def test_confirmation_idempotency_staleness_and_history(state):
    store,p=state
    from tests.product_flow_helpers import check_prepared
    p=check_prepared(store,p['id'])
    assert gate(p)==[]
    body=confirmation(p);result=confirm(store,p['id'],body)
    assert confirm(store,p['id'],body)['id']==result['id']
    changed=dict(body,scope_ids=[])
    with pytest.raises(Problem,match='幂等'):confirm(store,p['id'],changed)
    baseline=store.get_record(p['id'],result['baseline_id'])
    before=digest(baseline)
    with store.edit(p['id'],p['revision'],'修改权限') as (new,db):new['items'][1]['statement']='新的规则，等待重新确认'
    with pytest.raises(Problem,match='版本'):confirm(store,p['id'],dict(body,idempotency_key='different-key'))
    assert digest(store.get_record(p['id'],result['baseline_id']))==before
    exp=handoff(store,p['id'],result['baseline_id'],'export-key')
    assert handoff(store,p['id'],result['baseline_id'],'export-key')['id']==exp['id']

def test_export_without_confirmation(state):
    store,p=state
    with pytest.raises(Problem,match='确认'):handoff(store,p['id'],'missing','export-key')

def test_blocking_question_prevents_confirmation(state):
    store,p=state
    with store.edit(p['id'],p['revision'],'升级关键问题',bump=False) as (p,db):p['questions'][0]['blocking']=True
    with pytest.raises(Problem):confirm(store,p['id'],confirmation(p))

def test_restart_persists_immutable_content(state):
    store,p=state
    reopened=Store(store.folder)
    assert reopened.get(p['id'])==p

def test_legacy_read_does_not_rehash(state):
    store,p=state
    with store.connect() as db:
        import json
        p['schema_version']='1.0'
        raw=json.dumps(p,ensure_ascii=False)
        db.execute('UPDATE projects SET payload=? WHERE id=?',(raw,p['id']))
    assert Store(store.folder).get(p['id'])['schema_version']=='1.0'
    with store.connect() as db:assert db.execute('SELECT payload FROM projects WHERE id=?',(p['id'],)).fetchone()[0]==raw

def test_original_reference_hashes_unchanged():
    from app.contracts import PROFILES
    for s in PROFILES['sources']:assert hashlib.sha256((KIT/s['file']).read_bytes()).hexdigest()==s['sha256']

def test_acceptance_must_link_to_each_requirement(state):
    _,p=state
    p['items'][0]['related_refs']=[]
    p['items'][2]['related_refs']=[]
    assert any('缺少关联验收' in issue for issue in gate(p))
