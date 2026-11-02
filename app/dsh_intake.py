"""Task-scoped Harness intake. Business acceptance remains in Workflow.

The loop uses an isolated, pinned SDK runtime and a short-lived loopback relay.
Only the relay has the real credential; every actual HTTP dispatch is counted.
Neither the user's global dsh nor another workbench is used.
"""
import asyncio
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from .core import Problem, digest, dumps, ident, now, require, strict_model_json
from .dsh_bridge import DshBridge, SDK_VERSION, event_metadata
from .skill_guidance import BEGIN, END

VERSION = 'dsh-intake-6'
SNAPSHOT_CHARS = 4096
_SNAPSHOT_TOOL = r'''
  ctx.tools.register({
    name:'read_snapshot',
    description:'Read exact authorized snapshot text without line truncation. offset is a ZERO-BASED Unicode-character index, never a displayed line number. Copy next_offset to continue; complete is true only at file end. Follow input-index.json read_plan; independent chunks may be read in parallel.',
    parameters:{type:'object',properties:{file_path:{type:'string'},offset:{type:'integer',minimum:0},limit:{type:'integer',minimum:1,maximum:4096}},required:['file_path'],additionalProperties:false},
    output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},
    async execute(args) {
      if (!args || typeof args.file_path!=='string' || Object.keys(args).some(k=>!['file_path','offset','limit'].includes(k))) throw new Error('PM_REQ_INVALID_READ');
      const target=fs.realpathSync(path.resolve(config.workspace,args.file_path));
      const entry=config.snapshotFiles.find(row=>fs.realpathSync(path.join(config.workspace,row.file_path))===target);
      if (!entry) throw new Error('PM_REQ_PATH_DENIED');
      const offset=args.offset??0, limit=args.limit??4096;
      if (!Number.isInteger(offset)||offset<0||!Number.isInteger(limit)||limit<1||limit>4096) throw new Error('PM_REQ_INVALID_OFFSET');
      const raw=fs.readFileSync(target);
      const sha256=createHash('sha256').update(raw).digest('hex');
      if (sha256!==entry.sha256) throw new Error('PM_REQ_SNAPSHOT_CHANGED');
      const chars=Array.from(raw.toString('utf8'));
      if(offset>chars.length) throw new Error('PM_REQ_INVALID_OFFSET');
      const next_offset=Math.min(chars.length,offset+limit);
      return JSON.stringify({file_path:entry.file_path,offset,next_offset,total_chars:chars.length,sha256,
        complete:next_offset===chars.length,content:chars.slice(offset,next_offset).join('')});
    }
  });
'''


class WireMeter(httpx.SyncByteStream):
    """Hash raw transport bytes without retaining response bodies or reasoning."""
    def __init__(self, stream, record):
        self.stream,self.record=stream,record
        self.hash=hashlib.sha256()
        self.count=0

    def __iter__(self):
        for part in self.stream:
            self.hash.update(part);self.count+=len(part)
            self.record.update(raw_response_bytes=self.count,raw_response_sha256=self.hash.hexdigest())
            yield part

    def close(self):
        self.stream.close()


def runtime_failure(events):
    for event in reversed(events):
        reason=event.get('data',{}).get('reason',{})
        if event.get('type')=='turn/end' and isinstance(reason,dict) and reason.get('kind')=='error':
            code=(reason.get('error') or {}).get('code')
            code=code if isinstance(code,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}',code) else 'RUNTIME_ERROR'
            return dict(kind='error',code=code)
    return None


def aggregate_usage(records):
    """SDK inputTokens excludes cacheReadTokens; preserve both original counters."""
    def count(key):return sum(row.get(key,0) for row in records if type(row.get(key,0)) is int and row.get(key,0)>=0)
    cached=count('cacheReadTokens')
    prompt=count('inputTokens')+cached
    completion=count('outputTokens')
    return dict(prompt_tokens=prompt,completion_tokens=completion,
        total_tokens=count('totalTokens') or prompt+completion,
        prompt_tokens_details=dict(cached_tokens=cached))


def decode_final(content):
    """Remove only a whole-response single JSON code fence, never repair JSON."""
    normalization=None
    inner=content
    if content.lstrip().startswith('```'):
        match=re.fullmatch(r'[ \t\r\n]*```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```[ \t\r\n]*',content)
        if not match or '```' in match[1]:
            raise ValueError('not a single complete JSON code fence')
        inner=match[1]
        normalization=dict(version='dsh-single-json-fence-1',
            raw_sha256=hashlib.sha256(content.encode('utf-8')).hexdigest(),
            normalized_sha256=hashlib.sha256(inner.encode('utf-8')).hexdigest())
    value=strict_model_json(inner)
    if normalization and not isinstance(value,dict):
        raise ValueError('the fenced response must contain one JSON object')
    return value,normalization


class ReadingCoverage:
    """Count only exact, successfully delivered Unicode character intervals."""
    def __init__(self, workspace):
        self.workspace=workspace
        self.expected={p.name:p.read_text(encoding='utf-8') for p in workspace.iterdir() if p.is_file()}
        self.hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in workspace.iterdir() if p.is_file()}
        self.seen={name:[] for name in self.expected}
        self.calls={}
        self.skill_loaded=False

    def observe(self,event,clean):
        data=clean.get('data',{})
        call_id=data.get('callId')
        if event.get('type')=='tool/call':self.calls[call_id]=data
        if event.get('type')!='tool/result' or data.get('isError') is not False:return
        call=self.calls.get(call_id,{})
        args=call.get('arguments',{})
        if call.get('name')=='skill' and args.get('name')=='requirements-intake':self.skill_loaded=True
        if call.get('name')!='read_snapshot':return
        target=(self.workspace/args.get('file_path','')).resolve()
        if target.parent!=self.workspace or target.name not in self.expected:return
        def texts(node):
            if isinstance(node,dict):
                if node.get('type')=='text' and isinstance(node.get('text'),str):yield node['text']
                for child in node.values():yield from texts(child)
            elif isinstance(node,list):
                for child in node:yield from texts(child)
        expected=self.expected[target.name]
        for text in texts(event.get('data',{})):
            try:part=json.loads(text)
            except (ValueError,TypeError):continue
            if not isinstance(part,dict):continue
            start,end,content=part.get('offset'),part.get('next_offset'),part.get('content')
            if (type(start) is not int or type(end) is not int or not isinstance(content,str)
                or not 0<=start<=end<=len(expected) or start!=args.get('offset',0)
                or end-start!=len(content) or len(content)>args.get('limit',4096)
                or part.get('file_path')!=target.name or part.get('total_chars')!=len(expected)
                or part.get('sha256')!=self.hashes[target.name]
                or part.get('complete')!=(end==len(expected)) or content!=expected[start:end]):continue
            intervals=sorted(self.seen[target.name]+[(start,end)])
            merged=[]
            for left,right in intervals:
                if merged and left<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],right))
                else:merged.append((left,right))
            self.seen[target.name]=merged

    def report(self):
        rows=[]
        for name,text in self.expected.items():
            cursor=0;missing=[]
            for left,right in self.seen[name]:
                if cursor<left:missing.append([cursor,left])
                cursor=right
            if cursor<len(text):missing.append([cursor,len(text)])
            rows.append(dict(path=name,total_chars=len(text),sha256=self.hashes[name],
                delivered_chars=sum(end-start for start,end in self.seen[name]),
                delivered_ranges=self.seen[name],missing_ranges=missing,complete=not missing))
        return dict(protocol='unicode-character-ranges-1',skill_loaded=self.skill_loaded,files=rows)

    def complete(self):
        return self.skill_loaded and all(row['complete'] for row in self.report()['files'])


class IntakeReadPhase:
    """Progress-based protocol boundary, not a paid-request quota.

    Only verified successful tool delivery advances coverage. Once complete,
    exactly the next dispatch may produce the candidate, without tool calls.
    """
    def __init__(self, coverage, on_progress=None, *, json_mode=False):
        self.coverage=coverage
        self.on_progress=on_progress
        self.json_mode=json_mode is True
        self.finalization=None
        self.lock=threading.RLock()
        self.condition=threading.Condition(self.lock)
        self.received_results=set()
        self.last_progress=None
        self.completed=False
        self.finalization_claimed=False
        self.transition=None
        self.failure=None

    def observe(self,event,clean):
        with self.lock:
            self.coverage.observe(event,clean)
            if event.get("type")=="tool/result":
                self.received_results.add(clean.get("data",{}).get("callId"))
                self.condition.notify_all()
            if not self.completed and self.coverage.complete():
                self.completed=True
                self.transition=dict(event_seq=clean.get('seq'),event_time=clean.get('time'),
                    coverage=copy.deepcopy(self.coverage.report()))

    def prepare(self,body):
        with self.lock:
            # Runtime notifications and HTTP dispatch arrive on independent threads.
            # Wait for the actual matching result events; never infer delivery from arguments.
            expected={m.get("tool_call_id") for m in body.get("messages",[]) if m.get("role")=="tool" and m.get("tool_call_id")}
            if not self.condition.wait_for(lambda:expected<=self.received_results,timeout=5):
                self.failure="DSH_READING_EVENTS_INCOMPLETE"
                raise Problem(self.failure,"读取结果通知未与本轮消息同步，已停止发送并保留证据。",model_repair_allowed=False)
            current=digest(self.coverage.report())
            if self.finalization_claimed:
                self.failure='DSH_FINALIZATION_REENTRY'
                raise Problem(self.failure,'资料已经完整读取并已进入候选生成；运行组件再次请求后续步骤，已停止额外发送并保留证据。',model_repair_allowed=False)
            if self.completed:
                self.finalization_claimed=True
                # Preserve existing tools, messages and reasoning history verbatim.
                # The official Chat Completions contract disables tools with none.
                body['tool_choice']='none'
                identity=[{key:row[key] for key in ('path','sha256','total_chars')}
                    for row in self.transition['coverage']['files']]
                hint=('可信运行阶段：技能已加载，以下 '+str(len(identity))+' 个冻结文件的工具读取结果已按哈希完整送达。'
                    '读取阶段已结束；保持全部既有材料与原可信任务头Schema，直接返回符合该Schema的单一 JSON 对象。'
                    '不要再请求或输出任何工具调用，也不要输出工具调用标记或说明文字。此通知仅说明读取状态，不新增业务决定。'
                    '\n已核文件身份：'+json.dumps(identity,ensure_ascii=False,sort_keys=True))
                previous_messages_hash=digest(body['messages'])
                body['messages'].append(dict(role='system',content=hint))
                if self.json_mode:body['response_format']={'type':'json_object'}
                self.finalization=dict(protocol='dsh-candidate-json-phase-1',json_mode=self.json_mode,
                    response_format=copy.deepcopy(body.get('response_format')),
                    phase_instruction_sha256=hashlib.sha256(hint.encode('utf-8')).hexdigest(),
                    previous_messages_hash=previous_messages_hash,snapshot_identity=identity)
                if self.on_progress:self.on_progress('资料与输出格式已读完，正在生成需求初稿')
            elif self.last_progress is not None and current==self.last_progress:
                self.failure='DSH_NO_READING_PROGRESS'
                raise Problem(self.failure,'上一轮读取没有新增有效资料覆盖，已停止重复发送；已读内容与未读范围均已保留。',model_repair_allowed=False)
            self.last_progress=current
            return 'candidate_only' if self.finalization_claimed else 'reading'

    def report(self):
        with self.lock:
            return dict(protocol='complete-reading-then-candidate-1',
                phase='candidate_only' if self.finalization_claimed else 'reading',
                complete=self.completed,candidate_request_claimed=self.finalization_claimed,
                transition=copy.deepcopy(self.transition),failure=self.failure,finalization=copy.deepcopy(self.finalization))


def runtime_status():
    try:
        ready = all(importlib.metadata.version(name) == SDK_VERSION for name in
                    ('deepseek-harness-sdk', 'deepseek-harness-runtime-bin'))
        if ready:
            from deepseek_harness_runtime import bundled_runtime_path
            ready = bundled_runtime_path().is_file()
    except (importlib.metadata.PackageNotFoundError, ImportError, OSError, RuntimeError):
        ready = False
    return dict(ready=ready, code='READY' if ready else 'DSH_RUNTIME_UNAVAILABLE', version=SDK_VERSION,
                message='本机隔离运行组件可用；模型连通与草稿质量需实际验证。' if ready else '本机缺少已核验的资料整理运行组件，请先完成本机安装。')


def eligible(run, header):
    return bool(run.get('stage') == 'ingest' and header.get('initial_intake') is True and header.get('response_protocol') == 'business-draft-v1'
                and not header.get('detail_required') and not run.get('draft_request')
                and not run.get('benchmark') and not run.get('discussion_target')
                and not run.get('generation_target') and not run.get('requirement_recommendation_scope'))


class IntakeBridge(DshBridge):
    def __init__(self, *, relay_url, relay_token, config, **kwargs):
        super().__init__(**kwargs)
        self.relay_url, self.relay_token, self.config = relay_url, relay_token, config
        self.allowed_tools = ('read_snapshot', 'skill')

    def prepare(self, *, probe=False):
        path = super().prepare(probe=probe)
        patches = json.loads(path.read_text(encoding='utf-8'))
        plugin=self.home/'pm-req-boundary.mjs'
        source=plugin.read_text(encoding='utf-8')
        source="import {createHash} from 'node:crypto';\n"+source.replace('  const allowed =',_SNAPSHOT_TOOL+'\n  const allowed =',1)
        source=source.replace("['read','read_image','read_document']","['read','read_snapshot','read_image','read_document']")
        source=source.replace("name:'read',arguments:","name:'read_snapshot',arguments:")
        plugin.write_text(source,encoding='utf-8')
        for row in patches:
            if row.get('id') == 'llm-deepseek':
                row['config'].update(baseURL=self.relay_url,
                    thinking='disabled' if self.config.get('thinking_disabled') else 'enabled',
                    reasoningEffort='off' if self.config.get('thinking_disabled') else self.config.get('reasoning_effort', 'high'))
            for inserted in row.get('insert',[]):
                if inserted.get('id')=='pm-req-boundary':
                    inserted['config']['snapshotFiles']=[dict(file_path=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                        for p in self.workspace.iterdir() if p.is_file()]
        path.write_text(dumps(patches), encoding='utf-8')
        return path

    def _harness(self, api_key='', *, probe=False):
        from deepseek_harness import DeepSeekHarness
        require(runtime_status()['ready'], 'DSH_RUNTIME_UNAVAILABLE', '本机隔离运行组件尚未就绪')
        patch = self.prepare(probe=probe)
        return DeepSeekHarness(provider='deepseek-official', model=self.model,
            reasoning_effort='off' if self.config.get('thinking_disabled') else self.config.get('reasoning_effort', 'high'),
            max_tokens=self.max_tokens, cwd=str(self.workspace), runtime_cwd=str(self.workspace),
            dsh_home=str(self.home), profile='sdk', patches=(str(patch),),
            env={'DSH_TELEMETRY_DISABLED':'1', 'DSH_PERMISSION_MODE':'read-only',
                 'DSH_RUNTIME_MODE':'exe',
                 'DSH_PRIMARY_RUNTIME':'', 'DSH_BUNDLED_PRIMARY_RUNTIME':'',
                 'DSH_MAX_TOKENS_AS_SUCCESS':'false', 'DSH_AGENTS_HOME':str(self.home / 'empty-agents')},
            api_key=self.relay_token, base_url=self.relay_url, initialize_timeout_seconds=45,
            request_timeout_seconds=None, shutdown_timeout_seconds=5)


class RequestDeadlines:
    """Per-outbound absolute deadlines; SDK events cannot extend a request."""
    def __init__(self, seconds):
        self.seconds = seconds
        self.lock = threading.Lock()
        self.active = {}
        self.expired = None
        self.idle_since = time.monotonic()

    def begin(self, number):
        with self.lock:
            self._remaining()
            self.active[number] = time.monotonic()

    def end(self, number):
        with self.lock:
            self._remaining()
            self.active.pop(number, None)
            if not self.active:
                self.idle_since = time.monotonic()

    def _remaining(self):
        if self.expired is not None:return self.expired
        number, started = min(self.active.items(), key=lambda row: row[1]) if self.active else (None, self.idle_since)
        result = self.seconds - (time.monotonic() - started), number
        if result[0] <= 0:self.expired = result
        return result

    def remaining(self):
        with self.lock:
            return self._remaining()


class Relay:
    """Authenticated ephemeral relay; fixed upstream, no arbitrary URL forwarding."""
    def __init__(self, config, key, evidence, on_dispatch=None, *, read_phase=None, on_failure=None, deadlines=None):
        self.config, self.key, self.evidence = config, key, evidence
        self.on_dispatch = on_dispatch
        self.read_phase,self.on_failure=read_phase,on_failure
        self.deadlines=deadlines
        self.token = secrets.token_urlsafe(32)
        self.cancelled = threading.Event()
        self.requests = []
        self.error = None
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def setup(self):
                super().setup()
                self.connection.settimeout(min(5,config['timeout']))
            def handle(self):
                try: super().handle()
                except (ConnectionResetError, BrokenPipeError): pass
            def do_POST(self):
                if self.path not in ('/chat/completions', '/v1/chat/completions') or self.headers.get('Authorization') != 'Bearer ' + owner.token:
                    # Drain small rejected bodies before close; Windows otherwise
                    # resets the socket and can discard the explicit 403 response.
                    try:
                        length=int(self.headers.get('Content-Length','0'))
                        if 0<length<=65536:self.rfile.read(length)
                    except (OSError,ValueError):pass
                    self.send_error(403); return
                deadline_number = None
                try:
                    require(not owner.cancelled.is_set(), 'CANCELLED', '任务已取消')
                    length = int(self.headers.get('Content-Length', '0'))
                    require(0 < length <= 16000000, 'DSH_REQUEST_INVALID', '整理请求超出安全范围')
                    body = json.loads(self.rfile.read(length))
                    require(body.get('model') == config['model'], 'DSH_MODEL_CHANGED', '运行组件改变了冻结模型，未发送')
                    require(body.get('stream') is True, 'DSH_REQUEST_INVALID', '运行组件未请求流式响应，未发送')
                    # Meter at the network boundary, including SDK internal retries.
                    body['max_tokens'] = config['max_tokens']
                    body['thinking'] = {'type':'disabled' if config.get('thinking_disabled') else 'enabled'}
                    if not config.get('thinking_disabled'):
                        body['reasoning_effort'] = config.get('reasoning_effort', 'high')
                    phase=owner.read_phase.prepare(body) if owner.read_phase else None
                    record = dict(number=len(owner.requests)+1, started_at=now(), input_hash=digest(body), status='dispatch_unknown',
                                  model=body['model'],thinking=copy.deepcopy(body['thinking']),
                                  reasoning_effort=body.get('reasoning_effort'),max_tokens=body['max_tokens'],stream=True)
                    if phase:record.update(reading_phase=phase,tool_choice=body.get('tool_choice'),
                        response_format=copy.deepcopy(body.get('response_format')),
                        finalization=copy.deepcopy(owner.read_phase.finalization))
                    if owner.deadlines and owner.deadlines.remaining()[0] <= 0:
                        raise Problem('TIMEOUT', '资料整理上一步已超时，未发送后续请求。', model_repair_allowed=False)
                    if evidence and evidence.request_budget:
                        # A distinct ledger identity per actual outbound HTTP request.
                        ledger_id = ident('CALL')
                        evidence.request_budget.reserve(ledger_id, config)
                        evidence.request_budget.mark(ledger_id, 'dispatch_unknown')
                        record['call_id'] = ledger_id
                    owner.requests.append(record)
                    if owner.deadlines:
                        deadline_number = record['number']
                        owner.deadlines.begin(deadline_number)
                    owner.save()
                    if owner.on_dispatch: owner.on_dispatch(len(owner.requests))
                    with httpx.Client(timeout=config['timeout'], proxy=config.get('proxy') or None,
                                      follow_redirects=False, trust_env=False) as client:
                        with client.stream('POST', 'https://api.deepseek.com/chat/completions', json=body,
                                           headers={'Authorization':'Bearer '+key}) as response:
                            record.update(http_status=response.status_code, status='response_received')
                            encoding=response.headers.get('content-encoding','identity').lower()
                            content_type=response.headers.get('content-type','').split(';',1)[0].lower()
                            record.update(content_encoding=encoding if encoding in ('identity','gzip','deflate','br','zstd') else 'other',
                                content_type=content_type if content_type in ('text/event-stream','application/json') else 'other',
                                relay_decodes_content=True,sse_done=False,decoded_response_bytes=0)
                            response.stream=WireMeter(response.stream,record)
                            if record.get('call_id'):
                                evidence.request_budget.mark(record['call_id'], 'response_received')
                            owner.save()
                            self.send_response(response.status_code)
                            self.send_header('Content-Type', response.headers.get('content-type', 'application/json'))
                            self.send_header('Connection', 'close')
                            self.end_headers()
                            decoded_hash=hashlib.sha256();tail=b''
                            try:
                                # httpx negotiates compression. Forward decoded SSE;
                                # sending raw gzip without Content-Encoding corrupts it.
                                for part in response.iter_bytes():
                                    if owner.cancelled.is_set(): break
                                    decoded_hash.update(part)
                                    record['decoded_response_bytes']+=len(part)
                                    record['decoded_response_sha256']=decoded_hash.hexdigest()
                                    combined=tail+part
                                    if b'data: [DONE]' in combined:record['sse_done']=True
                                    tail=combined[-32:]
                                    self.wfile.write(part)
                                    self.wfile.flush()
                                record['relay_stream_complete']=not owner.cancelled.is_set()
                            finally:owner.save()
                except (BrokenPipeError, ConnectionResetError): pass
                except Exception as error:
                    if owner.error is None:
                        owner.error = error if isinstance(error, Problem) else Problem('DSH_TRANSPORT_ERROR', '资料整理请求失败，已有证据保留')
                    owner.cancelled.set()
                    owner.save()
                    if owner.on_failure:owner.on_failure(owner.error)
                    try: self.send_error(502, 'DSH request failed')
                    except OSError: pass
                finally:
                    if deadline_number is not None:owner.deadlines.end(deadline_number)
                    self.close_connection = True
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def save(self):
        if self.evidence:
            self.evidence.value['dsh_http_requests'] = copy.deepcopy(self.requests)
            self.evidence.value['outbound_http_requests'] = len(self.requests)
            if self.read_phase:self.evidence.value['dsh_read_phase']=self.read_phase.report()
            self.evidence.save()

    def start(self):
        self.thread.start()
        return 'http://127.0.0.1:' + str(self.server.server_port)

    def close(self):
        self.cancelled.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


async def request(config, messages, key, evidence, *, on_dispatch=None, on_progress=None):
    require(runtime_status()['ready'], 'DSH_RUNTIME_UNAVAILABLE', '本机隔离运行组件尚未就绪')
    require(config['base_url'].rstrip('/') == 'https://api.deepseek.com', 'DSH_ENDPOINT_UNSUPPORTED', '资料整理试用仅支持已授权的 DeepSeek 官方接收端')
    require(evidence is not None, 'DSH_EVIDENCE_REQUIRED', '资料整理需要绑定任务证据')
    root = evidence.path.parent.parent / 'dsh-intake' / evidence.value['call_id']
    workspace, skills = root / 'snapshot', root / 'skills'
    workspace.mkdir(parents=True, exist_ok=False)
    system = messages[0]['content']
    prefix, raw_header = system.split('可信任务头：', 1)
    header = json.loads(raw_header)
    from .dsh_skills import export
    skill_manifest = export(system, skills)
    if BEGIN in prefix:
        before, tail = prefix.split(BEGIN, 1)
        prefix = before + tail.split(END, 1)[1]
    # This is exactly the daily assembled input; no golden answers or new source IDs.
    context = messages[1]['content']
    if isinstance(context, list):
        require(all(block.get('type') == 'text' for block in context), 'DSH_SCOPE_UNSUPPORTED', '本阶段只读取已提取的文字资料；图片仍走既有读取流程')
        context = '\n'.join(block['text'] for block in context)
    # Pretty printing preserves the JSON value and avoids one giant truncated line.
    frozen = {'current-workspace.json':json.dumps(json.loads(context),ensure_ascii=False,indent=2),
              'business-draft.schema.json':json.dumps(header['schema'],ensure_ascii=False,indent=2)}
    for name, content in frozen.items(): (workspace / name).write_text(content, encoding='utf-8',newline='\n')
    index = dict(version=VERSION, files=[dict(path=name, sha256=hashlib.sha256((workspace/name).read_bytes()).hexdigest(),
                 total_chars=len(content)) for name, content in frozen.items()],
                 read_protocol='read_snapshot uses zero-based Unicode-character offsets; copy next_offset, never a line number',
                 read_plan=[dict(file_path=name,offset=start,limit=SNAPSHOT_CHARS)
                    for name,content in frozen.items() for start in range(0,len(content),SNAPSHOT_CHARS)],
                 input_hash=digest(messages), reading_scope='既有已提取文字、当前需求与已答信息；不声称重新读取原件')
    (workspace / 'input-index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2), encoding='utf-8',newline='\n')
    evidence.value['dsh_intake'] = dict(version=VERSION, sdk_version=SDK_VERSION, transport='chat_completions',
        snapshot=index, skills=skill_manifest, session_id='intake-'+evidence.value['call_id'],
        protocol_difference='DSH工具循环使用Chat Completions；现有直连使用Responses严格schema，最终均经相同应用守卫。')
    evidence.save()
    coverage = ReadingCoverage(workspace.resolve())
    read_phase=IntakeReadPhase(coverage,on_progress,json_mode=config.get('json_mode'))
    loop=asyncio.get_running_loop()
    relay_failure=loop.create_future()
    def notify_failure(error):
        def accept():
            if not relay_failure.done():relay_failure.set_result(error)
        loop.call_soon_threadsafe(accept)
    deadlines = RequestDeadlines(config['timeout'])
    relay = Relay(config, key, evidence, on_dispatch,read_phase=read_phase,on_failure=notify_failure,deadlines=deadlines)
    url = relay.start()
    bridge = IntakeBridge(relay_url=url, relay_token=relay.token, config=config,
        workspace=workspace, skill_root=skills, dsh_home=root/'runtime', evidence_dir=root/'runtime-evidence',
        model=config['model'], max_tokens=config['max_tokens'], timeout_seconds=config['timeout'])
    try:
        harness = bridge._harness()
    except BaseException:
        await asyncio.to_thread(relay.close)
        raise
    events = []
    started = time.monotonic()
    def receive(notification):
        if notification.method != 'session.event': return
        event = notification.payload.get('event', {})
        clean = event_metadata(event, (key, relay.token))
        events.append(clean)
        read_phase.observe(event, clean)
        if event.get('type') == 'assistant/message':
            data = event.get('data', {})
            message = data.get('message') if isinstance(data.get('message'),dict) else data
            content = message.get('content', [])
            visible = ''.join(b.get('text','') for b in content if isinstance(b,dict) and b.get('type')=='text')
            if visible and key not in visible and relay.token not in visible:
                evidence.value.setdefault('dsh_visible_outputs',[]).append(visible[:evidence.final_limit])
                evidence.save()
        if event.get('type') == 'tool/call' and on_progress and not read_phase.completed:
            on_progress('正在读取本轮资料与已答信息')
    prompt = (prefix + '\n可信任务头：' + raw_header +
              '\n本轮用 skill 工具加载 requirements-intake，再用 read_snapshot 完整读取 input-index.json 与其列出的文件。'
              'offset是从0开始的Unicode字符位置，不是行号。若complete=false，复制next_offset续读；可并行执行索引read_plan中的独立片段。'
              '当前工作区只有冻结的本次输入，不读取外部目录。最后返回符合任务头 schema 的 JSON 候选；不要写入文件。')
    if on_progress: on_progress('正在准备资料整理运行环境')
    worker = asyncio.create_task(asyncio.to_thread(harness.run, prompt,
        session_id='intake-'+evidence.value['call_id'], on_notification=receive))
    try:
        while True:
            remaining, number = deadlines.remaining()
            if remaining <= 0:
                evidence.value['dsh_timeout'] = dict(scope='http_request' if number is not None else 'runtime_idle',
                    request_number=number, timeout_seconds=config['timeout'])
                evidence.save()
                raise Problem('TIMEOUT', '资料整理单次请求或运行空闲超时；已发请求可能计费，已有证据保留。', model_repair_allowed=False)
            done,_=await asyncio.wait((worker,relay_failure),timeout=remaining,return_when=asyncio.FIRST_COMPLETED)
            if relay_failure in done:raise relay_failure.result()
            if worker in done and deadlines.remaining()[0] > 0:break
        result = await asyncio.shield(worker)
        if relay.error: raise relay.error
        failure=runtime_failure(events)
        if failure:
            evidence.value['dsh_runtime_error']=failure
            evidence.save()
            raise Problem('DSH_'+failure['code'],
                '资料整理运行失败：模型响应流提前结束；已有请求与读取证据保留。' if failure['code']=='STREAM_CLOSED'
                else '资料整理运行组件返回错误，已有请求与读取证据保留。',model_repair_allowed=False)
        final = result.final_response
        require(key not in final and relay.token not in final, 'SECURITY_BLOCKED', '输出包含凭据信息，已拒绝保存')
        usage_records = [e['data']['usage'] for e in events if e.get('type')=='assistant/message' and isinstance(e.get('data',{}).get('usage'),dict)]
        usage = aggregate_usage(usage_records)
        evidence.value['dsh_usage_records']=usage_records
        evidence.value['dsh_usage_mapping']='prompt_tokens=inputTokens+cacheReadTokens; cached_tokens=cacheReadTokens; original SDK counters retained'
        meta = dict(finish_reason=result.finish_reason, response_model=config['model'],
                    request_model=config['model'], usage=usage if usage_records else None, elapsed_seconds=round(time.monotonic()-started, 3),
                    intake_engine='deepseek-harness', outbound_http_requests=len(relay.requests), response_protocol='chat_completions')
        # Each HTTP request already has its own budget entry, not the aggregate call.
        ledger = evidence.request_budget
        evidence.request_budget = None
        try:
            evidence.response(final, finish_reason=result.finish_reason, usage=meta['usage'], response_model=config['model'], elapsed_seconds=meta['elapsed_seconds'])
        finally:
            evidence.request_budget = ledger
        evidence.value['request_state'] = 'response_received'
        if meta['usage']:
            evidence.value['usage']['prompt_tokens_details']=copy.deepcopy(meta['usage']['prompt_tokens_details'])
        require(result.finish_reason not in ('length','max_tokens'), 'OUTPUT_TRUNCATED', '整理输出截断；原始候选与资料证据保留')
        evidence.value['dsh_reading_coverage'] = coverage.report()
        require(coverage.complete(), 'DSH_READING_UNVERIFIED', '本轮资料尚未完整读取或技能加载失败，结果未采纳；读取范围和原始候选保留')
        try: value,normalization = decode_final(final)
        except (ValueError, TypeError) as error:
            raise Problem('SCHEMA_INVALID', '资料整理结果不是完整 JSON，原始候选保留', model_repair_allowed=False) from error
        if normalization:
            evidence.value['dsh_transport_normalization']=normalization
            meta['dsh_transport_normalization']=normalization
            evidence.save()
        return value, meta
    except asyncio.CancelledError:
        relay.cancelled.set()
        raise
    finally:
        if not relay_failure.done():relay_failure.cancel()
        relay.cancelled.set()
        await asyncio.to_thread(harness.close)
        await asyncio.to_thread(relay.close)
        if not worker.done():
            try: await asyncio.wait_for(asyncio.shield(worker), 7)
            except (Exception, asyncio.CancelledError): pass
        if worker.done() and not worker.cancelled():
            worker.exception()  # Consume a concurrent SDK failure after relay rejection.
        evidence.value['dsh_events'] = events
        evidence.value['dsh_reading_coverage'] = coverage.report()
        evidence.value['outbound_http_requests'] = len(relay.requests)
        evidence.save()
