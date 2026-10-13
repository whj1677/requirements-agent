"""Explicit real-provider smoke with synthetic briefs; never edits daily projects."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.business_draft import assemble, adapt, PROTOCOL
from app.provider import Provider, DEFAULT, input_metrics, origin
from app.model_evidence import ModelCallEvidence
from app.store import Store
from app.core import digest, now
from app.requirement_recommendation import scope
from app.requirement_details import detail_issues
from app.workflow import apply_response

CASES = {
 'simple': ('站点联系人维护', '''合成测试材料，不代表真实平台规则。储能云平台新增站点联系人维护。
本站管理员从站点详情进入联系人编辑窗口；有本站查看权限的人可读姓名，只有本站管理员可改。
只有联系人姓名一个可编辑字段：文本，必填，无默认值，去首尾空格后1至50个字，不接受全空白。
保存前可取消关闭并放弃编辑；保存中显示忙碌并禁重复点击，同次提交只修改一次。
校验失败时原数据不变，保留输入并显示原因；成功更新姓名并提示成功。
如果网络中断导致提交结果未知，需先查询原提交是否完成；未确认前不得当成保存失败另发提交。
无独立业务状态机。本期不增加电话、邮箱或短信。未明确的规则请保留待确认，不自行定数值。'''),
 'complex': ('多站点告警批量派单', '''合成测试材料，不代表真实平台规则。储能云平台新增多站点告警批量派单。
调度员在告警列表选择告警，预览按站点分组的待派单记录，逐站选择处理人并填写处理期限后提交。
只有拥有相应站点派单权限的调度员可操作；处理人必须有本站维修资格。服务端重新核验权限和告警最新状态。
同站全部告警必须原子建单，任何一条失效则该站整组失败；各站独立，可部分成功。
告警ID与站点来自选中记录只读；处理人由调度员从本站有资格人员中选择，必填无默认值。
处理期限为必填日期时间、无默认值，Asia/Shanghai，须晚于服务端提交时刻。
提交前关闭可放弃；提交已接受后关闭页面不撤销。提交中禁重复点击，同一提交标识只创建一次工单。
超时为结果未知，需查原请求；只有确认原请求终止且未建单，才能重试失败站点。成功站点不得重复建单。
批次结果为全部成功、部分成功、全部失败或仍在处理中；前3个仅在全部站点已得确定结果时展示。
成功的每站一张工单关联该站全部选中告警，并通知对应处理人；通知失败不回滚建单，可重试通知。
本期展开提交、查询、失败站点重试。后续维修验收不在本期，禁止擅自补维修状态机。
单次最大告警数、查询间隔及通知重试次数尚未确定，必须让产品经理确认，不能假设为100条、5秒或3次。
验收至少覆盖越权、同站原子失败、部分成功、未知结果、重复提交、只重试失败站点和通知失败不回滚。''')}

def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

async def run(args):
    output=args.output.resolve()
    assert output.is_relative_to(ROOT/'work') and not output.exists(), 'Use a fresh work directory'
    output.mkdir(parents=True)
    db_path=ROOT/'data/requirements.sqlite3'
    with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT payload FROM settings WHERE id='model'").fetchone()
    cfg={**DEFAULT,**(json.loads(row[0]) if row else {})}
    cfg.update(business_draft_protocol=PROTOCOL,max_tokens=24000,max_calls=None,timeout=180)
    provider=Provider(ROOT/'.env',ROOT/'data/.credentials')
    assert provider.key_status(cfg)['key_configured'], 'No configured key; no requests sent'
    results=[]
    for key in args.cases:
        title,brief=CASES[key]
        folder=output/key;folder.mkdir()
        with tempfile.TemporaryDirectory(prefix='detail-contract-real-') as scratch:
            p=Store(Path(scratch)).create('合成验证 '+title)
            ref=dict(source_id='SRC-synthetic',excerpt_id='EX-synthetic')
            p['workflow_mode']='shared-draft'
            p['product_context']=dict(product='储能云平台',scope_ids=['REQ-synthetic'])
            p['sources']=[dict(id=ref['source_id'],title='合成业务说明',purpose='goal',version=1,
                sha256=digest(brief),created=now(),excluded=False,parse_status='read',
                excerpts=[dict(id=ref['excerpt_id'],source_id=ref['source_id'],source_hash=digest(brief),
                    locator='完整合成说明',text=brief,method='synthetic')])]
            p['items']=[dict(id='REQ-synthetic',kind='requirement',title=title,statement=title,
                applies_to='to_be',epistemic_status='reported',source_refs=[ref],related_refs=[],
                selection_status='selected',revision=1)]
            local=scope(p,dict(kind='item',id='REQ-synthetic'))
            messages,excerpts,_=assemble(p,'clarify','按完整合成说明完善本行，展开字段、交互和流程；未知保持待确认。',cfg,folder,local)
            write(folder/'input-project.json',p)
            ev=ModelCallEvidence(folder,key,'detail-prd-validation','clarify',cfg['model'],origin(cfg),None,provider.key(cfg))
            ev.request_context(cfg,messages,'用户批准的方案验证；仅合成材料；本场景一次请求',input_metrics(messages))
            result=dict(case=key,status='running',real_model_calls=1,model=cfg['model'],cost='unknown',
                user_business_acceptance='NOT_RUN',daily_data_mutation=False)
            print(json.dumps(dict(case=key,status='request_started'),ensure_ascii=False),flush=True)
            try:
                raw,meta=await provider.request(cfg,messages,ev)
                canonical,report=adapt(raw,p,excerpts,'clarify',local)
                write(folder/'canonical.json',canonical);write(folder/'report.json',report)
                trial=copy.deepcopy(p);apply_response(trial,canonical,identity_only=True)
                candidates=[i for i in trial['items'] if i.get('detail_contract')]
                result.update(status='candidate_ready' if candidates and not report['rejected'] else 'rejected',
                    candidate_count=len(candidates),adapter_complete=report['complete'],rejected=report['rejected'],
                    details=[dict(id=i['id'],fields=len(i['detail_contract']['fields']),
                        interactions=len(i['detail_contract']['interactions']),nodes=len(i['detail_contract']['flow']['nodes']),
                        edges=len(i['detail_contract']['flow']['edges']),issues=detail_issues(i,trial),
                        selection_status=i['selection_status'],confirmed=bool(i.get('requirement_confirmation')))
                        for i in candidates],usage=meta.get('usage'))
                write(folder/'candidate-project.json',trial)
                ev.finish(result['status'])
            except Exception as error:
                result.update(status='failed',error_type=type(error).__name__,error=str(error))
                ev.finish('failed')
            results.append(result);write(output/'summary.json',results)
            print(json.dumps({k:result[k] for k in ('case','status','candidate_count','adapter_complete','error_type','error') if k in result},ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--real-model',action='store_true',required=True)
    parser.add_argument('--cases',nargs='+',choices=tuple(CASES),default=list(CASES))
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
