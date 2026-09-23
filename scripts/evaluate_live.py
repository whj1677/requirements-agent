"""Frozen live intake/vision evaluation. Human review remains a separate gate.

This runner deliberately never sends the oracle fields to the model. Later
interactive stages are recorded as pending instead of making business decisions.
"""
import argparse
import asyncio
import html
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.core import ROOT,KIT,dumps,digest,ident,read_json,now
from app.store import Store
from app.sources import save_source
from app.provider import Provider,DEFAULT,origin
from app.workflow import Workflow


async def main(args):
    frozen=read_json(KIT/'examples/evaluation_cases.json')
    output=ROOT/'evidence/live'/ident('evaluation')
    output.mkdir(parents=True)
    records=[]
    selected=[c for c in frozen['cases'] if not args.case or c['case_id']==args.case]
    if not selected:raise SystemExit('Unknown case')
    store=Store(output/'data')
    config=dict(DEFAULT,max_calls=min(args.max_calls or 1,8))
    provider=Provider()
    execute=args.execute and args.max_calls>0 and bool(provider.key(config))
    remaining=args.max_calls
    for case in selected:
        for repeat in range(1,args.repetitions+1):
            record=dict(case_id=case['case_id'],repeat=repeat,fixture_hash=digest(case),prompt_version='1.1',planned_stages=case['expected_modes'],status='NOT_RUN',semantic_review='NOT_RUN',calls=0,reason='未授权执行或缺少项目专用 Key / 调用预算',created=now())
            if execute and remaining>0:
                if case['case_id']=='EV-06':
                    record.update(status='BLOCKED_PRECONDITION',reason='请先通过测试 UI 建立该场景冻结合成基线；本入口不冒充真人内容批准')
                    records.append(record)
                    continue
                p=store.create(case['case_id']+' / '+str(repeat))
                with store.edit(p['id'],0,'载入冻结评测材料') as (p,db):
                    for material in case.get('materials',[]):
                        if material['kind']=='text':p['sources'].append(save_source(store,material['id']+'.txt',material['text'].encode(),'goal'))
                        elif material['kind']=='docx_reference':p['sources'].append(save_source(store,Path(material['path']).name,(KIT/material['path']).read_bytes(),'template'))
                        elif material['kind']=='url_fixture':p['sources'].append(dict(id=ident('SRC'),title='冻结 404 来源',purpose='reference',uri='https://fixture.invalid/404',sha256=None,created=now(),version=1,excluded=False,excerpts=[],parse_status='failed',failure_reason='HTTP 404（冻结传输故障注入）',image_mime=None))
                    if case.get('fixture_generation'):
                        g=case['fixture_generation'];text=g['critical_near_start']+'\n'+'\n'.join(f'第 {i+1} 段背景补充：本段没有新增业务规则。' for i in range(g['neutral_paragraphs']))+'\n'+g['critical_near_end']
                        p['sources'].append(save_source(store,'长材料.txt',text.encode(),'current'))
                if case.get('fixture_render'):
                    from playwright.async_api import async_playwright
                    fixture=case['fixture_render']
                    async with async_playwright() as pw:
                        browser=await pw.chromium.launch(headless=True);page=await browser.new_page(viewport={'width':1200,'height':800})
                        labels=fixture['visible_text']
                        markup='<html lang="zh"><meta charset="utf-8"><style>body{font:18px Microsoft YaHei;padding:40px}button{padding:12px;margin:8px}table{width:100%;border:1px solid #ccc;padding:30px}</style><h1>'+html.escape(labels[0])+'</h1><nav>'+''.join('<button>'+html.escape(x)+'</button>' for x in labels[1:-1])+'</nav><table><tr><td>'+html.escape(labels[-1])+'</td></tr></table></html>'
                        await page.set_content(markup);data=await page.screenshot();await browser.close()
                    with store.edit(p['id'],p['revision'],'载入实际像素截图') as (p,db):p['sources'].append(save_source(store,ident('image')+'.png',data,'reference'))
                p=store.get(p['id'])
                with store.edit(p['id'],p['revision'],'CLI 显式授权冻结合成资料',bump=False) as (p,db):p['grants'][origin(config)]={'source_ids':[s['id'] for s in p['sources']], 'created':now()}
                config['max_calls']=min(remaining,8);store.setting('model',config);store.setting('vision',config)
                workflow=Workflow(store,provider)
                stage='vision' if case.get('fixture_render') else 'ingest'
                run=workflow.start(p['id'],p['revision'],stage,'请理解本轮实际提供的材料，保留未知，提出关键决定。')
                await workflow.tasks[run['id']]
                result=store.get_record(p['id'],run['id'],'run')
                remaining-=result['calls']
                record.update(status='INTAKE_EXECUTED' if result['status'] in ('succeeded','partial','awaiting_user') else result['status'],calls=result['calls'],run_id=run['id'],project_id=p['id'],reason='仅完成首阶段；后续需 UI 业务决定和独立语义审查',remaining_stages=[s for s in case['expected_modes'] if s!=stage],result=result)
            records.append(record)
            (output/'results.json').write_text(dumps(dict(mode='LIVE' if execute else 'NOT_RUN',planned=len(selected)*args.repetitions,executed=sum(r['calls']>0 for r in records),passed=0,failed=sum(r['status']=='failed' for r in records),blocked=sum(r['status']=='BLOCKED_PRECONDITION' for r in records),not_run=sum(r['status']=='NOT_RUN' for r in records),records=records)),encoding='utf8')
    print(output/'results.json')
    if args.execute and not execute:print('真实调用未执行：请配置 RA_DEEPSEEK_API_KEY 并指定大于零的 --max-calls。')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--max-calls',type=int,default=0)
    parser.add_argument('--case',choices=['EV-'+str(n).zfill(2) for n in range(1,10)])
    parser.add_argument('--repetitions',type=int,default=3,choices=[1,2,3])
    asyncio.run(main(parser.parse_args()))
