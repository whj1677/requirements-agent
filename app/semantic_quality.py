"""Bounded semantic witnesses and a shared review contract, never business decisions."""
import re
from .core import require

VERSION = 'semantic-quality-1'
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
        match=ONE_EMOJI.search(sentence)
        if not match: continue
        # Explicitly qualified single-codepoint emoji is consistent. Do not
        # erase an earlier contradictory assertion because another sentence is clear.
        prefix=sentence[max(0,match.start()-12):match.start()]
        if re.search(r'单码点|单个码点|single.codepoint|基本',prefix,re.I): continue
        if re.search(r'不(?:是|应|能|再)?[^，,]{0,6}$',prefix): continue
        if re.search(r'(?:仅指|仅限)[^，,]{0,12}(?:单码点|single.codepoint)',sentence,re.I): continue
        if re.search(r'(?:emoji|表情(?:符号)?)[^。；]{0,8}(?:不按|不计为|并非)',sentence,re.I): continue
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
    return checks

def contract():
    return dict(version=VERSION,policy=POLICY,
        review_axes=['输入内部一致性与反例','当前决定与历史未知','运行角色与需求确认角色',
                     '既有事实/新增决定/模型建议','全部规范、行为字段、摘要与解释段条件量词'],
        finding_binding='semantic_checks每项check_id唯一绑定object_id、location、dimension；尚存冲突的review必须在对应blocker.message和required_decisions同时原样写check_id，给反例与PM选择。不能用同一对象的其他blocker、其他计数字段或其他问题抵消。ingest/clarify的问题source_refs和related_refs必须关联实际冲突来源/候选，复用同对象同维度开放问题，不建立无关问题来过关。',
        document_finding_binding='semantic_document_checks是正文引用语义风险，按check_id定位实际解释原句及引用，逐项在finding.message原样写ID。条件弱化/事实归属/义务外扩写纠错finding，不创造业务required_decisions；有明确依据的例外需说明证据。不得用另一文档、段落或维度的finding抵消。',
        scope='只识别可定位的语义问题，不以字数、来源数量或模板完整作为通过标准；启发式不是通用语义证明。')

def validate_analysis(value,p,excerpts):
    if value['stage'] not in ('ingest','clarify','change'):return
    pending=[q for q in p.get('questions',[]) if q.get('blocking') and q.get('status')!='answered' and not q.get('superseded_by') and not q.get('out_of_scope_reason')]
    questions=value['questions']+pending
    def counting_question(q):return q.get('blocking') and UNIT.search(q.get('question','')+' '+q.get('why',''))
    def question_for_object(i):return any(counting_question(q) and set(q.get('related_refs',[])) & {i['temp_id'],i.get('target_item_id')} for q in questions)
    def question_for_source(e):return any(counting_question(q) and (e['source_id'],e['id']) in {(r.get('source_id'),r.get('excerpt_id')) for r in q.get('source_refs',[])} for q in questions)
    ambiguous=[i for i in value['proposals'] if counting_witness(i.get('statement','')) or any(counting_witness(v) for v in (i.get('behavior') or {}).values() if isinstance(v,str))]
    # A sourced, unambiguous PM answer permits a proposed correction; it does not
    # forgive a contradictory proposal and never adopts the correction itself.
    def resolved(excerpt):
        pair=(excerpt['source_id'],excerpt['id'])
        items={i['id']:i for i in p.get('items',[])}
        for q in p.get('questions',[]):
            answer=q.get('answer') or ''
            if q.get('status')!='answered' or q.get('superseded_by') or q.get('out_of_scope_reason') or not UNIT.search(answer) or counting_witness(answer):continue
            targets=[items[r] for r in q.get('related_refs',[]) if r in items]
            refs=q.get('source_refs',[])+[r for i in targets for r in i.get('source_refs',[])]
            if pair not in {(r.get('source_id'),r.get('excerpt_id')) for r in refs}:continue
            corrections=[i for i in value['proposals'] if i.get('action')=='revise' and i.get('target_item_id') in q.get('related_refs',[]) and q['id'] in i.get('answer_refs',[]) and not counting_witness(i.get('statement',''))]
            current=[i for i in targets if i.get('selection_status')=='selected' and UNIT.search(i.get('statement','')) and not counting_witness(i.get('statement',''))]
            if corrections or current:return True
        return False
    raw=[e for e in excerpts if counting_witness(e.get('text','')) and not resolved(e)]
    require(all(question_for_object(i) for i in ambiguous) and all(question_for_source(e) for e in raw),'SEMANTIC_BLOCKED',
        '输入或候选的计数口径互相矛盾：码点与每个emoji恒为一码点不能同时覆盖复合emoji。请提出阻塞澄清和具体反例，不替PM选口径。')

def validate_review(value,p):
    checks=current_counting_checks(p)
    result=value['result']
    require(not checks or (result['assessment']=='needs_changes' and
        all(any(c['check_id'] in d and UNIT.search(d) for d in result['required_decisions']) and
            any(f['severity']=='blocker' and set(c['related_refs'])&set(f['related_refs']) and c['check_id'] in f['message'] and UNIT.search(f['message']+' '+f['suggested_resolution']) for f in value['findings']) for c in checks)),
        'SEMANTIC_BLOCKED','当前条款/人工回答存在可复现的计数冲突，不能ready；须定位blocker并列PM计数决定。'+str([c['location'] for c in checks]))
    document_checks=current_document_checks(p)
    require(not document_checks or (result['assessment']=='needs_changes' and
        all(any(c['check_id'] in f['message'] and (not c['related_refs'] or set(c['related_refs']) & set(f['related_refs'])) for f in value['findings']) for c in document_checks)),
        'SEMANTIC_BLOCKED','实际文档解释存在条件、量词或事实身份风险，须逐项定位finding；文案纠错不增加PM待决定：'+str([c['check_id'] for c in document_checks]))

def explanation_issues(text,evidence,existing_evidence):
    issues=[]
    for sentence in re.split(r'[。；;\n]',text):
        if re.search(r'不应|不得|不能|并非|反例|例如',sentence):continue
        conditional=('明确失败' in evidence and '未知' in evidence and
            re.search(r'(?:失败场景|失败时|失败后)',sentence) and '明确失败' not in sentence and
            re.search(r'原(?:记录|数据).{0,4}不变',sentence))
        weaker=(re.search(r'(?:所有|全部)[^。；]{0,16}成功',evidence) and
            re.search(r'(?:任一|任意一个|部分)[^。；]{0,16}成功',sentence) and '整批成功' in sentence)
        obligation=re.search(r'(?:需|须|必须|应当?)[^。；]{0,14}(?:培训|说明)[^。；]{0,12}(?:提示|告知)',sentence) and not re.search(r'建议|可考虑',sentence) and not re.search(r'(?:需|须|必须|应当?)[^。；]{0,14}(?:培训|说明)',evidence)
        assertion=re.search(r'(?:现有|当前|原有)[^。；]{0,30}(?:已|已经)(?:支持|实现|覆盖)',sentence) and not re.search(r'部署后|验收时|上线后|不代表',sentence)
        unsupported=assertion and not re.search(r'(?:已|已经)(?:支持|实现|覆盖)',existing_evidence)
        for dimension,present in [('failure_condition',conditional),('success_quantifier',weaker),('suggestion_obligation',obligation),('existing_implementation',unsupported)]:
            if present:issues.append(dict(dimension=dimension,quote=sentence.strip()))
    return issues

def validate_explanation(text,evidence,existing_evidence,at):
    require(not explanation_issues(text,evidence,existing_evidence),'SEMANTIC_BLOCKED',at+
        ' 解释超出引用语义：核对明确失败/未知结果、全部/部分、建议/义务与既有实现证据；不能删规范或代定规则来过关。')

def current_document_checks(p):
    # Evaluate actual stored explanation text against the very citations it claims.
    # Do not apply unrelated global rules to another function or rewrite history.
    from .prd import verified_context, verified_business_context
    context=verified_context(p);business=verified_business_context(p)
    items={i['id']:i for i in p.get('items',[]) if i.get('selection_status')=='selected'}
    scope=[r for r in (p.get('product_context') or {}).get('scope_ids',[]) if r in items]
    checks=[]
    for kind,artifact in p.get('documents',{}).items():
        for section in artifact.get('content',{}).get('sections',[]):
            for index,block in enumerate(section.get('blocks',[])):
                basis=block.get('evidence_refs')
                if block.get('kind')!='explanation' or not basis:continue
                refs=[r for r in basis.get('normative_refs',[]) if r in items]
                keys=[r for r in basis.get('context_refs',[]) if r in context]
                claims=[r for r in basis.get('business_claim_refs',[]) if r in business]
                evidence='\n'.join([context[r] for r in keys]+[items[r]['statement'] for r in refs]+[business[r]['text'] for r in claims])
                existing='\n'.join([context[r] for r in keys if r=='current_state']+[business[r]['text'] for r in claims])
                for issue in explanation_issues(block.get('text') or '',evidence,existing):
                    location=f"{artifact['id']}.{section['section_id']}.blocks[{index}]"
                    checks.append(dict(check_id='DOC:'+location+':'+issue['dimension'],object_id=artifact['id'],document_type=kind,document_version=artifact.get('document_version'),location=location,related_refs=refs or scope,**issue))
    return checks
