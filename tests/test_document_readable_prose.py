import asyncio
import copy
import io

from docx import Document
from docx.oxml.ns import qn
from app.document_prose import prose_nodes, covered_prose, has_layout
from app.document_reader import reader_document, reading_structure
from app.exports import document_files
from app.prd import saved_prd_plan, compile_plan, plan_contract
from app.workflow import apply_response
from tests.test_prd_v2_semantics import checked_project
from tests.fixtures.bms_readable_example import STATEMENT, ACCEPTANCE, BEHAVIOR


def test_prose_only_interprets_explicit_layout_and_keeps_uncertainty():
    text='## 输入与边界\n| 场景 | 预期 |\n| --- | --- |\n| 500 ms | 待确认：停止或继续 |\n\n1. 原始编号不重排。\n2. 保留“成功。失败。”提示。'
    nodes=prose_nodes(text,item_id='R1')
    assert nodes[0]['kind']=='subheading'
    assert nodes[1]['rows']==[['500 ms','待确认：停止或继续']]
    without_separator=prose_nodes(text.replace('| --- | --- |\n',''),item_id='R1')
    assert without_separator==nodes
    assert has_layout('| 参数 | 值 |\n| PCS 更新超时 | 500 ms |')
    assert [n['text'] for n in nodes[2:]]==['1. 原始编号不重排。','2. 保留“成功。失败。”提示。']
    assert all(n['item_id']=='R1' for n in nodes)
    malformed='| 只像表格 | 原文保留 |\n这不是分隔行'
    assert prose_nodes(malformed)==[dict(kind='paragraph',text=malformed,first_line_indent=True)]
    assert not covered_prose('不允许重试。','允许重试。')
    assert covered_prose('保存成功。允许重试。','允许重试。')
    assert prose_nodes('正常正文。')[0]['first_line_indent'] is True
    assert prose_nodes('1. 编号原文。')[0]['first_line_indent'] is False
    assert prose_nodes('I = min(J, I_prev + 1.0 A)')[0]['first_line_indent'] is False


def test_compact_flow_keeps_full_graph_and_every_explicit_condition():
    flow=dict(nodes=[dict(id='s',kind='start',label='开始'),
        dict(id='d',kind='decision',label='是否需要异常恢复'),
        dict(id='normal',kind='end',label='正常执行'),
        dict(id='recover',kind='end',label='进入恢复检查')],
        edges=[dict(id='e1',**{'from':'s','to':'d'},condition=''),
            dict(id='e2',**{'from':'d','to':'normal'},condition='正常运行，未进入异常'),
            dict(id='e3',**{'from':'d','to':'recover'},condition='已进入异常恢复')])
    content=dict(document_type='prd',sections=[dict(section_id='flow',title='处理流程',
        level=1,parent_section_id=None,blocks=[dict(kind='flowchart',text='处理流程',
            ref_ids=['R1'],flow=flow)])])
    original=copy.deepcopy(content)
    reading=reading_structure(copy.deepcopy(content),{},compact_prd=True)
    nodes=reading['sections'][0]['reading_nodes']
    assert next(n for n in nodes if n['kind']=='diagram')['flow']==flow
    table=next(n for n in nodes if n['kind']=='table')
    assert table['columns']==['起点','终点','条件']
    assert table['rows']==[['是否需要异常恢复','正常执行','正常运行，未进入异常'],
        ['是否需要异常恢复','进入恢复检查','已进入异常恢复']]
    assert not any(n.get('title')=='流程步骤' for n in nodes)
    assert content==original


def test_bms_shared_reader_and_word_have_same_rules_and_uniform_body(tmp_path):
    p=checked_project(tmp_path)
    req=p['items'][0];req.update(statement=STATEMENT,behavior=BEHAVIOR)
    ac=next(i for i in p['items'] if i['kind']=='acceptance');ac['statement']=ACCEPTANCE
    apply_response(p,compile_plan(saved_prd_plan(p),p,'prd',include_sketch=False))
    p['documents']['prd']['assembly_version']='prd-plan-4-local'
    before=copy.deepcopy(p)
    reader=reader_document(p['documents']['prd'])
    nodes=[n for s in reader['sections'] for n in s['reading_nodes']]
    assert not any(s['title']=='业务流程说明' for s in reader['sections'])
    assert not any(n.get('table_role')=='behavior' for n in nodes)
    assert any(n['kind']=='subheading' and n['text']=='AC11 状态记录验收' for n in nodes)
    assert not any(n.get('table_role')=='acceptance' for n in nodes)
    files=asyncio.run(document_files(p,'prd',mode='review'))
    doc=Document(io.BytesIO(files['PRD.docx']))
    word_text='\n'.join([x.text for x in doc.paragraphs]+[c.text for t in doc.tables for r in t.rows for c in r.cells])
    for node in nodes:
        if node['kind']=='table':
            for cell in [*node['columns'],*(cell for row in node['rows'] for cell in row)]:
                assert cell in word_text
        elif node['kind']=='paragraph':
            assert node['text'].replace('\n','') in word_text.replace('\n','')
    assert doc.styles['Normal'].font.size.pt==11
    assert doc.styles['Normal'].font.name=='Microsoft YaHei'
    assert next(p for p in doc.paragraphs if p.text.startswith('BMS 根据')).paragraph_format.first_line_indent.pt==22
    assert all(p.paragraph_format.first_line_indent in (None,0) for t in doc.tables for r in t.rows for c in r.cells for p in c.paragraphs)
    for table in doc.tables:
        assert table.rows[0]._tr.find('./'+qn('w:trPr')+'/'+qn('w:tblHeader')) is not None
        for row in table.rows:
            for cell in row.cells:
                assert all(run.font.size.pt==11 and run.font.name=='Microsoft YaHei'
                           for paragraph in cell.paragraphs for run in paragraph.runs)
    assert p==before
    assert '禁止把整篇规则' in plan_contract(p,'prd')['rules']
