"""Recovery reads acknowledgements without replaying a lost confirmation POST."""
import asyncio
import copy
import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.core import Problem
from app.store import Store
from app.refinement_confirmation import register, RECEIPT_KIND
from tests.test_requirement_batch_confirmation import setup
from tests.test_refinement_confirmation import api, payload, post


def database_rows(store):
    with store.connect() as db:
        return {table:[tuple(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1')]
                for table in ['projects','revisions','records','settings','migrations']}


def endpoint(pid,key):
    return '/api/projects/'+pid+'/refinement-confirmation/receipts/'+key


def test_real_asgi_commit_finishes_but_response_is_lost_then_one_read_recovers(tmp_path):
    store=Store(tmp_path);p=setup(store);body=payload(p);app=FastAPI();register(app,store)
    @app.exception_handler(Problem)
    async def problem(_,error):return JSONResponse(status_code=error.status,content=dict(code=error.code,message=error.message))
    class LostResponse(httpx.AsyncBaseTransport):
        def __init__(self):self.inner=httpx.ASGITransport(app=app);self.posts=0;self.gets=0
        async def handle_async_request(self,request):
            response=await self.inner.handle_async_request(request)
            if request.method=='POST':
                self.posts+=1
                assert response.status_code==200
                assert store.records(p['id'],RECEIPT_KIND)
                await asyncio.sleep(.02)
                raise httpx.ReadError('synthetic response lost after commit',request=request)
            self.gets+=1;return response
    async def run():
        transport=LostResponse()
        async with httpx.AsyncClient(transport=transport,base_url='http://test') as client:
            with pytest.raises(httpx.ReadError):await client.post('/api/projects/'+p['id']+'/refinement-confirmation',json=body)
            before=database_rows(store)
            result=await client.get(endpoint(p['id'],body['idempotency_key']))
            assert result.status_code==200,result.text
            value=result.json();receipt=store.records(p['id'],RECEIPT_KIND)[0]
            assert value['confirmed'] is True and value['receipt']==receipt
            assert value['project']==store.get(p['id']) and value['project']['revision']==receipt['result_revision']
            assert value['destination']==receipt['destination']
            assert value['implementation_pending_ids']==receipt['implementation_pending_ids']
            assert value['issues']==receipt['implementation_issues']
            assert database_rows(store)==before
            assert transport.posts==1 and transport.gets==1
            assert not store.records(p['id'],'run')
    asyncio.run(run())


@pytest.mark.parametrize('case',['missing','stale','other_project','invalid_short','invalid_long'])
def test_read_recovery_never_writes_or_claims_unconfirmed_or_old_receipts(tmp_path,case):
    store=Store(tmp_path);p=setup(store);body=payload(p)
    with api(store) as client:
        if case!='missing':assert post(client,p,body).status_code==200
        pid=p['id'];key=body['idempotency_key']
        if case=='stale':
            current=store.get(pid)
            with store.edit(pid,current['revision'],'synthetic later edit') as (changed,_):changed['name']+=' changed'
        elif case=='other_project':pid=store.create('isolated unrelated project')['id']
        elif case=='invalid_short':key='short'
        elif case=='invalid_long':key='x'*101
        before=database_rows(store);response=client.get(endpoint(pid,key))
        if case.startswith('invalid_'):
            assert response.status_code==400 and response.json()['code']=='INPUT_REQUIRED'
        else:
            assert response.status_code==200 and response.json()['confirmed'] is False
            assert response.json()['status']==('not_current' if case=='stale' else 'not_found')
            assert 'project' not in response.json() and 'receipt' not in response.json()
        assert database_rows(store)==before


def test_read_recovery_retains_pending_issues_and_no_replay(tmp_path):
    store=Store(tmp_path);p=setup(store);body=payload(p)
    with api(store) as client:
        assert post(client,p,body).status_code==200
        # Original server receipt payload controls the response; GET cannot manufacture decisions.
        with store.connect() as db:
            receipt=store.records(p['id'],RECEIPT_KIND,db)[0]
        before=database_rows(store);first=client.get(endpoint(p['id'],body['idempotency_key'])).json()
        second=client.get(endpoint(p['id'],body['idempotency_key'])).json()
        assert first==second and first['receipt']==receipt
        assert database_rows(store)==before
