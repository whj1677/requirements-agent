import asyncio
import copy
import json
import pytest
from app.prd import preserve_repair_explanation_refs,compile_plan
from app.core import Problem
from tests.test_prd_structured_explanation_recovery import packet
from tests.test_detail_document import project_fixture


def pair(p):
    old=packet(p);old['sections'][0]['section_key']='users'
    old['sections'][0]['normative_refs']=['REQ-0001']
    new=copy.deepcopy(old);new['sections'][0]['explanations'][0]['evidence_refs']['normative_refs']=[]
    return old,new


def test_same_paragraph_restores_valid_refs_and_compiles_without_mutating_raw(tmp_path):
    p=project_fixture(tmp_path);old,new=pair(p);before=copy.deepcopy((p,old,new))
    old['sections'][0]['explanations'][0]['evidence_refs']['normative_refs'].append('INVALID')
    before=copy.deepcopy((p,old,new))
    fixed,changes=preserve_repair_explanation_refs(old,new,p,'prd')
    assert changes[0]['restored_refs']==['REQ-0001']
    assert fixed['sections'][0]['explanations'][0]['evidence_refs']['normative_refs']==['REQ-0001']
    compile_plan(fixed,p,'prd',include_sketch=False)
    assert (p,old,new)==before


@pytest.mark.parametrize('fault',['text','section','duplicate_old','duplicate_new','duplicate_section','mrd','initial','version','invalid_old','disallowed_section'])
def test_reference_recovery_boundaries(tmp_path,fault):
    p=project_fixture(tmp_path);old,new=pair(p);kind='prd'
    if fault=='text':new['sections'][0]['explanations'][0]['text']+=' '
    elif fault=='section':new['sections'][0]['section_key']='background'
    elif fault=='duplicate_old':old['sections'][0]['explanations']*=2
    elif fault=='duplicate_new':new['sections'][0]['explanations']*=2
    elif fault=='duplicate_section':new['sections']*=2
    elif fault=='mrd':kind='mrd'
    elif fault=='initial':old=None
    elif fault=='version':new['plan_version']='3'
    elif fault=='invalid_old':old['sections'][0]['explanations'][0]['evidence_refs']['normative_refs']=['INVALID']
    else:
        old['sections'][0]['section_key']=new['sections'][0]['section_key']='function'
    fixed,changes=preserve_repair_explanation_refs(old,new,p,kind)
    assert fixed==new and not changes


def test_new_invalid_reference_remains_a_validation_error(tmp_path):
    p=project_fixture(tmp_path);old,new=pair(p)
    new['sections'][0]['explanations'][0]['evidence_refs']['normative_refs']=['INVALID']
    fixed,changes=preserve_repair_explanation_refs(old,new,p,'prd')
    assert changes and 'INVALID' in fixed['sections'][0]['explanations'][0]['evidence_refs']['normative_refs']
    with pytest.raises(Problem):compile_plan(fixed,p,'prd',include_sketch=False)


def test_workflow_repair_audit_preserves_raw_and_restores_paragraph_refs(tmp_path):
    from tests.test_local_prd import setup
    from tests.test_runtime import FakeProvider
    from app.workflow import Workflow
    async def run():
        store,p=setup(tmp_path);old,new=pair(p)
        old['sections'].append(dict(section_key='rules',context_refs=['INVALID'],normative_refs=[],discussion_refs=[],explanations=[]))
        class Capture(FakeProvider):
            async def request(self,c,m,evidence=None):
                value,meta=await super().request(c,m,evidence)
                evidence.response(json.dumps(value,ensure_ascii=False),http_status=200,finish_reason='stop',usage={})
                return value,meta
        provider=Capture([old,new]);workflow=Workflow(store,provider)
        task=workflow.start(p['id'],p['revision'],'prd','',kind='prd')
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['status']=='succeeded',saved
        assert saved['repair_count']==1 and len(provider.received)==2
        entries=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert json.loads(entries[0]['final_output'])==old
        assert json.loads(entries[1]['final_output'])==new
        normalized=entries[1]['normalized_prd_plan']
        assert normalized['sections'][0]['explanations'][0]['evidence_refs']['normative_refs']==['REQ-0001']
        assert entries[1]['response_normalizations'][0]['changes'][0]['restored_refs']==['REQ-0001']
        assert store.get(p['id'])['items']==p['items']
    asyncio.run(run())


@pytest.mark.parametrize('failures',[
    ['SCHEMA_INVALID'],['OUTPUT_TRUNCATED'],['SCHEMA_INVALID','OUTPUT_TRUNCATED']])
def test_unparsed_prd_attempts_do_not_create_or_reuse_paragraph_anchor(tmp_path,monkeypatch,failures):
    from tests.test_local_prd import setup
    from tests.test_runtime import FakeProvider
    from app.workflow import Workflow
    import app.prd
    async def run():
        store,p=setup(tmp_path);valid,_=pair(p)
        # No attempt has produced a parsed plan, so reference restoration must
        # never run, including after two different provider/parser failures.
        def unexpected_anchor(*args,**kwargs):
            raise AssertionError('Unparsed response cannot provide a paragraph anchor')
        monkeypatch.setattr(app.prd,'preserve_repair_explanation_refs',unexpected_anchor)
        class Capture(FakeProvider):
            async def request(self,c,m,evidence=None):
                if len(self.received)<len(failures):
                    code=failures[len(self.received)];self.received.append(m)
                    evidence.response('{invalid' if code=='SCHEMA_INVALID' else '{"plan_version":"4","sections":[',
                        http_status=200,finish_reason='stop' if code=='SCHEMA_INVALID' else 'length',usage={})
                    raise Problem(code,'Synthetic parser/provider failure')
                value,meta=await super().request(c,m,evidence)
                evidence.response(json.dumps(value,ensure_ascii=False),http_status=200,finish_reason='stop',usage={})
                return value,meta
        provider=Capture([valid]);workflow=Workflow(store,provider)
        # Exercise the format-repair branch, not the separate token-capacity
        # escalation branch used before repair when a larger cap is available.
        config=workflow.config('prd');config.pop('retry_max_tokens',None)
        workflow.config=lambda stage:copy.deepcopy(config)
        task=workflow.start(p['id'],p['revision'],'prd','',kind='prd')
        await workflow.tasks[task['id']]
        saved=store.get_record(p['id'],task['id'],'run')
        assert saved['status']=='succeeded',saved
        assert len(provider.received)==len(failures)+1 and saved['repair_count']==len(failures)
        entries=[json.loads((store.folder/'evidence/model-calls'/(a['call_id']+'.json')).read_text('utf-8')) for a in saved['attempts']]
        assert [entry['validation_result'] for entry in entries[:-1]]==failures
        assert all('normalized_prd_plan' not in entry for entry in entries)
        assert json.loads(entries[-1]['final_output'])==valid
    asyncio.run(run())
