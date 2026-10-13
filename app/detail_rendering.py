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

GROUP_TITLES = {'fields': '字段属性', 'interactions': '交互逻辑',
                'flow': '业务流程说明', 'state_transitions': '状态变化'}
VERSION = 'detail-rendering-4'


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


def detail_nodes(item, group, ref_labels=None):
    """Reading projection only; aliases never rewrite the saved business contract."""
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


def flow_layout(flow):
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
    columns = min(4, max(1, math.ceil(math.sqrt(len(nodes)*1.25))))
    positions = {}
    for index, node in enumerate(ordered):
        row, column = divmod(index, columns)
        positions[node['id']] = (row, column if row % 2 == 0 else columns-1-column)
    rows = math.ceil(len(nodes)/columns)
    corridor_counts = {}
    for edge in edges:
        corridor = frozenset((edge['from'], edge['to']))
        corridor_counts[corridor] = corridor_counts.get(corridor, 0)+1
    def direct(edge):
        sr, sc = positions[edge['from']]; tr, tc = positions[edge['to']]
        return edge['from'] != edge['to'] and corridor_counts[frozenset((edge['from'],edge['to']))] == 1 and (
            sr == tr and abs(sc-tc) == 1 or sc == tc and abs(sr-tr) == 1)
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
        lines = _wrap(node['id']+' · '+node.get('label', ''), 20 if node['kind']=='decision' else 26)
        if node.get('actor'):
            lines += _wrap('角色：'+node['actor'], 26)
        height = max(52, 18+len(lines)*line_height)
        if node['kind']=='decision':
            height = max(100, len(lines)*34+24)
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
    routes, lanes, exits_used, entries_used = [], [], {}, {}
    for edge in edges:
        start, end = boxes[edge['from']], boxes[edge['to']]
        sr, sc = positions[edge['from']]; tr, tc = positions[edge['to']]
        sx, sy = start['x']+box_w/2, start['y']+start['height']
        ex, ey = end['x']+box_w/2, end['y']
        if direct(edge) and sr==tr:
            forward = tc>sc
            sx = start['x']+(box_w if forward else 0)
            ex = end['x']+(0 if forward else box_w)
            sy = start['y']+start['height']/2
            ey = end['y']+end['height']/2
            points = [(sx,sy),(ex,ey)]
            tx,ty = (sx+ex)/2-10,sy-7
        elif direct(edge):
            if tr<sr:
                sy = start['y'];ey = end['y']+end['height']
            points = [(sx,sy),(ex,ey)]
            tx,ty = sx+7,(sy+ey)/2
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
        routes.append(dict(id=edge['id'],source=edge['from'],target=edge['to'],
            condition=edge.get('condition',''),points=points,lines=[edge['id']],x=tx,y=ty,anchor='start'))
    width = left+main_width+(66+(len(lanes)-1)*22 if lanes else 26)
    height = layers[-1]['bottom']+max(30,30+exits.get(rows-1,0)*14)
    if width*height>36_000_000 or height>26000 or width>15000:
        raise ValueError('流程图过大，请按业务子流程拆分后再导出。')
    return dict(width=width,height=height,boxes=boxes,routes=routes,layers=layers,
                font_size=font_size,line_height=line_height,edge_font_size=12,edge_line_height=15)


def flow_svg(flow, title='业务流程'):
    layout = flow_layout(flow)
    width, height = layout['width'], layout['height']
    def esc(value):
        return html.escape(str(value), quote=True)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
             '<title>'+esc(title)+'</title><rect width="100%" height="100%" fill="#ffffff"/>',
             '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#526d7b"/></marker></defs>']
    for route in layout['routes']:
        parts.append('<g><title>'+esc(route['id']+'：'+(route['condition'] or '顺序继续'))+'</title>')
        points = ' '.join(f'{x},{y}' for x, y in route['points'])
        parts.append(f'<polyline points="{points}" fill="none" stroke="#526d7b" stroke-width="1.8" marker-end="url(#arrow)"/>')
        parts.append('</g>')
    # Paint labels after every connector so later return lines cannot overwrite
    # an earlier edge number. Full conditions remain accessible as SVG titles.
    for route in layout['routes']:
        parts.append('<g><title>'+esc(route['id']+'：'+(route['condition'] or '顺序继续'))+'</title>')
        for n, line in enumerate(route['lines']):
            y = route['y']+n*layout['edge_line_height']
            parts.append(f'<text x="{route["x"]}" y="{y}" font-family="Microsoft YaHei,Noto Sans CJK SC,sans-serif" font-size="{layout["edge_font_size"]}" fill="#385466" paint-order="stroke" stroke="white" stroke-width="4">{esc(line)}</text>')
        parts.append('</g>')
    for box in layout['boxes'].values():
        x, y, w, h = box['x'], box['y'], box['width'], box['height']
        fill = '#fff6e0' if box['kind'] == 'decision' else '#edf5f8'
        if box['kind'] == 'decision':
            points = f'{x+w/2},{y} {x+w},{y+h/2} {x+w/2},{y+h} {x},{y+h/2}'
            parts.append(f'<polygon points="{points}" fill="{fill}" stroke="#456778" stroke-width="1.6"/>')
        else:
            radius = 30 if box['kind'] in ('start', 'end') else 7
            parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="#456778" stroke-width="1.6"/>')
        start_y = y+(h-len(box['lines'])*layout['line_height'])/2+layout['font_size']
        for n, line in enumerate(box['lines']):
            parts.append(f'<text x="{x+w/2}" y="{start_y+n*layout["line_height"]}" text-anchor="middle" font-family="Microsoft YaHei,Noto Sans CJK SC,sans-serif" font-size="{layout["font_size"]}" fill="#183849">{esc(line)}</text>')
    parts.append('</svg>')
    return ''.join(parts), layout


def _flow_image(layout):
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
        points = route['points']; draw.line(xy(points), fill='#526d7b', width=3)
        x, y = points[-1]; px, py = next((a,b) for a,b in reversed(points[:-1]) if (a,b)!=(x,y))
        angle = math.atan2(y-py, x-px)
        arrow = [(x,y), (x-10*math.cos(angle-.4), y-10*math.sin(angle-.4)), (x-10*math.cos(angle+.4), y-10*math.sin(angle+.4))]
        draw.polygon(xy(arrow), fill='#526d7b')
    for route in layout['routes']:
        for n, line in enumerate(route['lines']):
            draw.text((route['x']*scale, (route['y']+n*layout['edge_line_height'])*scale), line, font=small, anchor='ls', fill='#385466', stroke_width=3, stroke_fill='white')
    for box in layout['boxes'].values():
        x,y,w,h=box['x'],box['y'],box['width'],box['height']
        fill='#fff6e0' if box['kind']=='decision' else '#edf5f8'
        if box['kind']=='decision':
            draw.polygon(xy([(x+w/2,y),(x+w,y+h/2),(x+w/2,y+h),(x,y+h/2)]), fill=fill, outline='#456778', width=2)
        else:
            draw.rounded_rectangle(xy([(x,y),(x+w,y+h)]), radius=(30 if box['kind'] in ('start','end') else 7)*scale, fill=fill, outline='#456778', width=2)
        start_y=y+(h-len(box['lines'])*layout['line_height'])/2+layout['font_size']
        for n,line in enumerate(box['lines']):
            draw.text(((x+w/2)*scale,(start_y+n*layout["line_height"])*scale),line,font=font,anchor='ms',fill='#183849')
    return image, font_file, scale


def flow_png(flow):
    image, _, _ = _flow_image(flow_layout(flow))
    out=io.BytesIO();image.save(out,format='PNG')
    return out.getvalue()


def flow_png_pages(flow, title='业务流程'):
    """Compatibility entry point: a flow always remains one complete image."""
    caption = title+'（完整流程：'+'、'.join(n['id'] for n in flow.get('nodes', []))+'；连线条件见分支表）'
    return [(flow_png(flow), caption)]
