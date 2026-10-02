"""Bounded semantic witnesses and a shared review contract, never business decisions."""
import re
from .core import require

VERSION = 'semantic-quality-9'
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
    'counting_definition_facts按来源、当前字段、人工回答和实际文档位置区分定义缺失与定义矛盾。'
    '已写Unicode码点或扩展字素簇代表单位证据存在，不能虚报来源没有单位；但显式待PM确认采纳的建议只是候选，不能当成本期规范。'
    'decision_status及decision_evidence区分待采纳、明确人工确认和未见采纳表态；本期口径未确定可追问待采纳候选，不能反过来否认已有明确决定。'
    '同文人工确认须绑定本计数单位或明确指回该计数方案；其它能力的确认不证明采纳。定义或采纳存在都不证明内部一致。'
    '明确撤回只作用于同一计数对象；未采纳的替代建议不撤销已有本期决定。来源有明确对象ID时分开核对同名计数建议。'
    '例如已定义码点却又说每个emoji恒为一码点，应报告定义矛盾，给可复算反例并请PM选择，不能把矛盾改称缺定义或替PM选。'
)
UNIT = re.compile(r'码点|code\s*points?|字素簇|grapheme', re.I)
CODEPOINT = re.compile(r'(?:Unicode\s*)?码点|code\s*points?', re.I)
ONE_EMOJI = re.compile(r'(?:emoji|表情(?:符号)?)[^。；;\n]{0,35}?(?:按|计为|算作|计作|counts?\s+as)[^。；;\n]{0,8}?(?:一个|1个|one|1)\s*(?:Unicode\s*)?(?:码点|code\s*point)', re.I)

def counting_quotation(sentence, match):
    """A local correction governs only its exact quoted counting assertion."""
    for pattern in (r'“[^”]*”', r'「[^」]*」', r'『[^』]*』', r'"[^"\n]*"'):
        for quote in re.finditer(pattern, sentence):
            if not (quote.start() < match.start() and match.end() < quote.end()):continue
            prefix=re.split(r'[，,；;。\n]',sentence[:quote.start()])[-1]
            verb=r'(?:删除|删去|去掉|移除|撤回|撤销|弃用|纠正|修正)'
            correction=re.search(verb+r'(?:(?:或|并且|并|且)'+verb+r')?(?:原有|原文中的|原文|该)?$',prefix)
            if not correction:continue
            negated=re.search(r'不|未|没有|无须|无需',prefix[:correction.start()][-12:])
            return 'retained' if negated else 'withdrawn',sentence[quote.start()+1:match.start()]
    return None,None


def counting_witness(text, *, alternative=False):
    if not CODEPOINT.search(text or ''): return None
    for sentence in re.split(r'[。；;\n]', text):
        matches=[]
        for match in ONE_EMOJI.finditer(sentence):
            # Qualifiers/negation must govern this occurrence of the counting
            # predicate. An unrelated later emoji colour cannot waive it.
            prefix=re.split(r'[，,]|且|但是|但|并且',sentence[:match.start()])[-1][-12:]
            if alternative:
                quotation,quoted_prefix=counting_quotation(sentence,match)
                if quotation=='withdrawn':continue
                # "Do not delete <quote>" negates deletion, not the quoted rule.
                if quotation=='retained':prefix=quoted_prefix[-12:]
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


def counting_option_examples(witness):
    """Two checked alternatives, never a selected business counting unit."""
    example=witness['counterexample'];limit=example['extended_grapheme_clusters']
    points=example['codepoints'];letters=limit-1
    return [
        f'按Unicode码点分别计数：👍🏽占2个码点，{letters}个A加👍🏽共{points}个码点，超过上限{limit}而拒绝；复合emoji不能恒按一码点。',
        f'按扩展字素簇计数：👍🏽占1个扩展字素簇，{letters}个A加👍🏽共{limit}个扩展字素簇，符合上限{limit}而允许；需求和验收中的Unicode码点口径须同步改为扩展字素簇。',
    ]

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
        finding_binding='semantic_checks每项check_id唯一绑定object_id、location、dimension；尚存冲突的review必须在对应blocker.message和required_decisions同时原样写check_id，给反例与PM选择。不能用同一对象的其他blocker、其他计数字段或其他问题抵消。ingest/clarify/change中同一条可回答问题必须联合绑定冲突对象、source_id/excerpt_id与计数决定；问题直接询问计数单位或明确给出码点/字素簇选择，不能用两条问题分别命中，也不能以码点颜色等其它维度过关。计数options可空；有选项时每个备选内部必须一致，不能把复合emoji按一个码点计说成与Unicode码点口径一致。明确回答与修订或当前条款必须选择相同计数单位，否定某一单位或仍待确认不等于已决定。复用同对象同维度开放问题。',
        candidate_question_binding='先为本轮候选分配temp_id。若新增计数需求/验收候选仍保留旧争议的emoji一码点引句，即使标注待确认或条件适用，对应同一阻塞计数问题的related_refs仍必须同时包含该候选temp_id和原关联需求/验收ID，source_refs绑定该争议原片段。候选自己的related_refs不替代问题对候选的直接绑定；只问原条目、用另一问题补候选或只写why都不能覆盖。revise候选可按其明确target_item_id绑定。',
        counting_options_policy='counting_option_examples给出经反例计算核对的两种备选，不是人工选择。计数问题可逐字使用其options；不要附加“与现有emoji一码点表述一致”等判断，因为码点选项要求纠正复合emoji恒为一码点的错误，字素簇选项要求同步改写Unicode码点口径。options仅写可选的自洽规则，不引用含矛盾的旧句作为本选项规则或一致性理由；旧句和差异放why说明。',
        failure_condition_policy='原记录不变只绑定取消、超限拒绝或明确未提交失败等有依据的未写入条件。超时/结果未知时可保留输入，但不能断言原记录不变或已经改变；先查询同一请求结果，禁止自动重发。只有查询确认未提交或收到明确未提交结果后，才可断言原记录不变并允许再次确认。解释/摘要/验收说明不得将“明确未提交的失败、超时或结果未知等情形”并列概括为原记录不变；不能靠后附“不自动重发”或“遵循既定路径”弥补这个错误。',
        document_finding_binding='semantic_document_checks是正文引用语义风险，按check_id定位实际解释原句及引用，逐项在finding.message原样写ID。每一个声称的检查ID、文档/章节/块位置和该位置的原句都必须真实，另一文档没有对应错误时不能连带报告。章节标题与原句分别核对；原句可用省略号缩略，但各非空片段须依次真实出现在同一指定段落。引用来源作对比时不要把来源原文称为该段落原句。条件弱化/事实归属/义务外扩写纠错finding，不创造业务required_decisions；既有实现例外必须有同能力、同对象和同场景的肯定证据，否认/不确定/仅支持其它能力不能解除风险。',
        quotation_policy='document_quote_witnesses按真实文档位置列出计数表述，semantic_document_checks列解释原句。引用每一处必须取该处真实片段；需求的“emoji按一个码点计”与验收的“emoji一个码点”字面不同，不能用前一句加引号声称两处同文。分别写位置与原句，或不用引号归纳含义；不能把多个不同段落与同一引号原句联合绑定。',
        scope='只识别可定位的语义问题，不以字数、来源数量或模板完整作为通过标准；启发式不是通用语义证明。正式确认前最低覆盖全部本期已选需求、规则和验收条目；不完整审查保留缺口，不能解除gate。')


def source_pairs(refs):
    return {(r.get('source_id'),r.get('excerpt_id')) for r in refs}


def counting_question(q):
    text=q.get('question','')
    # The actual decision must concern counting. A rationale mentioning length
    # cannot turn a colour/font question into a counting decision.
    direct_unit=re.search(r'计数(?:单位|口径|方式)|(?:按|用|以).{0,6}单位.{0,4}(?:计数|统计|计算)',text)
    return bool(q.get('blocking') and (UNIT.search(text) or direct_unit) and
        not re.search(r'颜色|字体|colou?r|font',text,re.I) and
        (direct_unit or re.search(r'计数|计量|口径|长度|怎么算|如何计|count|length',text,re.I) or
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


ADOPTION_PENDING = re.compile(r'建议|候选|备选|待(?:采纳|确认|决定)|(?:尚未|未|不)(?:采纳|采用|确认)|'
    r'(?:需|须|需要).{0,12}(?:PM|产品经理).{0,8}确认|可以.{0,8}(?:按|采用)',re.I)
ADOPTION_CONFIRMED = re.compile(r'(?:PM|产品经理|人工).{0,8}(?:已|明确)(?:确认|采纳|决定|选择)|'
    r'(?:经|由)(?:PM|产品经理|人工)(?:确认|采纳)',re.I)
ADOPTION_REVOKED = re.compile(r'(?:PM|产品经理|人工).{0,8}(?:已|明确)(?:撤回|撤销|取消)',re.I)
ADOPTION_REFERENT = re.compile(r'(?:上述|前述|该|此|这个)(?:计数)?(?:方案|建议)|计数(?:口径|单位|方式)|(?:后才?|前).{0,5}采纳')


def counting_adoption(text,units):
    """Only an explicit, locally bound confirmation can adopt a proposal."""
    states={u:'not_stated' for u in units};active=set();evidence=[]
    for clause in clauses(text or ''):
        named={'codepoints' if CODEPOINT.fullmatch(m.group()) else 'graphemes' for m in UNIT.finditer(clause)}
        if named:active=named & units
        if not active or not (named or ADOPTION_REFERENT.search(clause)):continue
        confirmation=ADOPTION_CONFIRMED.search(clause)
        revocation=ADOPTION_REVOKED.search(clause)
        revoked=revocation and not re.search(r'未|没有|并非|不能|无法',clause[:revocation.end()])
        negated=confirmation and (re.search(r'未|没有|并非|不能|无法|尚待|待确认',clause[:confirmation.end()]) or
            re.search(r'不(?:采纳|采用|选择)|拒绝(?:采纳|采用)|未(?:采纳|采用)',clause[confirmation.end():]))
        if revoked:state='pending'
        elif confirmation and not negated:state='confirmed'
        elif ADOPTION_PENDING.search(clause) and not re.search(r'无需|不需要|不必',clause):state='pending'
        else:continue
        for unit in active:states[unit]=state
        evidence.append(dict(quote=clause,declared_units=sorted(active),decision_status=state))
    status='confirmed' if 'confirmed' in states.values() else 'pending' if 'pending' in states.values() else 'not_stated'
    return dict(decision_status=status,decision_evidence=evidence)


def counting_definition(text):
    """Definition presence is separate from consistency and from a PM choice."""
    units=set()
    for clause in clauses(text or ''):
        if re.search(r'尚未|未决定|未确定|待确认|待决定|不确定|还是|undecided',clause,re.I) or clause.endswith(('？','?')):
            continue
        for match in UNIT.finditer(clause):
            prefix=re.split(r'并且|且|但是|但',clause[:match.start()])[-1]
            if re.search(r'不按|不采用|不选择|不使用|并非|不是|而非|不得|不能|不应',prefix):continue
            if not (re.search(r'计数|计量|口径|长度|分别计|逐.*计|计入|最多|上限|不超过|count|length|maximum',clause,re.I)
                    or re.search(r'(?:按|采用|选择|以).*(?:码点|字素簇|code\s*points?|grapheme)',clause,re.I)
                    or re.search(r'\d',prefix)):
                continue
            units.add('codepoints' if CODEPOINT.fullmatch(match.group()) else 'graphemes')
    witness=counting_witness(text or '');adoption=counting_adoption(text,units)
    return dict(dimension='text_length_counting_unit',definition_present=bool(units),
                declared_units=sorted(units),status='contradictory' if witness else 'undecided' if units and adoption['decision_status']=='pending' else 'defined' if units else 'missing',**adoption)


def counting_source_scopes(text,owners):
    """Explicit object IDs bound local references; same unit names do not bind."""
    patterns={ref:re.compile(r'(?<![A-Za-z0-9_-])'+re.escape(ref)+r'(?![A-Za-z0-9_-])') for ref in owners}
    if not any(pattern.search(text) for pattern in patterns.values()):return None
    scoped={};active=set()
    for clause in clauses(text):
        named={ref for ref,pattern in patterns.items() if pattern.search(clause)}
        if named:active=named
        for ref in active:scoped.setdefault(ref,[]).append(clause)
    return scoped


def counting_definition_facts(p,excerpts=None):
    """Read-only facts keep object/source/location; never merge unrelated units."""
    facts=[]
    def add(origin,location,text,refs=(),sources=(),definition_text=None):
        if not (UNIT.search(text or '') or re.search(r'计数|口径|长度|字符|count|length',text or '',re.I)):return
        facts.append(dict(origin=origin,location=location,quote=text,related_refs=list(refs),
                          source_refs=list(sources),**counting_definition(text if definition_text is None else definition_text)))
    items=[i for i in p.get('items',[]) if i.get('selection_status')=='selected' and i.get('applies_to')=='to_be']
    for item in items:
        for field,text in [('statement',item.get('statement',''))]+[(f'behavior.{k}',v) for k,v in (item.get('behavior') or {}).items() if isinstance(v,str)]:
            add('current_item',item['id']+'.'+field,text,[item['id']],item.get('source_refs',[]))
    for q in p.get('questions',[]):
        if q.get('status')=='answered' and not q.get('superseded_by') and not q.get('out_of_scope_reason'):
            add('human_answer',q['id']+'.answer',q.get('answer') or '',[q['id']]+q.get('related_refs',[]),q.get('answer_source_refs',[]))
    if excerpts is None:
        excerpts=[e for s in p.get('sources',[]) if not s.get('excluded') for e in s.get('excerpts',[])]
    for e in excerpts:
        pair=(e['source_id'],e['id'])
        owners=[i['id'] for i in items if pair in source_pairs(i.get('source_refs',[]))]
        text=e.get('text','');scoped=counting_source_scopes(text,owners)
        sources=[dict(source_id=e['source_id'],excerpt_id=e['id'])]
        if scoped is None:add('source',e['source_id']+'/'+e['id'],text,owners,sources)
        else:
            for ref,parts in scoped.items():add('source',e['source_id']+'/'+e['id'],text,[ref],sources,'；'.join(parts))
    for artifact in p.get('documents',{}).values():
        visible=readable_document_blocks(artifact)
        for section in artifact.get('content',{}).get('sections',[]):
            for index,block in enumerate(section.get('blocks',[])):
                location=f"{artifact['id']}.{section['section_id']}.blocks[{index}]"
                if location in visible:add('document',location,visible[location],block.get('ref_ids',[]))
    return facts


DEFINITION_TOPIC = re.compile(r'计数(?:单位|口径|方式|定义)|长度.{0,8}(?:单位|计算|计数)|(?:按|用|以)哪种.{0,4}(?:单位|口径)|counting\s+(?:unit|definition)',re.I)
DEFINITION_MISSING = re.compile(r'(?:尚未|未|没有)(?:明确|定义|指定|确定|说明|提供|给出|标明|标注)|缺少|缺失|不明确|不清楚|undefined|missing',re.I)
DEFINITION_UNDECIDED = re.compile(r'(?:尚未|未|没有)(?:确定|决定|确认|采纳)|待(?:确认|决定|采纳)|undecided|unconfirmed',re.I)


def definition_issue(text):
    """Classify an asserted gap, not quoted evidence or a denial of that gap."""
    prose=re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"','',text)
    if not DEFINITION_TOPIC.search(prose):return None
    for match in re.finditer(DEFINITION_MISSING.pattern+'|'+DEFINITION_UNDECIDED.pattern,prose,re.I):
        if not re.search(r'不是|并非|不应(?:说|称|认为)|不能(?:说|称|认为)',prose[:match.start()]):
            return 'undecided_definition' if DEFINITION_UNDECIDED.fullmatch(match.group()) else 'missing_definition'
    return None


def validate_definition_claims(value,p,excerpts=None):
    facts=counting_definition_facts(p,excerpts)
    by_location={f['location']:f for f in facts}
    statements=[(f,f.get('message','')+' '+f.get('suggested_resolution','')) for f in value.get('findings',[])]
    from .understanding import leaves
    statements += [(q,q.get('question','')+' '+q.get('why','')) for q in leaves(value.get('questions',[]))]
    for statement,text in statements:
        active=[]
        for clause in clauses(text):
            locations=DOCUMENT_LOCATION.findall(clause)
            if locations:active=locations
            issue=definition_issue(clause)
            if not issue:continue
            # A named block owns its unit label; parent rules cannot supply a
            # missing local label, or turn a present local label into a gap.
            if active:
                bound=[by_location[loc] for loc in active if loc in by_location]
            else:
                refs=set(statement.get('related_refs',[]));pairs=source_pairs(statement.get('source_refs',[]))
                bound=[f for f in facts if f['origin']!='document' and (
                    f['origin']!='source' and refs & set(f['related_refs']) or
                    f['origin']=='source' and pairs & source_pairs(f['source_refs']) and
                    (not refs or not f['related_refs'] or refs & set(f['related_refs'])))]
            contradictory=[f for f in bound if f['definition_present'] and
                (issue=='missing_definition' or f['decision_status']!='pending')]
            message='计数定义已存在，不能报告定义缺失；' if issue=='missing_definition' else '计数口径已有明确依据，不能报告仍未确定；'
            require(not contradictory,'SEMANTIC_BLOCKED',
                    message+'核对定义是否矛盾，保留反例与PM选择：'+str([f['location'] for f in contradictory]))


def item_counting_choices(item):
    texts=[item.get('statement','')]+[v for v in (item.get('behavior') or {}).values() if isinstance(v,str)]
    return {choice for text in texts if (choice:=counting_choice(text))}


def validate_analysis(value,p,excerpts):
    if value['stage'] not in ('ingest','clarify','change'):return
    validate_definition_claims(value,p,excerpts)
    from .understanding import leaves
    items={i['id']:i for i in p.get('items',[])}
    pending=[q for q in p.get('questions',[]) if q.get('blocking') and q.get('status')!='answered' and not q.get('superseded_by') and not q.get('out_of_scope_reason')]
    questions=leaves(value['questions'])+pending
    require(all(not counting_witness(option,alternative=True) for q in leaves(value['questions'])
                if counting_question(q) for option in q.get('options',[])),
            'SEMANTIC_BLOCKED','计数澄清的备选自身仍含码点与每个emoji恒为一码点的矛盾；保留开放问题或给出一致选项。')
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
    validate_definition_claims(value,p)
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


NEGATIVE = re.compile(r'不应|不得|不能|不要|并非|不是|不算|不代表|不表示|不保证|不证明|不表明|不声称|不宣称|不认定|不主张|未必|尚未|未支持|未实现|未覆盖|没有证据|无证据|不确定|待验证|待核对')
IMPLEMENTED = re.compile(r'(?:已|已经)(?:支持|实现|覆盖|能|可以)')
UNCERTAIN_IMPLEMENTATION = re.compile(r'可能|或许|似乎|是否|能否|尚待|待确认|未确认|未证实|未经验证|无法(?:证明|证实|确认)|据说|据称')
CAPABILITIES = ('审计','登录','查看','推送','导出','批量','查询','编辑','保存')
SUBJECTS = ('备注','姓名','联系人','电价','时段','站点','任务','附件','文件','工单','标题','详情')
SCENARIOS = ('成功','失败','超时','未知')
RULE_SUBJECTS = (
    r'同日(?:未来)?安排|同(?:一)?(?:生效)?日期.{0,6}未来安排|未来生效安排',
    r'数量(?:边界|限制|上限)|单批.{0,6}(?:数量|上限)',
    r'批量原子|整批原子|原子处理',
)


def existing_rule_supported(claim,existing):
    """A current rule needs an affirmative current rule, not a to-be decision."""
    if not positive_match(r'(?:既有|原有|现有|现行)(?:约束|规则|校验|限制)',claim):return True
    subjects=[pattern for pattern in RULE_SUBJECTS if re.search(pattern,claim)]
    if not subjects:return True
    for pattern in subjects:
        support=any(re.search(pattern,sentence) and
            re.search(r'校验|拒绝|允许|限制|上限|原子|约束|规则',sentence) and
            not re.search(r'没有证据|无证据|不确定|尚未|未确认|未证实|待确认|待验证|无法确认|是否',sentence)
            and not re.search(r'(?:没有|无|未|不能|无法|不曾|不再).{0,8}(?:校验|限制|约束|规则|原子)',sentence)
            for sentence in re.split(r'[。；;\n]',existing))
        if not support:return False
    return True


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
    scenarios={word for word in SCENARIOS if word in claim}
    inherits_scenario='审计' in capabilities or re.search(r'这一场景|上述场景|本场景|该场景|此场景|上述行为|这一行为',claim)
    if not scenarios and inherits_scenario:scenarios={word for word in SCENARIOS if word in scenario_evidence}
    # An unrelated desired edit in the same context cannot change what an
    # existing login/export capability must prove. Inherit only its scenario.
    change=bool(re.search(r'变更|修改|更新',claim)) or '审计' in capabilities and any(
        capabilities.issubset({w for w in CAPABILITIES if w in part}) and
        subjects.issubset({w for w in SUBJECTS if w in part}) and
        re.search(r'变更|修改|更新',part)
        for sentence in re.split(r'[。；;\n]',scenario_evidence) for part in clauses(sentence))
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
            qualifier=r'(?<![不未非])明确(?:[^，,。；]{0,12}(?:未提交|未写入|未保存)(?:的)?)?\s*$'
            return (re.search(qualifier,prefix) and not re.search(r'无法明确[^，,]{0,12}$',prefix)
                    and positive_match(qualifier,prefix))
        unknown_pattern=r'(?:超时|(?:结果)?未知)[^。；;\n]{0,70}?原(?:记录|数据).{0,4}不变'
        def confirmed_uncommitted(match):
            # A later result can resolve the unknown branch. Merely listing a
            # known failure beside it does not confirm the unknown request.
            segment=match.group()
            qualifier=r'(?:查询[^，,。；]{0,12}(?:确认|明确)|(?:收到|得到|获得)[^，,。；]{0,12}(?:明确|确认))[^，,。；]{0,8}(?:未提交|未写入|未保存)'
            for proof in re.finditer(qualifier,segment):
                tail=re.split(r'[，,]',segment[proof.end():])[0]
                if (positive_match(re.escape(proof.group()),segment[:proof.end()])
                        and not UNCERTAIN_IMPLEMENTATION.search(proof.group())
                        and not re.search(r'未完成|未执行|未进行|没有完成|没有执行|没有进行',tail)):
                    return True
            return False
        explicit_basis=re.search(r'明确[^。；;\n]{0,16}(?:失败|未提交|未写入|未保存)',evidence)
        conditional=(explicit_basis and '未知' in evidence and (
            any(not explicit_failure(match) and positive_match(re.escape(match.group()),sentence)
                for match in re.finditer(failure_pattern,sentence)) or
            any(not confirmed_uncommitted(match) and positive_match(re.escape(match.group()),sentence)
                for match in re.finditer(unknown_pattern,sentence))))
        weaker=(re.search(r'(?:所有|全部)[^。；]{0,16}成功',evidence) and any(
            positive_match(r'(?:任一|任意一个|部分).{0,16}成功.{0,16}整批成功',part) for part in parts))
        obligation=any(positive_match(r'(?:需|须|必须|应当?).{0,14}(?:培训|说明).{0,12}(?:提示|告知)',part) and
            not re.search(r'建议|可考虑',part) for part in parts) and not re.search(r'(?:需|须|必须|应当?)[^。；]{0,14}(?:培训|说明)',evidence)
        unsupported=not existing_rule_supported(sentence,existing_evidence) or any(positive_match(IMPLEMENTED,part) and
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
                if block.get('kind')=='requirement' and location in visible:
                    for ref in block.get('ref_ids',[]):
                        item=items.get(ref,{})
                        behavior=item.get('behavior',{})
                        actor=behavior.get('actor','');flow=behavior.get('flow','')
                        pm=r'(?:PM|产品经理)'
                        has_pm_step=re.search(pm+r'\s*(?:人工)?(?:核对|确认|审批).{0,8}(?:提交|通过|应用)',flow)
                        explicit_role=re.search(pm+r'.{0,6}(?:业务审批|审批)|(?:提交前|业务流程).{0,8}(?:由)?'+pm+r'.{0,8}(?:核对|确认)',item.get('statement',''))
                        if actor and flow in visible[location] and not re.search(pm,actor) and has_pm_step and not explicit_role:
                            checks.append(dict(check_id='DOC:'+location+':operator_role',object_id=artifact['id'],
                                document_type=kind,document_version=artifact.get('document_version'),location=location,
                                related_refs=[ref],dimension='operator_role',quote=flow))
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
    'failure_condition':re.compile(r'明确失败|失败场景|失败条件|失败时|失败后|超时|结果未知|未知结果'),
    'success_quantifier':re.compile(r'整批成功|全部成功|部分成功|成功量词'),
    'suggestion_obligation':re.compile(r'培训|交付义务|建议.*义务'),
    'existing_implementation':re.compile(r'既有实现|现有.*(?:支持|实现|覆盖)|已覆盖|实现证据|(?:既有|原有|现有|现行)(?:约束|规则|校验|限制)'),
    'operator_role':re.compile(r'运行角色|操作者.{0,12}(?:误写|有误|错误|混淆|混入|替换|审批|确认提交|PM|产品经理)|审批人|PM.{0,8}(?:确认|核对|审批)|产品经理.{0,8}(?:确认|核对|审批)'),
}


def readable_document_blocks(artifact):
    from .document_reader import reader_document
    reader=reader_document(artifact, include_locations=True)
    return {block['source_location']:block.get('text') or ''
            for section in reader['sections'] for block in section['blocks']}


QUOTED = r'“([^”]+)”|「([^」]+)」|"([^"]+)"'
ORIGINAL_QUOTE = re.compile(r'(?:原句|原文|正文)[：:]?\s*(?:'+QUOTED+r')')


def quoted_text(text):
    return [a or b or c for a,b,c in re.findall(QUOTED,text)]


def quote_matches(quote,text):
    """Ellipsis is omission only: preserve order, paragraph and actual words."""
    if quote in text:return True
    if not re.search(r'…|\.{3}',quote):return False
    parts=[part for part in re.split(r'…+|\.{3,}',quote) if part]
    if not parts or any(len(part.strip())<4 for part in parts):return False
    offset=0
    for part in parts:
        found=text.find(part,offset)
        if found<0:return False
        offset=found+len(part)
    return True


def validate_document_findings(findings,p,checks):
    """Validate every claimed witness, including extras after coverage is met."""
    ids={c['check_id'] for c in checks}
    blocks={};by_kind={};titles={};titles_by_kind={}
    for kind,artifact in p.get('documents',{}).items():
        visible=readable_document_blocks(artifact)
        blocks.update(visible)
        by_kind[kind]=list(visible.values())
        section_titles={section['section_id']:section['title'] for section in artifact['content']['sections']}
        titles.update({location:section_titles[location.split('.')[1]] for location in visible})
        titles_by_kind[kind]=set(section_titles.values())
    for finding in findings:
        message=finding['message']
        claimed=DOCUMENT_CHECK.findall(message)
        require(all(ref in ids for ref in claimed),'SEMANTIC_BLOCKED',
            '复核引用了当前实际文档中不存在的语义检查：'+str([ref for ref in claimed if ref not in ids]))
        inherited={c['dimension'] for c in checks if c['check_id'] in claimed}
        active_locations=[]
        for clause in clauses(message):
            matches=list(DOCUMENT_LOCATION.finditer(clause))
            locations=[m.group() for m in matches]
            require(all(location in blocks for location in locations),'SEMANTIC_BLOCKED','复核文档段落定位不存在：'+str(locations))
            if locations:active_locations=locations
            primary=[a or b or c for a,b,c in ORIGINAL_QUOTE.findall(clause)]
            quotes=primary or quoted_text(clause)
            for index,match in enumerate(matches):
                location=match.group()
                # Each adjacent location owns its following quotation. A
                # single-location clause also owns the preceding title/quote.
                segment=clause if len(matches)==1 else clause[match.start():
                    matches[index+1].start() if index+1<len(matches) else len(clause)]
                originals=[a or b or c for a,b,c in ORIGINAL_QUOTE.findall(segment)]
                local_quotes=originals or quoted_text(segment)
                if index==0 and len(matches)>1:
                    local_quotes += [a or b or c for a,b,c in ORIGINAL_QUOTE.findall(clause[:match.start()])]
                require(all(quote_matches(quote,blocks[location]) or
                            not originals and quote==titles[location] for quote in local_quotes),
                        'SEMANTIC_BLOCKED','复核所引原句不在指定段落：'+location)
            if primary and not locations:
                for location in active_locations:
                    require(all(quote_matches(quote,blocks[location]) for quote in primary),
                            'SEMANTIC_BLOCKED','复核所引原句不在指定段落：'+location)
            # A paraphrase or "same error" assertion also needs a real witness.
            # Unknown dimensions remain for model/human review; these four are
            # the bounded deterministic dimensions already checked above.
            error=DOCUMENT_ERROR.search(clause) and not re.search(r'没有.*错误|不存在.*错误|无此错误|未误写|表述正确',clause)
            if error:
                # An explicit "this paragraph" refers to this finding's nearest
                # location; it cannot add an unsupported error after a valid one.
                if not locations and re.search(r'该段|此段|本段|这一段|该位置|此位置|此处',clause):
                    locations=active_locations
                dimensions={dimension for dimension,pattern in DOCUMENT_DIMENSIONS.items() if pattern.search(clause)} or inherited
                # A kind embedded in a block locator is handled by that block's
                # binding; it is not an assertion about the entire document.
                named_clause=DOCUMENT_LOCATION.sub('',clause)
                kinds={kind.lower() for kind in re.findall(r'(?<![A-Za-z])(PRD|MRD)(?![A-Za-z])',named_clause,re.I)}
                require(all(kind in by_kind for kind in kinds),'SEMANTIC_BLOCKED','复核声称错误的文档不存在：'+str(sorted(kinds)))
                require(not (kinds or locations) or not re.search(r'同样错误|同样问题|相同错误',clause) or dimensions,
                        'SEMANTIC_BLOCKED','同样错误断言缺少本条finding的冲突维度与真实位置证据；不能借另一条finding推断。')
                for location in locations:
                    require(all(any(c['location']==location and c['dimension']==dimension for c in checks) for dimension in dimensions),
                        'SEMANTIC_BLOCKED','指定段落不存在所声称的语义错误：'+location)
                for kind in kinds:
                    require(all(any(quote_matches(quote,text) for text in by_kind.get(kind,[])) or
                                not primary and quote in titles_by_kind.get(kind,set()) for quote in quotes),'SEMANTIC_BLOCKED',
                        '复核所引错误原句不在指定文档：'+kind.upper())
                    require(all(any(c['document_type']==kind and c['dimension']==dimension for c in checks) for dimension in dimensions),
                        'SEMANTIC_BLOCKED','指定文档不存在所声称的语义错误：'+kind.upper())
