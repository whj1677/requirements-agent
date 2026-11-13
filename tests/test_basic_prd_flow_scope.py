"""Fixed alarm/handover source-scope cases; generated-model semantics need live review."""
import copy
import json
import pytest

from app.prd import FLOW_RULES, validate_flowchart
from app.provider import DEFAULT, assemble
from app.requirement_confirmation import row_fingerprint
from tests.test_detail_document import project_fixture


def test_unique_endpoint_labels_are_normalized_without_changing_graph_content(tmp_path):
    from app.prd import normalize_flow_endpoint_labels
    p,requirement,_=basic_project(tmp_path)
    flow=dict(nodes=[dict(id='n1',label='开始',kind='start'),dict(id='n2',label='结束',kind='end')],
              edges=[dict(id='e1',**{'from':'开始','to':'结束'},condition='原分支条件')])
    plan=dict(plan_version='4',flowcharts=[dict(requirement_id=requirement['id'],flow=flow)])
    before=copy.deepcopy(plan)
    fixed,changes=normalize_flow_endpoint_labels(plan)
    normalized=fixed['flowcharts'][0]['flow']
    assert normalized['nodes']==flow['nodes'] and plan==before
    assert normalized['edges']==[dict(id='e1',**{'from':'n1','to':'n2'},condition='原分支条件')]
    assert len(changes)==2 and validate_flowchart(normalized,requirement,p)==normalized
    assert normalize_flow_endpoint_labels(fixed)==(fixed,[])


@pytest.mark.parametrize('fault',['ambiguous','unknown','duplicate_id'])
def test_endpoint_recovery_never_guesses_or_drops_invalid_branches(tmp_path,fault):
    from app.prd import normalize_flow_endpoint_labels
    from app.core import Problem
    p,requirement,_=basic_project(tmp_path)
    flow=dict(nodes=[dict(id='n1',label='开始',kind='start'),dict(id='n2',label='结束',kind='end')],
              edges=[dict(id='e1',**{'from':'开始','to':'结束'},condition='必须保留')])
    if fault=='ambiguous':flow['nodes'].append(dict(id='n3',label='结束',kind='end'))
    elif fault=='unknown':flow['edges'][0]['to']='不存在'
    else:flow['nodes'][1]['id']='n1'
    plan=dict(plan_version='4',flowcharts=[dict(requirement_id=requirement['id'],flow=flow)])
    fixed,changes=normalize_flow_endpoint_labels(plan)
    assert fixed==plan and changes==[]
    with pytest.raises(Problem) as error:validate_flowchart(flow,requirement,p)
    assert error.value.code=='REFERENCE_INVALID'


@pytest.mark.parametrize('charts',[None,{},['invalid'],[dict(flow=None)]])
def test_endpoint_normalizer_leaves_malformed_transport_for_schema_validation(charts):
    from app.prd import normalize_flow_endpoint_labels
    plan=dict(plan_version='4',flowcharts=charts)
    assert normalize_flow_endpoint_labels(plan)==(plan,[])


def basic_project(tmp_path):
    p = project_fixture(tmp_path)
    first = p['items'][0]
    first.pop('detail_contract', None)
    first.pop('detail_required', None)
    first['statement'] = '告警详情中新增处理记录。告警新增失败提示原因并保留输入，可手工重试。'
    first['behavior'] = dict(flow='查看告警记录；用户主动选择新增或修改。',
        exceptions='两种对象保存失败保留输入，修改失败保留原数据和输入。')
    second = copy.deepcopy(first)
    second.update(id='REQ-handover', title='交接备注', statement='场站交接页新增交接备注。交接备注允许查看、新增和修改。')
    second['behavior']['flow'] = '查看交接备注；用户主动选择新增或修改。'
    p['items'].append(second)
    for row in (first, second):
        row['requirement_confirmation']['content_hash'] = row_fingerprint(p, row)
    return p, first, second


def test_actual_prd_wire_shares_action_scope_rules_and_keeps_distinct_originals(tmp_path):
    p, alarm, handover = basic_project(tmp_path)
    before = copy.deepcopy(p)
    messages, _, _ = assemble(p, 'prd', '', DEFAULT, tmp_path)
    header = json.loads(messages[0]['content'].split('可信任务头：', 1)[1])
    assert FLOW_RULES in header['plan_contract']['rules']
    assert FLOW_RULES in header['schema']['properties']['flowcharts']['description']
    # The real prompt carries both the positive source and its non-transfer scope.
    wire = json.dumps(messages, ensure_ascii=False)
    assert alarm['statement'] in wire and handover['statement'] in wire
    assert '同一业务对象、同一动作及同一前提' in wire
    assert '提示原因、保留输入、保留原数据和允许重试分别核对' in wire
    assert '某对象新增失败的专有规则不得迁给另一对象或修改失败' in wire
    assert '空态与列表态都不能强制接续新增或修改' in wire
    assert '保留已明确的合法写入分支' in wire
    assert header['plan_contract']['version'] == 'prd-plan-contract-17'
    assert '不能强加给正常首次执行或正常上电路径' in wire
    assert '先判是否需要恢复，再判断恢复计时' in wire
    assert '不能因新增位于某页就推定查看或修改也在该页' in wire
    assert 'start及分流前所有公共节点都使用不带页面的业务对象名' in wire
    assert '页面名只放在新增分支内' in wire
    assert '不为未知位置新增必须由用户决定的业务问题' in wire
    assert '不将字段清单、每项必填或长度规则逐条展开为节点' in wire
    assert '不同动作的反馈或权限不同则不得为精简而合并' in wire
    assert '不为追求节点数量删去真实成功、失败、未知结果、查看结束或有据重试路径' in wire
    assert p == before


def test_valid_view_terminal_and_source_supported_write_branches_are_not_deleted(tmp_path):
    p, alarm, handover = basic_project(tmp_path)
    for requirement in (alarm, handover):
        nodes = [dict(id='s', label='查看记录', kind='start'),
            dict(id='view', label='展示空态或列表', kind='decision'),
            dict(id='end', label='查看结束', kind='end'),
            dict(id='new', label='告警详情新增保存' if requirement is alarm else '场站交接页新增保存', kind='action'),
            dict(id='modify', label='修改保存', kind='action'),
            dict(id='new_fail', label='提示原因并保留输入' if requirement is alarm else '保留输入', kind='action'),
            dict(id='modify_fail', label='保留原数据和输入', kind='action')]
        connections = [('s','view',''), ('view','end','仅查看'), ('view','new','用户选择新增'),
            ('view','modify','用户选择修改'), ('new','new_fail','失败'), ('modify','modify_fail','失败'),
            ('new_fail','end','结束本次操作'), ('modify_fail','end','结束本次操作')]
        if requirement is alarm:
            connections.append(('new_fail','new','用户手工重试'))
        flow = dict(nodes=nodes, edges=[dict(id='e'+str(n), **{'from':a,'to':b}, condition=c)
            for n,(a,b,c) in enumerate(connections)])
        validated = validate_flowchart(flow, requirement, p)
        assert validated == flow
        # These are frozen expected examples, not a substitute for model review:
        # handover failure and either modify failure have no copied retry/reason.
        assert next(n for n in validated['nodes'] if n['id']=='modify_fail')['label']=='保留原数据和输入'
        if requirement is handover:
            assert all('提示原因' not in n['label'] for n in validated['nodes'])
            assert all('重试' not in e['condition'] for e in validated['edges'])
