"""Regression against the gate implementation before navigation was added."""

import copy

from app.contracts import gate, gate_blockers, document_gaps, review_target
from app.core import brief_hash
from app.product_flow import status, question_open
from app.requirements import delivery_items
from app.source_fidelity import project_issues, DIMENSIONS
from app.store import Store
from tests.helpers import prepared


def original_gate(p):
    issues = [f['location']+': '+DIMENSIONS[f['dimension']]+' / '+f['quote'] for f in project_issues(p)]
    steps=status(p)
    first=next((s for s in steps[:5] if not s['retired'] and not s['complete']),None)
    if first:issues+=['请完成第'+str(first['display_step'])+'步「'+first['title']+'」核对']+first['missing']
    for kind in p.get('delivery_scope',{}).get('documents',['mrd','prd']):
        if kind not in p['documents']:issues.append('缺少 '+kind.upper()+' 评审稿')
    if 'prd' not in p['documents']:
        return issues+['缺少 PRD 内容对象；MRD 不能代替 PRD 确认']
    for doc in p['documents'].values():
        if doc['brief_hash'] != brief_hash(p):issues.append('文档与当前底稿不一致，请重新生成')
        issues.extend(document_gaps(doc['content'], p))
    if p.get('document_update_needed'):
        issues.append('页面变化后文档需要更新：'+'、'.join(p.get('stale_document_kinds',[])))
    issues.extend(q['question'] for q in p['questions'] if q['blocking'] and question_open(q))
    review = p.get('review')
    if not review or review['target_hash'] != review_target(p):issues.append('需要审查当前底稿和文档')
    else:
        result=review['response']['result']
        if result['assessment'] != 'ready_for_human_review' or any(f['severity'] == 'blocker' for f in review['response']['findings']):issues.append('语义审查仍有阻塞项')
        if result.get('required_decisions'):issues.append('语义审查仍有必要决定待产品处理：'+'；'.join(result['required_decisions']))
        reviewed=set(result.get('reviewed_refs',[]))
        uncovered=[i['id'] for i in delivery_items(p) if i['kind'] in ('requirement','rule','acceptance') and i['id'] not in reviewed]
        if uncovered or not result.get('perspectives'):
            issues.append('语义审查覆盖不足：'+('未审查规范条目 '+ '、'.join(uncovered) if uncovered else '缺少审查视角'))
    if not any(i['selection_status'] == 'selected' and i['applies_to']=='to_be' and i['kind'] == 'requirement' for i in p['items']):issues.append('本期没有已选需求')
    if not any(i['selection_status'] == 'selected' and i['applies_to']=='to_be' and i['kind'] == 'acceptance' for i in p['items']):issues.append('缺少已选验收条件')
    selected=delivery_items(p)
    for req in (i for i in selected if i['kind']=='requirement'):
        if not any(ac['kind']=='acceptance' and (req['id'] in ac['related_refs'] or ac['id'] in req['related_refs']) for ac in selected):issues.append('需求缺少关联验收条件：'+req['id'])
    return list(dict.fromkeys(issues))


def test_gate_compatibility_and_read_only(tmp_path):
    p=prepared(Store(tmp_path))
    cases=[copy.deepcopy(p)]
    for change in ('missing_prd','stale_docs','question','acceptance','scope','review'):
        case=copy.deepcopy(p)
        if change=='missing_prd':del case['documents']['prd']
        elif change=='stale_docs':
            for doc in case['documents'].values():doc['brief_hash']='old'
        elif change=='question':case['questions'][0]['blocking']=True
        elif change=='acceptance':
            case['items'][0]['related_refs']=[];case['items'][2]['related_refs']=[]
        elif change=='scope':case['items'][0]['selection_status']='candidate'
        else:case['review']=None
        cases.append(case)
    for case in cases:
        before=copy.deepcopy(case)
        rows=gate_blockers(case)
        assert [row['message'] for row in rows]==original_gate(case)
        assert gate(case)==original_gate(case)
        assert all(row['actions'] and row['key'] and row['code'] for row in rows)
        assert case==before


def test_actions_use_real_objects(tmp_path):
    p=prepared(Store(tmp_path))
    p['questions'][0]['blocking']=True
    p['items'][0]['related_refs']=[];p['items'][2]['related_refs']=[]
    rows=gate_blockers(p)
    assert any(a['kind']=='question' and a['question_id']==p['questions'][0]['id']
               for row in rows for a in row['actions'])
    assert any(a['kind']=='add_acceptance' and a['item_id']==p['items'][0]['id']
               for row in rows for a in row['actions'])
    assert any(a['kind']=='stage' and a['step']==next(s['step'] for s in status(p)[:5] if not s['retired'] and not s['complete'])
               for row in rows for a in row['actions'])


def test_prd_early_return_keeps_duplicate_messages(tmp_path):
    p=prepared(Store(tmp_path));del p['documents']['prd']
    assert gate(p)==original_gate(p)
    assert gate(p)[-1]=='缺少 PRD 内容对象；MRD 不能代替 PRD 确认'
