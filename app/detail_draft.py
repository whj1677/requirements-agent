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
fields.default_value 描述业务默认、未赋值及空值处理，不自动要求数据库默认值或精确显示占位文案。来源已明确某操作后的业务状态、或尚未发生对应业务事件时的空值处理时，在适用条件下写出已有的具体业务口径；不能由操作后状态反推初始默认值。仅涉及未定存储实现或显示占位、且不改变已有业务规则的限制，放在响应顶层 limitations 如实保留未知，不把它扩成整个属性缺少业务决定。若业务默认或空值处理本身仍未确定，本属性及 evidence 仍标 unknown，保留待确认阻塞，不能挪到 limitations 隐藏，也不能只换成 proposal 或填写“建议”来通过。不得编造初始值、存储 NULL、占位文字或迁移规则；资料支持已知业务口径，不等于支持这些技术取值。
必备属性按 schema 逐一给出。数字补 unit/precision，枚举给 enum_values，时间给 time_basis，计算来源用 calculated 并给 formula。其他可选属性无关时省略，不凭空制造唯一性、加载方式等业务决定。
每个实际业务属性用 evidence 区分 source/proposal/unknown。source 引用本轮真实 sources，并核对资料是否支持这一具体结论；权限规则不能证明入口隐藏、角色名称不能证明重试操作者。新增方案标 proposal，样例数字不变成限制。
若入口或前置条件已明确角色或状态成立，不能靠无来源的角色、归属或时点变化使相反条件成为可达场景。通用鉴权守卫与实际场景路径须分开说明；同时保留来源明确要求的原请求确定拒绝及恢复出口，不能由此推导请求必然成功。
同一未定参数只提出一个独立问题：已知含义保持已知，仅未定值/策略所在属性标 unknown，并写明同一问题影响的位置。不把同一未知复制到无关属性，不把 unknown 改成临时实现指令；真正不同的业务决定不得为减少问题数而合并或隐藏。
区分字段“是什么/用来做什么”和“由谁、何时、按什么规则产生”：已明确用原批次标识查询原提交结果时，meaning 保留这个已知用途；若标识生成方式未定，只把 source 中未定的产生方式标 unknown 并提问，不能连带把 meaning、名称或已知的只读属性也标 unknown。资料没有说明某属性时仍须如实待确认，不能由其他属性推导。
evidence 的键只对应本对象已有属性：例如 "validation":"待确认：单次上限", "evidence":{"validation":{"status":"unknown","reason":"单次上限未决定"}}。不能写 evidence.unknown 或用一个 evidence.source 代表全部属性。
简单编辑用一个完整交互：打开与回显写 entry，保存前放弃写 cancel，提交中/重复提交写 pending/repeat；独立输入和结果才拆另一个交互。本地取消无需制造“取消失败/重试次数”问题。
流程用 nodes 和 edges；判断出边写 condition，等待节点须给查询成功、确定失败及仍未知的后续路径。保存前取消从编辑节点分支，提交之后不得又宣称未提交。图、交互、状态表和验收描述同一规则。
kind 按当前流程中的业务含义选择：start 是本流程入口且没有入边；action 是仍有后续的动作或结果展示；decision 是业务判断且至少有两条条件明确、互不重复的出边；wait 是结果未决或等待外部事件，须写明已有依据的继续条件及去向；end 是本流程真正结束且没有任何出边。不能因“成功/失败/未知”的显示文案就标 end；展示后仍要核对权限、查询或继续处理时，展示节点仍为 action 或 wait。不得为通过校验把真实判断改成 action 或删掉已有连线。
同一张图允许多个独立 start，各自表达当前资料支持的真实入口；不要求全部操作先经过同一个页面入口。资料已明确支持的绕过页面直接提交路径，应表达其独立外部入口及后续校验、拒绝或处理去向，不能留下无入口的 action，也不能凭空补一条从查看页面或正常操作通往直提的业务边。是否独立入口须按当前资料和交互含义判断，不能仅按节点名称或为了消除不可达错误改成 start；所有节点仍须从某个真实 start 可达，角色、权限和各结果分支仍逐一核对。
输出前逐一沿成功、失败、拒绝和并发冲突等实际分支检查到达去向，不能只画成功主线：每个 action 必须有后续连线，每条路径都须能到达结束或等待状态。例如提示操作被拒并刷新显示后，若本次操作确已结束可按该业务含义表达 end；若仍需继续处理，须连接到有依据的后续节点，不能留下悬空 action。结束本次操作不等于关闭业务对象，也不等于该用户取得后续操作权限；不得为了闭合图把失败分支连到成功者专属操作。资料未定的后续只可明确标为 proposal/unknown 并提出真实待决定问题，不伪造事实或为通过校验随意补边、改节点类型。
flow 仅含 nodes 和 edges，连空的 flow.evidence 也不输出；节点 label/actor、连线 condition 分别在本对象 evidence 中给属性依据，值含 status 和按需 sources，不为 nodes/edges 数组整体添加依据。evidence 的 status 只能为 source、proposal、unknown，建议使用 proposal，不能使用 recommendation，也不能把 status/reason/sources 写到 evidence 容器这一层。
批量业务若对象独立处理：记录单个对象失败后继续汇总，仅全部结果确定才判定全成功/部分成功/全失败；未决不能当失败。“可重试”保留退出分支，不画成必须重试；再次重试仍允许失败或未决。只有资料支持才应用这些规则，不扩展无关场景。
状态表每行前后状态须属于同一对象，业务对象状态与请求结果分别表达；失败请求不改写全局归属，提交不等于成功。prohibited_actions 只写有依据或明确标建议的限制。
interaction_path_facts 是按明确 interaction_id 计算的只读路径事实，不是新规则或自动阻断。每个交互按资料核对允许及拒绝的操作者，包括有依据的绕过页面直接提交；成功后不主动触发下一独立操作时，本次请求能否结束；共享等待须保留原交互，确定结果回到该交互的成功或拒绝路径，仍未知则继续等待。首个跨交互边只供定位，合法共享等待或用户明确触发的后续不自动判错；未绑定节点不按名称猜所属操作。分别核对同一业务对象状态与本次请求状态、提交与成功、失败操作者与全局归属，不能因他人成功而把失败者接入成功者权限路径。竞争规则逐字对照当前实际来源与已答条件，不将请求先到或先发等条件擅自等同业务成功。资料已回答的矛盾是候选纠错，不新增产品决定或重复提问；只对真实未定业务保留问题。不得自动删边或改节点类型来消除反例。
保留已有 id；新对象用 F1/I1/N1/E1/T1。field_refs 用字段 id，depends_on 用 R#，rule_refs/已有验收用 C#，本行新增验收用 A1/A2 对应 acceptance 顺序。
depends_on 仅列有真实依据的其他需求依赖，不表示本行归属，不得引用本行 row_key 或 item_ref；无有据的其他需求依赖时返回空数组 []。
节点形状：{"id":"N1","label":"步骤说明","kind":"action","actor":"执行者","interaction_id":"I1","evidence":{...}}。
连线形状：{"id":"E1","from":"N1","to":"N2","condition":"转移条件","evidence":{"condition":{...}}}；interaction_id 只在节点，连线没有 label，也没有 index。这些仅为结构示意，须替换为本期真实内容，不能直接复制。
输出前逐个核对节点的入边和出边：start 无入边，end 无出边，decision 至少两条不同且非空条件的出边，wait 有明确继续条件，其他步骤有后续；所有节点可从 start 到达并能到达 end 或 wait，所有 from/to 引用真实节点。再核对来源真实、所有未知可定位，以及正文/图/表/验收之间无互相矛盾。若缺失分支涉及未决定的业务，保留已知内容并在 questions 提出具体缺口，不自动补成成功、失败、退出或重试规则，也不伪称已完整或已确认。输入如有 detail_validation_issues，按其稳定 path 核对既有定义；诊断只指出缺口，不是业务决定或授权。不得用来源定位有效代替业务或人工确认。
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
    if 'evidence' in detail and isinstance(detail['evidence'],dict) and not detail['evidence']:
        diagnostics.append(dict(path='detail_contract.evidence',
            code='BUSINESS_DRAFT_DETAIL_EMPTY_ROOT_EVIDENCE',raw={},
            reason='移除详细定义容器上严格为空的属性依据；未移动证据或修改业务含义'))
        detail.pop('evidence')
    flow = detail.get('flow')
    flow = flow if isinstance(flow, dict) else {}
    if 'evidence' in flow and isinstance(flow['evidence'],dict) and not flow['evidence']:
        diagnostics.append(dict(path='detail_contract.flow.evidence',
            code='BUSINESS_DRAFT_DETAIL_EMPTY_FLOW_EVIDENCE',raw={},
            reason='移除流程容器上严格为空的属性依据；未移动证据或修改节点、连线与业务含义'))
        flow.pop('evidence')
    collections = [(section, detail.get(section)) for section in
                   ('fields', 'interactions', 'state_transitions')]
    collections += [('flow.' + section, flow.get(section)) for section in ('nodes', 'edges')]

    # Some providers copy the outer business VALUE envelope into a detail text
    # slot. Unwrap only an exact, typed envelope with compatible evidence. Keep
    # its text, proposal/fact status and every reference; normal source and
    # detail validators still run afterwards. Conflicting shapes stay rejected.
    schema = model_schema()['properties']
    for section, rows in collections:
        if not isinstance(rows, list):
            continue
        parts = section.split('.')
        shape = schema[parts[0]]
        if len(parts) > 1:
            shape = shape['properties'][parts[1]]
        properties = shape['items']['properties']
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('evidence', {}), dict):
                continue
            # Some replies repeat the evidence for an explicit uniqueness
            # constraint under an absent optional property. Keep the actual
            # constraint and its identical source proof; do not invent a value
            # or discard unknown/conflicting evidence.
            evidence=row.get('evidence',{})
            for attribute, proof in evidence.items():
                if isinstance(proof,dict) and proof.get('status')=='recommendation':
                    diagnostics.append(dict(
                        path='detail_contract.'+section+'.'+str(row.get('id',''))+'.evidence.'+attribute+'.status',
                        code='BUSINESS_DRAFT_DETAIL_PROPOSAL_ALIAS', raw='recommendation', normalized='proposal',
                        reason='建议身份的同义枚举归一；业务原文、引用与建议身份保留，未提升为资料事实'))
                    proof['status']='proposal'
            if (section=='fields' and 'uniqueness' not in row
                    and '唯一' in str(row.get('constraints',''))
                    and evidence.get('uniqueness')==evidence.get('constraints')
                    and isinstance(evidence.get('uniqueness'),dict)
                    and evidence['uniqueness'].get('status') in ('source','proposal')):
                diagnostics.append(dict(path='detail_contract.fields.'+str(row.get('id',''))+'.evidence.uniqueness',
                    code='BUSINESS_DRAFT_DETAIL_REDUNDANT_EVIDENCE',raw=copy.deepcopy(evidence['uniqueness']),
                    reason='未提供独立唯一性属性；相同依据已完整保留在明确的唯一性约束中，未改变属性原文或来源'))
                evidence.pop('uniqueness')
            for key, value in list(row.items()):
                if (properties.get(key, {}).get('type') != 'string' or key in ('id','interaction_id','from','to')
                        or not isinstance(value, dict) or set(value) != {'text','basis','sources'}
                        or not isinstance(value['text'], str) or value['basis'] not in ('fact','recommendation')
                        or not isinstance(value['sources'], list)):
                    continue
                refs = value['sources']
                if not all(isinstance(ref, dict) and set(ref)=={'source','locator'}
                           and all(isinstance(v,str) for v in ref.values()) for ref in refs):
                    continue
                status = 'source' if value['basis']=='fact' else 'proposal'
                prior = row.get('evidence', {}).get(key, {})
                if (not isinstance(prior, dict) or prior.get('status', status)!=status
                        or set(prior)-{'status','sources','reason'}
                        or not isinstance(prior.get('sources', []), list)
                        or status=='source' and not refs):
                    continue
                if any(ref not in refs for ref in prior.get('sources', [])):
                    continue
                evidence = dict(prior, status=status)
                if refs:
                    evidence['sources'] = copy.deepcopy(refs)
                row.setdefault('evidence', {})[key] = evidence
                row[key] = value['text']
                diagnostics.append(dict(path='detail_contract.'+section+'.'+str(row.get('id',''))+'.'+key,
                    code='BUSINESS_DRAFT_DETAIL_VALUE_ENVELOPE', raw=copy.deepcopy(value),
                    reason='将业务文本封装恢复到原属性；原文、事实或建议状态及全部引用保持不变，仍须正常校验'))

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
    schema['properties']['depends_on'] = dict(schema['properties']['depends_on'],
        description='仅引用有真实依据的其他需求，不表示本行归属，不得引用本行 row_key 或 item_ref；无有据的其他需求依赖时返回空数组 []。')

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
            # DETAIL_SCHEMA deliberately shares EVIDENCE_SCHEMA across object
            # types. Narrow each copy independently or the final edge contract
            # overwrites the required evidence of fields, actions and nodes.
            props['evidence'] = copy.deepcopy(props['evidence'])
            props['evidence']['propertyNames'] = {'enum':[name for name in props if name not in ('id','evidence')]}
            # Graph identifiers are generated links, not source-backed business
            # facts. Require evidence for the condition/meaning rather than
            # repeatedly claiming that the source contains generated node IDs.
            props['evidence']['required'] = list(evidence)
            # A proof for an optional property requires that actual property,
            # not merely a name allowed somewhere in this object schema.
            for name in props:
                if name in ('id','evidence') or name in item['required']:continue
                item.setdefault('allOf',[]).append({
                    'if':{'required':['evidence'],'properties':{'evidence':{'required':[name]}}},
                    'then':{'required':[name]}})
        schema['properties']['fields']['description'] = '仅实际输入、显示和业务结果字段；未定规则参数放对应交互和问题，不造配置项'
        schema['properties']['interactions']['description'] = '按独立输入和处理结果组织；同一编辑过程不机械拆开打开、保存、取消'
        fields = schema['properties']['fields']['items']['properties']
        schema['properties']['fields']['items'].setdefault('allOf',[]).append(dict(
            **{'if':dict(properties={'data_type':{'const':'enum'}},required=['data_type']),
               'then':dict(required=['enum_values'],properties={'enum_values':dict(type='array',minItems=1,items=dict(type='string',minLength=1))})}))
        fields['meaning'] = dict(fields['meaning'], description='字段表示什么、用于什么业务；产生方式未定不改变已知用途，仅在含义本身未知时标 unknown')
        fields['source'] = dict(fields['source'], description='字段数据来自何处、由谁产生；未定产生方式在本属性标 unknown，不连带改写其他已知属性')
        fields['default_value'] = dict(fields['default_value'], description='业务默认、未赋值及空值处理；按来源写明适用条件，不由操作后状态推定初始默认。未定存储值或显示占位仅在不影响已知业务规则时放顶层 limitations；真正未知的业务默认或空值处理仍标 unknown，不编技术值或以 proposal 绕过待确认。')
        nodes = schema['properties']['flow']['properties']['nodes']['items']['properties']
        schema['properties']['flow']['description'] = '完整覆盖实际成功及异常分支；每个 action 有后续连线，每条路径可达结束或等待。按业务含义表达本次操作结束，不等于关闭业务对象；不为闭图把失败者连到成功者专属操作。未知去向保留待决，不伪造业务边。'
        nodes['kind'] = dict(nodes['kind'], enum=['start', 'action', 'decision', 'wait', 'end'],
            description='start 无入边；action 有后续；decision 至少两条不同且非空条件的出边；wait 有继续条件和去向；end 无出边。按业务含义选择，不能为消除诊断伪造分支或改换类型。')
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
    from .requirement_details import validate_detail, technical_issues, DetailProblem
    validate_detail(detail)
    # Graph topology is a deterministic generation acceptance check, not work
    # to defer until the PM confirms. Do not invent branches or change kinds.
    issues = technical_issues(dict(detail_contract=detail))
    if issues:
        labels = {row['id']: row.get('label') or row['id'] for row in detail.get('flow',{}).get('nodes',[])}
        for issue in issues:
            oid=issue.get('object_id')
            if oid in labels:issue['message']=issue['message'].replace(oid, '“'+labels[oid]+'”', 1)
        code=('BUSINESS_DRAFT_DETAIL_FLOW' if all(issue['code'].startswith('DETAIL_FLOW_') or issue['code']=='DETAIL_DUPLICATE_ID'
            for issue in issues) else 'BUSINESS_DRAFT_DETAIL_TECHNICAL')
        raise DetailProblem(issues, code)
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
