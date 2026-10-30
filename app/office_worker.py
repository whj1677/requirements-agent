"""Native, isolated COM extraction. No PowerShell, OCR or protection changes."""
import argparse
import base64
import gc
import io
import json
from pathlib import Path

from .office_process import processes, visible_process_ids


class ReadFailure(RuntimeError):
    def __init__(self, code, reason):
        super().__init__(code)
        self.code, self.reason = code, reason


def com_runtime():
    import pythoncom
    import win32com.client.dynamic
    return pythoncom, win32com.client.dynamic


def extract(input_path, owner_path, progid, process, clsid=None):
    from .office import APPLICATIONS
    kind = {'.doc': 'Word', '.docx': 'Word', '.xls': 'Excel', '.xlsx': 'Excel',
            '.ppt': 'PowerPoint', '.pptx': 'PowerPoint'}[input_path.suffix.lower()]
    assert progid in {row[0] for row in APPLICATIONS[kind]}
    assert process.casefold() in {row[1].casefold() for row in APPLICATIONS[kind]} | {'wps'}
    rows, images, limits = [], [], []
    char_count = 0
    app = document = prior_links = None
    owned = initialized = False
    stage = 'com_runtime'
    pythoncom = None

    def add(location, value):
        nonlocal char_count
        text = str(value or '').strip('\r\x07\n\0 ')
        if not text:
            return
        if len(rows) >= 20000 or char_count + len(text) > 2000000:
            raise ReadFailure('OFFICE_CONTENT_LIMIT', '内容超过本机提取上限，未标记为已读取；请拆分材料。')
        rows.append([location, text])
        char_count += len(text)

    def slides(shapes, location):
        for index in range(1, shapes.Count + 1):
            shape = shapes.Item(index)
            where = f'{location} / 对象 {index}'
            if shape.Type == 6:
                slides(shape.GroupItems, where)
            elif shape.HasTable:
                table = shape.Table
                for r in range(1, table.Rows.Count + 1):
                    for c in range(1, table.Columns.Count + 1):
                        add(f'{where} / 表格 R{r}C{c}', table.Cell(r, c).Shape.TextFrame.TextRange.Text)
            elif shape.HasTextFrame and shape.TextFrame.HasText:
                add(where, shape.TextFrame.TextRange.Text)
            else:
                limits.append(f'{where} 的图片、图表或其他非文本对象未提取')

    try:
        pythoncom, dynamic = com_runtime()
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        initialized = True
        stage = 'application_start'
        before = processes(process)
        wps = process.casefold() in ('wps', 'et', 'wpp')
        visible = visible_process_ids() if before and wps else set()
        if before and ((wps and any(p['pid'] in visible for p in before)) or (not wps and kind == 'PowerPoint')):
            raise ReadFailure('OFFICE_BUSY', '对应的办公软件正在使用中；请先保存并自行关闭后重试本机读取。')
        # Use the already discovered class, including registrations in the other bitness view.
        app = dynamic.Dispatch(pythoncom.CoCreateInstance(clsid or progid, None, pythoncom.CLSCTX_LOCAL_SERVER, pythoncom.IID_IDispatch))
        earlier = {(p['pid'], p['started']) for p in before}
        created = [p for p in processes(process) if (p['pid'], p['started']) not in earlier]
        collection = getattr(app, {'Word': 'Documents', 'Excel': 'Workbooks', 'PowerPoint': 'Presentations'}[kind])
        new_ids = {p['pid'] for p in created}
        roots = [p for p in created if p.get('parent_pid') not in new_ids]
        if len(roots) != 1 or collection.Count != 0:
            raise ReadFailure('OFFICE_BUSY', '未能确认空闲的新办公软件实例；未接管用户文档。')
        owner_path.write_text(json.dumps(created), 'utf-8')
        owned = True
        stage = 'readonly_security'
        app.AutomationSecurity = 3
        if app.AutomationSecurity != 3:
            raise ReadFailure('OFFICE_SAFE_MODE_UNSUPPORTED', '本机办公软件无法确认已禁用宏，未打开资料；这不是缺少读取接口，也不表示文件损坏。')
        stage = 'document_open_or_extract'
        if kind == 'Word':
            app.Visible, app.DisplayAlerts = False, 0
            prior_links = app.Options.UpdateLinksAtOpen
            app.Options.UpdateLinksAtOpen = False
            document = app.Documents.Open(str(input_path), False, True, False, '', '', False, '', '', 0, 0, False, False)
            if not document.ReadOnly:
                raise ReadFailure('OFFICE_NOT_READONLY', '办公软件未以只读方式打开资料，未提取内容。')
            for i in range(1, document.Paragraphs.Count + 1):
                paragraph = document.Paragraphs.Item(i)
                if not paragraph.Range.Information(12):
                    add(f'正文段落 {i}', paragraph.Range.Text)
            for i in range(1, document.Tables.Count + 1):
                cells = document.Tables.Item(i).Range.Cells
                for c in range(1, cells.Count + 1):
                    cell = cells.Item(c)
                    add(f'表格 {i} / 单元格 {c} / 行 {cell.RowIndex} 列 {cell.ColumnIndex}', cell.Range.Text)
            from PIL import Image
            for i in range(1, document.InlineShapes.Count + 1):
                shape = document.InlineShapes.Item(i)
                location = f'内嵌对象 {i} / 页 {shape.Range.Information(3)} / 字符 {shape.Range.Start}'
                if shape.Type != 3:
                    limits.append(f'{location} 不是内嵌图片，未提取（不执行对象或外链）')
                    continue
                try:
                    bits = bytes(shape.Range.EnhMetaFileBits)
                    with Image.open(io.BytesIO(bits)) as image:
                        if image.width * image.height > 25000000:
                            raise ValueError('IMAGE_LIMIT')
                        output = io.BytesIO()
                        image.save(output, 'PNG')
                    if output.tell() > 12 * 1024 * 1024:
                        raise ValueError('IMAGE_LIMIT')
                    images.append(dict(locator=location, mime='image/png', data_base64=base64.b64encode(output.getvalue()).decode('ascii')))
                except Exception:
                    limits.append(f'{location} 本机办公软件未能导出图片，未进入视觉分析')
            limits.append(f'正文和表格已提取；已提取 {len(images)} 张内嵌图片，尚未视觉分析；{document.Shapes.Count} 个浮动对象、页眉页脚、批注和修订未单独提取')
        elif kind == 'Excel':
            app.Visible, app.DisplayAlerts, app.EnableEvents = False, False, False
            app.AskToUpdateLinks = False
            missing = pythoncom.Missing
            document = app.Workbooks.Open(str(input_path), 0, True, missing, '', '', True, missing, missing, False, False, missing, False)
            if not document.ReadOnly:
                raise ReadFailure('OFFICE_NOT_READONLY', '办公软件未以只读方式打开资料，未提取内容。')
            for s in range(1, document.Worksheets.Count + 1):
                sheet = document.Worksheets.Item(s)
                used = sheet.UsedRange
                if used.CountLarge > 100000:
                    limits.append(f'工作表「{sheet.Name}」区域过大，未提取')
                    continue
                for c in range(1, used.Cells.Count + 1):
                    cell = used.Cells.Item(c)
                    location = f'工作表「{sheet.Name}」 / {cell.Address.replace("$", "")}'
                    text = str(cell.Text or '')
                    if cell.HasFormula:
                        text = f'公式（未主动触发重算、未请求外链更新）：{cell.Formula}；当前显示：{text}'
                    add(location, text)
            limits.append('单元格显示文字及公式已提取；公式显示结果未独立核验，图片、图表、批注和其他对象未提取')
        else:
            app.DisplayAlerts = 1
            document = app.Presentations.Open(str(input_path), -1, 0, 0)
            if not document.ReadOnly:
                raise ReadFailure('OFFICE_NOT_READONLY', '办公软件未以只读方式打开资料，未提取内容。')
            for s in range(1, document.Slides.Count + 1):
                slide = document.Slides.Item(s)
                slides(slide.Shapes, f'幻灯片 {slide.SlideIndex}')
            limits.append('幻灯片文本和表格已提取；图片、图表、动画、备注及其他对象未完整提取；未执行链接更新')
        if not rows and not images:
            raise ReadFailure('OFFICE_EMPTY', '本机办公软件已打开文件，但没有取得可用正文、表格或图片。')
        if not rows:
            add('读取限制', '未取得正文文字；已提取的图片尚未视觉分析。')
        limits.append('自动提取文字尚未与人工可见正文核对；若内容不符或疑似乱码，不应作为需求依据')
        return dict(status='partial', reason='；'.join(dict.fromkeys(limits)), rows=rows, images=images,
                    code='OFFICE_EXTRACTED', office=kind, method='local-office', engine='native-com',
                    text_extraction='extracted', structure_extraction='partial', visual_review='not_run',
                    manual_open='not_verified', content_match='not_verified')
    except Exception as error:
        hresult = getattr(error, 'hresult', None)
        detail = getattr(error, 'excepinfo', None)
        if detail and len(detail) > 5 and detail[5]:
            hresult = detail[5]
        if isinstance(error, ReadFailure):
            code, reason = error.code, error.reason
        elif hresult == -2147024891 or getattr(error, 'winerror', None) == 5:
            code, reason = 'OFFICE_PERMISSION_DENIED', '本机办公软件或企业策略拒绝提取；请由有权限的人员检查。原件保留。'
        elif stage == 'com_runtime':
            code, reason = 'OFFICE_COM_RUNTIME_UNAVAILABLE', '本机 COM 读取组件不可用，请检查安装包是否完整。'
        elif hresult == -2147221164:
            code, reason = 'OFFICE_UNAVAILABLE', '对应的办公软件自动化接口无法创建。'
        else:
            code, reason = 'OFFICE_OPEN_FAILED', '当前本机办公软件无法提取内容；原件保留，具体失败阶段见读取记录。'
        return dict(status='permission_denied' if code == 'OFFICE_PERMISSION_DENIED' else 'office_required',
                    reason=reason, rows=[], code=code, failure_stage=stage, hresult=hresult, engine='native-com')
    finally:
        if owned:
            try:
                if document is not None:
                    if kind == 'Word':
                        document.Close(0)
                    elif kind == 'Excel':
                        document.Close(False)
                    else:
                        document.Close()
            except Exception:
                pass
            if kind == 'Word' and prior_links is not None:
                try:
                    app.Options.UpdateLinksAtOpen = prior_links
                except Exception:
                    pass
            try:
                app.Quit(0) if kind == 'Word' else app.Quit()
            except Exception:
                pass
        document = app = None
        gc.collect()
        if initialized:
            pythoncom.CoUninitialize()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--owner', type=Path)
    parser.add_argument('--progid')
    parser.add_argument('--process')
    parser.add_argument('--clsid')
    parser.add_argument('--probe-runtime', action='store_true')
    args = parser.parse_args(argv)
    if args.probe_runtime:
        pythoncom, _ = com_runtime()
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        pythoncom.CoUninitialize()
        result = dict(status='COM_RUNTIME_READY', engine='native-com', powershell_used=False)
    else:
        result = extract(args.input, args.owner, args.progid, args.process, args.clsid)
    args.output.write_text(json.dumps(result, ensure_ascii=False), 'utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
