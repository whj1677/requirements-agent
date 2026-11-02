import copy
import io
from docx import Document
from PIL import ImageFont
from app import detail_rendering as render
from app.exports import add_full_flow_page
from docx.shared import Inches


def large_flow():
    nodes=[dict(id='N'+str(i),kind='decision' if i%6==0 else 'action',label='服务端核对原请求实际处理结果及当前业务状态',actor='云平台服务端',interaction_id='I'+str(i//6)) for i in range(24)]
    edges=[dict(id='E'+str(i),**{'from':'N'+str(i),'to':'N'+str((i+1)%24)},condition='原请求确定被拒绝时保留原输入') for i in range(24)]
    edges.extend([dict(id='E24',**{'from':'N18','to':'N3'},condition='仍未知'),dict(id='E25',**{'from':'N19','to':'N18'},condition='只读查看')])
    return dict(nodes=nodes,edges=edges)


def test_basic_views_measure_the_same_labels_only_notes_as_reader_and_export():
    flow=large_flow()
    for node in flow['nodes']:
        node.pop('interaction_id',None)
        node.pop('actor',None)
        node['label']='查看记录' if node['id']=='N0' else '处理业务动作'
    before=copy.deepcopy(flow)
    legacy=render.flow_views(flow)
    views=render.flow_views(flow,show_ids=False)
    assert views and len(views)<=len(legacy)
    assert sorted(e for view in views for e in view['edge_ids'])==sorted(e['id'] for e in flow['edges'])
    assert {n for view in views for n in view['node_ids']}=={n['id'] for n in flow['nodes']}
    names={n['id']:n['label'] for n in flow['nodes']}
    all_ids={view['id'] for view in views}
    for view in views:
        columns,measured=render._view_layout(flow,view,show_ids=False)
        _,actual=render.flow_svg(render.view_flow(flow,view),view['title'],fit_labels=True,
            columns=columns,show_ids=False,display_notes=render.view_notes(view,labels=names))
        assert (measured['width'],measured['height'])==(actual['width'],actual['height'])
        assert render.view_fit(actual)['edge_font_pt']>=8
        assert all(set(c['view_ids'])<=all_ids for c in view['continuations'])
    assert flow==before


def test_views_preserve_every_original_object_and_actual_word_font():
    flow=large_flow();before=copy.deepcopy(flow);views=render.flow_views(flow)
    assert {n for v in views for n in v['node_ids']}=={n['id'] for n in flow['nodes']}
    assert [e for v in views for e in v['edge_ids']]==[e['id'] for e in flow['edges']]
    allids={v['id'] for v in views}
    doc=Document();doc.styles.add_style('Document Metadata',1)
    s=doc.sections[0];s.page_width=Inches(8.27);s.page_height=Inches(11.69)
    s.top_margin=s.bottom_margin=Inches(.7);s.left_margin=s.right_margin=Inches(.75)
    for view in views:
        assert view['image_uri'].startswith('data:image/svg+xml;base64,')
        for c in view['continuations']:assert set(c['view_ids'])<=allids
        projected=render.view_flow(flow,view)
        for n in projected['nodes']:
            original=next(x for x in flow['nodes'] if x['id']==n['id'])
            if n['id'] in view.get('reference_nodes',{}):assert n['kind']=='reference' and '参见图' in n['label']
            else:assert n==original
        for e in projected['edges']:assert e==next(x for x in flow['edges'] if x['id']==e['id'])
        svg,layout=render.flow_svg(projected,display_notes=render.view_notes(view),fit_labels=True,columns=view.get('columns'))
        png=render.flow_png(projected,display_notes=render.view_notes(view),fit_labels=True,columns=view.get('columns'))
        add_full_flow_page(doc,png,view['title'],view_layout=layout)
        shape=doc.inline_shapes[-1];scale=shape.width.inches*72/layout['width']
        assert layout['font_size']*scale>=9 and layout['edge_font_size']*scale>=8
        assert '局部分图' in svg
    assert flow==before
    stream=io.BytesIO();doc.save(stream);reopened=Document(io.BytesIO(stream.getvalue()))
    assert len(reopened.inline_shapes)==len(views)


def test_basic_flow_uses_existing_readable_views_and_keeps_graph_unchanged(tmp_path,monkeypatch):
    import asyncio
    import base64
    import json
    import xml.etree.ElementTree as ET
    from app import exports
    from app.document_reader import reading_structure
    from tests.test_detail_document import project_fixture,compile_fixture
    flow=large_flow()
    for node in flow['nodes']:
        node.pop('actor');node.pop('interaction_id')
    before=copy.deepcopy(flow)
    def basic_nodes(graph):
        content=dict(document_type='prd',sections=[dict(section_id='BASIC-FLOW',title='基础流程',level=1,
            parent_section_id=None,blocks=[dict(kind='flowchart',text='基础流程',ref_ids=['REQ-0001'],flow=graph)])])
        return reading_structure(content,{})['sections'][0]['reading_nodes']
    nodes=basic_nodes(flow);diagram=next(n for n in nodes if n['kind']=='diagram')
    views=diagram['views'];assert views
    assert {e for v in views for e in v['edge_ids']}=={e['id'] for e in flow['edges']}
    assert [e for v in views for e in v['edge_conditions']]==[
        dict(id=e['id'],condition=e['condition']) for e in flow['edges']]
    assert any(n.get('title')=='流程步骤' for n in nodes)
    table=next(n for n in nodes if n.get('title')=='分支与流转条件')
    assert [row[-1] for row in table['rows']]==[e['condition'] for e in flow['edges']]
    labels={n['id']:n['label'] for n in flow['nodes']}
    continued=next(v for v in views if v['continuations'])
    notes=render.view_notes(continued,labels=labels)
    assert labels[continued['continuations'][0]['node_id']] in '\n'.join(notes)
    assert all(c['node_id'] not in '\n'.join(notes) for c in continued['continuations'])
    assert not any(e['id']+'：' in '\n'.join(notes) for e in flow['edges'])
    small=dict(nodes=[dict(id='s',label='开始',kind='start'),dict(id='e',label='结束',kind='end')],
        edges=[dict(id='se',**{'from':'s','to':'e'})])
    assert not next(n for n in basic_nodes(small) if n['kind']=='diagram')['views']
    p=project_fixture(tmp_path);compile_fixture(p);project_before=copy.deepcopy(p)
    original=exports.reader_document
    def reader(artifact):
        content=original(artifact)
        for section in content['sections']:
            for index,node in enumerate(section['reading_nodes']):
                if node['kind']=='diagram':section['reading_nodes'][index:index+1]=nodes[1:];break
        return content
    monkeypatch.setattr(exports,'reader_document',reader)
    files=asyncio.run(exports.document_files(p,'prd'))
    doc=Document(io.BytesIO(files['PRD.docx']))
    assert len(doc.inline_shapes)==1+len(views)
    for shape,view in zip(list(doc.inline_shapes)[1:],views):
        svg,layout=render.flow_svg(render.view_flow(flow,view),view['title'],show_ids=False,
            columns=view['columns'],fit_labels=True,display_notes=render.view_notes(view,labels=labels))
        scale=shape.width.inches*72/layout['width']
        assert layout['font_size']*scale>=9 and layout['edge_font_size']*scale>=8
        assert base64.b64decode(view['image_uri'].split(',',1)[1]).decode()==svg
        text=[el.text for el in ET.fromstring(svg).iter('{http://www.w3.org/2000/svg}text')]
        assert not any(n['id'] in text for n in flow['nodes'])
    bindings=json.loads(files['document_asset_bindings.json'])
    assert 'diagram_view' in json.dumps(bindings)
    assert flow==before and p==project_before


def test_basic_flow_wire_uses_short_labels_without_changing_saved_graph_schema(tmp_path):
    import jsonschema
    import pytest
    from app.prd import plan_schema, PLAN_SCHEMA_V4
    from tests.test_detail_document import project_fixture
    p=project_fixture(tmp_path)
    label=plan_schema(p,'prd')['properties']['flowcharts']['items']['properties']['flow']['properties']['nodes']['items']['properties']['label']
    assert label['maxLength']==24
    jsonschema.validate('发起查询并等待结果',label)
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate('长'*25,label)
    saved=PLAN_SCHEMA_V4['properties']['flowcharts']['items']['properties']['flow']['properties']['nodes']['items']['properties']['label']
    assert saved['maxLength']>24


def test_diamond_glyph_bounds_inside_sloping_border():
    layout=render.flow_layout(large_flow());_,fontfile,scale=render._flow_image(layout)
    font=ImageFont.truetype(str(fontfile),round(layout['font_size']*scale))
    for box in layout['boxes'].values():
        if box['kind']!='decision':continue
        start=(box['height']-len(box['lines'])*layout['line_height'])/2+layout['font_size']
        for i,line in enumerate(box['lines']):
            left,top,right,bottom=font.getbbox(line,anchor='ms')
            for x in (left/scale,right/scale):
                for y in (top/scale,bottom/scale):
                    assert abs(x)/(box['width']/2)+abs(start+i*layout['line_height']+y-box['height']/2)/(box['height']/2)<1


def test_export_views_assets_markdown_bindings_and_no_empty_sections(tmp_path,monkeypatch):
    import asyncio,json
    from docx.oxml.ns import qn
    from app import exports
    from tests.test_detail_document import project_fixture,compile_fixture
    p=project_fixture(tmp_path);compile_fixture(p);before=copy.deepcopy(p)
    original=exports.reader_document
    flow=large_flow()
    def reader(artifact):
        content=original(artifact)
        for section in content['sections']:
            for node in section['reading_nodes']:
                if node['kind']=='diagram':
                    node['flow']=flow;node['views']=render.flow_views(flow)
        return content
    monkeypatch.setattr(exports,'reader_document',reader)
    files=asyncio.run(exports.document_files(p,'prd'))
    doc=Document(io.BytesIO(files['PRD.docx']));views=render.flow_views(flow)
    assert len(doc.inline_shapes)==1+len(views)
    assert len(doc.sections)==len(views)+3
    bindings=json.loads(files['document_asset_bindings.json'])
    # Asset bindings retain original identity and add separate view records.
    text=json.dumps(bindings)
    assert 'diagram_view' in text
    for view in views:
        assert any(name.endswith('-'+view['id']+'.png') for name in files)
        assert any(name.endswith('-'+view['id']+'.svg') for name in files)
        assert view['title'] in files['PRD.md'].decode('utf-8')
        for edge in view['edge_conditions']:
            assert edge['condition'] in files['PRD.md'].decode('utf-8')
    assert p==before


def test_long_object_explicitly_references_full_original_table_without_mutation():
    flow=dict(nodes=[dict(id='N1',kind='decision',label='超长原文'*100)],edges=[])
    before=copy.deepcopy(flow);views=render.flow_views(flow)
    assert views and '全文见流程步骤表' in render.view_flow(flow,views[0])['nodes'][0]['label']
    assert flow==before
    _,layout=render.flow_svg(render.view_flow(flow,views[0]),display_notes=render.view_notes(views[0]),columns=views[0]['columns'])
    assert render.view_fit(layout)['edge_font_pt']>=8


def test_long_conditions_are_explicit_table_references_and_full_text_retained():
    flow=large_flow();flow['edges'][0]['condition']='原条件全文必须保留'*100
    before=copy.deepcopy(flow);views=render.flow_views(flow)
    view=next(v for v in views if 'E0' in v['edge_ids'])
    assert '原条件全文见分支与流转条件表 E0' in '\n'.join(render.view_notes(view))
    assert next(e for e in view['edge_conditions'] if e['id']=='E0')['condition']==flow['edges'][0]['condition']
    assert flow==before


def test_portrait_only_fit_still_gets_readable_views_and_many_isolated_nodes():
    flow=dict(nodes=[dict(id='A',kind='start',label='开始'),dict(id='B',kind='decision',label='条'*80),dict(id='C',kind='end',label='结束')],edges=[dict(id='E1',**{'from':'A','to':'B'}),dict(id='E2',**{'from':'B','to':'C'})])
    assert render.flow_views(flow)
    flow=large_flow();flow['edges']=[]
    views=render.flow_views(flow)
    assert {n for v in views for n in v['node_ids']}=={n['id'] for n in flow['nodes']}
    for view in views:
        _,layout=render.flow_svg(render.view_flow(flow,view),display_notes=render.view_notes(view),columns=view['columns'],fit_labels=True)
        assert render.view_fit(layout)['edge_font_pt']>=8
