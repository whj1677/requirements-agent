"""Synthetic offline checks for shared retrieval and source/requirement boundaries."""
import copy
import asyncio
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault('RA_DATA_DIR', str(Path(__file__).resolve().parents[1] / 'evidence' / 'platform-knowledge-tests'))

import pytest

from app import business_draft
from app.budgets import context_usage
from app.core import Problem, dumps
from app.platform_knowledge import RELATIVE_PATH, attach, install, load, protected_system
from app.provider import DEFAULT, assemble
from app.store import Store


def package(version='test-v1'):
    ids = ['CORE', 'TARIFF', 'ASSETS', 'ALARMS', 'STRATEGY']
    b = dict(schema_version='1.0', bundle_id=version, generated_at='2026-10-12T00:00:00Z',
             project=dict(id='energy-storage-cloud', name='合成云平台'),
             source_snapshot=dict(repositories=[dict(id='repo', revision='unknown', dirty=None)], deployment='unknown'),
             overview='合成平台，尚未确认线上部署。常用业务：园区/电站、BMS/PCS监测、告警工单、电价收益、策略下发。下发请求不等于现场成功。',
             modules=[dict(id=mid, name=mid, summary=mid+'概览', claim_ids=['c-'+mid],
                           depends_on=['ASSETS'] if mid == 'TARIFF' else []) for mid in ids],
             claims=[dict(id='c-'+mid, module_ids=[mid], dimension='business', text=mid+' 合成现状',
                          origin='code_observation', evidence_ids=['e-'+mid]) for mid in ids],
             evidence=[dict(id='e-'+mid, repository_id='repo', path=mid+'.py', symbol=mid,
                            line_start=1, line_end=2, sha256='a'*64, kind='implementation') for mid in ids],
             unknowns=[dict(id='u1', question='电价口径未知？', module_ids=['TARIFF'])], conflicts=[],
             coverage=dict(inspected_modules=ids, indexed_modules=[], excluded=[], limitations=['合成静态样本']),
             technical_summary='合成架构')
    return b


def raw(b):
    return json.dumps(b, ensure_ascii=False).encode()


def decoded(messages):
    return json.loads(messages[1]['content'][0]['text'])


def test_shared_package_serves_projects_without_mutating_them(tmp_path):
    install(tmp_path, raw(package()))
    store = Store(tmp_path)
    for name in ['储能云平台电价维护', '另一个储能云平台项目']:
        p = store.create(name)
        before = copy.deepcopy(p)
        messages, selected, omitted = assemble(p, 'interview', '电价模板增加筛选', DEFAULT, tmp_path)
        knowledge = decoded(messages)['platform_knowledge']
        assert knowledge['bundle_id'] == 'test-v1'
        assert set(knowledge['retrieval']['included_claim_ids']) == {'c-CORE', 'c-TARIFF', 'c-ASSETS'}
        assert knowledge['evidence'][1]['sha256'] == 'a'*64
        assert '未' in knowledge['overview']
        assert 'source_refs' in knowledge['policy']
        assert selected == omitted == []
        assert p == before == store.get(p['id'])
        assert all('source_id' not in c for c in knowledge['claims'])


@pytest.mark.parametrize('name,message,expected', [
    ('储能云平台', '这是教育小程序的课程管理', None),
    ('教育课程管理', '整理权限需求', None),
    ('教育云平台', '教育云平台课程管理', None),
    ('储能云平台', '这个产品与云平台无关', None),
    ('普通需求', '新增权限筛选', 'ambiguous'),
    ('普通需求', '储能电站新增电价模板', 'domain'),
    ('普通需求', 'SOH 显示百分比', 'domain'),
    ('普通需求', '荷电状态曲线', 'domain'),
    ('普通需求', '能源管理系统下发策略', 'domain'),
])
def test_relevance_and_ambiguous_overview(tmp_path, name, message, expected):
    install(tmp_path, raw(package()))
    p = Store(tmp_path).create(name)
    ctx = decoded(assemble(p, 'interview', message, DEFAULT, tmp_path)[0])
    if expected is None:
        assert 'platform_knowledge' not in ctx
    else:
        k = ctx['platform_knowledge']
        assert k['classification'] == expected
        if expected == 'ambiguous':
            assert k['retrieval']['included_claim_ids'] == ['c-CORE']
            assert not k['retrieval']['matched_module_ids']


def test_draft_materials_can_identify_domain_without_project_title(tmp_path):
    install(tmp_path, raw(package()))
    p = Store(tmp_path).create('需求一')
    p['sources'] = [dict(id='SRC-1', title='原始资料', parse_status='read', purpose='business',
        excluded=False, image_mime=None, excerpts=[dict(id='EX-1', locator='正文1', text='储能云平台电价模板需求')])]
    before = copy.deepcopy(p)
    messages, selected, omitted = business_draft.assemble(p, 'ingest', '整理', DEFAULT, tmp_path)
    ctx = decoded(messages)
    assert ctx['platform_knowledge']['classification'] == 'domain'
    assert 'c-TARIFF' in ctx['platform_knowledge']['retrieval']['included_claim_ids']
    assert ctx['excerpts'][0]['text'] == p['sources'][0]['excerpts'][0]['text']
    assert selected[0]['id'] == 'EX-1' and not omitted
    assert p == before


def test_local_row_retrieval_does_not_expand_to_other_rows(tmp_path):
    install(tmp_path, raw(package()))
    p = dict(name='储能云平台', product_context={}, items=[
        dict(id='R1', title='电价模板', statement='筛选'),
        dict(id='R2', title='告警和策略', statement='其他业务')])
    ctx = {}
    attach(ctx, 'system', DEFAULT, tmp_path, p, '完善本行', 'clarify', scope=dict(requirement_id='R1'))
    k = ctx['platform_knowledge']
    assert 'c-TARIFF' in k['retrieval']['included_claim_ids']
    assert 'c-ALARMS' not in k['retrieval']['included_claim_ids']
    assert 'c-STRATEGY' not in k['retrieval']['included_claim_ids']


def test_explicit_project_domain_background_is_respected(tmp_path):
    install(tmp_path, raw(package()))
    p = dict(name='需求', product_context={}, domain_context=dict(background='储能云平台'))
    ctx = {}
    attach(ctx, '', DEFAULT, tmp_path, p, '维护模板', 'interview')
    assert ctx['platform_knowledge']['classification'] == 'domain'
    assert 'c-TARIFF' in ctx['platform_knowledge']['retrieval']['included_claim_ids']


@pytest.mark.parametrize('entry', ['interview', 'draft'])
def test_full_common_business_overview_is_inline_before_any_topic_details(tmp_path, entry):
    b = package()
    install(tmp_path, raw(b))
    p = Store(tmp_path).create('普通需求')
    for message in ('请帮我分析需求', '储能云平台电价模板', '储能云平台告警列表'):
        if entry == 'interview':
            messages, _, _ = assemble(p, 'interview', message, DEFAULT, tmp_path)
        else:
            messages, _, _ = business_draft.assemble(p, 'ingest', message, DEFAULT, tmp_path)
        k = decoded(messages)['platform_knowledge']
        # Common business content is complete even if there is no topic hit.
        assert k['overview'] == b['overview']
        assert k['retrieval']['baseline_delivery'] == 'inline_overview'
        assert '不为这些常用信息再次检索知识包或源码' in messages[0]['content']
        assert decoded(messages)['excerpts'] == []


def test_budget_keeps_complete_user_material_and_omits_background(tmp_path):
    p = Store(tmp_path).create('储能云平台')
    p['sources'] = [dict(id='SRC-1', title='资料', parse_status='read', purpose='business',
        excluded=False, image_mime=None, excerpts=[dict(id='EX-1', locator='正文', text='用户决定'*1000)])]
    baseline, sources, _ = business_draft.assemble(p, 'ingest', '电价模板', DEFAULT, tmp_path)
    size = context_usage(baseline, dict(DEFAULT, budget_mode='fixed'))['estimated_input']
    install(tmp_path, raw(package()))
    cfg = dict(DEFAULT, budget_mode='fixed', context_chars=size + DEFAULT['max_tokens']*4 + 100)
    actual, selected, omitted = business_draft.assemble(p, 'ingest', '电价模板', cfg, tmp_path)
    assert actual == baseline and sources == selected and not omitted


@pytest.mark.parametrize('entry', ['interview', 'draft'])
def test_all_basic_concepts_are_inline_without_topic_or_document_claim_lookup(tmp_path, entry):
    b = package()
    concepts = ('【基础概念参考】BMS = Battery Management System；'
                'PCS = Power Conversion System；EMS = Energy Management System；'
                'SOC = State of Charge；SOH = State of Health；'
                '储能云平台及彼此关系：通用职责说明不证明本公司具备对应能力。')
    b['overview'] = concepts + '\n【本平台源码现状】' + b['overview']
    # Reference definitions are retained in the bundle, never relabelled as code facts.
    b['modules'][0]['claim_ids'].append('g-reference')
    b['claims'].append(dict(id='g-reference', module_ids=['CORE'], dimension='domain_reference',
                           text=concepts, origin='document_claim', evidence_ids=['e-reference']))
    b['evidence'].append(dict(id='e-reference', repository_id='repo', path='concepts.md',
                             symbol='基础概念', line_start=1, line_end=2, sha256='b'*64,
                             kind='documentation'))
    install(tmp_path, raw(b))
    p = Store(tmp_path).create('普通需求')
    before = copy.deepcopy(p)
    for message in ('分析这个需求', '储能云平台电价模板'):
        if entry == 'interview':
            messages, selected, omitted = assemble(p, 'interview', message, DEFAULT, tmp_path)
        else:
            messages, selected, omitted = business_draft.assemble(p, 'ingest', message, DEFAULT, tmp_path)
        k = decoded(messages)['platform_knowledge']
        assert k['overview'] == b['overview'] and concepts in k['overview']
        assert k['role'] == 'shared_domain_background'
        assert 'g-reference' not in k['retrieval']['included_claim_ids']
        assert all(c['origin'] == 'code_observation' for c in k['claims'])
        assert '基础概念属于通用参考' in messages[0]['content']
        assert '无需再次检索外部资料' in messages[0]['content']
        assert selected == omitted == [] and p == before == Store(tmp_path).get(p['id'])


def test_topic_detail_budget_edge_keeps_baseline_and_accounts_for_omission_list(tmp_path):
    b = package()
    b['claims'][2]['origin'] = 'inference'
    install(tmp_path, raw(b))
    p = dict(name='储能云平台', product_context={})
    reference = {}
    attach(reference, 'system', DEFAULT, tmp_path, p, '电价模板', 'interview')
    assert reference['platform_knowledge']['retrieval']['included_claim_ids'] == ['c-CORE', 'c-TARIFF']
    # This allowance can hold two details before accounting for one omitted ID.
    size = context_usage([dict(role='system', content=protected_system('system')),
                          dict(role='user', content=[dict(type='text', text=dumps(reference))])],
                         DEFAULT)['estimated_input']
    cfg = dict(DEFAULT, budget_mode='fixed', context_chars=DEFAULT['max_tokens'] * 4 + 1000 + size + 8)
    b['bundle_id'] = 'test-v2'
    b['claims'][2].update(origin='code_observation', text='大段合成详情' * 800)
    install(tmp_path, raw(b))
    ctx = {}
    attach(ctx, 'system', cfg, tmp_path, p, '电价模板', 'interview')
    k = ctx['platform_knowledge']
    assert k['overview'] == b['overview']
    assert 'c-ASSETS' in k['retrieval']['omitted_claim_ids']
    actual_size = context_usage([dict(role='system', content=protected_system('system')),
                                dict(role='user', content=[dict(type='text', text=dumps(ctx))])],
                               cfg)['estimated_input']
    assert actual_size <= size + 8


@pytest.mark.parametrize('entry', ['interview', 'draft'])
def test_user_architecture_update_is_inline_with_its_document_identity(tmp_path, entry):
    b = package()
    install(tmp_path, raw(b))
    p = Store(tmp_path).create('储能云平台')
    frozen = assemble(p, 'interview', '数据接入', DEFAULT, tmp_path)[0]
    saved = copy.deepcopy(frozen)
    b['bundle_id'] = 'test-v2'
    topology = '【用户确认架构】EMS→EMQX→CRRCLink-RAW-DATA→CRRCLink；物联网Redis/IoTDB；MEASURING→云平台Redis；SIGNALING→MySQL。'
    b['overview'] += '\n' + topology
    b['modules'][0]['claim_ids'].append('a-confirmed')
    b['claims'].append(dict(id='a-confirmed', module_ids=['CORE'], dimension='user_confirmed_architecture',
                           text=topology, origin='document_claim', evidence_ids=['a-evidence']))
    b['evidence'].append(dict(id='a-evidence', repository_id='repo', path='user-architecture.md',
                             symbol='用户图', line_start=1,line_end=1,sha256='b'*64,kind='documentation'))
    install(tmp_path, raw(b))
    if entry == 'interview':
        messages, _, _ = assemble(p, 'interview', '电价模板', DEFAULT, tmp_path)
    else:
        messages, _, _ = business_draft.assemble(p, 'ingest', '电价模板', DEFAULT, tmp_path)
    k = decoded(messages)['platform_knowledge']
    assert topology in k['overview'] and k['overview'] == b['overview']
    assert 'a-confirmed' not in k['retrieval']['included_claim_ids']
    assert '用户确认架构' in messages[0]['content']
    assert frozen == saved and topology not in decoded(frozen)['platform_knowledge']['overview']
    assert not p['sources']


def test_inference_and_conflicting_claims_not_used_as_facts(tmp_path):
    b = package()
    b['claims'][1]['origin'] = 'inference'
    b['conflicts'] = [dict(id='conflict', description='合成冲突', claim_ids=['c-ALARMS', 'c-STRATEGY'])]
    install(tmp_path, raw(b))
    p = dict(name='储能云平台', product_context={})
    ctx = {}
    attach(ctx, '', DEFAULT, tmp_path, p, '电价模板告警策略', 'interview')
    assert not {'c-TARIFF', 'c-ALARMS', 'c-STRATEGY'} & set(ctx['platform_knowledge']['retrieval']['included_claim_ids'])


def test_versions_are_immutable_and_existing_requests_remain_frozen(tmp_path):
    one = raw(package())
    install(tmp_path, one)
    p = Store(tmp_path).create('储能云平台')
    first = assemble(p, 'interview', '电价模板', DEFAULT, tmp_path)[0]
    saved = copy.deepcopy(first)
    two = package('test-v2'); two['claims'][1]['text'] = '新的合成观察'
    install(tmp_path, raw(two))
    second = assemble(p, 'interview', '电价模板', DEFAULT, tmp_path)[0]
    assert first == saved
    assert decoded(first)['platform_knowledge']['sha256'] == hashlib.sha256(one).hexdigest()
    assert decoded(second)['platform_knowledge']['bundle_id'] == 'test-v2'
    assert (tmp_path / RELATIVE_PATH.parent / 'versions/test-v1/business-context.json').read_bytes() == one
    two['claims'][1]['text'] = '偷偷覆盖同一版本'
    with pytest.raises(Problem):
        install(tmp_path, raw(two))
    assert load(tmp_path)[1]['bundle_id'] == 'test-v2'
    install(tmp_path, one)  # Explicit rollback, retaining v2.
    assert load(tmp_path)[1]['bundle_id'] == 'test-v1'


def test_bad_package_and_other_stages_do_not_leak_context(tmp_path):
    p = dict(name='储能云平台', product_context={})
    dest = tmp_path / RELATIVE_PATH
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b'invalid json')
    ctx = {}
    attach(ctx, '', DEFAULT, tmp_path, p, '', 'interview')
    assert ctx == {'platform_knowledge_status': {'state':'invalid', 'notice':'共享知识包无法读取或校验失败，未使用其中内容'}}
    install(tmp_path / 'clean', raw(package()))
    for stage in ('review', 'vision', 'decision_semantics'):
        ctx = {}
        attach(ctx, '', DEFAULT, tmp_path / 'clean', p, '', stage)
        assert ctx == {}


def test_status_api_reads_isolated_package(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    install(tmp_path, raw(package()))
    with TestClient(create_app(tmp_path)) as client:
        response = client.get('/api/platform-knowledge')
        assert response.status_code == 200
        assert response.json()['bundle_id'] == 'test-v1'
        assert response.json()['source_snapshot']['deployment'] == 'unknown'
        assert len(response.json()['modules']) == 5


def test_workflow_records_exact_package_identity_without_network(tmp_path):
    from app.provider import origin
    from app.workflow import Workflow
    from tests.test_runtime import FakeProvider, empty_ingest

    async def run():
        install(tmp_path, raw(package()))
        store = Store(tmp_path)
        p = store.create('储能云平台')
        with store.edit(p['id'], 0, '合成接收端授权', bump=False) as (p, db):
            p['grants'][origin(DEFAULT)] = dict(source_ids=[])
        provider = FakeProvider([empty_ingest()])
        workflow = Workflow(store, provider)
        task = workflow.start(p['id'], 0, 'ingest', '电价模板')
        await workflow.tasks[task['id']]
        record = store.get_record(p['id'], task['id'], 'run')
        assert record['status'] == 'succeeded'
        identity = record['authorization']['platform_knowledge']
        assert identity['sha256'] == hashlib.sha256(raw(package())).hexdigest()
        assert identity['bundle_id'] == 'test-v1'
        assert identity['retrieval']['included_claim_ids'] == ['c-CORE', 'c-TARIFF', 'c-ASSETS']
        assert record['source_snapshot']['references'] == []
        assert '共享知识使用规则' in provider.received[0][0]['content']
        assert not store.get(p['id'])['sources']

    asyncio.run(run())
