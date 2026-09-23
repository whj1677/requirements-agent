"""Deterministic engineering fixtures, never presented as live semantic results."""
import copy
from app.core import KIT, brief_hash, read_json, now
from app.contracts import profile, review_target

EXAMPLES=read_json(KIT/'examples/response_examples.json')['examples']


def prepared(store):
    p=store.create('合成工程验证项目')
    with store.edit(p['id'],0,'合成测试设置') as (p,db):
        p['sources']=[dict(id='SRC-0001',title='冻结工程材料',purpose='goal',uri=None,sha256='fixture',created=now(),version=1,excluded=False,parse_status='read',failure_reason='',image_mime=None,excerpts=[dict(id='EX-0001',source_id='SRC-0001',source_hash='fixture',locator='第 1 段',text='管理员维护电价时段；重叠拒绝；相接边界待确认。',method='fixture')])]
        for iid,kind,title,statement in [('REQ-0001','requirement','电价时段维护','管理员能够维护电价时段。'),('RULE-0001','rule','重叠拒绝','保存时段重叠时拒绝保存，原数据不变，编辑内容保留。'),('AC-0001','acceptance','重叠验收','管理员输入重叠时段并保存，应看到拒绝提示；原数据不变，编辑内容保留。')]:
            p['items'].append(dict(id=iid,kind=kind,title=title,statement=statement,applies_to='to_be',epistemic_status='reported',source_refs=[{'source_id':'SRC-0001','excerpt_id':'EX-0001'}],related_refs=['REQ-0001'] if kind!='requirement' else ['RULE-0001','AC-0001'],selection_status='selected',revision=1))
        p['questions']=[dict(id='Q-0001',topic_key='touching-boundary',question='相接边界如何处理？',why='影响边界验收',options=[],blocking=False,related_refs=['REQ-0001'],source_refs=[],status='open',answer=None)]
    p=store.get(p['id'])
    with store.edit(p['id'],p['revision'],'合成派生产物',bump=False) as (p,db):
        wire=copy.deepcopy(EXAMPLES['ui']['result']['spec']);wire['draft_revision']=p['revision']
        p['ui']=dict(spec=wire,brief_hash=brief_hash(p),created=now())
        for kind in ('prd','mrd'):
            doc=complete_document(p,kind)
            p['documents'][kind]=dict(id='DOC-'+kind,content=doc,brief_hash=brief_hash(p),draft_revision=p['revision'],created=now(),style_version='1',generator_version='1.1',limitations=['工程合成样例，不是实际模型输出'])
        response=copy.deepcopy(EXAMPLES['review'])
        response['result']['assessment']='ready_for_human_review'
        response['result']['required_decisions']=[]
        response['findings']=[]
        p['review']=dict(target_hash=review_target(p),response=response,created=now())
    return store.get(p['id'])


def complete_document(p,kind):
    doc=copy.deepcopy(EXAMPLES['prd' if kind=='prd' else 'mrd_document']['result'])
    sid=doc['sections'][0]['section_id']
    doc['sections'][0]['title']='本期范围 规则 验收与未知事项'
    doc['sections'][0]['blocks'].insert(1,{'kind':'rule','ref_ids':['RULE-0001'],'text':None})
    doc['coverage'].append({'item_id':'RULE-0001','section_ids':[sid]})
    doc['reference_mapping']=[]
    for d in profile(kind)['sections']:
        if not d['mapping_required']:continue
        doc['reference_mapping'].append(dict(profile_section_id=d['id'],scope_ref='REQ-0001' if d['repeat_per_function'] else None,disposition='pending',output_section_ids=[sid],reason='合成工程测试仅验证内容映射机制；此维度缺少完整业务材料，保留待确认。'))
    return doc
