import asyncio
import hashlib
import http.client
import io
import ipaddress
import socket
import ssl
import zipfile
import subprocess
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from docx import Document
from PIL import Image
from pypdf import PdfReader
from .core import ROOT, Problem, ident, now, require
from .office import OFFICE_EXTENSIONS, extract_office
from .office_formats import check_package, parse_workbook, parse_presentation

MAX_BYTES = 12 * 1024 * 1024
SOURCE_EXTENSIONS = ['.txt', '.md', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.pdf', '.png', '.jpg', '.jpeg', '.webp']


def public_target(url):
    u = urlsplit(url)
    require(u.scheme in ('http', 'https') and u.hostname and not u.username and not u.password,
            'SOURCE_FAILED', '仅支持无凭据的公开 HTTP(S) 网页')
    require(u.port in (None, 80, 443), 'SOURCE_FAILED', '网页端口不允许')
    try:
        addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == 'https' else 80), type=socket.SOCK_STREAM)))
    except OSError:
        raise Problem('SOURCE_FAILED', '域名解析失败')
    require(addresses and all(ipaddress.ip_address(a).is_global for a in addresses), 'SOURCE_FAILED', '拒绝回环、私网、保留地址及元数据地址')
    return u, addresses[0]


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self.ip = ip

    def connect(self):
        raw = socket.create_connection((self.ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def fetch_public(url):
    # Connect to the address that was validated, retaining TLS hostname checks.
    for _ in range(5):
        u, ip = public_target(url)
        conn = PinnedHTTPS(u.hostname, ip, u.port or 443, 10) if u.scheme == 'https' else http.client.HTTPConnection(ip, port=u.port or 80, timeout=10)
        try:
            conn.request('GET', (u.path or '/') + ('?' + u.query if u.query else ''), headers={'Host': u.netloc, 'User-Agent': 'RequirementsAgent/1.1', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            require(200 <= response.status < 300, 'SOURCE_FAILED', '网页响应 HTTP ' + str(response.status))
            payload = response.read(MAX_BYTES + 1)
            require(len(payload) <= MAX_BYTES, 'SOURCE_FAILED', '网页超过读取上限')
            return payload, response.getheader('Content-Type', 'text/html'), url
        finally:
            conn.close()
    raise Problem('SOURCE_FAILED', '重定向次数超限')


async def webpage(url, dynamic=False):
    if not dynamic:
        raw, mime, final = await asyncio.wait_for(asyncio.to_thread(fetch_public, url), 30)
        soup = BeautifulSoup(raw, 'html.parser')
        for e in soup(['script', 'style', 'iframe', 'noscript']):
            e.decompose()
        return raw, soup.get_text('\n', strip=True), 'http-text', final
    from playwright.async_api import async_playwright
    blocked = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--disable-background-networking', '--disable-quic'])
        context = await browser.new_context(service_workers='block', accept_downloads=False)
        async def route(request_route):
            req = request_route.request
            if req.method != 'GET' or req.resource_type not in ('document', 'script', 'stylesheet', 'xhr', 'fetch'):
                blocked.append(req.resource_type)
                return await request_route.abort()
            try:
                body, mime, final = await asyncio.to_thread(fetch_public, req.url)
                # Fulfillment prevents the browser from re-resolving the remote hostname.
                await request_route.fulfill(status=200, body=body, headers={'Content-Type': mime, 'Content-Security-Policy': "connect-src 'self' https:; frame-src 'none'; worker-src 'none'; object-src 'none'"})
            except Exception:
                blocked.append(req.resource_type)
                await request_route.abort()
        await context.route('**/*', route)
        await context.route_web_socket('**/*', lambda ws: ws.close())
        page = await context.new_page()
        page.on('dialog', lambda d: d.dismiss())
        try:
            await page.goto(url, wait_until='networkidle', timeout=25000)
            text = await page.locator('body').inner_text(timeout=3000)
            raw = (await page.content()).encode()
            return raw, text, 'controlled-browser' + (';partial-resources' if blocked else ''), url
        finally:
            await browser.close()


def parse_bytes(title, data):
    require(0 < len(data) <= MAX_BYTES, 'SOURCE_FAILED', '文件为空或超过 12 MiB')
    ext = Path(title).suffix.lower()
    rows, status, reason, image_mime = [], 'read', '', None
    if ext in ('.txt', '.md'):
        text = data.decode('utf-8-sig')
        rows = [(f'行 {n}', t) for n, t in enumerate(text.splitlines(), 1) if t.strip()]
    elif ext == '.docx':
        check_package(data)
        doc = Document(io.BytesIO(data))
        rows = [(f'段落 {i}', p.text) for i, p in enumerate(doc.paragraphs, 1) if p.text.strip()]
        rows += [(f'表格 {t} 行 {r}', ' | '.join(c.text for c in row.cells)) for t, table in enumerate(doc.tables, 1) for r, row in enumerate(table.rows, 1)]
        if len(doc.inline_shapes):
            status, reason = 'partial', '已提取正文/表格；内嵌图片未作视觉分析'
    elif ext == '.xlsx':
        rows, status, reason, image_mime = parse_workbook(data)
    elif ext == '.pptx':
        rows, status, reason, image_mime = parse_presentation(data)
    elif ext in ('.doc', '.xls', '.ppt'):
        raise Problem('OFFICE_REQUIRED', '旧版 Office 文件需要本机 Office 读取')
    elif ext == '.pdf':
        reader = PdfReader(io.BytesIO(data))
        require(len(reader.pages) <= 200, 'SOURCE_FAILED', 'PDF 超过 200 页')
        for i, page in enumerate(reader.pages, 1):
            try:
                text = page.extract_text() or ''
            except Exception:
                text = ''
            if text.strip():
                rows.append((f'第 {i} 页', text))
            else:
                status, reason = 'partial', reason + f'第 {i} 页未提取文字；'
    elif ext in ('.png', '.jpg', '.jpeg', '.webp'):
        image = Image.open(io.BytesIO(data))
        require(image.format in ('PNG', 'JPEG', 'WEBP') and image.width * image.height <= 25_000_000, 'SOURCE_FAILED', '图片格式或像素数不支持')
        image.verify()
        image_mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}[image.format]
        rows = [('整图', '图片原始像素；尚未视觉分析')]
        status = 'awaiting_vision'
    else:
        raise Problem('SOURCE_FAILED', '支持 Word、Excel、PowerPoint、TXT、MD、PDF、PNG、JPEG、WebP')
    require(rows, 'SOURCE_FAILED', '没有可读取内容；请提供可提取文本或截图')
    require(sum(len(text) for _, text in rows) <= 2000000 and len(rows) <= 20000,
            'SOURCE_LIMIT', '提取内容超过上限，请拆分材料')
    require(not any('\ufffd' in text or '\x00' in text for _, text in rows),
            'SOURCE_FAILED', '提取文本含无法识别的字符，尚不能标记为已读取')
    return rows, status, reason, image_mime


def parse_bounded(title,data):
    if Path(title).suffix.lower() in ('.txt','.md'):
        return parse_bytes(title,data)
    try:
        result=subprocess.run([sys.executable,'-m','app.parse_worker',Path(title).suffix.lower()],input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    except subprocess.TimeoutExpired:
        raise Problem('SOURCE_FAILED','文件解析超过 30 秒，解析子进程已终止')
    import json
    if result.returncode != 0:
        try:
            failure = json.loads(result.stdout.decode('utf8'))
        except (ValueError, UnicodeError):
            failure = {}
        raise Problem(failure.get('code','SOURCE_FAILED'), failure.get('message','当前读取方式无法解析；原件已保留，可重新读取'))
    return json.loads(result.stdout.decode('utf8'))


def save_source(store, title, data, purpose='current', uri=None, parsed=None):
    sid = ident('SRC')
    sha = hashlib.sha256(data).hexdigest()
    rawdir = store.folder / 'sources'
    rawdir.mkdir(exist_ok=True)
    path = rawdir / sid
    path.write_bytes(data)
    source = dict(id=sid, title=title, purpose=purpose, uri=uri, sha256=sha, created=now(), version=1, excluded=False, excerpts=[], parse_status='failed', failure_reason='', image_mime=None)
    try:
        reading = dict(method='standard-parser', visual_review='not_run', manual_open='not_verified')
        try:
            rows, status, reason, mime = parsed or parse_bounded(title, data)
        except Exception as error:
            if Path(title).suffix.lower() not in OFFICE_EXTENSIONS or (isinstance(error, Problem) and error.code in ('SOURCE_UNSAFE','SOURCE_LIMIT')):
                raise
            result = extract_office(title, data, rawdir)
            rows, status, reason, mime = result.get('rows', []), result['status'], result['reason'], None
            reading.update({key:value for key,value in result.items() if key not in ('rows','status','reason')})
            reading.update(method='local-office', standard_parser='failed')
            require(isinstance(rows,list) and all(isinstance(r,(list,tuple)) and len(r)==2 and all(isinstance(x,str) for x in r) for r in rows), 'SOURCE_FAILED', '本机 Office 返回无效摘录')
            if status in ('read','partial'):
                require(any(text.strip() for _,text in rows), 'SOURCE_FAILED', '本机 Office 没有返回可读取正文')
                require(not any('\ufffd' in text or '\x00' in text for _,text in rows), 'SOURCE_FAILED', '本机 Office 提取文本含无法识别的字符')
        source['reading'] = reading
        source.update(parse_status=status, failure_reason=reason, image_mime=mime)
        for locator, text in rows:
            # Bound each excerpt while retaining exact source positions.
            for offset in range(0, len(text), 5000):
                source['excerpts'].append(dict(id=ident('EX'), source_id=sid, source_hash=sha, locator=f'{locator} / 字符 {offset}', text=text[offset:offset+5000], method='image' if mime else ('local-office' if reading['method']=='local-office' else 'extracted')))
    except Exception as e:
        source['parse_status']='failed'
        source['excerpts']=[]
        source['failure_reason'] = e.message if isinstance(e, Problem) else '当前读取方式无法解析；原件已保留'
    return source
