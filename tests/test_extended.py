import asyncio
import copy
import httpx
import pytest
from app.core import Problem,brief_hash
from app.provider import Provider,DEFAULT
from app.contracts import validate_response,profile,gate,PROFILES
from app.preview import prototype
from app.sources import webpage
from app.store import Store
from tests.helpers import prepared,EXAMPLES

@pytest.mark.parametrize('status,expected',[(401,'AUTH_FAILED'),(403,'AUTH_FAILED'),(402,'PROVIDER_QUOTA'),(404,'MODEL_UNSUPPORTED'),(429,'RATE_LIMITED'),(500,'PROVIDER_ERROR'),(302,'PROVIDER_ERROR')])
def test_http_error_classification(monkeypatch,status,expected):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(lambda req:httpx.Response(status)),**kw))
    provider=Provider();provider.keys['https://api.deepseek.com']='unit-only-key'
    with pytest.raises(Problem) as error:asyncio.run(provider.request(DEFAULT,[]))
    assert error.value.code==expected

@pytest.mark.parametrize('payload,expected',[
 ({'choices':[{'message':{'content':''},'finish_reason':'stop'}]},'OUTPUT_EMPTY'),
 ({'choices':[{'message':{'content':'{}'},'finish_reason':'length'}]},'OUTPUT_TRUNCATED'),
 ({'choices':[{'message':{'content':'```json\n{}\n```'},'finish_reason':'stop'}]},'SCHEMA_INVALID'),
 ({'choices':[{'message':{'content':'{"leak":"unit-only-key"}'},'finish_reason':'stop'}]},'SECURITY_BLOCKED')])
def test_protocol_errors_do_not_parse_fragments(monkeypatch,payload,expected):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=payload)),**kw))
    provider=Provider();provider.keys['https://api.deepseek.com']='unit-only-key'
    with pytest.raises(Problem) as error:asyncio.run(provider.request(DEFAULT,[]))
    assert error.value.code==expected

def test_key_bound_to_origin(monkeypatch,tmp_path):
    monkeypatch.setenv('RA_DEEPSEEK_API_KEY','test-secret')
    provider=Provider(env_path=tmp_path/'.env')
    assert provider.key(DEFAULT)=='test-secret'
    assert provider.key(dict(DEFAULT,base_url='https://other.example'))==''
    provider.keys['https://api.deepseek.com']='session-only'
    assert provider.key(DEFAULT)=='session-only'
    provider.keys.pop('https://api.deepseek.com')
    assert provider.key(DEFAULT)=='test-secret'

def test_explicit_fallback_reference(tmp_path,monkeypatch):
    p=prepared(Store(tmp_path));p['reference_mode']='builtin'
    monkeypatch.setitem(PROFILES,'profiles',[])
    with pytest.raises(Problem):profile('prd')
    assert profile('prd',True)['id']=='builtin-prd'
    # Ordinary source and project work are independent of missing templates.
    assert p['items'][0]['statement']

def test_dynamic_page_uses_controlled_transport(monkeypatch):
    def fixture_fetch(url):
        assert url.startswith('https://fixture.example')
        return b'<html><body><div id="result">before</div><script>document.getElementById("result").textContent="rendered by JS";</script></body></html>','text/html','https://fixture.example/'
    monkeypatch.setattr('app.sources.fetch_public',fixture_fetch)
    raw,text,method,url=asyncio.run(webpage('https://fixture.example/',True))
    assert 'rendered by JS' in text and 'before' not in text and method=='controlled-browser'

def test_injection_is_visible_text_without_external_request(tmp_path):
    from playwright.async_api import async_playwright
    spec=copy.deepcopy(EXAMPLES['ui']['result']['spec'])
    attack='<img src="https://attacker.invalid/leak" onerror="alert(1)"></script><script>alert(2)</script>'
    spec['pages'][0]['regions'][0]['components'][0]['description']=attack
    async def scenario():
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(headless=True);page=await browser.new_page();requests=[];dialogs=[]
            page.on('request',lambda r:requests.append(r.url));page.on('dialog',lambda d:dialogs.append(d.message))
            await page.set_content(prototype(spec));assert attack in await page.inner_text('body')
            assert not requests and not dialogs
            await browser.close()
    asyncio.run(scenario())
