"""One isolated live DeepSeek comparison using synthetic business facts only.

Run once with --execute. This does not touch the daily Store or assert product acceptance.
"""
import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.business_context import build_source, selection_update
from app.core import DATA, ROOT, digest, dumps, now
from app.document_reader import reader_document
from app.exports import document_files
from app.product_flow import checkpoint, status
from app.provider import DEFAULT, Provider, origin
from app.sources import save_source
from app.store import Store
from app.workflow import Workflow

OUTPUT = ROOT / 'evidence' / 'business-context-20260929' / 'live'
SENTINEL = 'SENTINEL_UNSELECTED_MODULE_BODY_7AC2'
REQUEST = ('合成工程场景：将电价模板应用到园区指定月份，并反馈结果。请根据资料解释业务对象、'
           '参与者、已知流程与影响，保留未知；不要把代码观察自动当成新业务规则。')
MATERIAL = ('工程合成资料，仅用于隔离测试。产品为云平台电价管理。值班人员可选择一个电价模板、'
            '多个园区和目标月份发起应用。模板记录分时电价；园区月份保存生效快照。'
            '提交成功后应显示每个园区的处理结果。覆盖既有月份快照时采用何种策略尚未确定，'
            '不要默认覆盖、跳过或合并。')


def saved_model_config():
    """Read a single setting via SQLite read-only mode; never instantiate Store(data/)."""
    path = DATA / 'requirements.sqlite3'
    if not path.is_file():
        raise RuntimeError('日常模型设置数据库不存在')
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT payload FROM settings WHERE id='model'").fetchone()
    if not row:
        raise RuntimeError('日常模型设置不存在')
    saved = json.loads(row[0])
    allowed = set(DEFAULT) - {'proxy'}
    config = dict(DEFAULT, **{k: v for k, v in saved.items() if k in allowed})
    config['proxy'] = ''
    if origin(config) != 'https://api.deepseek.com' or config['model'] != 'deepseek-flash':
        raise RuntimeError('当前日常设置不是 DeepSeek 官方 deepseek-flash，停止真实调用')
    return config


def frozen_bundle(folder):
    source_root = folder / 'synthetic-source'
    evidence_file = source_root / 'synthetic' / 'price_flow.txt'
    evidence_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_file.write_text(MATERIAL, encoding='utf-8')
    sha = hashlib.sha256(evidence_file.read_bytes()).hexdigest()
    bundle = dict(
        schema_version='1.0', bundle_id='synthetic-price-20260929',
        generated_at='2026-09-29T00:00:00Z', project=dict(id='synthetic-cloud',name='合成云平台'),
        source_snapshot=dict(repositories=[dict(id='synthetic-repo',revision='unknown',dirty=None)],
                             deployment='unknown'),
        overview='工程合成电价模板应用场景；非线上事实。',
        modules=[dict(id='price-template',name='电价模板',summary='选择模板与目标月份',
                      claim_ids=['c-template'],depends_on=['park-price']),
                 dict(id='park-price',name='园区电价',summary='按园区月份保存应用结果',
                      claim_ids=['c-snapshot'],depends_on=[]),
                 dict(id='sentinel',name='未选哨兵模块',summary='隔离验证',
                      claim_ids=['c-sentinel'],depends_on=[])],
        claims=[dict(id='c-template',module_ids=['price-template'],dimension='场景',
                     text='值班人员选择一个电价模板、多个园区和一个目标月份发起应用。',
                     origin='document_claim',evidence_ids=['e-price']),
                dict(id='c-snapshot',module_ids=['park-price'],dimension='数据与结果',
                     text='模板包含分时电价；园区月份保存应用后的生效快照，并向操作者反馈每个园区的结果。',
                     origin='document_claim',evidence_ids=['e-price']),
                dict(id='c-sentinel',module_ids=['sentinel'],dimension='隔离哨兵',
                     text=SENTINEL,origin='document_claim',evidence_ids=['e-price'])],
        evidence=[dict(id='e-price',repository_id='synthetic-repo',path='synthetic/price_flow.txt',
                       symbol='synthetic-scenario',line_start=1,line_end=1,sha256=sha,
                       kind='documentation')],
        unknowns=[dict(id='u-overwrite',question='已有园区月份快照的覆盖策略是什么？',
                       module_ids=['park-price'])],
        conflicts=[dict(id='x-cross',description='未选模块说法与已选模块不同，需在需要时核对。',
                        claim_ids=['c-snapshot','c-sentinel'])],
        coverage=dict(inspected_modules=['price-template','park-price'],
                      indexed_modules=['sentinel'],excluded=[],limitations=['仅工程合成资料；未实跑业务系统']),
        technical_summary='只记录模板选择与园区月份快照的业务关联；无生产技术结论。')
    return bundle


def make_project(store, folder, config, *, with_bundle=False):
    p = store.create('合成电价模板应用反馈')
    raw = MATERIAL.encode('utf-8')
    with store.edit(p['id'],p['revision'],'载入工程合成材料') as (draft, _):
        draft['sources'].append(save_source(store,'合成电价需求.txt',raw,'goal'))
        if with_bundle:
            payload=dumps(frozen_bundle(folder)).encode('utf-8')
            (folder/'business-context.json').write_bytes(payload)
            business=build_source(store,'business-context.json',payload)
            business['business_selection']=selection_update(
                business,['price-template'],['c-template','c-snapshot'])
            business['business_active']=True
            draft['sources'].append(business)
    p=store.get(p['id'])
    with store.edit(p['id'],p['revision'],'隔离合成材料授权',bump=False) as (draft,_):
        draft['grants'][origin(config)]={'source_ids':[s['id'] for s in draft['sources']],
                                         'created':now(),'actor':'synthetic-live-runner'}
    return store.get(p['id'])


def freeze_document_project(store, folder, config):
    p=make_project(store,folder,config,with_bundle=True)
    with store.edit(p['id'],p['revision'],'冻结工程合成文档底稿') as (draft,_):
        src=draft['sources'][0]
        ref=[dict(source_id=src['id'],excerpt_id=src['excerpts'][0]['id'])]
        draft['product_context']={
            'product':'合成云平台电价管理','module':'电价模板与园区电价','intent':'提供模板应用反馈',
            'current_state':'值班人员可选择模板、园区和目标月份；园区月份保存生效快照。',
            'users':'值班人员','value':'减少逐园区确认应用结果的不确定性；无量化收益资料。',
            'change_scope':'本期说明模板应用后的逐园区结果反馈。',
            'preserve_scope':'模板的分时电价内容和园区月份快照身份保持原义。',
            'out_of_scope':'已有月份快照覆盖策略尚未确定，本次不擅自决定。',
            'priority':'合成试点优先级；不代表实际业务排序。',
            'scope_ids':['REQ-LIVE-1']}
        behavior=dict(actor='值班人员',entry='在模板应用入口选择模板、多个园区和目标月份。',
            flow='确认输入后发起应用，并逐园区查看返回结果。',
            data='模板含分时电价；园区月份保存应用后的生效快照。',
            permissions='仅说明值班人员是操作者；其它角色权限未知，不推定。',
            result='显示每个园区的处理结果。',
            exceptions='若目标园区月份已有快照，覆盖策略待业务决定；不得默认覆盖或跳过。')
        base=dict(applies_to='to_be',epistemic_status='reported',source_refs=ref,
                  selection_status='selected',revision=draft['revision']+1,
                  scope_evidence=[dict(quote='提交成功后应显示每个园区的处理结果。')],
                  classification_reason='仅工程合成底稿，人工定义的测试预期。')
        draft['items']=[
            dict(base,id='REQ-LIVE-1',kind='requirement',title='逐园区结果反馈',
                 statement='值班人员发起模板应用后，页面展示每个目标园区的处理结果。',
                 related_refs=['RULE-LIVE-1','AC-LIVE-1'],change_type='modified',behavior=behavior),
            dict(base,id='RULE-LIVE-1',kind='rule',title='既有月份策略未决',
                 statement='对已有月份快照的覆盖策略未确定，文档不得默认覆盖、跳过或合并。',
                 related_refs=['REQ-LIVE-1'],change_type='new',behavior={}),
            dict(base,id='AC-LIVE-1',kind='acceptance',title='逐园区反馈验收',
                 statement='选择模板、多个园区和目标月份发起应用后，应能逐园区查看处理结果；已有月份快照的处理结果仍待策略决定。',
                 related_refs=['REQ-LIVE-1'],change_type='new',behavior={})]
        for step in (1,2):
            current=status(draft)[step-1]
            checkpoint(draft,step,current['content_hash'])
    p=store.get(p['id'])
    with store.edit(p['id'],p['revision'],'核对冻结合成规则',bump=False) as (draft,_):
        checkpoint(draft,3,status(draft)[2]['content_hash'])
    return store.get(p['id'])


async def run_stage(workflow,store,p,stage,message,kind='prd'):
    started=workflow.start(p['id'],p['revision'],stage,message,kind=kind)
    await workflow.tasks[started['id']]
    result=store.get_record(p['id'],started['id'],'run')
    print(f'{stage}/{kind}: {result["status"]}, calls={result["calls"]}, run={result["id"]}',flush=True)
    return result,store.get(p['id'])


async def main(execute, resume=False):
    OUTPUT.mkdir(parents=True,exist_ok=True)
    folder=OUTPUT/'one-run'
    if folder.exists() and not resume:
        raise RuntimeError(f'隔离运行目录已存在，禁止自动重放可能计费请求：{folder}')
    if resume and not (folder/'result.json').is_file():
        raise RuntimeError('缺少隔离运行记录，不能断点继续')
    config=saved_model_config()
    provider=Provider()
    if not provider.key(config):
        raise RuntimeError('DeepSeek Key 未配置，未发起调用')
    if not execute:
        print('预检完成；使用 --execute 执行一次真实模型隔离验收。')
        return
    if not resume:folder.mkdir()
    store=Store(folder/'data')
    store.setting('model',config)
    workflow=Workflow(store,provider)
    record=(json.loads((folder/'result.json').read_text('utf-8')) if resume else
            dict(scenario='合成工程试点：电价模板应用园区反馈',status='RUNNING',
                 generated_at=now(),config={k:config[k] for k in ('base_url','model','max_tokens','timeout')},
                 runs=[],limitations=['非真人业务验收；工程合成规则由测试作者冻结。']))
    record['status']='RUNNING'
    record.pop('error',None)
    done={r['label'] for r in record['runs'] if r['run'].get('result_applied') and
          r['run']['status'] in ('succeeded','partial','awaiting_user')}
    try:
        for label,has_bundle in (('baseline',False),('with_bundle',True)):
            if label in done:continue
            p=make_project(store,folder/label,config,with_bundle=has_bundle)
            result,p=await run_stage(workflow,store,p,'ingest',REQUEST)
            record['runs'].append(dict(label=label,project_id=p['id'],run=result))
            (folder/f'{label}-ingest.json').write_text(dumps(p['messages'][-1]['response'] if p['messages'] else {}),'utf-8')
            if not result.get('result_applied') or result['status'] not in ('succeeded','partial','awaiting_user'):
                raise RuntimeError(f'{label} ingest 未成功：{result.get("error")}; run={result["id"]}')
        prior_document=next((r for r in record['runs'] if r['label']=='mrd' and r['label'] in done),None)
        p=(store.get(prior_document['project_id']) if prior_document else
           freeze_document_project(store,folder/'documents',config))
        for kind in ('mrd','prd'):
            if kind in done:continue
            result,p=await run_stage(workflow,store,p,'prd',
                '请依据冻结的合成底稿编写'+kind.upper()+'评审草稿；仅使用已核对事实与已选规范。',kind)
            record['runs'].append(dict(label=kind,project_id=p['id'],run=result))
            if not result.get('result_applied') or result['status'] not in ('succeeded','partial','awaiting_user'):
                raise RuntimeError(f'{kind} 未成功：{result.get("error")}; run={result["id"]}')
            files=await document_files(p,kind)
            md=next(data for name,data in files.items() if name.endswith('.md'))
            (folder/f'{kind.upper()}-reader.md').write_bytes(md)
        if 'semantic_review' not in done:
            result,p=await run_stage(workflow,store,p,'review',
                '请独立审查合成 MRD/PRD 实际可读正文、来源、未知与已选规范；报告真实问题。')
            record['runs'].append(dict(label='semantic_review',project_id=p['id'],run=result))
            if not result.get('result_applied') or result['status'] not in ('succeeded','partial','awaiting_user'):
                raise RuntimeError(f'review 未成功：{result.get("error")}; run={result["id"]}')
            record['review_result']=p['review']['response']['result'] if p.get('review') else None
        record['status']='EXECUTED_NEEDS_HUMAN_REVIEW'
    except Exception as error:
        record['status']='INTERRUPTED'
        record['error']=str(error)
        print(f'中止：{error}',flush=True)
        raise
    finally:
        record['actual_calls']=sum(r['run']['calls'] for r in record['runs'])
        record['model_call_evidence_dir']=str(folder/'data'/'evidence'/'model-calls')
        (folder/'result.json').write_text(dumps(record),'utf-8')
        print(folder/'result.json',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    asyncio.run(main(args.execute,args.resume))
