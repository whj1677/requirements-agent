"""Offline protocol/controller checks; semantic verdicts are synthetic fixtures."""
import asyncio,copy,json
import pytest
from app import intake_grounding as g
from app.core import Problem,dumps
from app.workflow import Workflow
from app.business_draft import SUMMARY_FIELDS
from tests.test_business_draft import packet,draft_row,business_value
from tests.test_intake_grounding import fixture,GroundingProvider
from tests.test_requirement_recommendation import project


def coverage_reply(plan):
    return dict(candidate_hash=plan['candidate_hash'],input_hash=plan['input_hash'],
        checked_input_ids=[row['id'] for row in plan['inputs']],
        checked_question_ids=[field['id'] for field in plan['fields'] if field['question']],findings=[])


def test_coverage_accepts_semantic_verdict_without_literal_success_targets():
    raw=packet(draft_row());raw['requirements'][0]['statement']=business_value('本次范围不包含审批功能。','fact')
    raw['requirements'][0]['questions']=[dict(text='两份材料中的释放权限规则冲突，采用哪一项？',reason='用户要求保留未决',blocking=True,sources=[],recommendations=[])]
    context=dict(excerpts=[dict(source='S1',locator='L1',text='本期不新增审批。')],
        user_message='先整理初稿，释放权限冲突保留待定，不替我选择。')
    plan=g.prepare(raw,context);reply=coverage_reply(plan);before=copy.deepcopy(reply)
    assert g.validate_coverage(plan,reply)['status']=='passed'
    assert reply==before and 'targets' not in dumps(reply)
    messages=g.coverage_messages(plan);payload=json.loads(messages[1]['content'][0]['text'])
    assert payload['context']==context and payload['candidate']==raw
    assert payload['questions'][0]['text']==raw['requirements'][0]['questions'][0]['text']


def test_missing_negative_range_restores_frozen_input_not_model_quote():
    plan=fixture();reply=coverage_reply(plan)
    reply['findings']=[dict(kind='input',id=plan['inputs'][0]['id'],reason='已答的禁止范围没有在候选正文实际表达。')]
    checked=g.validate_coverage(plan,reply)
    assert checked['status']=='blocked' and checked['findings'][0]['original']==plan['inputs'][0]
    assert 'quote' not in reply['findings'][0]


@pytest.mark.parametrize('mutation',['input_missing','question_missing','duplicate','hash','wrong_kind','invented','scope'])
def test_coverage_requires_exact_identity_and_complete_lists(mutation):
    plan=fixture()
    plan['candidate']['requirements'][0]['questions']=[dict(text='真实未决选择是什么？',reason='尚未回答',blocking=True,sources=[],recommendations=[])]
    plan=g.prepare(plan['candidate'],plan['context']);reply=coverage_reply(plan)
    if mutation=='input_missing':reply['checked_input_ids'].pop()
    if mutation=='question_missing':reply['checked_question_ids'].pop()
    if mutation=='duplicate':reply['checked_input_ids'].append(reply['checked_input_ids'][0])
    if mutation=='hash':reply['candidate_hash']='wrong'
    if mutation=='wrong_kind':reply['findings']=[dict(kind='question',id=plan['inputs'][0]['id'],reason='bad')]
    if mutation=='invented':reply['findings']=[dict(kind='input',id='I999',reason='bad')]
    if mutation=='scope':reply['scope']='changed'
    with pytest.raises(Problem):g.validate_coverage(plan,reply)


class PairProvider(GroundingProvider):
    def __init__(self,raw,mode):
        super().__init__(raw,'pass');self.pair_mode=mode;self.coverages=0;self.challenges=0;self.patch_findings=[]
    async def request(self,config,messages,evidence=None):
        h=json.loads(messages[0]['content'].split('可信任务头：',1)[1]);protocol=h.get('grounding_protocol')
        payload=json.loads(messages[1]['content'][0]['text']) if protocol else None
        if protocol==g.PATCH_VERSION:self.patch_findings.append(copy.deepcopy(payload['findings']))
        if protocol not in (g.COVERAGE_VERSION,g.CHALLENGE_VERSION):return await super().request(config,messages,evidence)
        if evidence:evidence.before_send(config)
        self.received.append(copy.deepcopy(messages))
        if protocol==g.COVERAGE_VERSION:
            self.coverages+=1;self.reviews+=1;result=coverage_reply(g.prepare(payload['candidate'],payload['context']))
            blocked=self.pair_mode in ('coverage','both') or self.pair_mode=='repair' and self.coverages==1 or self.pair_mode=='late_loss' and self.coverages in (1,3)
            if blocked:result['findings']=[dict(kind='input',id=payload['inputs'][0]['id'],reason='合成：已答禁止范围未保留')]
        else:
            self.challenges+=1
            result=dict(candidate_hash=payload['candidate_hash'],input_hash=payload['input_hash'],checked_field_ids=[f['id'] for f in payload['fields']],findings=[])
            blocked=self.pair_mode in ('source','both') or self.pair_mode=='repair' and self.challenges==1 or self.pair_mode=='late_loss' and self.challenges==2
            if blocked:
                field=next(f for f in payload['fields'] if f['path']==['requirements',0,'statement'])
                result['findings']=[dict(id=field['id'],quote=field['text'],reason='合成：动作的位置无自身来源支持',sources=field['sources'])]
        meta=dict(usage={'prompt_tokens':1,'completion_tokens':1},finish_reason='stop')
        if evidence:evidence.response(dumps(result),http_status=200,finish_reason='stop',usage=meta['usage'])
        return result,meta


@pytest.mark.parametrize('mode,expected_status,calls',[('coverage','blocked',9),('source','blocked',9),('both','blocked',9),('repair','repaired',6),('late_loss','blocked',9)])
def test_both_checks_run_and_findings_never_overwrite_each_other(tmp_path,mode,expected_status,calls):
    async def scenario():
        store,p=project(tmp_path);raw=packet(draft_row('R1'),draft_row('R2'))
        raw['summary_fields']={key:business_value() for key in SUMMARY_FIELDS}
        provider=PairProvider(raw,mode);workflow=Workflow(store,provider)
        run=workflow.start(p['id'],p['revision'],'ingest','请整理初稿')
        await workflow.tasks[run['id']];saved=store.get_record(p['id'],run['id'],'run')
        assert saved['intake_grounding']['status']==expected_status,saved
        assert saved['calls']==len(provider.received)==calls
        assert provider.coverages==provider.challenges==(2 if mode=='repair' else 3)
        if mode in ('both','repair'):
            assert {f['kind'] for f in provider.patch_findings[0]}=={'coverage_input','source_challenge'}
        if expected_status=='blocked':assert store.get(p['id'])==p and not saved.get('result_applied')
        else:assert saved['result_applied']
        records=[json.loads(path.read_text('utf8')) for path in (tmp_path/'evidence/model-calls').glob('*.json')]
        original=next(c for c in records if 'intake_grounding_history' in c)
        history=original['intake_grounding_history']
        assert sum(row['kind']=='coverage' for row in history)==provider.coverages
        assert sum(row['kind']=='source_challenge' for row in history)==provider.challenges
    asyncio.run(scenario())


def test_source_revision_changed_during_pair_cannot_commit_candidate(tmp_path):
    async def scenario():
        store,p=project(tmp_path);raw=packet(draft_row('R1'),draft_row('R2'))
        raw['summary_fields']={key:business_value() for key in SUMMARY_FIELDS}
        class MutatingProvider(PairProvider):
            async def request(self,config,messages,evidence=None):
                result,meta=await super().request(config,messages,evidence)
                h=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
                if h.get('grounding_protocol')==g.CHALLENGE_VERSION:
                    current=store.get(p['id'])
                    with store.edit(p['id'],current['revision'],'合成读取期间来源变化') as (changed,_):
                        changed['sources'][0]['excerpts'][0]['text']='新的来源原话'
                return result,meta
        provider=MutatingProvider(raw,'pass');workflow=Workflow(store,provider)
        run=workflow.start(p['id'],p['revision'],'ingest','请整理初稿')
        await workflow.tasks[run['id']];saved=store.get_record(p['id'],run['id'],'run')
        assert not saved.get('result_applied') and saved['status'] not in ('succeeded','partial','awaiting_user')
        assert store.get(p['id'])['items']==p['items']
        assert store.get(p['id'])['sources'][0]['excerpts'][0]['text']=='新的来源原话'
    asyncio.run(scenario())
