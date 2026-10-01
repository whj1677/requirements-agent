"""Bounded semantic witnesses and a shared review contract, never business decisions."""
import re
from .core import require

VERSION = 'semantic-quality-3'
POLICY = (
    '先核对输入自身是否一致，来源引用正确和人工回答有出处不证明其含义无矛盾。'
    '对计数单位、边界包含、量词、时点与条件构造具体反例；区分Unicode码点和扩展字素簇，'
    '肤色修饰、组合字符、旗帜和ZWJ emoji可能含多个码点。存在影响验收的冲突时提出blocking业务选择，不能替PM选。'
    '例如上限100时99个A加👍🏽是101码点、100字素簇；码点计数与字素簇计数都是可供PM选择的口径。'
    '后续明确回答可替代旧未知，但回答重复矛盾不能解除；同步核对statement、behavior、rule、AC、上下文和全部MRD/PRD正文。'
    '严格区分需求评审/采纳/正式确认角色与业务系统运行操作者，不能把PM人工确认混入运营人员运行流程或新增审批人。'
    '每个陈述区分既有事实、本期决定、模型建议；preserved和沿用是范围/本期要求，不证明其全部细则已实现。'
    '仅有既有审计正常与新增行为应沿用，不能断言现有能力已覆盖新增场景，也不能据此断言旧系统不支持。'
    '人工核对并采纳的具体行为选择可以构成本期决定，不要求原始诉求预先含后续决定；人工核对不证明原文蕴含的说明，不等于没有人工选择。'
    '但人工决定、preserved标签和classification_reason中的解释都不证明旧系统已实现新增细则；核对每句现状断言的真实实现依据。'
    '解释、摘要、风险段与规范原文同样受语义约束：明确失败不能概括为含未知结果的失败，所有不能弱化为部分/当前页，'
    '建议不能升级为必须培训等交付义务。引用是溯源，不是语义蕴含证明；逐句核对引用能否支持本句。'
    '检查真实明确来源例外：运行时PM审批有明确依据、已有代码/材料明确支持同一场景、明确业务决定培训或计数时不要误报。'
    '规范文案错误写finding并定位句子/字段，业务计数等真正未决写required_decisions；不得改历史基线或自动采纳。'
)
UNIT = re.compile(r'码点|code\s*points?|字素簇|grapheme', re.I)
CODEPOINT = re.compile(r'(?:Unicode\s*)?码点|code\s*points?', re.I)
ONE_EMOJI = re.compile(r'(?:emoji|表情(?:符号)?)[^。；;\n]{0,35}?(?:按|计为|算作|计作|counts?\s+as)[^。；;\n]{0,8}?(?:一个|1个|one|1)\s*(?:Unicode\s*)?(?:码点|code\s*point)', re.I)

def counting_witness(text):
    if not CODEPOINT.search(text or ''): return None
    for sentence in re.split(r'[。；;\n]', text):
        matches=[]
        for match in ONE_EMOJI.finditer(sentence):
            # Qualifiers/negation must govern this occurrence of the counting
            # predicate. An unrelated later emoji colour cannot waive it.
            prefix=re.split(r'[，,]|且|但是|但|并且',sentence[:match.start()])[-1][-12:]
            assertion=match.group()
            suffix=re.split(r'[，,]',sentence[match.end():])[0]
            if re.search(r'单码点|单个码点|single.codepoint|基本',prefix,re.I):continue
            if re.search(r'不(?:是|应|能|再)?[^，,]{0,6}$',prefix):continue
            if re.search(r'(?:仅指|仅限)[^，,]{0,12}(?:单码点|single.codepoint)',assertion+suffix,re.I):continue
            if re.search(r'不按|不计为|并非|不能|不应|不得',assertion):continue
            matches.append(match)
        if not matches:continue
        limit=re.search(r'(?:最多|上限|不超过|max(?:imum)?)[^\d]{0,10}(\d+)',text,re.I)
        n=int(limit.group(1)) if limit else 100
        if not 1<=n<=1000000: n=100
        return dict(label='计数口径冲突', counterexample=dict(
            description=f'{n-1}个A + 👍🏽', codepoints=n+1, extended_grapheme_clusters=n,
            sequence='👍🏽', sequence_codepoints=len('👍🏽')),
            decision='请选择按Unicode码点分别计数，或按扩展字素簇计数；同步修订边界、emoji表述与验收，不代选。')
    return None

def current_counting_checks(p):
    checks=[]
    for i in p.get('items',[]):
        if i.get('selection_status')!='selected' or i.get('applies_to')!='to_be': continue
        texts=[('statement',i.get('statement',''))]+[(f'behavior.{k}',v) for k,v in (i.get('behavior') or {}).items() if isinstance(v,str)]
        for field,text in texts:
            witness=counting_witness(text)
            if witness:checks.append(dict(check_id='COUNT:'+i['id']+':'+field,object_id=i['id'],dimension='text_length_counting_unit',location=i['id']+'.'+field,related_refs=[i['id']],source_refs=i.get('source_refs',[]),**witness))
    for q in p.get('questions',[]):
        if q.get('status')!='answered' or q.get('superseded_by') or q.get('out_of_scope_reason'):continue
        witness=counting_witness(q.get('answer') or '')
        if witness:checks.append(dict(check_id='COUNT:'+q['id']+':answer',object_id=q['id'],dimension='text_length_counting_unit',location=q['id']+'.answer',related_refs=[q['id']],source_refs=q.get('answer_source_refs',[]),**witness))
        choice=counting_choice(q.get('answer') or '')
        if not choice or not re.search(r'计数|口径|长度|码点|字素簇|count|length',q.get('question',''),re.I):continue
        for item in p.get('items',[]):
            if item['id'] not in q.get('related_refs',[]) or item.get('selection_status')!='selected' or item.get('applies_to')!='to_be':continue
            fields=[('statement',item.get('statement',''))]+[(f'behavior.{k}',v) for k,v in (item.get('behavior') or {}).items() if isinstance(v,str)]
            for field,text in fields:
                actual=counting_choice(text)
                if not actual or actual==choice:continue
                checks.append(dict(check_id=f"COUNT:{item['id']}:{field}:answer-{q['id']}",object_id=item['id'],
                    dimension='text_length_counting_unit',location=item['id']+'.'+field,related_refs=[item['id'],q['id']],
                    source_refs=q.get('answer_source_refs',[]),label='条款与人工计数选择不一致',
                    counterexample=dict(answer_unit=choice,item_unit=actual),
                    decision='核对人工回答与条款的Unicode码点/扩展字素簇口径；不能自动替换已选择的单位。'))
    return checks

def contract():
    return dict(version=VERSION,policy=POLICY,
        review_axes=['输入内部一致性与反例','当前决定与历史未知','运行角色与需求确认角色',
                     '既有事实/新增决定/模型建议','全部规范、行为字段、摘要与解释段条件量词'],
        finding_binding='semantic_checks每项check_id唯一绑定object_id、location、dimension；尚存冲突的review必须在对应blocker.message和required_decisions同时原样写check_id，给反例与PM选择。不能用同一对象的其他blocker、其他计数字段或其他问题抵消。ingest/clarify/change中同一条可回答问题必须联合绑定冲突对象、source_id/excerpt_id与计数决定；不能用两条问题分别命中，也不能以码点颜色等其它维度过关。明确回答与修订或当前条款必须选择相同计数单位，否定某一单位或仍待确认不等于已决定。复用同对象同维度开放问题。',
        document_finding_binding='semantic_document_checks是正文引用语义风险，按check_id定位实际解释原句及引用，逐项在finding.message原样写ID。每一个声称的检查ID、文档/章节/块位置和该位置的原句都必须真实，另一文档没有对应错误时不能连带报告。条件弱化/事实归属/义务外扩写纠错finding，不创造业务required_decisions；既有实现例外必须有同能力、同对象和同场景的肯定证据，否认/不确定/仅支持其它能力不能解除风险。',
        scope='只识别可定位的语义问题，不以字数、来源数量或模板完整作为通过标准；启发式不是通用语义证明。正式确认前最低覆盖全部本期已选需求、规则和验收条目；不完整审查保留缺口，不能解除gate。')


def source_pairs(refs):
    return {(r.get('source_id'),r.get('excerpt_id')) for r in refs}


def counting_question(q):
    text=q.get('question','')
    # The actual decision must concern counting. A rationale mentioning length
    # cannot turn a colour/font question into a counting decision.
    return bool(q.get('blocking') and UNIT.search(text) and
        not re.search(r'颜色|字体|colou?r|font',text,re.I) and
        (re.search(r'计数|计量|口径|长度|怎么算|如何计|count|length',text,re.I) or
         (CODEPOINT.search(text) and re.search(r'字素簇|grapheme',text,re.I) and
          re.search(r'还是|或|which|versus|\bvs\b',text,re.I))))


def counting_choice(text):
    """One affirmative finite unit choice; examples and undecided prose do not decide."""
    if counting_witness(text):return None
    choices=set()
    unresolved=False
    segments=re.split(r'([。；;，,\n？?])',text or '')
    for index in range(0,len(segments),2):
        clause=segments[index]
        terminator=segments[index+1] if index+1<len(segments) else ''
        if terminator in ('？','?') or re.search(r'尚未|未决定|未确定|待确认|待决定|不确定|暂不|还是|undecided',clause,re.I):
            if UNIT.search(clause) or re.search(r'计数|口径|长度|何种|count|length',clause,re.I):unresolved=True
            continue
        affirmatives=set()
        for part in re.split(r'并且|且|但是|但',clause):
            if not (re.search(r'计数|分别计|逐.*计|计入|count|最多|上限|不超过|maximum',part,re.I) or
                    re.search(r'(?:按|采用|选择|以).*(?:码点|字素簇|code\s*points?|grapheme)',part,re.I)):continue
            for unit in UNIT.finditer(part):
                prefix=part[:unit.end()]
                if re.search(r'不按|不采用|不选择|不使用|并非|不是|而非|不得|不能|不应',prefix):continue
                affirmatives.add('codepoints' if CODEPOINT.fullmatch(unit.group()) else 'graphemes')
        if affirmatives:
            choices.update(affirmatives)
            unresolved=False
    return next(iter(choices)) if len(choices)==1 and not unresolved else None


def counting_decision(text):
    return counting_choice(text) is not None


def item_counting_choices(item):
    texts=[item.get('statement','')]+[v for v in (item.get('behavior') or {}).values() if isinstance(v,str)]
    return {choice for text in texts if (choice:=counting_choice(text))}


def validate_analysis(value,p,excerpts):
    if value['stage'] not in ('ingest','clarify','change'):return
    from .understanding import leaves
    items={i['id']:i for i in p.get('items',[])}
    pending=[q for q in p.get('questions',[]) if q.get('blocking') and q.get('status')!='answered' and not q.get('superseded_by') and not q.get('out_of_scope_reason')]
    questions=leaves(value['questions'])+pending
    def bound(objects,pair):
        return any(counting_question(q) and objects & set(q.get('related_refs',[])) and
                   pair in source_pairs(q.get('source_refs',[])) for q in questions)
    def proposal_pairs(i):
        return source_pairs(i.get('source_refs',[]) or items.get(i.get('target_item_id'),{}).get('source_refs',[]))
    input_conflicts={(e['source_id'],e['id']) for e in excerpts if counting_witness(e.get('text',''))}
    def conflict_pairs(i):
        pairs=proposal_pairs(i)
        # Other citations may support unrelated parts of this proposal. Require
        # every cited counting conflict, without inventing questions for them.
        return pairs & input_conflicts or pairs
    ambiguous=[i for i in value['proposals'] if counting_witness(i.get('statement','')) or any(counting_witness(v) for v in (i.get('behavior') or {}).values() if isinstance(v,str))]
    answers={q['id']:counting_choice(q.get('answer') or '') for q in p.get('questions',[])
             if q.get('status')=='answered' and not q.get('superseded_by') and not q.get('out_of_scope_reason')}
    require(all(not answers.get(ref) or not item_counting_choices(i) or item_counting_choices(i)=={answers[ref]}
                for i in value['proposals'] for ref in i.get('answer_refs',[])),
            'SEMANTIC_BLOCKED','候选计数口径与其引用的人工回答不一致；保留人工选择，不代换计数单位。')
    # A sourced, unambiguous PM answer permits a proposed correction; it does not
    # forgive a contradictory proposal and never adopts the correction itself.
    def resolved(excerpt):
        pair=(excerpt['source_id'],excerpt['id'])
        for q in p.get('questions',[]):
            answer=q.get('answer') or ''
            choice=counting_choice(answer)
            if q.get('status')!='answered' or q.get('superseded_by') or q.get('out_of_scope_reason') or not choice:continue
            targets=[items[r] for r in q.get('related_refs',[]) if r in items]
            refs=q.get('source_refs',[])+[r for i in targets for r in i.get('source_refs',[])]
            if pair not in {(r.get('source_id'),r.get('excerpt_id')) for r in refs}:continue
            corrections=[i for i in value['proposals'] if i.get('action')=='revise' and i.get('target_item_id') in q.get('related_refs',[]) and q['id'] in i.get('answer_refs',[]) and item_counting_choices(i)=={choice}]
            current=[i for i in targets if i.get('selection_status')=='selected' and item_counting_choices(i)=={choice}]
            if corrections or current:return True
        return False
    raw=[e for e in excerpts if counting_witness(e.get('text','')) and not resolved(e)]
    def source_bound(e):
        pair=(e['source_id'],e['id'])
        owners={ref for i in ambiguous if pair in proposal_pairs(i) for ref in (i['temp_id'],i.get('target_item_id')) if ref}
        if not owners:
            owners={ref for i in value['proposals'] if pair in proposal_pairs(i) for ref in (i['temp_id'],i.get('target_item_id')) if ref}
            owners.update(i['id'] for i in items.values() if pair in source_pairs(i.get('source_refs',[])))
        return bound(owners,pair)
    require(all(conflict_pairs(i) and all(bound({i['temp_id'],i.get('target_item_id')},pair) for pair in conflict_pairs(i)) for i in ambiguous) and all(source_bound(e) for e in raw),'SEMANTIC_BLOCKED',
        '输入或候选的计数口径互相矛盾：码点与每个emoji恒为一码点不能同时覆盖复合emoji。请提出阻塞澄清和具体反例，不替PM选口径。')

def validate_review(value,p):
    checks=current_counting_checks(p)
    result=value['result']
    require(not checks or (result['assessment']=='needs_changes' and
        all(any(c['check_id'] in d and UNIT.search(d) for d in result['required_decisions']) and
            any(f['severity']=='blocker' and set(c['related_refs'])&set(f['related_refs']) and c['check_id'] in f['message'] and UNIT.search(f['message']+' '+f['suggested_resolution']) for f in value['findings']) for c in checks)),
        'SEMANTIC_BLOCKED','当前条款/人工回答存在可复现的计数冲突，不能ready；须定位blocker并列PM计数决定。'+str([c['location'] for c in checks]))
    document_checks=current_document_checks(p)
    validate_document_findings(value['findings'],p,document_checks)
    require(not document_checks or (result['assessment']=='needs_changes' and
        all(any(c['check_id'] in f['message'] and (not c['related_refs'] or set(c['related_refs']) & set(f['related_refs'])) for f in value['findings']) for c in document_checks)),
        'SEMANTIC_BLOCKED','实际文档解释存在条件、量词或事实身份风险，须逐项定位finding；文案纠错不增加PM待决定：'+str([c['check_id'] for c in document_checks]))

def clauses(text):
    """Keep quoted diagnostic prose together; punctuation bounds other claims."""
    parts=[];start=0;quote=None
    for index,char in enumerate(text):
        if quote:
            if char==quote:quote=None
        elif char in ('“','「','"'):
            quote={'“':'”','「':'」','"':'"'}[char]
        elif char in '。；;，,\n':
            parts.append(text[start:index]);start=index+1
    parts.append(text[start:])
    return [part.strip() for part in parts if part.strip()]


NEGATIVE = re.compile(r'不应|不得|不能|并非|不是|不算|不代表|不保证|未必|尚未|未支持|未实现|未覆盖|没有证据|无证据|不确定|待验证|待核对')
IMPLEMENTED = re.compile(r'(?:已|已经)(?:支持|实现|覆盖)')
UNCERTAIN_IMPLEMENTATION = re.compile(r'可能|或许|似乎|尚待|待确认|未确认|未证实|未经验证|无法(?:证明|证实|确认)|据说|据称')
CAPABILITIES = ('审计','登录','推送','导出','批量','查询','编辑','保存')
SUBJECTS = ('备注','姓名','联系人','电价','时段','站点','任务','附件','文件')
SCENARIOS = ('成功','失败','超时','未知')


def positive_match(pattern,clause):
    for match in re.finditer(pattern,clause):
        # A coordinated earlier predicate does not negate the current one.
        # Keep negation inside the predicate (e.g. 部分成功不算整批成功).
        scope=re.split(r'[，,]|并且|且|但是|但|同时',clause[:match.end()])[-1]
        if not NEGATIVE.search(scope):return True
    return False


def implementation_supported(claim,scenario_evidence,existing_evidence):
    """Conservative finite capability/subject/scenario signatures, not NLI."""
    def signature(words):
        direct={word for word in words if word in claim}
        return direct or {word for word in words if word in scenario_evidence}
    capabilities=signature(CAPABILITIES);subjects=signature(SUBJECTS)
    scenarios=signature(SCENARIOS)
    change=bool(re.search(r'变更|修改|更新',claim+' '+scenario_evidence))
    if not capabilities or not subjects:return False
    matching=[]
    for sentence in re.split(r'[。；;\n]',existing_evidence):
        parts=[predicate.strip() for clause in clauses(sentence)
               for predicate in re.split(r'并且|且|但是|但|同时',clause) if predicate.strip()]
        relevant=[part for part in parts if capabilities.issubset({w for w in CAPABILITIES if w in part}) and
                  subjects.issubset({w for w in SUBJECTS if w in part})]
        for part in relevant:
            if NEGATIVE.search(part) and (not scenarios or not any(w in part for w in SCENARIOS) or any(w in part for w in scenarios)):
                return False
        positive=[part for part in relevant if positive_match(IMPLEMENTED,part) and not UNCERTAIN_IMPLEMENTATION.search(part) and
                  (not change or re.search(r'变更|修改|更新',part))]
        if not positive:continue
        # An explicit restriction/denial in the same implementation statement
        # applies to its elliptical continuation as well as its named subject.
        if any(re.search(r'仅限|仅在|只在',part) and any(w not in part for w in scenarios) for part in parts):continue
        if any(NEGATIVE.search(part) and any(w in part for w in scenarios) and
               (not any(w in part for w in SUBJECTS) or any(w in part for w in subjects)) for part in parts):continue
        special=scenarios-{'成功'}
        def continuation(part):
            # A subject/capability-free continuation can describe the preceding
            # implementation. A different named capability cannot lend its scene.
            return (not any(w in part for w in CAPABILITIES+SUBJECTS) and
                    positive_match(IMPLEMENTED,part) and not UNCERTAIN_IMPLEMENTATION.search(part))
        for implementation in positive:
            position=parts.index(implementation)
            continuation_parts=[]
            for part in parts[position+1:]:
                if any(w in part for w in CAPABILITIES+SUBJECTS):break
                if continuation(part):continuation_parts.append(part)
            if all(any(w in part for part in [implementation]+continuation_parts) for w in special):
                matching.append(sentence)
                break
    return bool(matching)


def explanation_issues(text,evidence,existing_evidence,scenario_evidence=None):
    issues=[]
    for sentence in re.split(r'[。；;\n]',text):
        parts=clauses(sentence)
        failure_pattern=r'(?:失败场景(?:下)?|失败时|失败后)[^。；;\n]{0,50}?原(?:记录|数据).{0,4}不变'
        def explicit_failure(match):
            prefix=sentence[:match.start()]
            return (re.search(r'(?<![不未非])明确\s*$',prefix) and
                    not re.search(r'无法明确\s*$',prefix) and positive_match(r'明确\s*$',prefix))
        conditional=('明确失败' in evidence and '未知' in evidence and any(
            not explicit_failure(match) and positive_match(re.escape(match.group()),sentence)
            for match in re.finditer(failure_pattern,sentence)))
        weaker=(re.search(r'(?:所有|全部)[^。；]{0,16}成功',evidence) and any(
            positive_match(r'(?:任一|任意一个|部分).{0,16}成功.{0,16}整批成功',part) for part in parts))
        obligation=any(positive_match(r'(?:需|须|必须|应当?).{0,14}(?:培训|说明).{0,12}(?:提示|告知)',part) and
            not re.search(r'建议|可考虑',part) for part in parts) and not re.search(r'(?:需|须|必须|应当?)[^。；]{0,14}(?:培训|说明)',evidence)
        unsupported=any(positive_match(IMPLEMENTED,part) and
            not re.search(r'部署后|验收时|上线后',part) and
            not implementation_supported(part,evidence if scenario_evidence is None else scenario_evidence,existing_evidence) for part in parts)
        for dimension,present in [('failure_condition',conditional),('success_quantifier',weaker),('suggestion_obligation',obligation),('existing_implementation',unsupported)]:
            if present:issues.append(dict(dimension=dimension,quote=sentence.strip()))
    return issues

def validate_explanation(text,evidence,existing_evidence,at,*,scenario_evidence=None):
    require(not explanation_issues(text,evidence,existing_evidence,scenario_evidence),'SEMANTIC_BLOCKED',at+
        ' 解释超出引用语义：核对明确失败/未知结果、全部/部分、建议/义务与既有实现证据；不能删规范或代定规则来过关。')

def current_document_checks(p):
    # Evaluate actual stored explanation text against the very citations it claims.
    # Do not apply unrelated global rules to another function or rewrite history.
    checks=[]
    for kind,artifact in p.get('documents',{}).items():
        # A later edit cannot change the meaning of citations in an already
        # frozen document. Missing historical snapshots do not prove support.
        context=artifact.get('product_context_snapshot',{})
        business=artifact.get('business_fact_snapshot',{})
        items={i['id']:i for i in artifact.get('item_snapshot',[]) if i.get('selection_status')=='selected'}
        scope=[r for r in context.get('scope_ids',[]) if r in items]
        visible=readable_document_blocks(artifact)
        for section in artifact.get('content',{}).get('sections',[]):
            for index,block in enumerate(section.get('blocks',[])):
                location=f"{artifact['id']}.{section['section_id']}.blocks[{index}]"
                basis=block.get('evidence_refs')
                if block.get('kind')!='explanation' or not basis or location not in visible:continue
                refs=[r for r in basis.get('normative_refs',[]) if r in items]
                keys=[r for r in basis.get('context_refs',[]) if r in context]
                claims=[r for r in basis.get('business_claim_refs',[]) if r in business]
                evidence='\n'.join([context[r] for r in keys]+[items[r]['statement'] for r in refs]+[business[r]['text'] for r in claims])
                existing='\n'.join([context[r] for r in keys if r=='current_state']+[business[r]['text'] for r in claims])
                scenario='\n'.join([context[r] for r in keys if r!='current_state']+[items[r]['statement'] for r in refs])
                for issue in explanation_issues(visible[location],evidence,existing,scenario):
                    checks.append(dict(check_id='DOC:'+location+':'+issue['dimension'],object_id=artifact['id'],document_type=kind,document_version=artifact.get('document_version'),location=location,related_refs=refs or scope,**issue))
    return checks


DOCUMENT_LOCATION = re.compile(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.blocks\[\d+\]')
DOCUMENT_CHECK = re.compile(r'DOC:[A-Za-z0-9_.-]+(?:\.blocks\[\d+\])?:[A-Za-z0-9_]+')
DOCUMENT_ERROR = re.compile(r'误写|有误|错误|泛化|弱化|超出|断言|风险')
DOCUMENT_DIMENSIONS = {
    'failure_condition':re.compile(r'明确失败|失败场景|失败条件|失败时|失败后'),
    'success_quantifier':re.compile(r'整批成功|全部成功|部分成功|成功量词'),
    'suggestion_obligation':re.compile(r'培训|交付义务|建议.*义务'),
    'existing_implementation':re.compile(r'既有实现|现有.*(?:支持|实现|覆盖)|已覆盖|实现证据'),
}


def readable_document_blocks(artifact):
    from .document_reader import reader_document
    reader=reader_document(artifact, include_locations=True)
    return {block['source_location']:block.get('text') or ''
            for section in reader['sections'] for block in section['blocks']}


def validate_document_findings(findings,p,checks):
    """Validate every claimed witness, including extras after coverage is met."""
    ids={c['check_id'] for c in checks}
    blocks={};by_kind={}
    for kind,artifact in p.get('documents',{}).items():
        visible=readable_document_blocks(artifact)
        blocks.update(visible)
        by_kind[kind]=list(visible.values())
    for finding in findings:
        message=finding['message']
        claimed=DOCUMENT_CHECK.findall(message)
        require(all(ref in ids for ref in claimed),'SEMANTIC_BLOCKED',
            '复核引用了当前实际文档中不存在的语义检查：'+str([ref for ref in claimed if ref not in ids]))
        inherited={c['dimension'] for c in checks if c['check_id'] in claimed}
        for clause in clauses(message):
            locations=DOCUMENT_LOCATION.findall(clause)
            require(all(location in blocks for location in locations),'SEMANTIC_BLOCKED','复核文档段落定位不存在：'+str(locations))
            quotes=[a or b or c for a,b,c in re.findall(r'“([^”]+)”|「([^」]+)」|"([^"]+)"',clause)]
            for location in locations:
                require(all(quote in blocks[location] for quote in quotes),'SEMANTIC_BLOCKED','复核所引原句不在指定段落：'+location)
            # A paraphrase or "same error" assertion also needs a real witness.
            # Unknown dimensions remain for model/human review; these four are
            # the bounded deterministic dimensions already checked above.
            error=DOCUMENT_ERROR.search(clause) and not re.search(r'没有.*错误|不存在.*错误|无此错误|未误写|表述正确',clause)
            if error:
                dimensions={dimension for dimension,pattern in DOCUMENT_DIMENSIONS.items() if pattern.search(clause)} or inherited
                kinds={kind.lower() for kind in re.findall(r'(?<![A-Za-z])(PRD|MRD)(?![A-Za-z])',clause,re.I)}
                require(all(kind in by_kind for kind in kinds),'SEMANTIC_BLOCKED','复核声称错误的文档不存在：'+str(sorted(kinds)))
                require(not (kinds or locations) or not re.search(r'同样错误|同样问题|相同错误',clause) or dimensions,
                        'SEMANTIC_BLOCKED','同样错误断言缺少本条finding的冲突维度与真实位置证据；不能借另一条finding推断。')
                for location in locations:
                    require(all(any(c['location']==location and c['dimension']==dimension for c in checks) for dimension in dimensions),
                        'SEMANTIC_BLOCKED','指定段落不存在所声称的语义错误：'+location)
                for kind in kinds:
                    require(all(any(quote in text for text in by_kind.get(kind,[])) for quote in quotes),'SEMANTIC_BLOCKED',
                        '复核所引错误原句不在指定文档：'+kind.upper())
                    require(all(any(c['document_type']==kind and c['dimension']==dimension for c in checks) for dimension in dimensions),
                        'SEMANTIC_BLOCKED','指定文档不存在所声称的语义错误：'+kind.upper())
