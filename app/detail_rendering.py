"""Deterministic document tables and diagrams from the saved detail contract.

The renderer accepts business data, never model-authored HTML/SVG. The same
layout primitives produce browser SVG and Word PNG; no network or model call.
"""
import base64
import html
import io
import math
import os
from pathlib import Path
import unicodedata

from .core import digest
from .detail_business_scope import eligible_path, LEGACY_PROTOCOL

GROUP_TITLES = {'fields': '字段属性', 'interactions': '交互逻辑',
                'flow': '业务流程说明', 'state_transitions': '状态变化'}
VERSION = 'detail-rendering-8'
FLOW_PALETTE=dict(line='#526d7b',edge_text='#385466',decision='#fff6e0',
                  node='#edf5f8',border='#456778',text='#183849')


def detail_hash(item):
    return digest(item['detail_contract'])


def detail_block(item, group):
    return dict(kind='detail', ref_ids=[item['id']], text=item['title'],
                detail_group=group, detail_hash=detail_hash(item))


def _text(value):
    if isinstance(value, list):
        return '、'.join(str(v) for v in value) or '无'
    if value is None or value == '':
        return '待确认'
    return str(value)


def _references(value, ref_labels=None):
    labels = {'field_refs': '数据字段', 'rule_refs': '业务规则', 'acceptance_refs': '验收条件'}
    aliases = ref_labels or {}
    return '\n'.join(label+'：'+_text([aliases.get(ref, ref) for ref in value[key]])
                     for key, label in labels.items() if value.get(key))


def _table(item, title, columns, rows):
    return dict(kind='table', title=title, text=title, columns=columns, rows=rows,
                item_id=item['id'], detail_hash=detail_hash(item))


def detail_nodes(item, group, ref_labels=None, scope_reviews=()):
    """Reading projection only; aliases never rewrite the saved business contract."""
    nodes = _detail_nodes(item, group, ref_labels)
    detail=item['detail_contract']
    objects=(detail.get('flow',{}).get('nodes',[])+detail.get('flow',{}).get('edges',[])
             if group=='flow' else detail.get(group,[]))
    labels={'editability':'编辑权限','visibility':'可见范围','permissions':'权限','prohibited_actions':'禁止操作',
        'condition':'生效条件','label':'业务动作','actor':'操作者','name':'名称','meaning':'含义',
        'required':'必填条件','default_value':'默认值','constraints':'约束','source':'数据来源',
        'entry':'入口','preconditions':'前置条件','trigger':'触发动作','inputs':'输入','validation':'校验',
        'pending':'处理中反馈','repeat':'重复操作','cancel':'取消','success':'成功结果','failure':'失败结果',
        'effects':'数据影响','from_state':'原状态','to_state':'新状态','enum_values':'枚举选项',
        'data_type':'数据类型','unit':'单位','precision':'精度','time_basis':'时间口径'}
    scoped={p['path']:p for r in scope_reviews if r.get('requirement_id')==item['id'] and r.get('detail_hash')==detail_hash(item) for p in r.get('properties',[]) if p.get('classification')=='technical_only' and eligible_path(p.get('path',''),r.get('protocol',LEGACY_PROTOCOL))}
    rows=[]
    for obj in objects:
        for prop,evidence in obj.get('evidence',{}).items():
            if evidence.get('status') not in ('proposal','unknown') or prop not in obj:continue
            rows.append([obj.get('name') or obj.get('label') or obj['id'],labels.get(prop,prop),
                '建议来源；是否已采纳以本版人工确认记录为准' if evidence['status']=='proposal' else '待确认',
                _text(obj[prop])])
            path='detail_contract.'+group+'.'+obj['id']+'.'+prop
            if path in scoped:
                review=scoped[path]
                rows[-1][2]='unknown：技术未定，业务确认保留；非技术已确认'
                rows[-1][3]+='\n原依据说明：'+_text(evidence.get('reason'))+'\n已知业务范围：'+review['known_business']+'\n保留技术事项：'+review['unknown_technical']+'\n范围审查说明：'+review['explanation']
    if rows:
        nodes.append(_table(item,'属性依据与待确认说明',['业务对象','属性','依据身份','内容'],rows))
    return nodes


def _detail_nodes(item, group, ref_labels=None):
    detail = item['detail_contract']
    applicable = detail.get('applicability', {}).get(group, {})
    if applicable.get('applicable') is False:
        return [dict(kind='paragraph', text='不适用：'+_text(applicable.get('reason')),
                     item_id=item['id'], detail_hash=detail_hash(item))]
    if group == 'fields':
        rows = []
        for field in detail.get('fields', []):
            attributes = [('约束', 'constraints'), ('枚举选项', 'enum_values'),
                          ('单位', 'unit'), ('精度', 'precision'), ('时间口径', 'time_basis'),
                          ('计算公式', 'formula'), ('空值处理', 'empty_value'),
                          ('唯一性范围', 'uniqueness'), ('父字段', 'parent_id')]
            values = '\n'.join(label+'：'+_text(field.get(key)) for label, key in attributes
                               if key in field or key == 'constraints')
            permissions = '\n'.join(label+'：'+_text(field.get(key)) for label, key in
                                      (('可见范围', 'visibility'), ('编辑权限', 'editability')))
            if field.get('permissions'):
                permissions += '\n补充权限：'+field['permissions']
            type_name={'text':'文本','string':'文本','id':'标识符','number':'数值','integer':'整数',
                       'boolean':'布尔值','enum':'枚举','date':'日期','datetime':'日期时间','object':'对象','array':'列表'}
            source_name={'calculated':'系统计算'}
            references = _references(field, ref_labels)
            rows.append([field['id']+' · '+_text(field.get('name'))+'\n'+_text(field.get('meaning')),
                         '类型：'+type_name.get(field.get('data_type'),_text(field.get('data_type')))
                         +'\n来源：'+source_name.get(field.get('source'),_text(field.get('source')))
                         +'\n必填条件：'+_text(field.get('required'))
                         +'\n默认值：'+_text(field.get('default_value')),
                         values+'\n'+permissions+('\n'+references if references else '')])
        return [_table(item, '字段属性', ['字段与含义', '类型、来源与初值', '约束、权限与关联'], rows)]
    if group == 'interactions':
        result = []
        for interaction in detail.get('interactions', []):
            def attributes(*pairs):
                return '\n'.join(label+'：'+_text(interaction.get(key)) for key, label in pairs)
            # Paired phases keep an interaction readable as one compact section,
            # without combining its success/failure semantics or dropping details.
            rows = [
                ['进入', attributes(('actor', '操作者'), ('entry', '入口')),
                 attributes(('preconditions', '前置条件'))],
                ['输入与提交', attributes(('trigger', '触发动作'), ('inputs', '输入')),
                 attributes(('validation', '校验'))],
                ['处理中', attributes(('pending', '处理中反馈')),
                 attributes(('repeat', '重复操作'), ('cancel', '取消或离开'))],
                ['结束结果', attributes(('success', '成功结果')),
                 attributes(('failure', '失败结果'))],
                ['数据影响', attributes(('effects', '数据影响')),
                 _references(interaction, ref_labels) or '无其他关联'],
            ]
            result.append(_table(item, interaction['id']+' · '+_text(interaction.get('name')),
                                 ['阶段', '行为与条件', '反馈与约束'], rows))
        return result
    if group == 'state_transitions':
        return [_table(item, '状态变化', ['编号', '原状态 → 新状态', '触发动作', '生效条件', '禁止操作'], [
            [row['id'], _text(row.get('from_state'))+' → '+_text(row.get('to_state')),
             _text(row.get('trigger')), _text(row.get('condition')),
             _text(row.get('prohibited_actions'))+('\n'+_references(row, ref_labels) if _references(row, ref_labels) else '')]
            for row in detail.get(group, [])])]
    flow = detail.get('flow', {})
    svg, layout = flow_svg(flow, title=item['title']+' · 业务流程')
    diagram = dict(kind='diagram', text='业务流程图', title=item['title']+' · 业务流程',
                   item_id=item['id'], detail_hash=detail_hash(item), renderer_version=VERSION,
                   image_uri='data:image/svg+xml;base64,'+base64.b64encode(svg.encode('utf-8')).decode('ascii'),
                   svg=svg, flow=flow, width=layout['width'], height=layout['height'])
    diagram['views']=flow_views(flow,detail.get('interactions',[]))
    names = {n['id']: n.get('label', n['id']) for n in flow.get('nodes', [])}
    steps = _table(item, '流程步骤', ['步骤', '参与角色', '业务动作', '类型', '关联定义'], [
        [n['id'], _text(n.get('actor')) if n['kind'] not in ('start', 'end') else '—',
         _text(n.get('label')), {'start':'开始', 'action':'处理', 'decision':'判断', 'wait':'等待', 'end':'结束'}[n['kind']],
         ('交互：'+n['interaction_id']+'\n' if n.get('interaction_id') else '')+(_references(n, ref_labels) or '无其他关联')]
        for n in flow.get('nodes', [])])
    branches = _table(item, '分支与流转条件', ['连线', '从', '到', '条件'], [
        [e['id'], e['from']+' · '+names[e['from']], e['to']+' · '+names[e['to']],
         (_text(e.get('condition')) if e.get('condition') else '顺序继续')+('\n'+_references(e, ref_labels) if _references(e, ref_labels) else '')]
        for e in flow.get('edges', [])])
    return [diagram, steps, branches]


def table_markdown(node):
    def cell(value):
        # Text-only cells: source data must not inject Markdown images or HTML.
        text=html.escape(str(value), quote=False).replace('\\', '\\\\')
        for token in ('|','[',']','*','_','`'):
            text=text.replace(token,'\\'+token)
        return text.replace('\r','').replace('\n','<br>')
    rows = [node['columns'], ['---']*len(node['columns']), *node['rows']]
    return '\n'.join('| '+' | '.join(cell(value) for value in row)+' |' for row in rows)


def detail_plaintext(item, group):
    """Full same-snapshot text for semantic review and source-location repair."""
    lines=[item['title']+' · '+GROUP_TITLES[group]]
    for node in detail_nodes(item,group):
        if node['kind']=='table':
            lines.append(node['title'])
            for row in node['rows']:
                lines.append('\n'.join(label+'：'+str(value) for label,value in zip(node['columns'],row)))
        elif node['kind']=='paragraph':
            lines.append(node['text'])
    return '\n\n'.join(lines)


def _wrap(text, limit=24):
    lines, line, width = [], '', 0
    for ch in str(text):
        size = 2 if unicodedata.east_asian_width(ch) in ('F', 'W') else 1
        if ch == '\n' or (line and width+size > limit):
            lines.append(line); line = ''; width = 0
            if ch == '\n':
                continue
        line += ch; width += size
    if line:
        lines.append(line)
    return lines or ['']


def flow_layout(flow, *, columns=None, show_ids=True):
    """Compact complete graph, folded into rows without removing any edges.

    Long chains must not force a tall strip or a multi-page illustration.
    Nodes follow a stable topological order; arrows own the actual business
    order, including branches, disconnected nodes and return paths.
    """
    nodes, edges = flow.get('nodes', []), flow.get('edges', [])
    if not nodes:
        raise ValueError('流程没有节点，无法绘制。')
    by_id = {n['id']: n for n in nodes}
    if len(by_id) != len(nodes) or any(e['from'] not in by_id or e['to'] not in by_id for e in edges):
        raise ValueError('流程节点重复或连线端点不存在。')
    outgoing = {key: [] for key in by_id}
    for edge in edges:
        outgoing[edge['from']].append(edge)
    visiting, visited, order, back = set(), set(), [], set()
    def visit(key):
        if key in visited:
            return
        visiting.add(key)
        for edge in outgoing[key]:
            if edge['to'] in visiting:
                back.add(edge['id'])
            else:
                visit(edge['to'])
        visiting.remove(key); visited.add(key); order.append(key)
    for node in sorted(nodes, key=lambda n: n['kind'] != 'start'):
        visit(node['id'])
    ranks = {key: 0 for key in by_id}
    for key in reversed(order):
        for edge in outgoing[key]:
            if edge['id'] not in back:
                ranks[edge['to']] = max(ranks[edge['to']], ranks[key]+1)
    ordered = sorted(nodes, key=lambda node: ranks[node['id']])
    columns = columns or min(4, max(1, math.ceil(math.sqrt(len(nodes)*1.25))))
    positions = {}
    for index, node in enumerate(ordered):
        row, column = divmod(index, columns)
        positions[node['id']] = (row, column if row % 2 == 0 else columns-1-column)
    rows = math.ceil(len(nodes)/columns)
    # Keep each row compact while aligning branches with their predecessors.
    # A swap is accepted only when it shortens existing local connections.
    for row in range(1,rows):
        members = [key for key,(r,_) in positions.items() if r == row]
        local_edges = [e for e in edges if e['id'] not in back and
            positions[e['to']][0] == row and positions[e['from']][0] in (row-1,row)]
        def distance():
            return sum(abs(positions[e['from']][1]-positions[e['to']][1]) for e in local_edges)
        improved = True
        while improved:
            improved = False
            for index,a in enumerate(members):
                for b in members[index+1:]:
                    before = distance()
                    positions[a],positions[b] = positions[b],positions[a]
                    if distance() < before:
                        improved = True
                    else:
                        positions[a],positions[b] = positions[b],positions[a]
    corridor_counts = {}
    for edge in edges:
        corridor = frozenset((edge['from'], edge['to']))
        corridor_counts[corridor] = corridor_counts.get(corridor, 0)+1
    def direct(edge):
        sr, sc = positions[edge['from']]; tr, tc = positions[edge['to']]
        return edge['from'] != edge['to'] and corridor_counts[frozenset((edge['from'],edge['to']))] == 1 and (
            sr == tr and abs(sc-tc) == 1 or sc == tc and abs(sr-tr) == 1)
    connections, ports = {}, {}
    for edge in edges:
        sr, sc = positions[edge['from']]; tr, tc = positions[edge['to']]
        if direct(edge) and sr == tr:
            sides = ('right','left') if tc > sc else ('left','right')
        elif direct(edge) and tr < sr:
            sides = ('top','bottom')
        else:
            sides = ('bottom','bottom' if sr == tr else 'top')
        connections[edge['id']] = sides
        for endpoint, side in zip(('from','to'), sides):
            ports.setdefault((edge[endpoint],side),[]).append((edge['id'],endpoint))
    exits, entrances = {}, {}
    for edge in edges:
        if not direct(edge):
            sr, _ = positions[edge['from']]; tr, _ = positions[edge['to']]
            exits[sr] = exits.get(sr, 0)+1
            entrances[tr] = entrances.get(tr, 0)+1
    box_w, gap_x, left = 200, 58, 26
    main_width = columns*box_w+(columns-1)*gap_x
    font_size, line_height = 14, 18
    sizes = {}
    for node in ordered:
        lines = _wrap((node['id']+' · ' if show_ids else '')+node.get('label', ''), 12 if node['kind']=='decision' else 26)
        if node.get('actor'):
            lines += _wrap('角色：'+node['actor'], 12 if node['kind']=='decision' else 26)
        height = max(52, 18+len(lines)*line_height)
        if node['kind']=='decision':
            height = max(100, len(lines)*40+40)
        sizes[node['id']] = (lines, height)
    boxes, layers = {}, []
    y = 28+entrances.get(0,0)*14
    for row in range(rows):
        members = [node for node in ordered if positions[node['id']][0]==row]
        height = max(sizes[node['id']][1] for node in members)
        layers.append(dict(rank=row,top=y,bottom=y+height,node_ids=[n['id'] for n in members]))
        for node in members:
            lines, node_h = sizes[node['id']]
            column = positions[node['id']][1]
            boxes[node['id']] = dict(x=left+column*(box_w+gap_x),y=y+(height-node_h)/2,
                width=box_w,height=node_h,lines=lines,kind=node['kind'],rank=row,column=column)
        y += height+max(66,42+14*(exits.get(row,0)+entrances.get(row+1,0)))
    def port(edge, endpoint, side):
        box = boxes[edge[endpoint]]
        members = ports[(edge[endpoint],side)]
        offset = (members.index((edge['id'],endpoint))-(len(members)-1)/2)*min(18,80/max(1,len(members)-1))
        x,y,w,h = box['x'],box['y'],box['width'],box['height']
        if side in ('top','bottom'):
            inset = abs(offset)*h/w if box['kind']=='decision' else 0
            return x+w/2+offset, y+inset if side=='top' else y+h-inset
        inset = abs(offset)*w/h if box['kind']=='decision' else 0
        return (x+inset if side=='left' else x+w-inset), y+h/2+offset
    routes, lanes, exits_used, entries_used = [], [], {}, {}
    for edge in edges:
        start, end = boxes[edge['from']], boxes[edge['to']]
        sr, sc = positions[edge['from']]; tr, tc = positions[edge['to']]
        source_side,target_side = connections[edge['id']]
        sx,sy = port(edge,'from',source_side)
        ex,ey = port(edge,'to',target_side)
        if direct(edge) and sr==tr:
            mid = (sx+ex)/2
            points = [(sx,sy),(mid,sy),(mid,ey),(ex,ey)]
            tx,ty = (sx+ex)/2-10,sy-7
        elif direct(edge):
            mid = (sy+ey)/2
            points = [(sx,sy),(sx,mid),(ex,mid),(ex,ey)]
            tx,ty = sx+7,(sy+ey)/2
        elif sr == tr or tr == sr+1:
            # Nearby edges use the row gap, not a detour around the whole graph.
            lane_y = layers[sr]['bottom']+18+exits_used.get(sr,0)*14
            exits_used[sr] = exits_used.get(sr,0)+1
            points = [(sx,sy),(sx,lane_y),(ex,lane_y),(ex,ey)]
            tx,ty = (sx+ex)/2+4,lane_y-5
        else:
            # Outside lanes traverse only row gaps and never node interiors.
            out_y = layers[sr]['bottom']+18+exits_used.get(sr,0)*14
            in_y = layers[tr]['top']-18-entries_used.get(tr,0)*14
            exits_used[sr] = exits_used.get(sr,0)+1
            entries_used[tr] = entries_used.get(tr,0)+1
            interval = (min(out_y,in_y)-10,max(out_y,in_y)+10)
            lane_index = next((i for i,used in enumerate(lanes)
                if all(interval[1]<old[0] or interval[0]>old[1] for old in used)),len(lanes))
            if lane_index==len(lanes):
                lanes.append([])
            lanes[lane_index].append(interval)
            lane = left+main_width+24+lane_index*22
            points = [(sx,sy),(sx,out_y),(lane,out_y),(lane,in_y),(ex,in_y),(ex,ey)]
            tx,ty = lane+4,out_y-4
        points = [point for index,point in enumerate(points) if index == 0 or point != points[index-1]]
        lines = ([edge['id']] if show_ids else
            [(edge['condition'][:10]+'…') if len(edge['condition'])>10 else edge['condition']] if edge.get('condition') else [])
        anchor = 'start'
        if not show_ids and lines and direct(edge) and sr == tr:
            lines = _wrap(lines[0],max(2,int((abs(ex-sx)-8)/6)))
            tx,ty,anchor = (sx+ex)/2,min(sy,ey)-7-(len(lines)-1)*15,'middle'
        routes.append(dict(id=edge['id'],source=edge['from'],target=edge['to'],
            condition=edge.get('condition',''),points=points,lines=lines,x=tx,y=ty,anchor=anchor))
    width = left+main_width+(66+(len(lanes)-1)*22 if lanes else 26)
    height = layers[-1]['bottom']+max(30,30+exits.get(rows-1,0)*14)
    if width*height>36_000_000 or height>26000 or width>15000:
        raise ValueError('流程图过大，请按业务子流程拆分后再导出。')
    return dict(width=width,height=height,boxes=boxes,routes=routes,layers=layers,
                font_size=font_size,line_height=line_height,edge_font_size=12,edge_line_height=15)


def _label_canvas(layout):
    """Reserve export-only room for full connector IDs without moving nodes."""
    width,height=layout['width'],layout['height']
    for route in layout['routes']:
        for index,line in enumerate(route['lines']):
            width=max(width,math.ceil(route['x']+len(line)*layout['edge_font_size']+16))
            height=max(height,math.ceil(route['y']+index*layout['edge_line_height']+8))
    return dict(layout,width=width,height=height)


def flow_svg(flow, title='业务流程', *, palette=None,fit_labels=False,display_notes=(),columns=None,show_ids=True):
    colors=palette or FLOW_PALETTE
    layout = flow_layout(flow,columns=columns,show_ids=show_ids)
    if fit_labels:layout=_label_canvas(layout)
    layout=_notes_layout(layout,display_notes)
    width, height = layout['width'], layout['height']
    def esc(value):
        return html.escape(str(value), quote=True)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
             '<title>'+esc(title)+'</title><rect width="100%" height="100%" fill="#ffffff"/>',
             f'<defs><marker id="arrow" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="10" refX="12" refY="5" orient="auto"><path d="M0 0 L12 5 L0 10 Z" fill="{colors["line"]}"/></marker></defs>']
    for route in layout['routes']:
        parts.append('<g><title>'+esc(route['id']+'：'+(route['condition'] or '顺序继续'))+'</title>')
        points = ' '.join(f'{x},{y}' for x, y in route['points'])
        parts.append(f'<polyline points="{points}" fill="none" stroke="white" stroke-width="6" stroke-linejoin="round"/>')
        parts.append(f'<polyline points="{points}" fill="none" stroke="{colors["line"]}" stroke-width="2.4" stroke-linejoin="round" marker-end="url(#arrow)"/>')
        parts.append('</g>')
    # Paint labels after every connector so later return lines cannot overwrite
    # an earlier edge number. Full conditions remain accessible as SVG titles.
    for route in layout['routes']:
        parts.append('<g><title>'+esc(route['id']+'：'+(route['condition'] or '顺序继续'))+'</title>')
        for n, line in enumerate(route['lines']):
            y = route['y']+n*layout['edge_line_height']
            parts.append(f'<text x="{route["x"]}" y="{y}" text-anchor="{route["anchor"]}" font-family="Microsoft YaHei,Noto Sans CJK SC,sans-serif" font-size="{layout["edge_font_size"]}" fill="{colors["edge_text"]}" paint-order="stroke" stroke="white" stroke-width="4">{esc(line)}</text>')
        parts.append('</g>')
    for box in layout['boxes'].values():
        x, y, w, h = box['x'], box['y'], box['width'], box['height']
        fill = colors['decision'] if box['kind'] == 'decision' else colors['node']
        if box['kind'] == 'decision':
            points = f'{x+w/2},{y} {x+w},{y+h/2} {x+w/2},{y+h} {x},{y+h/2}'
            parts.append(f'<polygon points="{points}" fill="{fill}" stroke="{colors["border"]}" stroke-width="1.6"/>')
        else:
            radius = 30 if box['kind'] in ('start', 'end') else 7
            parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{colors["border"]}" stroke-width="1.6"/>')
        start_y = y+(h-len(box['lines'])*layout['line_height'])/2+layout['font_size']
        for n, line in enumerate(box['lines']):
            parts.append(f'<text x="{x+w/2}" y="{start_y+n*layout["line_height"]}" text-anchor="middle" font-family="Microsoft YaHei,Noto Sans CJK SC,sans-serif" font-size="{layout["font_size"]}" fill="{colors["text"]}">{esc(line)}</text>')
    for index,line in enumerate(layout.get('display_notes',[])):
        parts.append(f'<text x="16" y="{layout["notes_y"]+index*18}" font-family="Microsoft YaHei,sans-serif" font-size="12">{esc(line)}</text>')
    parts.append('</svg>')
    return ''.join(parts), layout


def _flow_image(layout, palette=None):
    colors=palette or FLOW_PALETTE
    from PIL import Image, ImageDraw, ImageFont
    scale = 2.5
    image = Image.new('RGB', (math.ceil(layout['width']*scale), math.ceil(layout['height']*scale)), 'white')
    draw = ImageDraw.Draw(image)
    fonts = [Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts/msyh.ttc',
             Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
             Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font_file = next((path for path in fonts if path.exists()), None)
    if font_file is None:
        raise ValueError('缺少可用的本地绘图字体。')
    font = ImageFont.truetype(str(font_file), round(layout['font_size']*scale))
    small = ImageFont.truetype(str(font_file), round(layout['edge_font_size']*scale))
    def xy(points):
        return [(round(x*scale), round(y*scale)) for x, y in points]
    for route in layout['routes']:
        points = route['points']
        draw.line(xy(points), fill='white', width=round(6*scale), joint='curve')
        draw.line(xy(points), fill=colors['line'], width=round(2.4*scale), joint='curve')
        x, y = points[-1]; px, py = next((a,b) for a,b in reversed(points[:-1]) if (a,b)!=(x,y))
        angle = math.atan2(y-py, x-px)
        arrow = [(x,y), (x-12*math.cos(angle)+5*math.sin(angle), y-12*math.sin(angle)-5*math.cos(angle)),
                 (x-12*math.cos(angle)-5*math.sin(angle), y-12*math.sin(angle)+5*math.cos(angle))]
        draw.polygon(xy(arrow), fill=colors['line'])
    for route in layout['routes']:
        for n, line in enumerate(route['lines']):
            draw.text((route['x']*scale, (route['y']+n*layout['edge_line_height'])*scale), line, font=small, anchor='ms' if route['anchor']=='middle' else 'ls', fill=colors['edge_text'], stroke_width=3, stroke_fill='white')
    for box in layout['boxes'].values():
        x,y,w,h=box['x'],box['y'],box['width'],box['height']
        fill=colors['decision'] if box['kind']=='decision' else colors['node']
        if box['kind']=='decision':
            draw.polygon(xy([(x+w/2,y),(x+w,y+h/2),(x+w/2,y+h),(x,y+h/2)]), fill=fill, outline=colors['border'], width=2)
        else:
            draw.rounded_rectangle(xy([(x,y),(x+w,y+h)]), radius=(30 if box['kind'] in ('start','end') else 7)*scale, fill=fill, outline=colors['border'], width=2)
        start_y=y+(h-len(box['lines'])*layout['line_height'])/2+layout['font_size']
        for n,line in enumerate(box['lines']):
            draw.text(((x+w/2)*scale,(start_y+n*layout["line_height"])*scale),line,font=font,anchor='ms',fill=colors['text'])
    for index,line in enumerate(layout.get('display_notes',[])):
        draw.text((16*scale,(layout['notes_y']+index*18)*scale),line,font=small,anchor='ls',fill=colors['text'])
    return image, font_file, scale


def flow_png(flow, *, palette=None,fit_labels=False,display_notes=(),columns=None,show_ids=True):
    layout=flow_layout(flow,columns=columns,show_ids=show_ids)
    image, _, _ = _flow_image(_notes_layout(_label_canvas(layout) if fit_labels else layout,display_notes),palette)
    out=io.BytesIO();image.save(out,format='PNG')
    return out.getvalue()


def flow_png_pages(flow, title='业务流程'):
    """Compatibility entry point: a flow always remains one complete image."""
    caption = title+'（完整流程：'+'、'.join(n['id'] for n in flow.get('nodes', []))+'；连线条件见分支表）'
    return [(flow_png(flow), caption)]



def _notes_layout(layout,notes):
    if not notes:return layout
    lines=[line for note in notes for line in _wrap(note,60)]
    return dict(layout,width=max(layout['width'],450),height=layout['height']+24+18*len(lines),
                notes_y=layout['height']+18,display_notes=lines)



def view_notes(view, labels=None):
    notes=['局部分图；引用卡片仅表示原节点在其他图，非业务终态。']
    if labels is not None:
        return notes+[labels.get(c['node_id'],c['node_id'])+' · 续见分图 '+ '、'.join(c['view_ids'])
                      for c in view.get('continuations',[])]
    notes += [c['text'] for c in view.get('continuations',[])]
    if view.get('edge_conditions'):
        notes.append('本图分支条件（原文；长文按原编号查表）：')
        for edge in view['edge_conditions']:
            text=edge['condition']
            notes.append(edge['id']+'：'+(text if len(_wrap(text,60))<=3 else '原条件全文见分支与流转条件表 '+edge['id']))
    return notes


def view_fit(layout):
    """Conservative A4 fit, inches; reserve 1.6 inches for headings/captions."""
    choices=[]
    for landscape,w,h in ((False,6.77,8.69),(True,10.19,5.27)):
        scale=min(w/layout['width'],h/layout['height'])*72
        choices.append((scale,landscape))
    scale,landscape=max(choices)
    return dict(landscape=landscape,points_per_pixel=scale,
                node_font_pt=layout['font_size']*scale,edge_font_pt=layout['edge_font_size']*scale)


def view_flow(flow,view):
    """Read-only display cards retain original IDs; original graph is untouched."""
    nodes=[]
    for node in flow['nodes']:
        if node['id'] not in view['node_ids']:continue
        projected=dict(node)
        if node['id'] in view.get('reference_nodes',{}):
            projected.update(kind='reference',label='展示引用：'+view['reference_nodes'][node['id']],actor='')
        elif len(_wrap(node.get('label',''),12 if node['kind']=='decision' else 26))>6 or len(_wrap(node.get('actor',''),26))>3:
            projected.update(label='原节点/角色全文见流程步骤表 '+node['id'],actor='')
        nodes.append(projected)
    return dict(nodes=nodes,edges=[e for e in flow['edges'] if e['id'] in view['edge_ids']])


def _view_layout(flow,view,*,show_ids=True):
    projected=view_flow(flow,view)
    notes=view_notes(view,labels=None if show_ids else {n['id']:n['label'] for n in flow['nodes']})
    candidates=[]
    for columns in range(1,min(4,len(projected['nodes']))+1):
        layout=_notes_layout(_label_canvas(flow_layout(projected,columns=columns,show_ids=show_ids)),notes)
        candidates.append((view_fit(layout)['points_per_pixel'],columns,layout))
    _,columns,layout=max(candidates,key=lambda row:row[0])
    return columns,layout


def flow_views(flow,interactions=(),*,show_ids=True):
    full=_label_canvas(flow_layout(flow,columns=None if show_ids else 3,show_ids=show_ids))
    # The unchanged overview export uses landscape, not the best of two pages.
    if min(10.19/full['width'],5.27/full['height'])*72*full['edge_font_size']>=8:return []
    nodes={n['id']:n for n in flow['nodes']}
    owners={n['id']:n.get('interaction_id') or '共享流程' for n in flow['nodes']}
    names={i['id']:i.get('name',i['id']) for i in interactions}
    groups={owner:[] for owner in dict.fromkeys(owners.values())}
    for edge in flow['edges']:groups[owners[edge['from']]].append(edge)
    specs=[dict(owner=owner,edge_ids=[e['id'] for e in edges],node_ids=[n['id'] for n in flow['nodes'] if owners[n['id']]==owner or any(n['id'] in (e['from'],e['to']) for e in edges)]) for owner,edges in groups.items()]
    # Only finite edge bisection, after trying four column counts and two pages.
    while True:
        for index,spec in enumerate(specs,1):
            spec.update(id='V'+str(index),title='图 V'+str(index)+' · '+str(names.get(spec['owner'],spec['owner'])))
        for spec in specs:
            refs={};continuations=[]
            for nid in spec['node_ids']:
                if owners[nid]!=spec['owner']:
                    targets=[s['id'] for s in specs if s['owner']==owners[nid] and nid in s['node_ids']]
                    refs[nid]='参见图 '+ '、'.join(targets)+' · '+str(names.get(owners[nid],owners[nid]))+'（原节点）'
                else:
                    links=[dict(view_id=s['id'],edge_ids=[e['id'] for e in flow['edges'] if e['id'] in s['edge_ids'] and nid in (e['from'],e['to'])]) for s in specs if s is not spec and s['owner']==spec['owner'] and nid in s['node_ids']]
                    if links:continuations.append(dict(node_id=nid,view_ids=[l['view_id'] for l in links],links=links,text=nid+' 分段续接：'+ '；'.join('参见图 '+l['view_id']+'（'+ '、'.join(l['edge_ids'])+'）' for l in links)))
            spec.update(reference_nodes=refs,continuations=continuations,edge_conditions=[dict(id=e['id'],condition=e.get('condition','')) for e in flow['edges'] if e['id'] in spec['edge_ids']])
        split_index=None
        for index,spec in enumerate(specs):
            columns,layout=_view_layout(flow,spec,show_ids=show_ids)
            if view_fit(layout)['edge_font_pt']<8 and (len(spec['edge_ids'])>1 or len(spec['node_ids'])>2):split_index=index;break
        if split_index is None:break
        old=specs[split_index]
        if len(old['edge_ids'])<=1:
            bound={e[k] for e in flow['edges'] if e['id'] in old['edge_ids'] for k in ('from','to')}
            first=[nid for nid in old['node_ids'] if nid in bound] or old['node_ids'][:len(old['node_ids'])//2]
            rest=[nid for nid in old['node_ids'] if nid not in first]
            specs[split_index:split_index+1]=[dict(owner=old['owner'],edge_ids=old['edge_ids'],node_ids=first),dict(owner=old['owner'],edge_ids=[],node_ids=rest)]
            continue
        middle=len(old['edge_ids'])//2;parts=[]
        for edgeids in (old['edge_ids'][:middle],old['edge_ids'][middle:]):
            ids={e[k] for e in flow['edges'] if e['id'] in edgeids for k in ('from','to')}
            parts.append(dict(owner=old['owner'],edge_ids=edgeids,node_ids=[nid for nid in old['node_ids'] if nid in ids]))
        covered={nid for part in parts for nid in part['node_ids']}
        parts[0]['node_ids'] += [nid for nid in old['node_ids'] if nid not in covered]
        specs[split_index:split_index+1]=parts
    result=[]
    for spec in specs:
        columns,layout=_view_layout(flow,spec,show_ids=show_ids)
        notes=view_notes(spec,labels=None if show_ids else {n['id']:n['label'] for n in flow['nodes']})
        svg,layout=flow_svg(view_flow(flow,spec),spec['title'],fit_labels=True,display_notes=notes,columns=columns,show_ids=show_ids)
        result.append(dict(id=spec['id'],title=spec['title'],image_uri='data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode(),
            width=layout['width'],height=layout['height'],node_ids=spec['node_ids'],edge_ids=spec['edge_ids'],continuations=spec['continuations'],
            edge_conditions=spec['edge_conditions'],reference_nodes=spec['reference_nodes'],columns=columns))
    return result
