import hashlib
import io
import zipfile
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from .core import brief_hash, digest, dumps, ident, now, require
from .preview import prototype
from .contracts import profile
from .document_reader import reader_document


def block_text(block, p):
    items = {i['id']:i for i in p['items']}
    if block['kind'] in ('requirement','rule','acceptance'):
        return '\n'.join(f'{r} — {items[r]["statement"]}' for r in block['ref_ids'])
    return ('【建议／待确认】' if block['kind'] == 'ui_suggestion' else '') + (block['text'] or '')


async def capture(spec):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width':1100,'height':780}, device_scale_factor=1)
        images = []
        try:
            for selected in spec['pages']:
                await page.set_content(prototype(dict(spec, pages=[selected])), wait_until='load')
                images.append((selected['page_id'], await page.screenshot(full_page=True)))
        finally:
            await browser.close()
    return images


async def document_files(p, kind, status='草稿／待产品经理内容确认', *, reader=False):
    require(kind in p['documents'], 'NOT_FOUND', '请先生成此类型文档', 404)
    artifact = p['documents'][kind]
    require(artifact['brief_hash'] == brief_hash(p), 'STALE_REVISION', '底稿已改变，请重新成文', 409)
    require(kind not in p.get('stale_document_kinds', []), 'STALE_REVISION', '页面已改变，请重新生成此文档后导出', 409)
    content = reader_document(artifact) if reader else artifact['content']
    meta = f'{status} · 底稿版本 {artifact["draft_revision"]} · 内容哈希 {digest(content)[:16]}'
    if reader:
        meta=f'{status} · 版本 v{artifact["draft_revision"]}'
    rendered = [(s, [b['text'] if reader else block_text(b,p) for b in s['blocks']]) for s in content['sections']]
    md = [f'# {content["title"]}', meta, '本文用于需求内容评审；页面及数据为原型模拟，不能证明业务系统已经实现。']
    if not reader and content['content_profile_id'].startswith('builtin-'):
        md[2]+=' 已明确选择内置章节回退，未按用户原始 Word 参考生成。'
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(.7)
    section.left_margin = section.right_margin = Inches(.75)
    for style_name in ('Normal','Title','Heading 1','Heading 2','Heading 3','Heading 4','Heading 5'):
        st = doc.styles[style_name]
        st.font.name = 'Microsoft YaHei'
        st.font.color.rgb = RGBColor(0,0,0)
        st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
        for border in st.element.findall('.//' + qn('w:pBdr')):
            border.getparent().remove(border)
        for attr in ('asciiTheme','eastAsiaTheme','hAnsiTheme','cstheme'):
            st.element.get_or_add_rPr().rFonts.attrib.pop(qn('w:'+attr),None)
        if style_name == 'Normal':
            st.font.size = Pt(11)
            st.font.bold = False
            st.paragraph_format.space_after = Pt(7)
    doc.add_paragraph(content['title'], 'Title')
    doc.add_paragraph(meta)
    doc.add_paragraph(md[2])
    ui_current = bool(p.get('ui') and p['ui']['brief_hash'] == brief_hash(p))
    images = await capture(p['ui']['spec']) if ui_current else []
    image_map = dict(images)
    files, bindings = {}, []
    if not ui_current:
        notice = '未提供当前底稿可用的页面方案；本文未插入原型图片。' if not p.get('ui') else '页面方案对应旧底稿；本文未插入过期原型图片。'
        md.append(notice)
        doc.add_paragraph(notice)
    for s, texts in rendered:
        md.append('#'*(s['level']+1) + ' ' + s['title'])
        doc.add_heading(s['title'], s['level'])
        for text in texts:
            md.append(text)
            doc.add_paragraph(text)
        picture_dimension = 'PRD-4.F.1' if kind == 'prd' else 'MRD-5.1.F.3'
        maps = [m for m in content['reference_mapping'] if s['section_id'] in m['output_section_ids'] and m['profile_section_id'] == picture_dimension and m['disposition'] in ('included','merged')]
        if maps and images:
            allowed_refs = {m['scope_ref'] for m in maps if m['scope_ref']}
            if not allowed_refs:
                continue
            for page in p['ui']['spec']['pages']:
                if not allowed_refs.intersection(page['requirement_refs']):
                    continue
                image = image_map[page['page_id']]
                filename = 'assets/' + hashlib.sha256(page['page_id'].encode()).hexdigest()[:16] + '.png'
                caption = f'低保真模拟 {page["title"]} · UI 版本 {p["ui"]["spec"]["draft_revision"]} · 待确认'
                from PIL import Image
                with Image.open(io.BytesIO(image)) as preview:
                    width=min(6.4,8.2*preview.width/preview.height)
                # Reserve room for its caption on one page, including tall prototypes.
                doc.add_picture(io.BytesIO(image), width=Inches(width))
                doc.paragraphs[-1].paragraph_format.keep_with_next = True
                doc.add_paragraph(caption)
                md.append(f'![{caption}]({filename})')
                files[filename] = image
                bindings.append(dict(section_id=s['section_id'], ui_page_id=page['page_id'], ui_spec_hash=digest(p['ui']['spec']), asset_hash=hashlib.sha256(image).hexdigest(), caption=caption))
    if not reader:
        doc.add_heading('参考维度处置与待确认事项', 1)
        md.append('## 参考维度处置与待确认事项')
        labels={d['id']:d['reference_heading'] for d in profile(kind,content['content_profile_id'].startswith('builtin-'))['sections']}
        groups={}
        states={'pending':'待确认','not_applicable':'不适用','merged':'合并表达'}
        for m in content['reference_mapping']:
            if m['disposition'] in states:
                key=(m['disposition'],m['reason'],m['scope_ref'])
                groups.setdefault(key,[]).append(labels.get(m['profile_section_id'],m['profile_section_id']))
        for (state,reason,scope),dimensions in groups.items():
            line=f'{states[state]}'+(f' · {scope}' if scope else '')+'：'+'；'.join(dimensions)+'。\n'+reason
            doc.add_paragraph(line)
            md.append(line)
        for q in p['questions']:
            if q['status'] != 'answered':
                line = f'待确认 {q["id"]}：{q["question"]}；影响：{q["why"]}'
                doc.add_paragraph(line)
                md.append(line)
    footer = doc.sections[0].footer.paragraphs[0]
    footer.add_run('需求内容评审 · ')
    fld = OxmlElement('w:fldSimple'); fld.set(qn('w:instr'),'PAGE'); footer._p.append(fld)
    out = io.BytesIO(); doc.save(out)
    files[kind.upper()+'.docx'] = out.getvalue()
    files[kind.upper()+'.md'] = ('\n\n'.join(md)+'\n').encode('utf-8')
    files['document_asset_bindings.json'] = dumps(bindings).encode('utf-8')
    files['reference_mapping.json'] = dumps(content['reference_mapping']).encode('utf-8')
    return files


def zip_files(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


def handoff(store, pid, baseline_id, idempotency_key):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = [r for r in store.records(pid,'export',db) if r['idempotency_key'] == idempotency_key]
        if prior:
            require(prior[0]['baseline_id'] == baseline_id, 'IDEMPOTENCY_CONFLICT', '导出幂等键冲突', 409)
            return prior[0]
        baseline = next((b for b in store.records(pid,'baseline',db) if b['id'] == baseline_id),None)
        require(baseline is not None, 'CONFIRMATION_REQUIRED', '需要有效人工确认基线', 409)
        p = baseline['project']
        items = [i for i in p['items'] if i['selection_status']=='selected' and i['applies_to']=='to_be']
        canonical = '\n\n'.join(f'## {i["id"]} {i["title"]}\n\n{i["statement"]}' for i in items)
        refs = {i['id']: i for i in items}
        traces = []
        for req in [i for i in items if i['kind']=='requirement']:
            related = [i for i in items if i['id'] in req['related_refs'] or req['id'] in i['related_refs']]
            traces.append(dict(requirement_id=req['id'], rule_ids=[i['id'] for i in related if i['kind']=='rule'], ac_ids=[i['id'] for i in related if i['kind']=='acceptance'], ui_page_ids=[page['page_id'] for page in p['ui']['spec']['pages'] if req['id'] in page['requirement_refs']] if p['ui'] else [], frontend_sections=[req['id']], backend_sections=[req['id']], open_question_ids=[q['id'] for q in p['questions'] if req['id'] in q['related_refs'] and q['status']!='answered']))
        files = {
            'README.md': f'# 研发交接\n\n基线 {baseline_id}\n\n产品经理内容确认记录 {baseline["confirmation_id"]}。内容确认不代表技术设计或测试验收通过。\n\n本包为中性交接；ai-engineer-context 私有格式适配未验证。',
            'requirements.md': '# 共同需求\n\n'+canonical,
            'frontend_spec.md': '# 前端交接\n\n以下为同一基线原文；页面结构与模拟交互见 ui_spec.json / prototype.html。接口及技术实现由开发确认。\n\n'+canonical,
            'backend_spec.md': '# 后端交接\n\n以下为同一基线原文；不得以界面隐藏代替权限规则。接口路径、表结构和事务方案未由本包代定。\n\n'+canonical,
            'acceptance_criteria.md': '# 验收条件\n\n'+'\n\n'.join(f'{i["id"]} — {i["statement"]}' for i in items if i['kind']=='acceptance'),
            'open_questions.md': '# 未决事项\n\n'+'\n\n'.join(q['question']+'\n影响：'+q['why'] for q in p['questions'] if q['status']!='answered'),
            'ui_spec.json': dumps(p['ui']['spec'] if p['ui'] else None),
            'prototype.html': prototype(p['ui']['spec']) if p['ui'] else '<!doctype html><meta charset="utf-8"><p>本基线未包含 UI 方案</p>',
            'traceability.json': dumps(traces)}
        files = {k:v.encode('utf-8') for k,v in files.items()}
        manifest = dict(schema_version='1.1', baseline_id=baseline_id, baseline_hash=digest(baseline['hashes']), confirmation_id=baseline['confirmation_id'], generator_version='1.1', prompt_version='1.1', created=now(), artifacts={k:dict(sha256=hashlib.sha256(v).hexdigest(), role=k) for k,v in files.items()})
        files['manifest.json'] = dumps(manifest).encode()
        eid = ident('EXPORT')
        folder=store.folder/'exports'; folder.mkdir(exist_ok=True)
        (folder/(eid+'.zip')).write_bytes(zip_files(files))
        result=dict(baseline_id=baseline_id, confirmation_id=baseline['confirmation_id'], idempotency_key=idempotency_key, manifest=manifest)
        store.record(pid,'export',result,eid,db)
        return dict(id=eid,**result)
