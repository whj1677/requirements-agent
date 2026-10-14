"""Translate detailed business candidates without granting approval.

The model sees local references. Stable server identities, confirmation state and
source validation remain owned by the existing application.
"""
import copy

from .core import require


PROMPT = '''
详细需求输出契约：detail_required=true 时，每行给 detail_contract；仅输出 schema 的键，不输出 index、审批状态、图片或 HTML。
这是待人工核对的业务定义。按本期实际功能组织 fields/interactions/flow/state_transitions；无关部分在 applicability 写 false 和理由，集合为空；不得省略已有详细定义来删除它。
detail_contract 已展开的规则，behavior 七个维度仅保留必要概述，不重复整张属性表；不要求为了篇幅或填空制造新的内容。
图、文、表由程序统一排版。节点 label 只写一个清楚的业务动作或判断，条件、禁止操作和异常处理放对应 edge.condition、交互或状态属性，不把整段规则塞进节点括号；不得为图短小删除真实分支。交互 name 用具体动作名，不反复使用整个功能名称。
验收每项表达一个可观察结果，用具体场景起句；已存在且口径不变的验收用已有 C# 引用，不另生成同义验收。确需改变已有口径时明确提出修订候选，不能把原条款和新条款都描述成已确定且同时生效。已有问题已覆盖同一决定时沿用，不换句话重复提问。未知值保留待确认，不能用样例数字换取简洁。
fields 仅列本期实际输入、显示或业务结果数据。规则参数（例如未定的查询间隔或重试上限）写在所属交互属性与对应 questions 中；没有配置页面就不要把参数变成字段，继而追问可见性、可编辑性等无关属性。
必备属性按 schema 逐一给出。数字补 unit/precision，枚举给 enum_values，时间给 time_basis，计算来源用 calculated 并给 formula。其他可选属性无关时省略，不凭空制造唯一性、加载方式等业务决定。
每个实际业务属性用 evidence 区分 source/proposal/unknown。source 引用本轮真实 sources，并核对资料是否支持这一具体结论；权限规则不能证明入口隐藏、角色名称不能证明重试操作者。新增方案标 proposal，样例数字不变成限制。
同一未定参数只提出一个独立问题：已知含义保持已知，仅未定值/策略所在属性标 unknown，并写明同一问题影响的位置。不把同一未知复制到无关属性，不把 unknown 改成临时实现指令；真正不同的业务决定不得为减少问题数而合并或隐藏。
evidence 的键只对应本对象已有属性：例如 "validation":"待确认：单次上限", "evidence":{"validation":{"status":"unknown","reason":"单次上限未决定"}}。不能写 evidence.unknown 或用一个 evidence.source 代表全部属性。
简单编辑用一个完整交互：打开与回显写 entry，保存前放弃写 cancel，提交中/重复提交写 pending/repeat；独立输入和结果才拆另一个交互。本地取消无需制造“取消失败/重试次数”问题。
流程用 nodes 和 edges；判断出边写 condition，等待节点须给查询成功、确定失败及仍未知的后续路径。保存前取消从编辑节点分支，提交之后不得又宣称未提交。图、交互、状态表和验收描述同一规则。
flow 对象只能含 nodes 和 edges，不得添加 flow.evidence；来源逐项放在节点或连线的 evidence。evidence 的 status 只能为 source、proposal、unknown，建议使用 proposal，不能使用 recommendation，也不能把 status/reason/sources 写到 evidence 容器这一层。
批量业务若对象独立处理：记录单个对象失败后继续汇总，仅全部结果确定才判定全成功/部分成功/全失败；未决不能当失败。“可重试”保留退出分支，不画成必须重试；再次重试仍允许失败或未决。只有资料支持才应用这些规则，不扩展无关场景。
状态变化说明所属业务对象，不把批次结果、通知状态和后续处理阶段混成一套枚举；prohibited_actions 只写有依据或明确标建议的限制。
保留已有 id；新对象用 F1/I1/N1/E1/T1。field_refs 用字段 id，depends_on 用 R#，rule_refs/已有验收用 C#，本行新增验收用 A1/A2 对应 acceptance 顺序。
节点形状：{"id":"N1","label":"步骤说明","kind":"action","actor":"执行者","interaction_id":"I1","evidence":{...}}。
连线形状：{"id":"E1","from":"N1","to":"N2","condition":"转移条件","evidence":{"condition":{...}}}；interaction_id 只在节点，连线没有 label，也没有 index。这些仅为结构示意，须替换为本期真实内容，不能直接复制。
输出前核对结构完整、来源真实、所有未知可定位、判断/等待有出路，以及正文/图/表/验收之间无互相矛盾；不得用来源定位有效代替业务或人工确认。
'''


def normalize_redundant_metadata(value):
    """Remove only provably redundant output noise, retaining exact diagnostics.

    This is not a general JSON repair. Unknown keys, misplaced relationships,
    conflicting references and all business text remain subject to the normal
    validators. The caller also retains the entire original model row.
    """
    detail, diagnostics = copy.deepcopy(value), []
    if not isinstance(detail, dict):
        return detail, diagnostics
    flow = detail.get('flow')
    flow = flow if isinstance(flow, dict) else {}
    collections = [(section, detail.get(section)) for section in
                   ('fields', 'interactions', 'state_transitions')]
    collections += [('flow.' + section, flow.get(section)) for section in ('nodes', 'edges')]

    def remove(row, section, key, reason):
        diagnostics.append(dict(path='detail_contract.' + section + '.' + str(row.get('id', '')) + '.' + key,
            code='BUSINESS_DRAFT_DETAIL_REDUNDANT_METADATA', raw=copy.deepcopy(row[key]), reason=reason))
        del row[key]

    for section, rows in collections:
        if not isinstance(rows, list):
            continue
        for position, row in enumerate(rows, 1):
            if (isinstance(row, dict) and type(row.get('index')) is int and row['index'] == position
                    and isinstance(row.get('evidence', {}), dict) and 'index' not in row.get('evidence', {})):
                remove(row, section, 'index', '多余 index 与数组顺序完全一致；保留原始值，仅去除重复序号，未改业务内容')

    nodes, edges = flow.get('nodes'), flow.get('edges')
    interactions = detail.get('interactions')
    if not all(isinstance(rows, list) for rows in (nodes, edges, interactions)):
        return detail, diagnostics
    node_ids = [row.get('id') for row in nodes if isinstance(row, dict)]
    by_id = {row['id']: row for row in nodes if isinstance(row, dict)
             and isinstance(row.get('id'), str) and node_ids.count(row['id']) == 1}
    interaction_ids = [row.get('id') for row in interactions if isinstance(row, dict)]
    for edge in edges:
        if not isinstance(edge, dict) or not isinstance(edge.get('evidence', {}), dict):
            continue
        relation = edge.get('interaction_id')
        if (isinstance(relation, str) and relation and interaction_ids.count(relation) == 1
                and 'interaction_id' not in edge.get('evidence', {})
                and isinstance(edge.get('from'), str) and isinstance(edge.get('to'), str)
                and by_id.get(edge['from'], {}).get('interaction_id') == relation
                and by_id.get(edge['to'], {}).get('interaction_id') == relation):
            remove(edge, 'flow.edges', 'interaction_id',
                   '连线的交互引用与两端已有节点完全一致；原值保留，仅去除重复引用，未移动或推断业务关系')
    return detail, diagnostics


def model_schema(*, generation=False):
    from .requirement_details import DETAIL_SCHEMA, FIELD_PROPERTIES, INTERACTION_PROPERTIES, TRANSITION_PROPERTIES
    schema = copy.deepcopy(DETAIL_SCHEMA)

    def walk(value):
        if isinstance(value, dict):
            props = value.get('properties')
            if isinstance(props, dict) and 'source_refs' in props:
                props['sources'] = dict(type='array', maxItems=50, items=dict(
                    type='object', additionalProperties=False,
                    properties={'source': {'type':'string'}, 'locator': {'type':'string'}},
                    required=['source', 'locator']))
                props.pop('source_refs')
                if 'required' in value:
                    value['required'] = ['sources' if k == 'source_refs' else k for k in value['required']]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)
    if generation:
        collections = [(schema['properties']['fields']['items'], FIELD_PROPERTIES, FIELD_PROPERTIES),
            (schema['properties']['interactions']['items'], INTERACTION_PROPERTIES, INTERACTION_PROPERTIES),
            (schema['properties']['state_transitions']['items'], TRANSITION_PROPERTIES, TRANSITION_PROPERTIES),
            (schema['properties']['flow']['properties']['nodes']['items'], ('label','kind','actor'), ('label','actor')),
            (schema['properties']['flow']['properties']['edges']['items'], ('from','to','condition'), ('condition',))]
        for item, required, evidence in collections:
            item['required'] = ['id', *required, 'evidence']
            props = item['properties']
            props['evidence']['propertyNames'] = {'enum':[name for name in props if name not in ('id','evidence')]}
            # Graph identifiers are generated links, not source-backed business
            # facts. Require evidence for the condition/meaning rather than
            # repeatedly claiming that the source contains generated node IDs.
            props['evidence']['required'] = list(evidence)
        schema['properties']['fields']['description'] = '仅实际输入、显示和业务结果字段；未定规则参数放对应交互和问题，不造配置项'
        schema['properties']['interactions']['description'] = '按独立输入和处理结果组织；同一编辑过程不机械拆开打开、保存、取消'
    return schema


def require_reference_aliases(value, available_ids):
    """Do not let a model round trip erase unavailable saved relationships.

    Unavailable references need an explicit human edit of the saved definition.
    Failing before assembly also avoids spending a model call on hidden input.
    """
    available = set(available_ids)
    missing = []

    def walk(part, path):
        if isinstance(part, list):
            for index, child in enumerate(part):
                identity = child.get('id', index) if isinstance(child, dict) else index
                walk(child, path + '.' + str(identity))
        elif isinstance(part, dict):
            for key, child in part.items():
                location = path + '.' + key
                if key in ('depends_on', 'rule_refs', 'acceptance_refs') and isinstance(child, list):
                    absent = [ref for ref in child if ref not in available]
                    if absent:
                        missing.append(location + '：' + '、'.join(absent))
                else:
                    walk(child, location)
    walk(value, 'detail_contract')
    require(not missing, 'BUSINESS_DRAFT_DETAIL_REFERENCE',
            '已有详细定义含本轮无法引用的关联，请先在详细需求中明确核对或移除；原定义保留，不交由 AI 静默删除：'
            + '；'.join(missing[:8]) + (f'；另有 {len(missing)-8} 处' if len(missing) > 8 else ''))


def canonical_detail(value, source_aliases, item_aliases, *, existing_detail=None):
    if existing_detail is not None:
        require_reference_aliases(existing_detail, item_aliases.values())
    def walk(part):
        if isinstance(part, list):
            return [walk(child) for child in part]
        if not isinstance(part, dict):
            return copy.deepcopy(part)
        result = {}
        for key, child in part.items():
            if key == 'sources':
                refs = []
                for ref in child:
                    pair = (ref.get('source'), ref.get('locator'))
                    require(pair in source_aliases, 'BUSINESS_DRAFT_SOURCE',
                            '详细定义引用了本轮不存在的资料位置：' + str(pair))
                    if source_aliases[pair] not in refs:
                        refs.append(copy.deepcopy(source_aliases[pair]))
                result['source_refs'] = refs
            elif key in ('depends_on', 'rule_refs', 'acceptance_refs') and isinstance(child, list):
                require(all(ref in item_aliases for ref in child), 'BUSINESS_DRAFT_DETAIL_REFERENCE',
                        '详细定义只能引用当前输入条目或本行有内容的验收')
                result[key] = [item_aliases[ref] for ref in child]
            else:
                result[key] = walk(child)
        if result.get('status') == 'source':
            require(bool(result.get('source_refs')), 'BUSINESS_DRAFT_SOURCE',
                    '详细属性标为资料明确时必须附真实来源')
        return result
    detail = walk(value)
    from .requirement_details import validate_detail
    validate_detail(detail)
    return detail


def input_detail(value, source_aliases, item_aliases):
    require_reference_aliases(value, item_aliases)
    def walk(part):
        if isinstance(part, list):
            return [walk(child) for child in part]
        if not isinstance(part, dict):
            return copy.deepcopy(part)
        result = {}
        for key, child in part.items():
            if key == 'source_refs':
                result['sources'] = [copy.deepcopy(source_aliases[(r['source_id'],r['excerpt_id'])])
                    for r in child if (r['source_id'],r['excerpt_id']) in source_aliases]
            elif key in ('depends_on','rule_refs','acceptance_refs') and isinstance(child, list):
                result[key] = [item_aliases[ref] for ref in child]
            else:
                result[key] = walk(child)
        # A source excluded from this task remains unresolved, never a new fact.
        if result.get('status') == 'source' and not result.get('sources'):
            result['status'] = 'unknown'
            result['reason'] = '原引用不在本次资料范围内，需核对来源'
        return result
    return walk(value)


def source_refs(detail):
    refs = []
    def walk(part):
        if isinstance(part, dict):
            for key, child in part.items():
                if key == 'source_refs':
                    for ref in child:
                        if ref not in refs:
                            refs.append(copy.deepcopy(ref))
                else:
                    walk(child)
        elif isinstance(part, list):
            for child in part:
                walk(child)
    walk(detail)
    return refs


def validate_candidate_links(detail, kinds, owner_id=None):
    """Candidates may reference candidates, but never dangling/wrong-kind IDs."""
    expected = {'depends_on': 'requirement', 'rule_refs': 'rule', 'acceptance_refs': 'acceptance'}
    def walk(part):
        if isinstance(part, dict):
            for key, child in part.items():
                if key in expected and isinstance(child, list):
                    require(all(kinds.get(ref) == expected[key] and not (key == 'depends_on' and ref == owner_id)
                                for ref in child), 'DETAIL_ITEM_REFERENCE',
                            '详细定义引用了不存在、类型错误或自我依赖的条目：' + key)
                else:
                    walk(child)
        elif isinstance(part, list):
            for child in part:
                walk(child)
    walk(detail)
