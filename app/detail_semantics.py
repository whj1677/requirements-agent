"""Source-bound review of generated detail, without another business schema.

The reviewer may report a counterexample, never approve a requirement or create
a PM decision. Exact quotes/graph traces are checked locally; their semantic
interpretation remains a model review, recorded separately from human review.
"""
import copy
import json
import re
import jsonschema
from .core import dumps, require,digest
from .requirement_details import interaction_path_facts, _objects, DETAIL_SCHEMA

VERSION='detail-source-review-2'
POOL_VERSION='detail-source-review-3'
REFERENCE_ENCODING='detail-property-reference-encoding-1'
UNKNOWN_SCOPE='detail-unknown-scope-1'
DIMENSIONS=('operation_results','actor_paths','success_timing')
RECOVERY_DIMENSIONS=('recovery_success','recovery_rejection','recovery_pending')


def obj(properties):
    return dict(type='object',additionalProperties=False,required=list(properties),properties=properties)


TEXT=dict(type='string',minLength=1,maxLength=6000)
STRINGS=dict(type='array',items=TEXT,uniqueItems=True,maxItems=300)
SOURCE=obj(dict(key=TEXT,source_id=TEXT,excerpt_id=TEXT,quote=TEXT))
HUMAN=obj(dict(key=TEXT,statement_id=TEXT,quote=TEXT))
TRACE=obj(dict(kind=dict(enum=['normal','exception','unknown','not_applicable'],
    description='normal表示本操作按当前场景定义的完成路径，不等同业务成功；仅允许拒绝的场景，其有来源的拒绝完成路径可为normal。exception/unknown表示该场景的异常/未决分支，not_applicable仅用于有依据的不适用。'),
    edge_ids=dict(STRINGS,description='按实际行进顺序列出一条连续单路径；前边to必须等于后边from。互斥出边须拆成独立trace，不得把边集合串成路径。'),explanation=TEXT))
VERDICT=dict(enum=['supported','contradicted','unsupported','unclear'])
REFERENCES=dict(candidate_keys=STRINGS,affected_keys=STRINGS,source_quote_keys=STRINGS,human_quote_keys=STRINGS,
    explanation=TEXT,correction=TEXT)
CHECK=obj(dict(scope=TEXT,dimension=dict(enum=[*DIMENSIONS,*RECOVERY_DIMENSIONS,'field_conditions','coverage']),
    verdict=VERDICT,traces=dict(type='array',items=TRACE,maxItems=20),**REFERENCES))
SOURCE_CHECK=obj(dict(key=TEXT,verdict=VERDICT,source_quote_keys=STRINGS,affected_keys=STRINGS,explanation=TEXT,correction=TEXT))
STATE_CHECK=obj(dict(transition_id=TEXT,verdicts=obj({key:VERDICT for key in ('object_identity','success_timing','actor_perspective')}),**REFERENCES))
SCHEMA=obj(dict(source_quotes=dict(type='array',items=SOURCE,maxItems=500),human_quotes=dict(type='array',items=HUMAN,maxItems=300),
    rows=dict(type='array',items=obj(dict(row_key=TEXT,candidate_hash=TEXT,
    checks=dict(type='array',items=CHECK,minItems=1,maxItems=2+DETAIL_SCHEMA['properties']['interactions']['maxItems']*len(DIMENSIONS+RECOVERY_DIMENSIONS)),
    source_checks=dict(type='array',items=SOURCE_CHECK,maxItems=2000),
    state_checks=dict(type='array',items=STATE_CHECK,maxItems=300))),minItems=1,maxItems=100)))


def properties(detail):
    result={}
    for section,applicability in detail.get('applicability',{}).items():
        for key in ('applicable','reason'):
            if key in applicability:
                result['detail_contract.applicability.'+section+'.'+key]=(dumps(applicability[key])
                    if isinstance(applicability[key],bool) else applicability[key])
    for section,row in _objects(detail):
        for key,value in row.items():
            if key not in ('id','evidence') and (isinstance(value,str) and value.strip()
                    or isinstance(value,list) and all(isinstance(x,str) for x in value)):
                result['detail_contract.'+section+'.'+row['id']+'.'+key]=value if isinstance(value,str) else dumps(value)
    return result


def entry_nodes(detail,interaction):
    """First nodes bound to this operation along the actual graph, not labels."""
    nodes={n['id']:n for n in detail['flow']['nodes']}
    outgoing={nid:[] for nid in nodes}
    for edge in detail['flow']['edges']:
        if edge.get('from') in outgoing and edge.get('to') in nodes:outgoing[edge['from']].append(edge['to'])
    seen=set();entries=set();todo=[nid for nid,n in nodes.items() if n.get('kind')=='start']
    while todo:
        nid=todo.pop()
        if nid in seen:continue
        seen.add(nid)
        if nodes[nid].get('interaction_id')==interaction:entries.add(nid)
        else:todo.extend(outgoing[nid])
    return entries


def human_binding_hash(question,text,kind):
    return digest(dict(question_id=question['id'],question_text=question.get('question'),
        related_refs=question.get('related_refs',[]),status=question.get('status'),
        understanding_status=question.get('understanding_status'),decision_answer_id=question.get('decision_answer_id'),
        answer_content_hash=digest(text),kind=kind))


def prepare(raw,canonical,report,excerpts,*,project=None,generation_context=None,protocol_version=2,recovery_guard=False,correction_protocol=1,recovery_outcome_checks=False,reference_encoding=None,unknown_scope=None,default_scope=None):
    """Only new, locally accepted detail is reviewable; old saved detail is not.

    Candidate identity comes from adapter metadata rather than text similarity.
    """
    rows=[]
    proposals={row['temp_id']:row for row in canonical.get('proposals',[])}
    for accepted in report.get('accepted',[]):
        original=next((r for r in raw.get('requirements',[]) if r.get('row_key')==accepted['row_key']),None)
        if not original or not isinstance(original.get('detail_contract'),dict):continue
        if any(r.get('path',[])==['requirements',raw['requirements'].index(original),'detail_contract']
               for r in accepted.get('field_rejected',[])):continue
        candidate=proposals.get(accepted.get('candidate_temp_id'))
        if candidate is None:continue
        detail=candidate.get('detail_contract')
        if not isinstance(detail,dict):continue
        from .requirement_details import technical_issues,DetailProblem
        issues=technical_issues(candidate)
        if issues:raise DetailProblem(issues,'DETAIL_REVIEW_TECHNICAL_INVALID')
        expected=[dict(scope=r['id'],dimension=d) for r in detail['interactions'] for d in (DIMENSIONS+RECOVERY_DIMENSIONS if recovery_outcome_checks else DIMENSIONS)]
        expected += [dict(scope='detail',dimension=d) for d in ('coverage','field_conditions')]
        property_text=properties(detail)
        property_refs={f'P{index:03d}':dict(path=path,value=value) for index,(path,value) in enumerate(sorted(property_text.items()),1)}
        property_keys={value['path']:key for key,value in property_refs.items()}
        source_claims=[]
        for section,item in _objects(detail):
            for attribute,proof in item.get('evidence',{}).items():
                path='detail_contract.'+section+'.'+item['id']+'.'+attribute
                if proof.get('status')=='source' and path in properties(detail):
                    source_claims.append(dict(key=property_keys[path],path=path,value=item[attribute],source_refs=copy.deepcopy(proof.get('source_refs',[]))))
        rows.append(dict(row_key=accepted['row_key'],requirement_id=accepted['requirement_id'],
            statement=candidate['statement'],behavior=candidate.get('behavior',{}),
            detail_contract=copy.deepcopy(detail),property_text=property_text,property_refs=property_refs,candidate_hash=digest(detail),
            interaction_entry_nodes={i['id']:sorted(entry_nodes(detail,i['id'])) for i in detail['interactions']},
            interaction_path_facts=interaction_path_facts(detail),required_checks=expected,source_claims=source_claims))
    human=[]
    if project is not None and generation_context is not None:
        current_rows={r['row_key']:r for r in generation_context.get('current_rows',[])}
        def add(identity,text,kind,scope,status,**context):
            if isinstance(text,str) and text.strip():
                human.append(dict(statement_id=identity,kind=kind,row_key=scope,text=text,
                    content_hash=digest(text),project_revision=project['revision'],decision_status=status,**context))
        add('current-user-message',generation_context.get('user_message'),'current_user_words',None,'user_words_not_automatic_approval')
        from .understanding import current_decision_answer
        for row in rows:
            sent=current_rows.get(row['row_key'],{}).get('questions',[])
            owner=next((i for i in project.get('items',[]) if i['id']==row['requirement_id']),{})
            related={row['requirement_id'],*owner.get('related_refs',[])}
            related.update(i['id'] for i in project.get('items',[]) if row['requirement_id'] in i.get('related_refs',[]) or i.get('target_item_id')==row['requirement_id'])
            for question in project.get('questions',[]):
                matches=[q for q in sent if q.get('text')==question.get('question')]
                if (not matches or not related.intersection(question.get('related_refs',[]))
                        or question.get('superseded_by') or question.get('out_of_scope_reason')):continue
                context=dict(question_id=question['id'],question_text=question['question'],
                    question_reason=question.get('why',''),related_refs=copy.deepcopy(question.get('related_refs',[])))
                if question.get('answer') and any(q.get('answer')==question['answer'] for q in matches):
                    add(question['id']+':answer',question['answer'],'saved_answer',row['row_key'],
                        question.get('understanding_status','saved_unreviewed'),
                        binding_hash=human_binding_hash(question,question['answer'],'saved_answer'),**context)
                group=current_decision_answer(project,question)
                if group and any(q.get('saved_group_words')==group.get('text') for q in matches):
                    add(question['id']+':'+group['id'],group.get('text'),'saved_group_words',row['row_key'],
                        'group_words_do_not_confirm_every_leaf',
                        binding_hash=human_binding_hash(question,group.get('text'),'saved_group_words'),**context)
    if not rows:return None
    if recovery_guard:
        from .detail_recovery import facts
        for row in rows:
            detail=row['detail_contract']
            row['recovery_structure_facts']=facts(detail,{i['id']:entry_nodes(detail,i['id']) for i in detail['interactions']})
    plan=dict(version=VERSION,recovery_outcome_checks=bool(recovery_outcome_checks),correction_protocol=correction_protocol,recovery_guard=bool(recovery_guard),rows=rows,human_statements=human,sources=[dict(source_id=e['source_id'],excerpt_id=e['id'],text=e['text']) for e in excerpts])
    require(protocol_version in (2,3),'DETAIL_REVIEW_INVALID','不支持的来源审查协议版本。')
    if protocol_version==3:
        plan['version']=POOL_VERSION
        plan['quote_pool']=quote_pool(plan)
        plan['quote_pool_hash']=digest(plan['quote_pool'])
        for row in rows:
            for claim in row['source_claims']:
                own={(r['source_id'],r['excerpt_id']) for r in claim['source_refs']}
                claim['allowed_quote_keys']=[q['key'] for q in plan['quote_pool'] if q['kind']=='source' and
                    (q['material']['source_id'],q['material']['excerpt_id']) in own]
    if reference_encoding is not None:
        require(reference_encoding==REFERENCE_ENCODING and protocol_version==3,'DETAIL_REVIEW_INVALID','不支持的属性引用编码。')
        plan['reference_encoding']=property_encoding(plan)
        plan['reference_encoding_hash']=digest(plan['reference_encoding'])
    if unknown_scope is not None:
        require(unknown_scope==UNKNOWN_SCOPE and protocol_version==3,'DETAIL_REVIEW_INVALID','不支持的未知属性范围核对协议。')
        plan['unknown_scope']=unknown_scope_contract(plan)
        plan['unknown_scope_hash']=digest(plan['unknown_scope'])
    if default_scope is not None:
        from .detail_business_scope import PROTOCOL, LEGACY_PROTOCOL, prepare_bindings
        require(default_scope in (LEGACY_PROTOCOL,PROTOCOL) and unknown_scope==UNKNOWN_SCOPE,'DETAIL_REVIEW_INVALID','不支持的默认值范围审查协议。')
        plan['default_scope_review']=prepare_bindings(plan,project,default_scope)
    return plan


def quote_pool(plan):
    """Lossless contiguous natural-text spans; identity never comes from a candidate."""
    result=[]
    for kind,materials in (('source',plan['sources']),('human',plan.get('human_statements',[]))):
        for material in materials:
            text=material['text'];start=0
            identity={k:copy.deepcopy(v) for k,v in material.items() if k!='text'}
            while start<len(text):
                end=min(start+2400,len(text))
                if end<len(text):
                    boundaries=list(re.finditer(r'[。！？；\n]',text[start:end]))
                    if boundaries:end=start+boundaries[-1].end()
                result.append(dict(key=('S' if kind=='source' else 'H')+str(len(result)+1).zfill(4),
                    kind=kind,material=copy.deepcopy(identity),content_hash=digest(text),start=start,end=end,text=text[start:end]))
                start=end
    return result


def messages(plan,*,transport_protocol=None):
    instruction=(
        '独立核对已生成详细定义与当前来源是否一致。你不是业务决策者；不得新增PM问题、补决定或采纳候选。'
        '若入口或前置条件已明确角色或状态成立，不能靠无来源的角色、归属或时点变化使相反条件成为可达场景；须区分通用鉴权守卫与实际场景路径。仍须保留来源明确要求的原请求确定拒绝及恢复出口，不能因此推导请求必然成功。'
        '逐row原样返回candidate_hash、逐required_checks检查、逐source_claims.key返回source_checks、逐state_transitions.id返回state_checks，不能抽样或只检查修过的内容。'
        '每项检查的scope必须原样复制本行required_checks中的精确scope编号（如I1或detail），不得追加中文说明、空格或改写；中文解释写入已有explanation字段，scope与dimension组合须逐项唯一覆盖原矩阵。'
        '通过项explanation只用一句简短说明，引用使用已有key，不重复抄写长段来源或候选；不通过项仍须保留精确位置、具体反例和依据。简洁表达不能省略任何矩阵项、逐属性source_check、状态判定或必要traces。'
        '不适用检查仍须选择本操作已有属性作为candidate_keys，不发明恢复路径。候选属性不重复抄写：candidate_keys和affected_keys只选择该行property_refs的短key；程序按冻结hash解析原path/value并记录属性引用解析，不把它说成模型逐字引文。'
        '来源原句放顶层source_quotes池，每项唯一key/source_id/excerpt_id/quote；人工作答原句放human_quotes池，每项唯一key/statement_id/quote。各检查只引用source_quote_keys/human_quote_keys，重复依据复用同一key。'
        'supported/contradicted必须引用真实原句；unsupported表示当前来源不支持新增断言，不能用资料缺失证明相反业务；'
        'unclear表示定义表达不能唯一判断，指出已有来源和歧义，不让PM重复决定。'
        'human_statements是本轮生成实际包含的用户原话及已保存回答，问句/关联/决定身份一并核对；不是候选的自证。'
        '具体已答规则须保留，不因没有附件片段反复追问；决定组原话不自动确认每个子问题，本轮操作指令不自动变业务事实。'
        '人工作答可用于判定一致性，但不能替status=source属性伪造source_refs；无真实来源绑定仍保持proposal/unknown。'
        'operation_results逐操作核对正常、拒绝、结果未知及恢复是否仍属于原操作；actor_paths核对各角色在条件成立时能否真的到达分支；'
        '每个state_check分别给object_identity、success_timing、actor_perspective三个verdict，区分业务对象、成功事件和操作者结果，不能把提交写成已生效或把操作者未获成功写成全局未改变。'
        'source_checks逐属性判断来源是否支持具体含义，不把来源存在等于支持；supported须引用该属性source_refs的真实引文。每项key只能出现一次，不遗漏、不用一个总评代替。'
        '新增、追加、覆盖、更新、一次或多次提交各产生几条记录等写入方式与基数，须由该属性自身来源明确或无歧义支持同一动作、对象及前提；不能移用其他动作的写入规则。动作/字段清单或保留历史不能推出写入策略，无据肯定断言应指出，未定保持unknown，不默认新建或更新；明确或无歧义有据规则不得降级。'
        'proposal/unknown只核对是否如实标注，不将合理建议强制升级事实或要求PM决定。'
        'detail.field_conditions核对条件必填、空值、字段存在与值非空的口径，不能只因required是且允许空就判错。'
        'detail.coverage必须引用四个applicability.*.applicable属性并核对每项本条来源已说明的操作/范围是否被实际定义覆盖；'
        '不能因生成器宣称NA或漏掉interaction就免检。确实不适用可以通过，不强制任何固定数量、节点名或业务模块。'
        '图trace只能列现有连续edge_ids，按适用场景给normal及exception/unknown路径；通过结论须从start或该操作interaction_entry_nodes中的真实绑定入口完整走到end/wait或本操作边界。恢复维度可从本操作入口不经其他操作可达的wait起；本操作边界仅指绑定本操作的action且所有出边均进入其他操作真实入口。结构边界不证明业务成功、只读或权限，仍须按来源核对结果与时点；不能用成功尾段冒充入口可达；失败反例允许停在实际断点。'
        'normal是本操作按当前场景定义的完成路径标签，不等同允许操作或业务成功；若来源明确本场景只拒绝（如非本人直接请求），审查可将其完整拒绝完成路径明确标normal，不得为凑normal新增允许或成功业务边。普通操作仍须有其实际正常完成路径，不能用不相干拒绝分支替代。每个edge_ids是有序连续单路径，逐对检查前边to等于后边from；互斥出边分为独立trace，不把各分支边的集合串行列在一个trace中。'
        '无来源异常分支可not_applicable并引用解释，不能发明异常。'
        '共享wait/end和统一结果显示合法，只要保留原操作身份；仍未知到end可表示本次查询结束，须结合提示/后续刷新入口核对，不强制循环。'
        '越权直接请求可以有独立入口或中性提交节点，不要求给无权限者显示按钮。失败期间外部并发成功可改变全局状态。'
        '同一操作的operation_results和actor_paths可以共享原始traces，不跨操作借用；有来源的异常须核对，无来源异常不强造。'
        'actor_paths解释条件约束；来源明确越权直提时，从此操作者入口逐边走到拒绝，不能只看到图有拒绝边就通过；'
        '若该入口/路径本来缺失，以精确候选条件和来源原句说明反例并判不通过，不必伪造一条不存在的路径。'
        '检查正文、字段、交互、图及状态的一致性；每个不通过项给具体反例和仅依据现有来源的精确纠正建议。'
        'affected_keys仅列本项实际需纠正的候选key且须属于candidate_keys；source_check的affected_keys只能为本项key；supported时必须[]。覆盖检查的key不自动代表全部属性错误。'
        'supported的correction写无需纠正；不得为了过关删除有来源内容。只返回Schema JSON。\n可信任务头：')
    if plan.get('recovery_outcome_checks',False):
        instruction=instruction.replace('只返回Schema JSON。','recovery_success核对已提交且原结果未知后，核对取得原请求成功结果的完整路径；recovery_rejection核对该原请求取得确定拒绝或失败结果的路径，不拿提交前校验替代已提交后的未知恢复；recovery_pending核对本次核对后仍未知时提示及继续核对是否保留。每项独立给traces；仅按当前来源判断适用性，无来源义务用not_applicable并引用说明，不新增异常、状态或重试规则。共享wait/end合法，end可表示本次查询结束但保留待核对提示与后续入口，不强制节点类型或画法。只返回Schema JSON。')
    packet=copy.deepcopy(plan)
    if plan['version']==POOL_VERSION:
        instruction=instruction.replace('来源原句放顶层source_quotes池，每项唯一key/source_id/excerpt_id/quote；人工作答原句放human_quotes池，每项唯一key/statement_id/quote。各检查只引用source_quote_keys/human_quote_keys，重复依据复用同一key。','')
        instruction=instruction.replace('只返回Schema JSON。\n可信任务头：',
            '引用只选择输入quote_pool的key，不自行创建source_quotes/human_quotes池，不抄写身份或原文。'
            '原样返回quote_pool_hash；source_quote_keys只选source，human_quote_keys只选human。'
            '每个supported的source_check须选该source_claim.allowed_quote_keys；材料原文相同也不得借用其他材料身份。'
            'human材料保留material.kind/decision_status：current_user_words仍是当前输入（可含未提交草稿），不是saved_answer或确认决定。'
            '原文按连续区间完整保留；条件跨区间时选相邻多个key，结合整份材料理解，不将单段截取当作完整条件。'
            '程序按冻结池解析引用并单独审计，不表示模型逐字引句。只返回Schema JSON。\n可信任务头：')
        packet.pop('sources');packet.pop('human_statements')
    if plan.get('reference_encoding'):
        instruction=instruction.replace('只返回Schema JSON。\n可信任务头：','candidate_keys/affected_keys仅可选本行reference_encoding声明的完整P key或精确字符串suffix；其他字段及S/H引文不得使用此编码。只返回Schema JSON。\n可信任务头：')
    if checked_unknown_scope(plan) is not None:
        instruction=instruction.replace('proposal/unknown只核对是否如实标注，不将合理建议强制升级事实或要求PM决定。',
            'proposal/unknown须先核对其所指命题是否属于当前业务属性，再核对依据身份是否恰当；标注unknown并非范围与内容正确的充分条件。不将合理建议强制升级事实，不要求PM决定资料外的技术实现。')
        instruction=instruction.replace('detail.field_conditions核对条件必填、空值、字段存在与值非空的口径，不能只因required是且允许空就判错。',
            'detail.field_conditions核对条件必填、空值、字段存在与值非空的口径；default_value仅指本期业务默认、未赋值及空值处理，不自动要求数据库默认值或精确显示占位。来源支持的操作后状态只在对应条件下适用，不反推初始默认；不能只因required是且允许空就判错。')
        instruction=instruction.replace('只返回Schema JSON。\n可信任务头：','unknown_scope逐行列出现有未知阻塞诊断对应的属性及理由；全局detail/field_conditions的candidate_keys必须逐项包含该行全部unknown属性key。结合原来源分别核对：真实业务默认、空值、权限等决定仍缺失，还是仅未定存储/显示实现被误扩为已知业务属性整体未知。不得凭词面认定。若来源已明确业务口径但unknown归因错误，按证据如实判定并给出该项精确affected_keys与引文，交原纠正流程；保留具体业务值或据来源修正适用条件，不编初始值、NULL、占位或规则，不以proposal遮盖。若unknown确实表示真实业务缺口，可如实判supported（仅表示unknown归因恰当，不表示业务值已确定）；保留unknown，确认门槛仍阻塞，不强制改成已知。只返回Schema JSON。\n可信任务头：')
    for row in packet['rows']:
        row.pop('property_text',None)
        row['source_claims']=[{key:claim[key] for key in ('key','source_refs','allowed_quote_keys') if key in claim} for claim in row['source_claims']]
    if plan.get('default_scope_review'):
        instruction=instruction.replace('只返回Schema JSON。\n可信任务头：','仅对unknown_scope中fields.*.default_value的未知项，在detail/field_conditions.default_scopes逐项返回scope判定。business_gap表示当前业务默认/空值决定缺失，须指出当前范围内具体动作、条件或可观察结果如何因该缺口无法确定；unclear表示无法可靠区分，也须说明尚不能区分的具体业务影响，二者继续阻塞。不得仅因资料未给NULL、显示占位或存储实现，或假设范围外的字段缺失场景，就认定业务阻塞。technical_only仍须依据当前属性绑定的真实来源或可复核已保存回答，说明已明确的本期业务规则及剩余仅存储或显示实现的差异，不要求来源逐字宣告“本期业务已足够”；不能据此编造业务初始值、归属或迁移决定。混合缺口不得technical_only。分别写known_business、unknown_technical、解释和精确source/human引文key；不编初始值、NULL、占位、迁移或角色。technical_only不更改原value/proof/unknown状态，不是人工批准或技术已确认；全部其他来源、角色、状态、恢复检查仍须通过。无法严谨复核的当前草稿/临时输入或群组原话不能作为technical_only授权依据。只返回Schema JSON。\n可信任务头：')
        if plan['default_scope_review']['protocol']=='detail-technical-scope-2':
            instruction=instruction.replace('仅对unknown_scope中fields.*.default_value的未知项', '仅对unknown_scope中有限可分类路径fields.*的default_value、constraints、editability、required、source及interactions.*的cancel、pending未知项')
            instruction=instruction.replace('business_gap表示当前业务默认/空值决定缺失','business_gap表示当前业务条件、输入数据、权限或操作结果所必需的决定仍缺失')
            instruction=instruction.replace('剩余仅存储或显示实现的差异','剩余仅技术实现或未要求的实现细节，不改变本期业务行为的差异')
            instruction=instruction.replace('只返回Schema JSON。\n可信任务头：','这些路径只是分类候选，不是技术豁免清单。technical_only须逐项证明当前业务行为、条件和可观察结果已有真实来源支持，剩余仅接口取值、存储格式、显示或未要求的技术实现；原value与unknown状态不变。权限、归属、必填、取消或等待若确实影响本期动作结果仍business_gap；无法区分仍unclear。资料未定义额外取消、同步异步或网络恢复，不等于必须新增这些业务决定，也不等于已决定不能取消或没有异步。不能仅因详细定义出现状态字段就引入未要求的新对象初始化或存量迁移决定；当前资料确实要求这些且仍缺决定时，仍为business_gap。只返回Schema JSON。\n可信任务头：')
    header=dict(stage='review',schema=wire_schema(plan))
    if transport_protocol is not None:
        require(transport_protocol=='responses-json-schema-v1','DETAIL_REVIEW_TRANSPORT_INVALID','不支持的详细审查传输协议。')
        header.update(transport_protocol=transport_protocol,strict_json_protocol='detail-review-strict-json-1')
    return [dict(role='system',content=instruction+dumps(header)),
            dict(role='user',content=[dict(type='text',text=dumps(packet))])]


def review_schema(plan):
    """Freeze finite checklist lengths/keys in the request as well as validation."""
    schema=copy.deepcopy(SCHEMA);variants=[]
    for origin in plan['rows']:
        row=copy.deepcopy(schema['properties']['rows']['items']);props=row['properties']
        props['row_key']={'const':origin['row_key']};props['candidate_hash']={'const':origin['candidate_hash']}
        props['checks'].update(minItems=len(origin['required_checks']),maxItems=len(origin['required_checks']))
        props['checks']['items']['properties']['scope']={'enum':list(dict.fromkeys(
            check['scope'] for check in origin['required_checks']))}
        for group,keys,id_field in (
            ('source_checks',[c['key'] for c in origin['source_claims']],'key'),
            ('state_checks',[s['id'] for s in origin['detail_contract']['state_transitions']],'transition_id')):
            props[group].update(minItems=len(keys),maxItems=len(keys))
            if keys:props[group]['items']['properties'][id_field]={'enum':keys}
        for group in ('checks','source_checks','state_checks'):
            fields=props[group]['items']['properties']
            if plan['version']==POOL_VERSION:
                for field,kind in (('source_quote_keys','source'),('human_quote_keys','human')):
                    if field not in fields:continue
                    keys=[q['key'] for q in plan['quote_pool'] if q['kind']==kind]
                    fields[field]={**fields[field],'items':{'enum':keys} if keys else {'not':{}}}
            for name in ('candidate_keys','affected_keys'):
                if name in fields:
                    fields[name]={**fields[name],'items':{'enum':list(origin['property_refs'])} if origin['property_refs'] else {'not':{}},
                                  'minItems':1 if name=='candidate_keys' else 0}
        if plan.get('default_scope_review'):
            from .detail_business_scope import bind_schema
            bind_schema(plan,origin,props['checks']['items'])
        variants.append(row)
    schema['properties']['rows'].update(minItems=len(variants),maxItems=len(variants),
        items=variants[0] if len(variants)==1 else {'oneOf':variants})
    if plan['version']==POOL_VERSION:
        for key in ('source_quotes','human_quotes'):
            schema['properties'].pop(key);schema['required'].remove(key)
        schema['properties']['quote_pool_hash']={'const':plan['quote_pool_hash']}
        schema['required'].append('quote_pool_hash')
    return schema




def unknown_scope_contract(plan):
    # Identity/property verification only; no host classification of unknown meaning.
    property_encoding(plan)
    rows=[]
    for row in plan['rows']:
        from .requirement_details import detail_issues
        by_path={prop['path']:key for key,prop in row['property_refs'].items()}
        actual={'detail_contract.'+section+'.'+obj['id']+'.'+name:(obj[name],obj.get('evidence',{}).get(name,{}))
            for section,obj in _objects(row['detail_contract']) for name in obj if name not in ('id','evidence')}
        unknown={}
        for issue in detail_issues(dict(detail_contract=row['detail_contract'])):
            if issue['code']=='DETAIL_PROPERTY_UNKNOWN':
                messages=unknown.setdefault(issue['path'],[])
                if issue['message'] not in messages:messages.append(issue['message'])
        properties=[]
        for path,diagnostics in sorted(unknown.items()):
            require(path in by_path and path in actual,'DETAIL_REVIEW_INVALID','未知属性诊断无法绑定现有属性引用，候选保留。')
            value,evidence=actual[path]
            properties.append(dict(key=by_path[path],path=path,value=copy.deepcopy(value),reason=evidence.get('reason',''),diagnostics=diagnostics))
        require(len(properties)<=CHECK['properties']['candidate_keys']['maxItems'],'DETAIL_REVIEW_INVALID','未知属性范围超过现有单项引用容量，未发送审查；候选与未知状态保留。')
        rows.append(dict(row_key=row['row_key'],candidate_hash=row['candidate_hash'],property_refs_hash=digest(row['property_refs']),properties=sorted(properties,key=lambda p:p['key'])))
    return dict(protocol=UNKNOWN_SCOPE,rows=rows)


def checked_unknown_scope(plan):
    if 'unknown_scope' not in plan:
        require('unknown_scope_hash' not in plan,'DETAIL_REVIEW_INVALID','未知属性范围声明缺失。')
        return None
    expected=unknown_scope_contract(plan)
    require(plan['version']==POOL_VERSION and plan['unknown_scope']==expected and plan.get('unknown_scope_hash')==digest(expected),
            'DETAIL_REVIEW_INVALID','未知属性范围冻结输入已变化。')
    return {row['row_key']:row for row in expected['rows']}


def unknown_scope_missing(scope,row_key,check):
    if scope is None or check.get('scope')!='detail' or check.get('dimension')!='field_conditions':return []
    return [prop['key'] for prop in scope[row_key]['properties'] if prop['key'] not in check['candidate_keys']]

def property_encoding(plan):
    """Finite per-candidate property identifiers, not a general reference codec."""
    rows=[];seen=set()
    for row in plan['rows']:
        expected={f'P{index:03d}':dict(path=path,value=value) for index,(path,value) in enumerate(sorted(properties(row['detail_contract']).items()),1)}
        require(row['row_key'] not in seen and row['candidate_hash']==digest(row['detail_contract']) and row['property_refs']==expected,
                'DETAIL_REVIEW_INVALID','属性编码候选身份或属性表不一致。')
        seen.add(row['row_key']);aliases={}
        for key in row['property_refs']:
            require(isinstance(key,str) and re.fullmatch(r'P[0-9]+',key),'DETAIL_REVIEW_INVALID','属性编码只支持声明的P数字key。')
            suffix=key[1:]
            require(suffix not in aliases and suffix not in row['property_refs'],'DETAIL_REVIEW_INVALID','属性编码存在歧义。')
            aliases[suffix]=key
        rows.append(dict(row_key=row['row_key'],candidate_hash=row['candidate_hash'],property_refs_hash=digest(row['property_refs']),aliases=aliases))
    return dict(protocol=REFERENCE_ENCODING,rows=rows)


def checked_encoding(plan):
    if 'reference_encoding' not in plan:
        require('reference_encoding_hash' not in plan,'DETAIL_REVIEW_INVALID','属性编码声明缺失。')
        return None
    expected=property_encoding(plan)
    require(plan['version']==POOL_VERSION and plan['reference_encoding']==expected and plan.get('reference_encoding_hash')==digest(expected),
            'DETAIL_REVIEW_INVALID','属性编码冻结声明不一致。')
    return {row['row_key']:row for row in expected['rows']}


def wire_item(plan,origin,group,item):
    item=copy.deepcopy(item);encoding=checked_encoding(plan)
    scope=checked_unknown_scope(plan)
    if scope is not None and group=='checks':
        clauses=[]
        for prop in scope[origin['row_key']]['properties']:
            keys=[prop['key']]
            if encoding is not None:keys.extend(alias for alias,key in encoding[origin['row_key']]['aliases'].items() if key==prop['key'])
            clauses.append({'contains':{'enum':keys}})
        if clauses:item.setdefault('allOf',[]).append({'if':dict(required=['scope','dimension'],properties={'scope':{'const':'detail'},'dimension':{'const':'field_conditions'}}),
            'then':dict(required=['candidate_keys'],properties={'candidate_keys':{'allOf':clauses}})})
    if encoding is None:return item
    for field in ('candidate_keys','affected_keys'):
        if field in item['properties']:
            item['properties'][field]['items']={'enum':[*origin['property_refs'],*encoding[origin['row_key']]['aliases']]}
    quoted={'anyOf':[dict(required=[field],properties={field:dict(minItems=1)}) for field in ('source_quote_keys','human_quote_keys') if field in item['properties']]}
    if group=='state_checks':
        names=('object_identity','success_timing','actor_perspective')
        condition=dict(required=['verdicts'],properties={'verdicts':{'anyOf':[
            dict(required=[name],properties={name:{'const':'contradicted'}}) for name in names]+[
            dict(required=list(names),properties={name:{'const':'supported'} for name in names})]}})
    else:condition=dict(required=['verdict'],properties={'verdict':{'enum':['supported','contradicted']}})
    item.setdefault('allOf',[]).append({'if':condition,'then':quoted})
    return item


def wire_schema(plan):
    schema=review_schema(plan)
    if checked_encoding(plan) is None and checked_unknown_scope(plan) is None:return schema
    items=schema['properties']['rows']['items'];variants=items.get('oneOf',[items])
    for row,origin in zip(variants,plan['rows']):
        for group in ('checks','source_checks','state_checks'):
            row['properties'][group]['items']=wire_item(plan,origin,group,row['properties'][group]['items'])
    return schema


def decode_property_entry(plan,row_key,entry,path,changes):
    encoding=checked_encoding(plan)
    result=copy.deepcopy(entry)
    if encoding is None:return result
    require(row_key in encoding and isinstance(result,dict),'DETAIL_REVIEW_INVALID','属性编码行或条目无效。')
    aliases=encoding[row_key]['aliases'];canonical=set(aliases.values())
    for field in ('candidate_keys','affected_keys'):
        if field not in result:continue
        require(isinstance(result[field],list),'DETAIL_REVIEW_INVALID','属性引用须为数组。')
        for index,key in enumerate(result[field]):
            require(isinstance(key,str) and (key in canonical or key in aliases),'DETAIL_REVIEW_INVALID','未声明的属性引用编码。')
            if key in aliases:
                result[field][index]=aliases[key]
                changes.append(dict(path=[*path,field,index],before=key,after=aliases[key],row_key=row_key,
                                    candidate_hash=encoding[row_key]['candidate_hash'],rule=REFERENCE_ENCODING))
    return result


def decode_review(plan,response):
    encoding=checked_encoding(plan);result=copy.deepcopy(response);changes=[]
    if encoding is None:return result,None
    require(isinstance(result,dict) and result.get('quote_pool_hash')==plan['quote_pool_hash'] and isinstance(result.get('rows'),list),
            'DETAIL_REVIEW_INVALID','编码审查冻结身份缺失。')
    require(len(result['rows'])==len(encoding) and all(isinstance(r,dict) and isinstance(r.get('row_key'),str) for r in result['rows'])
        and {r['row_key'] for r in result['rows']}==set(encoding),'DETAIL_REVIEW_INVALID','编码审查行身份不匹配。')
    for ri,row in enumerate(result['rows']):
        require(row.get('candidate_hash')==encoding[row['row_key']]['candidate_hash'],'DETAIL_REVIEW_INVALID','编码审查候选hash不匹配。')
        for group in ('checks','source_checks','state_checks'):
            require(isinstance(row.get(group),list),'DETAIL_REVIEW_INVALID','编码审查检查组缺失。')
            row[group]=[decode_property_entry(plan,row['row_key'],entry,['rows',ri,group,ci],changes) for ci,entry in enumerate(row[group])]
    return result,dict(protocol=REFERENCE_ENCODING,encoding_hash=plan['reference_encoding_hash'],input_hash=digest(response),canonical_hash=digest(result),changes=changes)

def normalize_review(response):
    """Only supported checks have a uniquely determined empty correction set."""
    value=copy.deepcopy(response);changes=[]
    if not isinstance(value,dict) or not isinstance(value.get('rows'),list):return value,changes
    for ri,row in enumerate(value['rows']):
        if not isinstance(row,dict) or not isinstance(row.get('checks'),list):continue
        for group in ('checks','source_checks','state_checks'):
            for ci,check in enumerate(row.get(group,[])):
                supported=(check.get('verdict')=='supported' or group=='state_checks' and
                    isinstance(check.get('verdicts'),dict) and set(check['verdicts'])=={'object_identity','success_timing','actor_perspective'}
                    and all(v=='supported' for v in check['verdicts'].values())) if isinstance(check,dict) else False
                if supported and 'affected_keys' not in check:
                    check['affected_keys']=[]
                    changes.append(dict(path=['rows',ri,group,ci,'affected_keys'],raw_missing=True,normalized=[],
                        rule='supported-empty-correction-scope-2',reason='通过项没有可授权纠正的位置；仅补唯一合法空集合。'))
    return value,changes


def _property_key_diagnostic(plan,response):
    """Report reference locations only, never echo source prose or candidate values."""
    safe=lambda value: value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_:-]{1,80}',value) else '<invalid-id>'
    origins={row['row_key']:row['property_refs'] for row in plan['rows']}
    for ri,row in enumerate(response.get('rows',[]) if isinstance(response,dict) else []):
        if not isinstance(row,dict) or not isinstance(row.get('row_key'),str) or row['row_key'] not in origins:continue
        refs=origins[row['row_key']]
        for group in ('checks','source_checks','state_checks'):
            if not isinstance(row.get(group),list):continue
            for ci,check in enumerate(row[group]):
                if not isinstance(check,dict):continue
                for field in ('candidate_keys','affected_keys'):
                    keys=check.get(field)
                    if not isinstance(keys,list):continue
                    unknown=[k if isinstance(k,str) and re.fullmatch(r'P[0-9]{1,10}',k) else '<invalid-property-key>'
                             for k in keys if not isinstance(k,str) or k not in refs]
                    if (field=='candidate_keys' and not keys) or unknown:
                        return ('候选属性引用无效：row='+safe(row['row_key'])+' scope='+safe(check.get('scope',check.get('transition_id','source')))+
                            ' dimension='+safe(check.get('dimension','state_identity' if group=='state_checks' else 'source_support'))+
                            ' path=rows['+str(ri)+'].'+group+'['+str(ci)+'].'+field+
                            (' empty=true；请选择本行现有属性key（不适用也须定位）。' if not keys else ' unknown_keys='+dumps(unknown[:20])))
    return ''


def validate(plan,response):
    if plan['version']==POOL_VERSION:
        require(not list(jsonschema.Draft202012Validator(review_schema(plan)).iter_errors(response)),
            'DETAIL_REVIEW_INVALID',_property_key_diagnostic(plan,response) or '固定引文池审查结构或池身份不匹配，原候选保留。')
        scope=checked_unknown_scope(plan)
        if scope is not None:
            require(len(response['rows'])==len(scope) and {row['row_key'] for row in response['rows']}==set(scope),
                'DETAIL_REVIEW_INVALID','未知属性核对的行身份不匹配。')
            for row in response['rows']:
                require(row['candidate_hash']==scope[row['row_key']]['candidate_hash'],'DETAIL_REVIEW_INVALID','未知属性核对候选hash不匹配。')
                checks=[c for c in row['checks'] if c['scope']=='detail' and c['dimension']=='field_conditions']
                require(len(checks)==1,'DETAIL_REVIEW_INVALID','未知属性须由唯一全局field_conditions条目核对。')
                missing=unknown_scope_missing(scope,row['row_key'],checks[0])
                require(not missing,'DETAIL_REVIEW_INVALID','全局field_conditions遗漏冻结unknown属性：'+dumps(missing))
        expected=quote_pool(plan)
        require(plan['quote_pool']==expected and plan['quote_pool_hash']==digest(expected),
            'DETAIL_REVIEW_INVALID','冻结引文池的原文区间、材料身份或哈希已变化。')
        pool={q['key']:q for q in expected};selected={};expanded=dict(rows=copy.deepcopy(response['rows']),source_quotes=[],human_quotes=[])
        for row in response['rows']:
            selected[row['row_key']]=set()
            for group in ('checks','source_checks','state_checks'):
                for check in row[group]:
                    for field,kind in (('source_quote_keys','source'),('human_quote_keys','human')):
                        for key in check.get(field,[]):
                            require(key in pool and pool[key]['kind']==kind,'DETAIL_REVIEW_INVALID',
                                '引文key不存在或材料类型不匹配：'+key)
                            q=pool[key]
                            if kind=='human':
                                require(q['material']['row_key'] in (None,row['row_key']),
                                    'DETAIL_REVIEW_INVALID','人工作答引用不属于当前需求行：'+key)
                            selected[row['row_key']].add(key)
        all_selected=set().union(*selected.values()) if selected else set()
        for key in sorted(all_selected):
            q=pool[key];material=q['material']
            item=dict(key=key,quote=q['text'])
            if q['kind']=='source':
                item.update(source_id=material['source_id'],excerpt_id=material['excerpt_id']);expanded['source_quotes'].append(item)
            else:
                item['statement_id']=material['statement_id'];expanded['human_quotes'].append(item)
        if plan.get('default_scope_review'):
            from .detail_business_scope import validate_scopes
            for row in expanded['rows']:
                origin=next(r for r in plan['rows'] if r['row_key']==row['row_key'])
                for check in row['checks']:
                    validate_scopes(plan,origin,check)
                    check.pop('default_scopes',None)
        outcomes=_validate_v2(plan,expanded)
        if plan.get('default_scope_review'):
            from .detail_business_scope import review_record
            for row in response['rows']:
                if outcomes[row['row_key']]['status']=='passed':
                    outcomes[row['row_key']]['default_scope_review']=review_record(plan,row,response)
        for key,outcome in outcomes.items():
            outcome['quote_reference_resolution']=dict(mode='frozen_quote_pool_key',quote_pool_hash=plan['quote_pool_hash'],
                notice='模型只选择引文key，程序按冻结材料身份及连续区间解析；非模型逐字引文，未选池项不作为证据。',
                selected_quotes=[copy.deepcopy(pool[k]) for k in sorted(selected[key])])
        return outcomes
    return _validate_v2(plan,response)


def _validate_v2(plan,response):
    require(not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(response)),
        'DETAIL_REVIEW_INVALID','详细定义语义检查返回结构不完整，原候选保留待核对。')
    source_pool={q['key']:q for q in response['source_quotes']};human_pool={q['key']:q for q in response['human_quotes']}
    require(len(source_pool)==len(response['source_quotes']) and len(human_pool)==len(response['human_quotes']),
        'DETAIL_REVIEW_INVALID','共享引文key重复，不能唯一解析依据。')
    sources={(s['source_id'],s['excerpt_id']):s['text'] for s in plan['sources']}
    humans={s['statement_id']:s for s in plan.get('human_statements',[])}
    for quote in source_pool.values():
        require(quote['quote'] in sources.get((quote['source_id'],quote['excerpt_id']),''),
            'DETAIL_REVIEW_INVALID','共享来源引文与当前原文不匹配。')
    for quote in human_pool.values():
        require(quote['quote'] in humans.get(quote['statement_id'],{}).get('text',''),
            'DETAIL_REVIEW_INVALID','共享人工作答引文与当前原话不匹配。')
    prepared=copy.deepcopy(plan);expanded=dict(rows=[])
    origins={r['row_key']:r for r in prepared['rows']}
    for row in response['rows']:
        require(row['row_key'] in origins,'DETAIL_REVIEW_INVALID','检查返回了不属于本次候选的行。')
        origin=origins[row['row_key']];refs=origin['property_refs']
        require(row['candidate_hash']==origin['candidate_hash']==digest(origin['detail_contract']),
            'DETAIL_REVIEW_INVALID','属性短引用不属于本次冻结候选，未采用过期检查。')
        def expand(check,*,scope=None,dimension=None,verdict=None,candidate_keys=None):
            keys=candidate_keys if candidate_keys is not None else check['candidate_keys']
            if not keys or not set(keys)<=set(refs):
                require(False,'DETAIL_REVIEW_INVALID',_property_key_diagnostic(plan,response) or '候选属性key不存在或未覆盖具体属性。')
            require(set(check['source_quote_keys'])<=set(source_pool) and set(check.get('human_quote_keys',[]))<=set(human_pool),
                'DETAIL_REVIEW_INVALID','检查引用了不存在的共享引文key。')
            bound=[key for key in check['affected_keys'] if key in keys]
            unbound=[key for key in check['affected_keys'] if key not in keys]
            return dict(scope=scope or check['scope'],dimension=dimension or check['dimension'],verdict=verdict or check['verdict'],
                candidate_quotes=[dict(path=refs[k]['path'],quote=refs[k]['value']) for k in keys],
                candidate_reference_mode='frozen_property_key',candidate_keys=copy.deepcopy(keys),
                affected_paths=[refs[k]['path'] for k in bound],correction_unbound_keys=unbound,
                source_quotes=[{k:v for k,v in source_pool[q].items() if k!='key'} for q in check['source_quote_keys']],
                human_quotes=[{k:v for k,v in human_pool[q].items() if k!='key'} for q in check.get('human_quote_keys',[])],
                traces=copy.deepcopy(check.get('traces',[])),explanation=check['explanation'],correction=check['correction'])
        checks=[expand(check) for check in row['checks']]
        claims={c['key']:c for c in origin['source_claims']}
        source_keys=[c['key'] for c in row['source_checks']]
        require(len(source_keys)==len(claims) and set(source_keys)==set(claims),'DETAIL_REVIEW_INVALID',
            '来源属性检查未逐key完整覆盖，不能以部分引用代表全部通过。')
        for check in row['source_checks']:
            require(set(check['affected_keys'])<={check['key']},'DETAIL_REVIEW_INVALID','来源属性检查不能授权改写另一属性。')
            resolved=expand(check,scope='source:'+check['key'],dimension='source_support',candidate_keys=[check['key']])
            if check['verdict']=='supported':
                own={(r['source_id'],r['excerpt_id']) for r in claims[check['key']]['source_refs']}
                require(any((q['source_id'],q['excerpt_id']) in own for q in resolved['source_quotes']),
                    'DETAIL_REVIEW_INVALID','来源属性的通过结论没有引用该属性自身的来源：'+check['key']+' '+refs[check['key']]['path']+
                    '；原绑定='+dumps(sorted(own))+'；本次引用='+dumps([(q['source_id'],q['excerpt_id']) for q in resolved['source_quotes']]))
            checks.append(resolved);origin['required_checks'].append(dict(scope=resolved['scope'],dimension=resolved['dimension']))
        transitions={t['id'] for t in origin['detail_contract']['state_transitions']}
        state_ids=[c['transition_id'] for c in row['state_checks']]
        require(len(state_ids)==len(transitions) and set(state_ids)==transitions,'DETAIL_REVIEW_INVALID',
            '状态转换检查未逐id覆盖全部对象、时点和操作者视角。')
        for check in row['state_checks']:
            prefix='detail_contract.state_transitions.'+check['transition_id']+'.'
            require(any(refs.get(key,{}).get('path','').startswith(prefix) for key in check['candidate_keys']),
                'DETAIL_REVIEW_INVALID','状态检查没有引用其指定转换的真实属性。')
            require(all(key not in refs or not refs[key]['path'].startswith('detail_contract.state_transitions.')
                        or refs[key]['path'].startswith(prefix) for key in check['affected_keys']),
                'DETAIL_REVIEW_INVALID','逐状态检查不能授权改写另一转换。跨切面属性仍须精确绑定。')
            verdict=next(v for v in ('contradicted','unsupported','unclear','supported') if v in check['verdicts'].values())
            resolved=expand(check,scope='state:'+check['transition_id'],dimension='state_identity',verdict=verdict)
            resolved['state_verdicts']=copy.deepcopy(check['verdicts'])
            checks.append(resolved);origin['required_checks'].append(dict(scope=resolved['scope'],dimension=resolved['dimension']))
        expanded['rows'].append(dict(row_key=row['row_key'],checks=checks))
    outcomes=_validate_expanded(prepared,expanded)
    for key,outcome in outcomes.items():
        origin=origins[key]
        outcome['property_reference_resolution']=dict(mode='frozen_property_key',candidate_hash=origin['candidate_hash'],
            notice='模型选择属性key，程序按冻结候选解析原path/value；并非模型逐字输出候选引文。')
        if plan.get('recovery_guard',False):
            from .detail_recovery import guard
            outcomes[key]=guard(origin['detail_contract'],origin['recovery_structure_facts'],outcome)
    return outcomes


def _validate_expanded(plan,response):
    expected={r['row_key']:r for r in plan['rows']}
    returned=[r['row_key'] for r in response['rows']]
    require(len(returned)==len(expected) and set(returned)==set(expected),
        'DETAIL_REVIEW_INVALID','详细定义语义检查没有逐行覆盖本次候选。')
    sources={(r['source_id'],r['excerpt_id']):r['text'] for r in plan['sources']}
    human={r['statement_id']:r for r in plan.get('human_statements',[])}
    outcomes={}
    for row in response['rows']:
        origin=expected[row['row_key']];checks=row['checks']
        keys=[(r['scope'],r['dimension']) for r in checks]
        targets={(r['scope'],r['dimension']) for r in origin['required_checks']}
        require(len(keys)==len(targets) and set(keys)==targets,'DETAIL_REVIEW_INVALID',
                '详细定义语义检查缺少操作/属性维度或重复覆盖。')
        edges={e['id']:e for e in origin['detail_contract']['flow']['edges']}
        nodes={n['id']:n for n in origin['detail_contract']['flow']['nodes']}
        from .detail_recovery import facts,trace_witness
        all_entries={i['id']:entry_nodes(origin['detail_contract'],i['id']) for i in origin['detail_contract']['interactions']}
        structure=facts(origin['detail_contract'],all_entries);trace_witnesses=[];verified_boundaries={};coverage_boundaries=[]
        for check in checks:
            require(set(check['affected_paths'])<={q['path'] for q in check['candidate_quotes']}
                and (not check['affected_paths'] and not check.get('correction_unbound_keys') if check['verdict']=='supported'
                     else bool(check['affected_paths'] or check.get('correction_unbound_keys'))),
                'DETAIL_REVIEW_INVALID','纠正位置须逐字引句绑定；通过项不得授权改写。')
            if check['scope']!='detail' and not check['scope'].startswith(('source:','state:')):
                bound={nid for nid,node in nodes.items() if node.get('interaction_id')==check['scope']}
                bound_edges={eid for eid,edge in edges.items() if edge['from'] in bound or edge['to'] in bound}
                require(any(q['path'].startswith('detail_contract.interactions.'+check['scope']+'.') or
                    any(q['path'].startswith('detail_contract.flow.nodes.'+nid+'.') for nid in bound) or
                    any(q['path'].startswith('detail_contract.flow.edges.'+eid+'.') for eid in bound_edges) or
                    (check['dimension']=='state_identity' and q['path'].startswith('detail_contract.state_transitions.'))
                    for q in check['candidate_quotes']),'DETAIL_REVIEW_INVALID',
                    '逐操作检查须引用该操作的实际属性，不能用另一对象替代。')
            for quote in check['candidate_quotes']:
                actual=origin['property_text'].get(quote['path'])
                require(actual is not None and quote['quote'] in actual,'DETAIL_REVIEW_INVALID',
                    '检查原句与候选属性位置不匹配，未将未经核验的意见当作结论。')
            for quote in check['source_quotes']:
                actual=sources.get((quote['source_id'],quote['excerpt_id']))
                require(actual is not None and quote['quote'] in actual,'DETAIL_REVIEW_INVALID',
                    '检查依据与当前来源原句不匹配。')
            for quote in check['human_quotes']:
                actual=human.get(quote['statement_id'])
                require(actual is not None and actual['row_key'] in (None,row['row_key']) and quote['quote'] in actual['text'],
                    'DETAIL_REVIEW_INVALID','检查人工作答与本轮当前原话不匹配。')
            if check['scope']=='detail' and check['dimension']=='source_support':
                covered={q['path'] for q in check['candidate_quotes']}
                require({c['path'] for c in origin['source_claims']}<=covered,'DETAIL_REVIEW_INVALID',
                    '来源支持检查没有逐属性覆盖所有标为source的断言。')
                if check['verdict']=='supported':
                    cited={(q['source_id'],q['excerpt_id']) for q in check['source_quotes']}
                    require(all(cited&{(r['source_id'],r['excerpt_id']) for r in claim['source_refs']}
                        for claim in origin['source_claims']),'DETAIL_REVIEW_INVALID',
                        '通过结论未引用每项source属性自身绑定的来源。')
            if check['dimension']=='coverage':
                require({'detail_contract.applicability.'+section+'.applicable' for section in origin['detail_contract']['applicability']}
                    <={q['path'] for q in check['candidate_quotes']},'DETAIL_REVIEW_INVALID',
                    '覆盖核对须逐项引用实际适用性声明，不能按生成器的NA声明跳过已答操作。')
            if check['verdict'] in ('supported','contradicted'):
                require(bool(check['source_quotes'] or check['human_quotes']),'DETAIL_REVIEW_INVALID','支持或冲突结论必须附原来源或当前人工作答引文。')
            if check['dimension'] in RECOVERY_DIMENSIONS and check['verdict']=='supported':
                require(any(t['edge_ids'] or t['kind']=='not_applicable' and t['explanation'].strip() for t in check['traces']),'DETAIL_REVIEW_INVALID','每个恢复结果通过项须有真实路径或有依据的not_applicable说明。')
            for trace_index,trace in enumerate(check['traces']):
                ids=trace['edge_ids']
                location=f"row={row['row_key']} scope={check['scope']} dimension={check['dimension']} trace_index={trace_index}"
                missing=next((edge for edge in ids if edge not in edges),None)
                require(missing is None,'DETAIL_REVIEW_INVALID',location+' 路径引用不存在的edge_id='+str(missing))
                disconnected=next(((a,b) for a,b in zip(ids,ids[1:]) if edges[a]['to']!=edges[b]['from']),None)
                if disconnected:
                    a,b=disconnected
                    require(False,'DETAIL_REVIEW_INVALID',location+' 首个不连续边对：'+
                        f"{a}({edges[a]['from']}->{edges[a]['to']}) / {b}({edges[b]['from']}->{edges[b]['to']})；未自动补边或改写原路径。")
                missing_path=(not ids and trace['kind'] in ('exception','unknown') and check['verdict']!='supported'
                    and bool(check['source_quotes'] or check['human_quotes']) and bool(check['affected_paths'])
                    and bool(trace['explanation'].strip()))
                require(bool(ids) or trace['kind']=='not_applicable' or missing_path,
                    'DETAIL_REVIEW_INVALID','适用路径须给连线，或以来源与精确属性绑定证明失败路径缺失。')
                if missing_path:trace['evidence_kind']='missing_path_counterexample'
                recovery_association=False
                if ids and check['verdict']=='supported':
                    visited={node for eid in ids for node in (edges[eid]['from'],edges[eid]['to'])}
                    entries=all_entries.get(check['scope'],set())
                    proof=trace_witness(origin['detail_contract'],all_entries,structure,check['scope'],
                        edges[ids[0]]['from'],edges[ids[-1]]['to'],check['dimension'] in RECOVERY_DIMENSIONS)
                    recovery_association=bool(proof.get('recovery_premise'))
                    require((bool(visited&entries) if entries else nodes[edges[ids[0]]['from']].get('kind')=='start')
                        or bool(proof.get('recovery_premise')),'DETAIL_REVIEW_INVALID',location+' 通过路径缺少真实入口或可验证的本操作等待前提。')
                    terminal=nodes[edges[ids[-1]]['to']].get('kind') in ('end','wait') or bool(proof.get('interaction_boundary'))
                    if not terminal and check['scope']=='detail' and check['dimension']=='coverage':
                        coverage_boundaries.append((tuple(ids),trace_index,location))
                    else:require(terminal,'DETAIL_REVIEW_INVALID',location+' 通过路径未到结果终点或可验证的本操作边界。')
                    if proof.get('interaction_boundary') and check['dimension'] in ('operation_results','actor_paths'):
                        verified_boundaries[tuple(ids)]=dict(scope=check['scope'],dimension=check['dimension'],proof=copy.deepcopy(proof))
                    if proof:trace_witnesses.append(dict(scope=check['scope'],dimension=check['dimension'],trace_index=trace_index,
                        **proof,notice='程序结构见证，未拼入模型路径；不证明业务成功或权限。'))
                if ids and check['scope']!='detail' and trace['kind']=='normal':
                    associated={n['id'] for n in origin['detail_contract']['flow']['nodes'] if n.get('interaction_id')==check['scope']}
                    require(not associated or recovery_association or bool(associated&{node for e in ids for node in (edges[e]['from'],edges[e]['to'])}),
                        'DETAIL_REVIEW_INVALID','正常路径没有经过所检查操作的绑定节点。')
        for ids,trace_index,location in coverage_boundaries:
            require(ids in verified_boundaries,'DETAIL_REVIEW_INVALID',location+' 全局覆盖路径没有同一行操作检查已验证的相同边界路径。')
            trace_witnesses.append(dict(scope='detail',dimension='coverage',trace_index=trace_index,
                reused_operation_witness=copy.deepcopy(verified_boundaries[ids]),edge_ids=list(ids),
                notice='同一冻结候选内复用已验证操作边界；不证明业务成功，未修改模型路径。'))
        trace_coverage=[]
        if origin['detail_contract']['applicability']['flow']['applicable']:
            for interaction in origin['detail_contract']['interactions']:
                scoped=[c for c in checks if c['scope']==interaction['id'] and c['dimension'] in ('operation_results','actor_paths')]
                # A source-bound contradiction can establish that a path does
                # not exist. Do not require the missing path to reject it.
                if all(c['verdict']=='supported' for c in scoped):
                    require(any(t['kind']=='normal' for c in scoped for t in c['traces']),
                        'DETAIL_REVIEW_INVALID','通过的操作须在同一操作检查中提供完整正常路径，不能借用另一操作。')
                trace_coverage.append(dict(scope=interaction['id'],from_dimensions=[c['dimension'] for c in scoped if c['traces']],
                    contains_failed_path_check=any(c['verdict']!='supported' for c in scoped)))
        failures=[copy.deepcopy(c) for c in checks if c['verdict']!='supported']
        source_ids={(q['source_id'],q['excerpt_id']) for check in checks for q in check['source_quotes']}
        human_ids={q['statement_id'] for check in checks for q in check['human_quotes']}
        outcomes[row['row_key']]=dict(status='failed' if failures else 'passed',checks=copy.deepcopy(checks),
            findings=failures,code='DETAIL_SEMANTIC_REVIEW',candidate_hash=digest(origin['detail_contract']),
            trace_coverage=trace_coverage,structural_trace_witnesses=trace_witnesses,
            source_bindings=[dict(source_id=sid,excerpt_id=eid,content_hash=digest(sources[(sid,eid)])) for sid,eid in sorted(source_ids)],
            human_bindings=[{key:copy.deepcopy(value) for key,value in human[identity].items() if key!='text'} for identity in sorted(human_ids)],
            reason=('本次详细定义需纠正：'+'；'.join(f['explanation'][:220]+' 建议：'+f['correction'][:220] for f in failures[:3]))[:1400]
                if failures else '来源支持语义检查通过，仍待人工核对。')
    return outcomes


def challenge_plan(plan,outcomes=None):
    """Project every frozen property, including unknown/proposal, to the existing challenge."""
    fields=[]
    for index,row in enumerate(plan['rows'],1):
        proofs={'detail_contract.'+section+'.'+item['id']+'.'+attribute:proof
            for section,item in _objects(row['detail_contract'])
            for attribute,proof in item.get('evidence',{}).items()}
        for key,prop in row['property_refs'].items():
            proof=proofs.get(prop['path'],{})
            fields.append(dict(id=f'R{index}:{key}',path=[row['row_key'],prop['path']],
                text=prop['value'],basis={'source':'fact','proposal':'recommendation'}.get(proof.get('status'),'unknown'),
                evidence=copy.deepcopy(proof),sources=[dict(source=r['source_id'],locator=r['excerpt_id'])
                    for r in proof.get('source_refs',[])]))
            if prop['path'] in {'detail_contract.applicability.'+section+'.'+attribute
                    for section in row['detail_contract']['applicability'] for attribute in ('applicable','reason')}:
                fields[-1]['reference_policy']='scope_without_independent_evidence_slot'
    candidate=[dict(row_key=r['row_key'],candidate_hash=r['candidate_hash'],detail_contract=copy.deepcopy(r['detail_contract']))
        for r in plan['rows']]
    for index,row in enumerate(plan['rows'],1):
        outcome=(outcomes or {}).get(row['row_key'],{})
        record=outcome.get('default_scope_review')
        if outcomes is not None and plan.get('default_scope_review',{}).get('protocol')=='detail-technical-scope-2':
            require(record and record.get('protocol')=='detail-technical-scope-2','DETAIL_CHALLENGE_INVALID','本次完整审查缺少当前范围分类记录。')
        if not record or record.get('protocol')!='detail-technical-scope-2':continue
        require(outcome.get('status')=='passed' and not outcome.get('findings')
            and outcome.get('candidate_hash')==row['candidate_hash']==record.get('detail_hash')
            and record.get('plan_hash')==digest(plan)
            and record.get('record_hash')==digest({k:v for k,v in record.items() if k!='record_hash'}),
            'DETAIL_CHALLENGE_INVALID','范围分类与本次完整审查、计划或候选不一致。')
        candidate[index-1]['scope_review']=copy.deepcopy(record)
        for decision in record['properties']:
            key=decision['property_key'];prop=row['property_refs'].get(key)
            require(prop and prop['path']==decision['path'],'DETAIL_CHALLENGE_INVALID','范围分类属性定位不一致。')
            fields.append(dict(id=f'R{index}:{key}:scope',path=[row['row_key'],prop['path'],'scope_review'],
                text=dumps({k:decision[k] for k in ('classification','known_business','unknown_technical','explanation')}),
                basis='review_metadata',scope_review=copy.deepcopy(decision),scope_record_hash=record['record_hash'],
                candidate_property=copy.deepcopy(prop),sources=[dict(source=q['material']['source_id'],locator=q['material']['excerpt_id'])
                    for q in decision['quotes'] if q['kind']=='source']))
    context=dict(excerpts=[dict(source=s['source_id'],locator=s['excerpt_id'],text=s['text']) for s in plan['sources']],
        saved_human_statements=[copy.deepcopy(s) for s in plan.get('human_statements',[])
            if s.get('kind') in ('saved_answer','saved_group_words')],
        notice='本次核对详细定义的每个属性；结构ID、类型标记本身不要求业务来源。unknown/proposal中的肯定事实仍须核对，真正建议不升级事实。其他属性的引用不补足本属性；主体、前提及适用范围不得省略或泛化。')
    return dict(candidate=candidate,fields=fields,context=context,candidate_hash=digest(candidate),input_hash=digest(context))


def challenge_messages(packet,*,transport_protocol=None):
    from .intake_grounding import challenge_messages as source_messages
    result=source_messages(packet)
    prompt,header=result[0]['content'].split('可信任务头：',1)
    header=json.loads(header)
    header['scope_reference_fields']=[f['id'] for f in packet['fields']
        if f.get('reference_policy')=='scope_without_independent_evidence_slot']
    prompt+=('仅可信头scope_reference_fields列出的applicability布尔及理由，schema没有独立引用槽；'
        '按本轮原始范围资料及已保存回答核对其业务含义，不因own_sources为空拒收。理由自身、旧AI或旧审查结论不能自证；'
        '仍须检查真实新增范围、否定范围及主体条件是否有据，不能省略这些属性或完整coverage。'
        '需要纠正时只可精确定位对应reason，不得改变applicable布尔掩盖已答范围。'
        '其他具有证据槽的属性包括unknown/proposal仍严格核自身来源，不适用此例外。\n')
    payload=json.loads(result[1]['content'][0]['text'])
    for field in payload['fields']:
        if 'scope_review' not in field:continue
        # Scope decisions selected exact quote spans, not every word of the
        # surrounding excerpt. Keep that same narrower evidence boundary.
        field['own_sources']=[dict(source=q['material']['source_id'],locator=q['material']['excerpt_id'],text=q['text'])
            for q in field['scope_review']['quotes'] if q['kind']=='source']
    result[1]['content'][0]['text']=dumps(payload)
    header['scope_review_fields']=[f['id'] for f in packet['fields'] if 'scope_review' in f]
    if header['scope_review_fields']:
        prompt+=('scope_review_fields是本次完整审查的范围分类元数据，不是业务来源，也不是原候选属性。逐项核classification、known_business、unknown_technical及解释，'
            '仅按scope_review.quotes中实际所选source/human原文判断；旧AI、旧passed、别字段引文或QA纠错反馈不能单独证明已知业务。'
            '原属性仍按原ID独立核查，元数据错误用带:scope的ID报告，quote必须逐字来自该目录text；不能借此要求改原unknown值或状态。'
            'technical_only不得掩盖已明确子决定，也不能把成功记录缺告警关联/操作人的业务影响直接当存储异常；须区分本期业务要求与范围外存储异常处理。'
            'business_gap/unclear同样核当前范围依据，不能仅因状态字段就新增初始化、迁移或恢复义务；缺来源只指出分类证据不足，不代PM补值。\n')
    result[0]['content']=prompt+'可信任务头：'+dumps(header)
    if transport_protocol is not None:
        require(transport_protocol=='responses-json-schema-v1','DETAIL_REVIEW_TRANSPORT_INVALID','不支持的详细审查传输协议。')
        prompt,header=result[0]['content'].split('可信任务头：',1)
        header=json.loads(header)
        header.update(stage='review',transport_protocol=transport_protocol,strict_json_protocol='detail-review-strict-json-1')
        result[0]['content']=prompt+'可信任务头：'+dumps(header)
    return result


def challenge_outcomes(plan,packet,response,outcomes):
    from .intake_grounding import validate_challenge
    require(packet==challenge_plan(plan,outcomes),'DETAIL_CHALLENGE_INVALID','质疑属性与冻结详细定义不一致。')
    checked=validate_challenge(packet,response)
    result=copy.deepcopy(outcomes)
    sources={(s['source_id'],s['excerpt_id']):s['text'] for s in plan['sources']}
    for finding in checked['findings']:
        row_key,path,*kind=finding['path']
        scope_error=kind==['scope_review']
        entry=dict(scope='source:'+finding['id'],dimension='scope_source_challenge' if scope_error else 'source_challenge',verdict='unsupported',traces=[],
            candidate_quotes=[] if scope_error else [dict(path=path,quote=finding['quote'])],affected_paths=[] if scope_error else [path],human_quotes=[],
            source_quotes=[dict(source_id=s['source'],excerpt_id=s['locator'],quote=sources[(s['source'],s['locator'])])
                for s in finding['sources']],explanation=finding['reason'],correction=finding['reason'])
        if scope_error:
            entry['scope_property_path']=path
            entry['scope_record_hash']=outcomes[row_key]['default_scope_review']['record_hash']
            entry['scope_quote']=finding['quote']
        result[row_key]['findings'].append(entry)
        result[row_key]['checks'].append(copy.deepcopy(entry))
        result[row_key].update(status='failed',reason='独立来源质疑需纠正：'+finding['reason'])
    from .detail_business_scope import scope_review_identity
    for key,row in result.items():
        row['source_challenge']=copy.deepcopy(checked)
        record=row.get('default_scope_review') or {}
        if record.get('protocol')=='detail-technical-scope-2':
            row['scope_source_audit']=dict(protocol='detail-scope-source-audit-1',
                status='passed' if checked['status']=='passed' and row['status']=='passed' else 'failed',
                scope_identity_hash=scope_review_identity(record),challenge_hash=digest(checked),
                property_keys=sorted(p['property_key'] for p in record['properties']))
    return result


def scope_correction_feedback(outcomes):
    return {key:dict(candidate_hash=row.get('candidate_hash'),
        prior_scope_review=copy.deepcopy(row.get('default_scope_review')),
        findings=[copy.deepcopy(f) for f in row.get('findings',[]) if f['dimension']=='scope_source_challenge'])
        for key,row in outcomes.items() if any(f['dimension']=='scope_source_challenge' for f in row.get('findings',[]))}


def candidate_correction_outcomes(outcomes):
    result=copy.deepcopy(outcomes)
    for row in result.values():
        row['findings']=[f for f in row.get('findings',[]) if f['dimension']!='scope_source_challenge']
        if row.get('status')=='failed' and not row['findings']:row['status']='passed'
    return result


def scope_correction_messages(messages,feedback):
    if not feedback:return messages
    result=copy.deepcopy(messages)
    payload=json.loads(result[1]['content'][0]['text'])
    payload['scope_review_correction']=dict(authority='prior_failed_review_diagnostics_not_business_source',rows=feedback,
        instruction='本次是占用既有语义纠正额度的全量新审查；原范围分类未通过独立来源核对。重新按当前原资料与已保存回答判定所有属性，不继承旧分类或通过结论、不本地补引用。候选不因分类证据错误而改值或改unknown；原范围/已知业务/技术未知均需所选真实引文支持。')
    result[1]['content'][0]['text']=dumps(payload)
    return result


def pending(plan,code,reason):
    return {r['row_key']:dict(status='pending',checks=[],findings=[],code=code,reason=reason) for r in plan['rows']}


def correction_messages(raw,plan,outcomes,excerpts):
    from .detail_draft import model_schema
    from .business_draft import _aliases
    failed={key for key,value in outcomes.items() if value['status']=='failed'}
    schema=obj(dict(rows=dict(type='array',minItems=1,items=obj(dict(row_key=TEXT,detail_contract=model_schema(generation=True))))))
    from .detail_correction import contract
    patch_schema,write_scopes=contract(raw,outcomes)
    patch_mode=plan.get('correction_protocol',1)==2
    if patch_mode:schema=patch_schema
    aliases,_,_=_aliases(excerpts)
    packet=dict(correction_protocol=2 if patch_mode else 1,write_scopes=write_scopes,rows=[copy.deepcopy(r) for r in raw['requirements'] if r['row_key'] in failed],
        findings={key:copy.deepcopy(outcomes[key]['findings']) for key in failed},
        human_statements=copy.deepcopy(plan.get('human_statements',[])),
        sources=[dict(**aliases[(e['source_id'],e['id'])],source_id=e['source_id'],excerpt_id=e['id'],text=e['text']) for e in excerpts])
    graph_facts={}
    for row in packet['rows']:
        detail=row['detail_contract'];flow=detail.get('flow') or {};edges=flow.get('edges') or []
        writable=set(write_scopes[row['row_key']]['allowed_write_paths'])
        graph_facts[row['row_key']]=dict(candidate_hash=digest(detail),
            flow_applicable=(detail.get('applicability',{}).get('flow') or {}).get('applicable'),
            nodes=[dict(id=node['id'],kind=node.get('kind'),interaction_id=node.get('interaction_id'),
                incoming_edge_ids=[e['id'] for e in edges if e.get('to')==node['id']],
                outgoing_edge_ids=[e['id'] for e in edges if e.get('from')==node['id']],
                writable_paths=sorted(p for p in writable if p.startswith('detail_contract.flow.nodes.'+node['id']+'.')))
                for node in flow.get('nodes') or []],
            edges=[dict(id=e['id'],**{'from':e.get('from'),'to':e.get('to')},
                writable_paths=sorted(p for p in writable if p.startswith('detail_contract.flow.edges.'+e['id']+'.'))) for e in edges],
            notice='仅为冻结候选当前结构事实及已有写入权限，不是业务来源、修复答案或新增授权。')
    packet['graph_structure_facts']=graph_facts
    return schema,[dict(role='system',content=(
        '只返回符合可信任务头schema的JSON对象，不输出Markdown或额外说明。'
        '仅纠正本次详细定义检查失败行的detail_contract；按findings的精确属性与来源反例修复，保留不受影响的既有内容、ID和有效引用。'
        +('default_value只描述本期业务默认、未赋值及空值处理；若findings指出技术缺失误扩成业务unknown，须据来源修订具体业务命题并重核对应evidence，不能只替换status。仅存储或显示实现未定仍明确保留：已有limitations保持只读，可在本次获准的evidence.reason保留该技术限制；不能越权写limitations、补初始值、NULL、占位或迁移规则。真正业务缺口仍unknown，不用proposal遮盖。' if plan.get('unknown_scope') else '')+
        '将source依据改为proposal时，实际属性须保留具体的建议业务内容，不能只填“建议”等依据身份词；确实未知则明确未知，不补造规则，仍遵守授权写入范围。'
        'source_binding_missing只说明原属性没有绑定来源，不表示原业务值已被判错；按材料核对后修正，不机械补引用或自动降级。'
        '纠正新增、追加、覆盖、更新、一次或多次提交各产生几条记录等写入方式与基数时，须依据该属性自身原文明确或无歧义支持的同一动作、对象及前提；不能移用其他动作的规则，也不能从动作/字段清单或保留历史推断。未定策略保持unknown，不默认新建或更新；保留明确或无歧义有据的规则与不受影响内容，不整段降级，仍只写获准详细定义属性。'
        '只可写write_scopes.allowed_write_paths列出的属性及new_object_kinds允许的新增对象；其余属性只读，不能顺手修正或删除。属性值沿用原字段类型，evidence仍按source/proposal/unknown和本轮来源别名。'
        +('write_scopes.existing_object_ids是本行所有种类的既有对象ID；既有对象只能通过获准updates修改，new_objects只能使用本行不存在的新ID，同批新增ID也不得重复，不能把现有对象重报为新增。' if patch_mode else '')+
        'graph_structure_facts只描述修改前的技术结构，不证明业务含义。修改连线起终点时须联合核对受影响节点的最终kind及入出边；'
        'decision至少两个条件明确且不重复的出边，start无入边，end无出边，普通节点不得悬空，wait保留有依据的继续条件与去向。'
        '若原节点表达与纠正后的业务含义不一致，由你依据来源在既有授权路径内联动纠正；不得仅为消除结构错误改kind、编造分支或挪成重复边。'
        '若当前来源和失败反例涉及非本人绕过页面直接提交，须从其实际入口核对；这不等于本人发起的原请求永远不会被拒。已有来源支持的原请求确定拒绝结果仍须保留，不能因分离操作者入口而删除或改写该结果。'
        '仅补漏引时，保留当前仍支持未改断言的原引用；只有本次finding证明原引用错误或已无对应断言时才移除，不能以其他字段的来源代替本属性自身引用。'
        '不能修改正文、验收、问题或人工决定；不能新增PM问题。资料已明确时直接按原句纠正，资料不足保留proposal/unknown，不能伪装source。'
        +('仅返回每个失败row的updates路径赋值及new_objects；不返回完整detail_contract，不支持删除或整对象替换。' if patch_mode else '返回每个失败row的完整detail_contract，不返回其他row。')+
        '来源使用本轮source/locator别名。程序会合回原稿、重新结构校验并完整语义复审，仅有这一次纠正机会。\n可信任务头：'+dumps(dict(stage='ingest',schema=schema)))),
        dict(role='user',content=[dict(type='text',text=dumps(packet))])]


def apply_correction(raw,response,schema,outcomes,*,normalization_audit=None,protocol_version=1,patch_audit=None):
    from .detail_draft import model_schema,normalize_redundant_metadata
    require(protocol_version in (1,2),'DETAIL_CORRECTION_INVALID','不支持的属性纠正协议版本。')
    if protocol_version==2:
        from .detail_correction import merge
        response=merge(raw,response,outcomes,patch_audit)
    response=copy.deepcopy(response)
    acceptance_schema=obj(dict(rows=dict(type='array',minItems=1,items=obj(dict(row_key=TEXT,detail_contract=model_schema())))))
    def normalized(detail,row_key,phase):
        result,changes=normalize_redundant_metadata(detail)
        if changes and normalization_audit is not None:
            normalization_audit.append(dict(rule='existing-detail-metadata-normalization',row_key=row_key,phase=phase,
                input_hash=digest(detail),output_hash=digest(result),changes=changes,
                request_schema_hash=digest(schema),acceptance_schema_hash=digest(acceptance_schema)))
        return result
    if isinstance(response,dict) and isinstance(response.get('rows'),list):
        for row in response['rows']:
            if isinstance(row,dict) and 'detail_contract' in row:
                row['detail_contract']=normalized(row['detail_contract'],row.get('row_key'),'correction_output')
    require(not list(jsonschema.Draft202012Validator(acceptance_schema).iter_errors(response)),
        'DETAIL_CORRECTION_INVALID','详细定义纠正格式无效，原候选和检查意见保留。')
    expected={key for key,value in outcomes.items() if value['status']=='failed'}
    keys=[r['row_key'] for r in response['rows']]
    require(len(keys)==len(expected) and set(keys)==expected,'DETAIL_CORRECTION_INVALID','纠正必须只覆盖失败行，不得修改其它需求。')
    result=copy.deepcopy(raw)
    replacements={r['row_key']:r['detail_contract'] for r in response['rows']}
    for row in result['requirements']:
        if row['row_key'] not in replacements:continue
        before=normalized(row['detail_contract'],row['row_key'],'original_candidate');after=replacements[row['row_key']]
        findings=outcomes[row['row_key']]['findings']
        from .detail_correction import write_scope
        scope=write_scope(before,findings)
        exact,objects,graph,states=(scope[key] for key in ('exact','objects','graph','states'))
        require(set(before['applicability'])==set(after['applicability']) and
            all(prior['applicable']==after['applicability'][section]['applicable'] and
                (prior['reason']==after['applicability'][section]['reason'] or
                 'detail_contract.applicability.'+section+'.reason' in exact)
                for section,prior in before['applicability'].items()) and before.get('depends_on')==after.get('depends_on'),
            'DETAIL_CORRECTION_SCOPE','纠正不能更改适用范围或依赖来绕过核对。')
        old_objects={'detail_contract.'+section+'.'+item['id']:item for section,item in _objects(before)}
        new_objects={'detail_contract.'+section+'.'+item['id']:item for section,item in _objects(after)}
        require(set(old_objects)<=set(new_objects),'DETAIL_CORRECTION_SCOPE','纠正不得删除原对象或清空已定义集合。')
        require(all((graph and path.startswith('detail_contract.flow.')) or
                    (states and path.startswith('detail_contract.state_transitions.'))
                    for path in set(new_objects)-set(old_objects)),
            'DETAIL_CORRECTION_SCOPE','纠正不能新增无关字段、交互或状态。')
        for path,old in old_objects.items():
            new=new_objects[path]
            for key in set(old)|set(new):
                if old.get(key)==new.get(key):continue
                if key=='evidence':
                    for attribute in set(old.get(key,{}))|set(new.get(key,{})):
                        prior=old.get(key,{}).get(attribute);current=new.get(key,{}).get(attribute)
                        if prior==current:continue
                        location=path+'.'+attribute
                        require(path in objects or location in exact,'DETAIL_CORRECTION_SCOPE','纠正修改了无关属性的依据。')
                        if prior and prior.get('status')=='source' and (not current or current.get('status')!='source'):
                            require(any(((f['dimension']=='source_support' and f['verdict']=='unsupported')
                                or (f['dimension']=='source_binding_missing' and f['verdict']=='unbound'
                                    and prior.get('sources') in (None,[]))) and location in f['affected_paths']
                                for f in findings),'DETAIL_CORRECTION_SCOPE','不能通过降低已有事实依据状态绕过语义错误。')
                else:
                    require(key!='id' and (path in objects or path+'.'+key in exact),'DETAIL_CORRECTION_SCOPE','纠正修改了未被反例定位的属性。')
        row['detail_contract']=copy.deepcopy(after)
    return result
