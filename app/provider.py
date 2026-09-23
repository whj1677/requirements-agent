import asyncio
import base64
import json
import os
import time
from urllib.parse import urlsplit
import httpx
from .core import KIT, Problem, dumps, now, read_json, require
from .contracts import SCHEMA, profile

DEFAULT = dict(name='DeepSeek 官方', base_url='https://api.deepseek.com', model='deepseek-flash',
               key_env='RA_DEEPSEEK_API_KEY', vision='documented', json_mode=True, timeout=120,
               max_calls=8, max_tokens=6000, context_chars=180000, action_seconds=300,
               thinking_disabled=True, documented_at='2026-09-23', local_allowed=False, proxy='', input_price=None, output_price=None)
STAGES = read_json(KIT / 'prompts/registry.json')['stage_files']


def origin(config):
    u = urlsplit(config['base_url'])
    require(u.scheme in ('https', 'http') and u.hostname and not u.username and not u.password and not u.query and not u.fragment, 'CONFIG_INVALID', '模型地址无效')
    require(u.scheme == 'https' or config.get('local_allowed'), 'CONFIG_INVALID', 'HTTP 模型需要显式本地白名单授权')
    return f'{u.scheme}://{u.netloc}'


class Provider:
    def __init__(self):
        self.keys = {}
        self.env_origins = {'RA_DEEPSEEK_API_KEY':'https://api.deepseek.com'}

    def key(self, config):
        endpoint=origin(config)
        if endpoint in self.keys:
            return self.keys[endpoint]
        env=config.get('key_env','')
        return os.environ.get(env,'') if self.env_origins.get(env)==endpoint else ''

    async def request(self, config, messages):
        key = self.key(config)
        require(key, 'CONFIG_MISSING', '尚未配置此接收端的 API Key；未发送材料')
        body = dict(model=config['model'], messages=messages, max_tokens=config['max_tokens'], stream=False)
        if config['json_mode']:
            body['response_format'] = {'type': 'json_object'}
        if config.get('thinking_disabled') and origin(config) == 'https://api.deepseek.com':
            body['thinking'] = {'type': 'disabled'}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=config['timeout'], follow_redirects=False, trust_env=False, proxy=config.get('proxy') or None) as client:
                response = await client.post(config['base_url'].rstrip('/') + '/chat/completions', json=body, headers={'Authorization': 'Bearer ' + key})
        except httpx.TimeoutException:
            raise Problem('TIMEOUT', '模型请求超时；已发请求可能计费')
        except httpx.HTTPError:
            raise Problem('NETWORK_ERROR', '无法连接模型接收端')
        codes = {400:'PARAMETER_UNSUPPORTED', 401:'AUTH_FAILED', 403:'AUTH_FAILED', 402:'PROVIDER_QUOTA', 404:'MODEL_UNSUPPORTED', 429:'RATE_LIMITED'}
        require(response.status_code == 200, codes.get(response.status_code, 'PROVIDER_ERROR'), '模型接口 HTTP ' + str(response.status_code))
        try:
            raw = response.json()
            choice = raw['choices'][0]
            content = choice['message'].get('content')
            meta = dict(request_model=config['model'], response_model=raw.get('model'), usage=raw.get('usage'), finish_reason=choice.get('finish_reason'), elapsed_seconds=round(time.monotonic()-started, 3), origin=origin(config))
        except (KeyError, ValueError, IndexError, TypeError):
            raise Problem('SCHEMA_INVALID', '模型协议响应无法读取')
        require(choice.get('finish_reason') != 'length', 'OUTPUT_TRUNCATED', '模型输出截断；请缩减生成范围或提高输出预算')
        require(isinstance(content, str) and content.strip(), 'OUTPUT_EMPTY', '模型返回空内容')
        require(key not in content, 'SECURITY_BLOCKED', '模型输出包含凭据信息，已拒绝保存')
        # Never persist hidden reasoning; only final response is processed.
        try:
            value = json.loads(content, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except ValueError:
            raise Problem('SCHEMA_INVALID', '模型最终内容不是完整 JSON')
        return value, meta


def assemble(p, stage, user_message, config, folder, kind='prd'):
    header = dict(stage=stage, project_id=p['id'], current_revision=p['revision'], mode=p['mode'], schema=SCHEMA, remaining_budget=config['max_calls'])
    if stage == 'prd':
        header.update(document_type=kind, content_profile=profile(kind,p.get('reference_mode')=='builtin'))
    system = (KIT / 'prompts/00_system.md').read_text('utf-8') + '\n' + (KIT / 'prompts' / STAGES[stage]).read_text('utf-8') + '\n可信任务头：' + dumps(header)
    context = {k:p[k] for k in ('items','questions','options','documents','ui')}
    context['user_message'] = user_message
    context['recent_messages'] = p['messages'][-8:]
    context['source_status'] = [{k:s.get(k) for k in ('id','title','parse_status','failure_reason','excluded','purpose')} for s in p['sources']]
    remaining = config['context_chars'] - len(system) - len(dumps(context)) - config['max_tokens'] * 4 - 4000
    require(remaining > 0, 'BUDGET_EXHAUSTED', '关键底稿与输出预留已超过上下文预算；不能截断已选规则')
    selected, omitted, images = [], [], []
    for source in p['sources']:
        if source['excluded']:
            continue
        for ex in source['excerpts']:
            cost = len(dumps(ex)) + (12000 if source['image_mime'] else 0)
            if cost > remaining or (source['image_mime'] and stage != 'vision'):
                omitted.append(ex['id'])
                continue
            if source['image_mime']:
                require(config['vision'] in ('documented','verified'), 'VISION_UNAVAILABLE', '所选视觉模型能力不支持或未知；图片未分析')
                data = (folder / 'sources' / source['id']).read_bytes()
                images.append({'type':'image_url', 'image_url': {'url':'data:' + source['image_mime'] + ';base64,' + base64.b64encode(data).decode()}})
            selected.append(ex)
            remaining -= cost
    if stage == 'vision':
        require(images, 'VISION_UNAVAILABLE', '没有实际图片可发送')
    context['excerpts'] = selected
    context['omitted_excerpt_ids'] = omitted
    content = [{'type':'text','text':dumps(context)}] + images
    return [{'role':'system','content':system},{'role':'user','content':content}], selected, omitted
