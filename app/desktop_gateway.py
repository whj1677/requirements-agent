"""Customer workbench lifecycle: direct startup with local request protection."""
import hmac
import secrets

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse


class DesktopGateway:
    def __init__(self, business_factory=None, shutdown=None, active_tasks=None, instance_id=''):
        self.business = (business_factory or self._load_business)()
        self.shutdown = shutdown or (lambda: None)
        self.active_tasks = active_tasks or (lambda: [])
        self.instance_id = instance_id
        self.csrf = secrets.token_urlsafe(32)
        self.active_requests = 0
        self.stopping = False
        self.application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self._routes()

    @staticmethod
    def _load_business():
        from .main import app
        return app

    def _instance(self):
        return {'product': 'requirements-agent', 'instance_id': self.instance_id,
                'business_ready': True}

    def _routes(self):
        @self.application.get('/desktop/api/instance')
        async def instance():
            return self._instance()

        @self.application.get('/desktop/api/status')
        async def status():
            return {**self._instance(), 'license_required': False, 'csrf': self.csrf}

        @self.application.post('/desktop/api/shutdown')
        async def stop():
            if self.active_requests or self.active_tasks():
                return JSONResponse({'code': 'WORKBENCH_BUSY',
                                     'message': '材料读取或模型任务尚未结束，请稍后退出。'}, status_code=409)
            self.stopping = True
            self.shutdown()
            return {'status': 'STOPPING'}

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            await self.business(scope, receive, send)
            return
        if scope['type'] != 'http':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        request = Request(scope, receive)
        client = scope.get('client')
        expected_origin = 'http://127.0.0.1:8765'
        origin = request.headers.get('origin')
        if request.headers.get('host') != '127.0.0.1:8765' or not client or client[0] != '127.0.0.1':
            await JSONResponse({'code': 'HOST_DENIED'}, status_code=403)(scope, receive, send)
            return
        if origin and origin != expected_origin:
            await JSONResponse({'code': 'ORIGIN_DENIED'}, status_code=403)(scope, receive, send)
            return
        path = scope['path']
        control = path == '/desktop' or path.startswith('/desktop/')
        if control and scope['method'] not in ('GET', 'HEAD', 'OPTIONS'):
            supplied = request.headers.get('x-desktop-csrf', '').encode('utf-8')
            if origin != expected_origin or not hmac.compare_digest(supplied, self.csrf.encode('ascii')):
                await JSONResponse({'code': 'CSRF_DENIED'}, status_code=403)(scope, receive, send)
                return
        if self.stopping:
            await JSONResponse({'code': 'WORKBENCH_STOPPING'}, status_code=503)(scope, receive, send)
            return

        async def safe_send(message):
            if message['type'] == 'http.response.start':
                headers = list(message.get('headers', []))
                headers += [(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                            (b'referrer-policy', b'no-referrer')]
                message = {**message, 'headers': headers}
            await send(message)

        if path in ('/activation', '/activation/') and scope['method'] in ('GET', 'HEAD'):
            # Existing bookmarks return to the workbench after an upgrade.
            await RedirectResponse('/')(scope, receive, safe_send)
        elif path == '/activation' or path.startswith('/activation/'):
            await JSONResponse({'code': 'NOT_FOUND'}, status_code=404)(scope, receive, safe_send)
        elif control:
            await self.application(scope, receive, safe_send)
        else:
            self.active_requests += 1
            try:
                await self.business(scope, receive, send)
            finally:
                self.active_requests -= 1
