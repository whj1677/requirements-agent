"""Compact reading tables retain the saved business contract and review text."""
import copy
import io
import re

from PIL import Image

from app.detail_rendering import detail_nodes, detail_plaintext, flow_png_pages
from tests.test_detail_document import detail_fixture


def item_fixture():
    return dict(id='REQ-presentation', title='紧凑排版合成验证', detail_contract=detail_fixture())


def text_in(nodes):
    return '\n'.join(str(value) for node in nodes
                     for row in node.get('rows', []) for value in row)


def test_three_column_fields_keep_optional_properties_unknowns_and_permissions():
    item=item_fixture()
    field=item['detail_contract']['fields'][0]
    field.update(data_type='number', source='calculated', required='展示结果时必有',
                 default_value='待确认：不以零代替未知值', constraints='结果必须保留来源精度',
                 enum_values=['候选甲', '候选乙'], unit='kWh', precision='四位小数',
                 time_basis='按站点日历日', formula='两个输入相加', empty_value='缺任一输入则为空',
                 uniqueness='同站点同日唯一', parent_id='F-parent', permissions='管理员不能修改历史结果',
                 rule_refs=['RULE-original-long-id'], acceptance_refs=['AC-original-long-id'])
    before=copy.deepcopy(item)
    nodes=detail_nodes(item, 'fields')
    assert len(nodes)==1 and len(nodes[0]['columns'])==3
    text=text_in(nodes)
    for key,value in field.items():
        if key in ('data_type', 'source'):
            continue
        for expected in value if isinstance(value,list) else [value]:
            assert expected in text, key
            assert expected in detail_plaintext(item,'fields'), key
    assert '数值' in text and '系统计算' in text
    assert item==before


def test_paired_interaction_phases_keep_every_outcome_and_unknown_without_rewriting():
    item=item_fixture()
    interaction=item['detail_contract']['interactions'][0]
    interaction.update(inputs='待确认：是否复用原处理人和期限',
        failure='原请求结果未知时只查询，不得再次建单',
        effects='通知失败不回滚建单', rule_refs=['RULE-original-long-id'],
        acceptance_refs=['AC-original-long-id'])
    before=copy.deepcopy(item)
    nodes=detail_nodes(item,'interactions')
    assert len(nodes)==1 and len(nodes[0]['columns'])==3 and len(nodes[0]['rows'])==5
    text=text_in(nodes)+'\n'+nodes[0]['title']
    for key,value in interaction.items():
        for expected in value if isinstance(value,list) else [value]:
            assert expected in text, key
            assert expected in detail_plaintext(item,'interactions'), key
    assert '成功结果：显示新姓名' in text
    assert '失败结果：原请求结果未知时只查询，不得再次建单' in text
    assert item==before


def test_reference_aliases_are_display_only_and_do_not_replace_business_text():
    item=item_fixture()
    detail=item['detail_contract']
    detail['fields'][0].update(rule_refs=['RULE-original-long-id'],
                              constraints='原文中的 AC-original-long-id 保留不改')
    detail['interactions'][0]['acceptance_refs']=['AC-original-long-id']
    detail['flow']['nodes'][1]['acceptance_refs']=['AC-original-long-id']
    detail['flow']['edges'][0]['rule_refs']=['RULE-original-long-id']
    detail['applicability']['state_transitions']['applicable']=True
    detail['state_transitions']=[dict(id='T-save',from_state='旧姓名',to_state='新姓名',
        trigger='保存',condition='合法姓名',prohibited_actions='不得替换他人姓名',
        acceptance_refs=['AC-original-long-id'])]
    aliases={'AC-original-long-id':'A01','RULE-original-long-id':'R01'}
    before=copy.deepcopy(item)
    for group in ('fields','interactions','flow','state_transitions'):
        view=text_in(detail_nodes(item,group,ref_labels=aliases))
        assert '验收条件：AC-original-long-id' not in view
        assert '业务规则：RULE-original-long-id' not in view
        assert ('验收条件：A01' in view or '业务规则：R01' in view)
        canonical=detail_plaintext(item,group)
        assert ('AC-original-long-id' in canonical or 'RULE-original-long-id' in canonical)
    assert '原文中的 AC-original-long-id 保留不改' in text_in(detail_nodes(item,'fields',ref_labels=aliases))
    assert item==before


def test_complex_flow_is_a_single_complete_image_without_losing_nodes():
    node_ids=['N1','N2','N3','N4','N5','N6','N6a','N6b','N8','N7','N10','N9','N11']
    endpoints=[('N1','N2'),('N2','N3'),('N3','N4'),('N4','N5'),('N4','N11'),
               ('N5','N6'),('N5','N6a'),('N6','N6a'),('N6','N8'),('N6a','N6b'),
               ('N6a','N8'),('N6a','N7'),('N8','N6a'),('N8','N8'),('N7','N9'),
               ('N7','N10'),('N9','N6a'),('N9','N8'),('N9','N6a'),('N6b','N6a')]
    kinds={'N1':'start','N5':'decision','N8':'wait','N10':'end','N11':'end'}
    flow=dict(nodes=[dict(id=nid, label='保留完整内容的合成业务步骤 '+nid,
                         kind=kinds.get(nid,'action'), actor='系统') for nid in node_ids],
              edges=[dict(id=f'E{i}', **{'from':source, 'to':target}, condition='合成分支条件')
                     for i,(source,target) in enumerate(endpoints,1)])
    flow['nodes'][7]['label']='较长的合成步骤说明，完整保留文字以核对整张图的布局空间；这里只检验排版，不定义真实业务规则'
    before=copy.deepcopy(flow)
    pages=flow_png_pages(flow)
    assert len(pages)==1
    captions='\n'.join(caption for _,caption in pages)
    assert sorted(re.findall(r'\bN\d+[a-z]?\b',captions))==sorted(n['id'] for n in flow['nodes'])
    for png,_ in pages:
        image=Image.open(io.BytesIO(png))
        assert image.height/image.width*6.4<=8.0
    assert flow==before
