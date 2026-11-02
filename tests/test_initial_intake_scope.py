"""A storage-cloud initial draft is not an implicit detailed-definition request."""
import copy
import json

import pytest

from app.business_draft import (PROTOCOL, ROW, adapt, initial_intake,
                               INITIAL_INTAKE_SOURCE_CHECK, INITIAL_INTAKE_SOURCE_CHECK_VERSION)
from app.provider import DEFAULT, assemble
from app.prd import cloud_prd_required
from tests.test_requirement_recommendation import project
from tests.test_business_draft import packet, draft_row, business_value


def cloud(tmp_path):
    _,p=project(tmp_path)
    p['name']='大型储能集成商云平台告警查询'
    p.setdefault('product_context',{})['product']='大型储能云平台'
    return p


def test_both_engines_share_actual_cloud_initial_contract(tmp_path):
    p=cloud(tmp_path)
    cfg=dict(DEFAULT,business_draft_protocol=PROTOCOL)
    direct,excerpts,_=assemble(p,'ingest','请读取资料整理初稿',cfg,tmp_path)
    harness,other,_=assemble(p,'ingest','请读取资料整理初稿',dict(cfg,intake_engine='deepseek-harness'),tmp_path)
    assert direct==harness and excerpts==other
    header=json.loads(direct[0]['content'].split('可信任务头：',1)[1])
    assert cloud_prd_required(p,'prd')
    assert header['initial_intake'] and not header['detail_required']
    assert header['initial_intake_source_check_version']==INITIAL_INTAKE_SOURCE_CHECK_VERSION
    assert direct[0]['content'].count(INITIAL_INTAKE_SOURCE_CHECK)==1
    assert 'detail_contract' not in header['schema']['properties']['requirements']['items']['properties']
    assert 'detail_contract' in ROW['properties']  # Global detailed contract untouched.
    raw=packet(draft_row())
    raw['summary_fields']={'intent':business_value('仍未知'),'users':business_value('管理员','fact')}
    raw['requirements'][0]['detail_contract']={'out_of_scope':'must remain raw'}
    canonical,report=adapt(raw,p,excerpts,'ingest',initial_intake_scope=header['initial_intake'])
    assert any(row['code']=='DETAIL_NOT_REQUESTED' for row in report['field_rejected'])
    assert report['accepted'][0]['raw']['detail_contract']==raw['requirements'][0]['detail_contract']
    assert all(not row.get('detail_required') and 'detail_contract' not in row for row in canonical['proposals'])


@pytest.mark.parametrize('intent',['详细定义','详细需求','交互细节','字段定义','状态机','详细设计','detail_contract'])
def test_explicit_detail_text_preserves_cloud_detail_path(tmp_path,intent):
    p=cloud(tmp_path)
    messages,_,_=assemble(p,'ingest','请整理'+intent,dict(DEFAULT,business_draft_protocol=PROTOCOL),tmp_path)
    header=json.loads(messages[0]['content'].split('可信任务头：',1)[1])
    assert not header['initial_intake'] and header['detail_required']
    assert 'initial_intake_source_check_version' not in header
    assert INITIAL_INTAKE_SOURCE_CHECK not in messages[0]['content']
    assert 'detail_contract' in header['schema']['properties']['requirements']['items']['required']


def test_typed_detail_scope_existing_detail_and_later_stages_are_unchanged(tmp_path):
    p=cloud(tmp_path)
    assert not initial_intake(p,'clarify')
    assert not initial_intake(p,'ingest',{'requirement_id':'REQ-0001','detail_required':True})
    assert not initial_intake(p,'ingest',draft_request={'mode':'required'})
    p['items'][0]['detail_required']=True
    assert not initial_intake(p,'ingest')
    p['items'][0].pop('detail_required')
    p['items'][0]['detail_contract']={'saved':'existing definition'}
    assert not initial_intake(p,'ingest')
    assert cloud_prd_required(p,'prd')
