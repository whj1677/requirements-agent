"""Loopback-only activation gateway. Business imports occur only after licensing."""
import asyncio
import hmac
import json
from pathlib import Path
import secrets
import tempfile

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from .licensing import LicenseError, LicenseManager, MAX_LICENSE_BYTES


class ActivationGateway:
    def __init__(self, manager=None, business_factory=None, shutdown=None, active_tasks=None,
                 asset_dir=None, instance_id=''):
        self.manager = manager or LicenseManager()
        self.business_factory = business_factory or self._load_business
        self.shutdown = shutdown or (lambda: None)
        self.active_tasks = active_tasks or (lambda: [])
        self.asset_dir = Path(asset_dir) if asset_dir else Path(__file__).resolve().parents[1] / 'web/activation'
        self.instance_id = instance_id
        self.csrf = secrets.token_urlsafe(32)
        self.business = None
        self.active_requests = 0
        self.stopping = False
        self.start_lock = asyncio.Lock()
        self.application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self._routes()

    @staticmethod
    def _load_business():
        from .main import app
        return app

    async def _body(self, request, limit=MAX_LICENSE_BYTES):
        length = request.headers.get('content-length')
        if length is not None and (not length.isdecimal() or int(length) > limit):
            raise LicenseError('LICENSE_DOCUMENT_INVALID')
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                raise LicenseError('LICENSE_DOCUMENT_INVALID')
        return bytes(data)

    def _routes(self):
        app = self.application

        @app.exception_handler(LicenseError)
        async def license_error(_request, error):
            return JSONResponse({'code': error.code, 'message': error.message}, status_code=403)

        @app.get('/')
        @app.get('/activation/')
        async def page():
            return FileResponse(self.asset_dir / 'index.html', media_type='text/html')

        @app.get('/activation')
        async def redirect():
            return RedirectResponse('/activation/')

        @app.get('/activation/assets/{name}')
        async def asset(name: str):
            if name not in ('app.js', 'style.css'):
                return JSONResponse({'code': 'NOT_FOUND'}, status_code=404)
            return FileResponse(self.asset_dir / name,
                                media_type='text/javascript' if name.endswith('.js') else 'text/css')

        @app.get('/activation/api/status')
        async def status():
            result = {'licensed': False, 'csrf': self.csrf, 'business_ready': self.business is not None,
                      'product': 'requirements-agent', 'instance_id': self.instance_id}
            try:
                payload = await asyncio.to_thread(self.manager.verify_cached)
                result.update(licensed=True, subject=payload['subject']['displayName'])
            except LicenseError as error:
                result.update(code=error.code, message=error.message)
            return result

        @app.get('/activation/api/instance')
        async def instance():
            # Startup/reopen identity must not depend on WMI or license latency.
            return {'product': 'requirements-agent', 'instance_id': self.instance_id,
                    'business_ready': self.business is not None}

        @app.post('/activation/api/request')
        async def make_request(request: Request):
            try:
                body = json.loads(await self._body(request, 2048))
                if not isinstance(body, dict) or set(body) != {'display_name'}:
                    raise ValueError()
            except (UnicodeError, ValueError):
                return JSONResponse({'code': 'REQUEST_INVALID', 'message': '请填写授权名称。'}, status_code=400)
            result = await asyncio.to_thread(self.manager.create_request, body['display_name'])
            return JSONResponse(result, headers={'Content-Disposition': 'attachment; filename="requirements-agent-request.json"'})

        @app.post('/activation/api/install')
        async def install(request: Request):
            replacement = request.query_params.get('replace', 'false')
            if replacement not in ('true', 'false'):
                raise LicenseError('LICENSE_REPLACEMENT_FLAG_INVALID')
            data = await self._body(request)
            # Preserve original JSON bytes, including duplicate fields, for DLL verification.
            with tempfile.TemporaryDirectory(prefix='requirements-agent-license-') as folder:
                source = Path(folder) / 'incoming.json'
                source.write_bytes(data)
                result = await asyncio.to_thread(self.manager.install_license, source, replacement == 'true')
            return {'licensed': True, 'subject': result['payload']['subject']['displayName']}

        @app.post('/activation/api/start')
        async def start():
            async with self.start_lock:
                if self.stopping:
                    return JSONResponse({'code': 'WORKBENCH_STOPPING'}, status_code=503)
                await asyncio.to_thread(self.manager.verify_cached)
                if self.stopping:
                    return JSONResponse({'code': 'WORKBENCH_STOPPING'}, status_code=503)
                if self.business is None:
                    # Synchronous factory initialization cannot interleave with shutdown.
                    self.business = self.business_factory()
            return {'ready': True, 'url': '/'}

        @app.post('/activation/api/shutdown')
        async def stop():
            if self.active_requests or self.start_lock.locked() or self.active_tasks():
                return JSONResponse({'code': 'WORKBENCH_BUSY', 'message': '材料读取或模型任务尚未结束，请稍后退出。'}, status_code=409)
            self.stopping = True
            self.shutdown()
            return {'status': 'STOPPING'}

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            await self.application(scope, receive, send)
            return
        if scope['type'] != 'http':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        request = Request(scope, receive)
        host = request.headers.get('host', '')
        client = scope.get('client')
        origin = request.headers.get('origin')
        expected_origin = 'http://127.0.0.1:8765'
        if host != '127.0.0.1:8765' or not client or client[0] != '127.0.0.1':
            await JSONResponse({'code': 'HOST_DENIED'}, status_code=403)(scope, receive, send)
            return
        if origin and origin != expected_origin:
            await JSONResponse({'code': 'ORIGIN_DENIED'}, status_code=403)(scope, receive, send)
            return
        path = scope['path']
        activation = path == '/activation' or path.startswith('/activation/')
        if activation and scope['method'] not in ('GET', 'HEAD', 'OPTIONS'):
            supplied = request.headers.get('x-activation-csrf', '').encode('utf-8')
            if origin != expected_origin or not hmac.compare_digest(supplied, self.csrf.encode('ascii')):
                await JSONResponse({'code': 'CSRF_DENIED'}, status_code=403)(scope, receive, send)
                return
        if self.stopping:
            await JSONResponse({'code': 'WORKBENCH_STOPPING'}, status_code=503)(scope, receive, send)
            return

        async def safe_send(message):
            if message['type'] == 'http.response.start' and (activation or self.business is None):
                headers = list(message.get('headers', []))
                headers += [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                            (b'referrer-policy', b'no-referrer'),
                            (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")]
                message = {**message, 'headers': headers}
            await send(message)

        if activation or (self.business is None and path == '/'):
            operation = scope['method'] == 'POST' and path != '/activation/api/shutdown'
            if operation:
                self.active_requests += 1
            try:
                await self.application(scope, receive, safe_send)
            finally:
                if operation:
                    self.active_requests -= 1
        elif self.business is None:
            await JSONResponse({'code': 'LICENSE_REQUIRED', 'message': '请先在激活页导入授权并进入工作台。'}, status_code=403)(scope, receive, safe_send)
        else:
            self.active_requests += 1
            try:
                await self.business(scope, receive, send)
            finally:
                self.active_requests -= 1
