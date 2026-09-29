"""Compare dd43d52 and current MRD/PRD with one frozen synthetic brief.

All model calls use an isolated Store and synthetic material. Run with --execute.
Successful or uncertain prior calls are never sent again by this runner.
"""
import argparse
import asyncio
import copy
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / 'evidence' / 'engineering-context-20260929' / 'live-v4'
BASELINE = 'dd43d5241a48fb2690c398a2c8704c7933c75723'
MATERIAL = '''合成测试材料；以下不是实际云平台源码或线上事实。
已有实现：Vue 页面经网关调用 Java 电价服务，再访问数据库。操作者选择一个电价模板、多个园区和一个目标月份；服务先校验全部目标园区的权限，然后整批原子提交。任一个园区权限失败则不写入；事务写入失败则全部回滚。当前接口只返回整体 code/message，无法知晓具体园区结果。
本期问题：值班人员无法从现有整体响应识别各园区是否处理，需逐园区反馈；无量化价值资料，不承诺市场或收益数字。
本期需求 REQ-SYN-1：正常成功时，页面对每个目标园区显示园区名、目标月份和“成功”；模板应用选择与提交沿用现有入口。
本期需求 REQ-SYN-2：权限预检失败时没有写入，失败园区显示“无权限”，其余目标园区显示“未执行”，整体明确失败。写入异常时整批回滚，所有目标园区显示失败并说明“未保存”。
本期需求 REQ-SYN-3：提交期间禁止重复点击。网络异常时保留已选模板、园区和月份，提供恢复按钮，明确结果尚不能确认；系统不自动重试，是否人工重试由操作者决定。
本期规则 RULE-SYN-1：原有整批权限预检与原子事务继续适用；权限或写入失败不得呈现部分保存成功。
本期规则 RULE-SYN-2：本期只补逐园区反馈，保持已有月份快照覆盖策略和电价计算规则不变；材料未说明其具体实现，不推定覆盖、跳过或计算细节。
本期规则 RULE-SYN-3：网络异常不能被写成已成功或已失败；恢复按钮仅恢复可操作状态，不自动重发请求。
验收 AC-SYN-1：选择模板、多个园区和月份后成功提交，逐个园区能看到园区名、目标月份和成功状态。
验收 AC-SYN-2：任一园区权限预检失败，验证没有写入；失败园区显示无权限，其余显示未执行，整体显示失败。模拟事务写入异常，验证整批回滚且每个园区显示失败、未保存。
验收 AC-SYN-3：提交期间重复点击无第二次提交；网络异常后仍保留选择并显示结果未确认，恢复按钮不自动重试，人工决定是否再次提交。
'''
ARCHITECTURE = ('合成源码观察：Vue 页面经网关调用 Java 电价服务，服务访问数据库；'
                '提交前对全部目标园区执行权限校验，然后以单事务整批写入。权限预检失败不写入，'
                '事务异常全部回滚。当前接口仅有整体 code/message，没有逐园区结果。')


def branch_path(label):
    return OUTPUT.parent / ('live-v4-'+label) if label.startswith('candidate-') else OUTPUT / label


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding='utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def config_read_only():
    from app.provider import DEFAULT, Provider, origin
    path = ROOT / 'data' / 'requirements.sqlite3'
    if not path.is_file():
        raise RuntimeError('日常模型设置不存在；未发起调用')
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        row = db.execute("SELECT payload FROM settings WHERE id='model'").fetchone()
    if not row:
        raise RuntimeError('日常模型设置不存在；未发起调用')
    saved = json.loads(row[0])
    config = dict(DEFAULT, **{k:v for k,v in saved.items() if k in DEFAULT and k != 'proxy'})
    config['proxy'] = ''
    if origin(config) != 'https://api.deepseek.com' or config['model'] != 'deepseek-flash':
        raise RuntimeError('接收端不是 DeepSeek 官方 deepseek-flash；未发起调用')
    if not Provider(env_path=ROOT/'.env').key(config):
        raise RuntimeError('DeepSeek Key 不可用；未发起调用')
    return config


def freeze(store, config):
    from app.business_context import build_source, selection_update
    from app.core import now
    from app.product_flow import checkpoint, status
    from app.provider import origin
    from app.sources import save_source
    project = store.create('合成云平台电价模板应用反馈')
    with store.edit(project['id'],project['revision'],'冻结隔离合成底稿') as (draft,_):
        source=save_source(store,'合成电价反馈资料.txt',MATERIAL.encode('utf-8'),'goal')
        if source['parse_status']!='read' or not source['excerpts']:
            raise RuntimeError('合成材料无法读取，停止')
        draft['sources'].append(source)
        fact_file=store.folder.parent/'synthetic-source'/'synthetic'/'price-application.txt'
        fact_file.parent.mkdir(parents=True,exist_ok=True)
        fact_file.write_text(ARCHITECTURE+'\n',encoding='utf-8')
        fact_sha=hashlib.sha256(fact_file.read_bytes()).hexdigest()
        bundle=dict(schema_version='1.0',bundle_id='synthetic-price-v4',
            generated_at='2026-09-29T00:00:00Z',
            project=dict(id='synthetic-price',name='合成电价反馈'),
            source_snapshot=dict(repositories=[dict(id='synthetic-repo',revision='synthetic-frozen-1',dirty=False)],deployment='synthetic'),
            overview='合成电价反馈场景',
            modules=[dict(id='price-flow',name='模板应用链路',summary='合成架构和提交观察',
                          claim_ids=['c-flow'],depends_on=[])],
            claims=[dict(id='c-flow',module_ids=['price-flow'],dimension='现状链路与结果',
                         text=ARCHITECTURE,origin='code_observation',evidence_ids=['e-flow'])],
            evidence=[dict(id='e-flow',repository_id='synthetic-repo',
                           path='synthetic/price-application.txt',symbol='synthetic-price-flow',
                           line_start=1,line_end=1,sha256=fact_sha,kind='implementation')],
            unknowns=[],conflicts=[],
            coverage=dict(inspected_modules=['price-flow'],indexed_modules=[],excluded=[],
                          limitations=['仅合成材料；无实际产品运行证据']),
            technical_summary='合成架构观察，不代表新需求批准')
        payload=json.dumps(bundle,ensure_ascii=False).encode('utf-8')
        (store.folder.parent/'business-context.json').write_bytes(payload)
        business=build_source(store,'business-context.json',payload)
        business['business_selection']=selection_update(business,['price-flow'],[])
        business['business_active']=True
        draft['sources'].append(business)
        draft['product_context']=dict(product='合成云平台电价管理',module='电价模板应用与园区月份结果',
            intent='补充模板应用后的逐园区反馈',
            current_state='值班人员选择模板、多个园区和月份；现有响应只有整体 code/message，无法区分园区结果。',
            users='值班人员',value='降低逐园区结果不明造成的核对不确定性；暂无量化收益资料。',
            change_scope='本期补正常、权限预检失败、事务写入失败和网络异常的逐园区结果反馈。',
            preserve_scope='保留现有整批权限预检、原子事务、月份快照覆盖策略和电价计算规则。',
            out_of_scope='不改变覆盖策略和计算规则；材料未给出其具体实现，不推定细节。',
            priority='合成场景用于文档质量对比，不代表真实业务排序。',
            scope_ids=['REQ-SYN-1','REQ-SYN-2','REQ-SYN-3'])
        def item(item_id,kind,title,statement,related,behavior=None,change_type='new'):
            matching=[excerpt for excerpt in source['excerpts'] if statement in excerpt['text']]
            if len(matching)!=1:
                raise RuntimeError(item_id+' 合成来源定位不唯一')
            ref=[dict(source_id=source['id'],excerpt_id=matching[0]['id'])]
            return dict(id=item_id,kind=kind,title=title,statement=statement,
                applies_to='to_be',epistemic_status='reported',source_refs=copy.deepcopy(ref),
                selection_status='selected',revision=draft['revision']+1,
                scope_evidence=[dict(quote=statement)],classification_reason='合成测试底稿明确陈述',
                related_refs=related,change_type=change_type,behavior=behavior or {})
        draft['items']=[
            item('REQ-SYN-1','requirement','成功反馈',
                 '正常成功时，页面对每个目标园区显示园区名、目标月份和“成功”；模板应用选择与提交沿用现有入口。',
                 ['AC-SYN-1','RULE-SYN-1'],dict(actor='值班人员',entry='选择一个电价模板、多个园区和一个目标月份。',
                 flow='确认选择后提交；提交期间禁止重复点击；服务先完成全部园区权限校验再整批写入。',
                 data='模板、目标园区、目标月份；正常结果含园区名、目标月份和成功状态。',
                 permissions='提交前校验全部目标园区权限。',result='成功后逐个目标园区显示成功。',
                 exceptions='权限或事务失败按 REQ-SYN-2；网络异常按 REQ-SYN-3。'),change_type='modified'),
            item('REQ-SYN-2','requirement','权限和写入失败反馈',
                 '权限预检失败时没有写入，失败园区显示“无权限”，其余目标园区显示“未执行”，整体明确失败。写入异常时整批回滚，所有目标园区显示失败并说明“未保存”。',
                 ['AC-SYN-2','RULE-SYN-1'],dict(actor='值班人员',entry='提交已选择的模板、园区和月份。',
                 flow='服务先预检全部园区权限；权限通过后才开启整批写入。',
                 data='目标园区与月份、园区结果状态和整体失败状态。',
                 permissions='任一目标园区无权限即停止整批写入。',
                 result='权限失败园区显示无权限，其余显示未执行；事务异常时全部显示失败和未保存。',
                 exceptions='权限失败不写入；事务写入异常整批回滚，不呈现部分成功。')),
            item('REQ-SYN-3','requirement','提交及网络异常',
                 '提交期间禁止重复点击。网络异常时保留已选模板、园区和月份，提供恢复按钮，明确结果尚不能确认；系统不自动重试，是否人工重试由操作者决定。',
                 ['AC-SYN-3','RULE-SYN-3'],dict(actor='值班人员',entry='发起模板应用提交。',
                 flow='请求提交期间禁重复点击；网络异常后保留选择，点击恢复按钮恢复可操作状态。',
                 data='保留已选模板、园区和目标月份；结果状态为尚不能确认。',
                 permissions='沿用提交前的全部园区权限预检；网络异常不改变授权结论。',
                 result='明确显示结果尚不能确认，恢复后由操作者决定是否再次提交。',
                 exceptions='不自动重试，不把网络异常写成成功或失败。')),
            item('RULE-SYN-1','rule','整批提交约束',
                 '原有整批权限预检与原子事务继续适用；权限或写入失败不得呈现部分保存成功。',
                 ['REQ-SYN-1','REQ-SYN-2']),
            item('RULE-SYN-2','rule','保持既有策略',
                 '本期只补逐园区反馈，保持已有月份快照覆盖策略和电价计算规则不变；材料未说明其具体实现，不推定覆盖、跳过或计算细节。',
                 ['REQ-SYN-1','REQ-SYN-2','REQ-SYN-3']),
            item('RULE-SYN-3','rule','网络不确定性',
                 '网络异常不能被写成已成功或已失败；恢复按钮仅恢复可操作状态，不自动重发请求。',
                 ['REQ-SYN-3']),
            item('AC-SYN-1','acceptance','成功结果验收',
                 '选择模板、多个园区和月份后成功提交，逐个园区能看到园区名、目标月份和成功状态。',
                 ['REQ-SYN-1']),
            item('AC-SYN-2','acceptance','整批失败验收',
                 '任一园区权限预检失败，验证没有写入；失败园区显示无权限，其余显示未执行，整体显示失败。模拟事务写入异常，验证整批回滚且每个园区显示失败、未保存。',
                 ['REQ-SYN-2']),
            item('AC-SYN-3','acceptance','网络和重复提交验收',
                 '提交期间重复点击无第二次提交；网络异常后仍保留选择并显示结果未确认，恢复按钮不自动重试，人工决定是否再次提交。',
                 ['REQ-SYN-3'])]
        for step in (1,2):
            checkpoint(draft,step,status(draft)[step-1]['content_hash'])
    project=store.get(project['id'])
    with store.edit(project['id'],project['revision'],'核对合成规范',bump=False) as (draft,_):
        checkpoint(draft,3,status(draft)[2]['content_hash'])
        draft['grants'][origin(config)]=dict(source_ids=[s['id'] for s in draft['sources']],
                                              created=now(),actor='synthetic-live-runner')
    return store.get(project['id'])


def archive_baseline(folder):
    target=folder/'baseline-source'
    if (target/'app'/'provider.py').is_file():
        return target
    archive=folder/'baseline-dd43d52.zip'
    subprocess.run(['git','archive','--format=zip','-o',str(archive),BASELINE],
                   cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    target.mkdir()
    with zipfile.ZipFile(archive) as z:
        z.extractall(target)
    return target


def correct_candidate_refs(branch, pid):
    """Repair only the copied synthetic fixture, leaving first-round data intact."""
    from app.product_flow import checkpoint, status
    from app.store import Store
    store=Store(branch/'data')
    mapping_path=branch/'source-ref-correction.json'
    if mapping_path.is_file():
        project=store.get(pid)
        if not status(project)[2]['complete']:
            with store.edit(pid,project['revision'],'复核修正后的合成条款版本',bump=False) as (draft,_):
                checkpoint(draft,3,status(draft)[2]['content_hash'])
        return
    if store.records(pid,'run'):
        raise RuntimeError('候选已有模型调用，不能在原地修改来源')
    project=store.get(pid)
    source=next(s for s in project['sources'] if s['title']=='合成电价反馈资料.txt')
    rows=[]
    with store.edit(pid,project['revision'],'修正隔离测试来源定位',bump=False) as (draft,_):
        for item in draft['items']:
            matching=[excerpt for excerpt in source['excerpts'] if item['statement'] in excerpt['text']]
            if len(matching)!=1:
                raise RuntimeError(item['id']+' 在合成资料中没有唯一原句')
            old=copy.deepcopy(item['source_refs'])
            item['source_refs']=[dict(source_id=source['id'],excerpt_id=matching[0]['id'])]
            rows.append(dict(item_id=item['id'],before=old,after=item['source_refs'],
                             locator=matching[0]['locator'],statement_sha256=hashlib.sha256(
                                 item['statement'].encode('utf-8')).hexdigest()))
        checkpoint(draft,3,status(draft)[2]['content_hash'])
    corrected=store.get(pid)
    if not status(corrected)[2]['complete']:
        with store.edit(pid,corrected['revision'],'复核修正后的合成条款版本',bump=False) as (draft,_):
            checkpoint(draft,3,status(draft)[2]['content_hash'])
        corrected=store.get(pid)
    write_json(mapping_path,dict(note='业务文字、product_context、behavior 不变；仅修正合成条款的 source_refs。'
        '因来源字段属于内容版本，Store 为条款记新版本；相对初轮并非字节相同输入。',
        project_id=pid,source_id=source['id'],rows=rows,
        versions={item['id']:item.get('content_version') for item in corrected['items']}))


def prepare(config, candidates=()):
    from app.core import digest
    from app.store import Store
    OUTPUT.mkdir(parents=True,exist_ok=True)
    archive_baseline(OUTPUT)
    frozen=OUTPUT/'frozen'
    if not (frozen/'data'/'requirements.sqlite3').is_file():
        frozen.mkdir(exist_ok=True)
        store=Store(frozen/'data')
        project=freeze(store,config)
        write_json(frozen/'project.json',project)
        write_json(frozen/'identity.json',dict(project_id=project['id'],brief_hash=digest(project),
            source_sha256={s['id']:s['sha256'] for s in project['sources']},
            baseline_commit=BASELINE,material_sha256=hashlib.sha256(MATERIAL.encode()).hexdigest()))
    for label in ('baseline','current',*candidates):
        branch=branch_path(label)
        if not (branch/'data'/'requirements.sqlite3').exists():
            branch.mkdir(exist_ok=True)
            shutil.copytree(frozen/'data',branch/'data')
        if label.startswith('candidate-'):
            correct_candidate_refs(branch,read_json(frozen/'identity.json')['project_id'])
    return read_json(frozen/'identity.json')


async def worker(label):
    code_root=OUTPUT/'baseline-source' if label=='baseline' else ROOT
    sys.path.insert(0,str(code_root))
    from app.core import digest
    from app.document_reader import reader_document
    from app.exports import document_files
    from app.provider import Provider
    from app.store import Store
    from app.workflow import Workflow
    branch=branch_path(label)
    manifest_path=branch/'result.json'
    manifest=read_json(manifest_path) if manifest_path.is_file() else dict(
        label=label,code_version=BASELINE if label=='baseline' else 'working-tree-v4',
        status='RUNNING',runs=[])
    store=Store(branch/'data')
    identity=read_json(OUTPUT/'frozen'/'identity.json')
    pid=identity['project_id']
    initial=store.get(pid)
    if not manifest['runs'] and not label.startswith('candidate-') and digest(initial)!=identity['brief_hash']:
        raise RuntimeError(label+' 冻结底稿哈希与本次输入不一致')
    config=config_read_only()
    provider=Provider(env_path=ROOT/'.env')
    workflow=Workflow(store,provider)
    async def one(stage,kind=None):
        existing=[r for r in store.records(pid,'run') if r['stage']==stage and
                  (stage!='prd' or r['document_type']==kind)]
        if existing:
            run=existing[-1]
            if run.get('result_applied') and run['status'] in ('succeeded','partial','awaiting_user'):
                return run
            raise RuntimeError(f'{label}/{stage}/{kind}: 已有 {run["status"]} 请求，可能计费；禁止自动重发')
        project=store.get(pid)
        message=('请根据同一冻结合成底稿生成'+kind.upper()+'评审草稿；区分已有链路与新增反馈，'
                 '明确成功、权限失败、写入回滚和网络不确定性。' if stage=='prd' else
                 '独立审查当前合成 MRD/PRD 的实际可读内容，指出重复、缺口、无依据表述与规范保真问题。')
        started=workflow.start(pid,project['revision'],stage,message,kind=kind or 'prd')
        await workflow.tasks[started['id']]
        run=store.get_record(pid,started['id'],'run')
        print(f'{label}/{stage}/{kind or "-"}: {run["status"]}, calls={run["calls"]}',flush=True)
        return run
    try:
        for kind in ('mrd','prd'):
            run=await one('prd',kind)
            manifest['runs']=[r for r in manifest['runs'] if r['label']!=kind]
            manifest['runs'].append(dict(label=kind,run=run))
            write_json(manifest_path,manifest)
            if not run.get('result_applied') or run['status'] not in ('succeeded','partial','awaiting_user'):
                raise RuntimeError(f'{label}/{kind} 生成未保存：{run.get("error")}')
            project=store.get(pid)
            files=await document_files(project,kind)
            md=next(data for name,data in files.items() if name.endswith('.md'))
            (branch/(kind.upper()+'-reader.md')).write_bytes(md)
            write_json(branch/(kind.upper()+'-artifact.json'),project['documents'][kind])
        if label=='current':
            run=await one('review')
            manifest['runs']=[r for r in manifest['runs'] if r['label']!='review']
            manifest['runs'].append(dict(label='review',run=run))
            write_json(branch/'review.json',store.get(pid).get('review'))
            if not run.get('result_applied') or run['status'] not in ('succeeded','partial','awaiting_user'):
                raise RuntimeError(f'{label}/review 未形成可用审查：{run.get("error")}')
        manifest['status']='EXECUTED_NEEDS_HUMAN_REVIEW'
    except Exception as error:
        manifest['status']='INTERRUPTED'
        manifest['error']=str(error)
        raise
    finally:
        manifest['actual_calls']=sum(r['run'].get('calls',0) for r in manifest['runs'])
        manifest['model_call_evidence_dir']=str(branch/'data'/'evidence'/'model-calls')
        write_json(manifest_path,manifest)


def report():
    rows=[]
    labels=['baseline','current']+sorted(path.name.removeprefix('live-v4-')
        for path in OUTPUT.parent.glob('live-v4-candidate-*') if path.is_dir())
    for label in labels:
        path=branch_path(label)/'result.json'
        if not path.is_file():
            continue
        result=read_json(path)
        for entry in result['runs']:
            run=entry['run']
            tokens=[]
            for attempt in run.get('attempts',[]):
                usage=attempt.get('usage') or {}
                tokens.append(dict(call_id=attempt.get('call_id'),status=attempt.get('validation_result'),
                                   prompt_tokens=usage.get('prompt_tokens'),
                                   completion_tokens=usage.get('completion_tokens'),
                                   finish_reason=attempt.get('finish_reason')))
            rows.append(dict(version=label,kind=entry['label'],status=run['status'],
                             calls=run['calls'],model=(run.get('provider') or {}).get('model'),
                             tokens=tokens,result_applied=run.get('result_applied',False),
                             run_id=run['id'],error=run.get('error'),message=run.get('message'),
                             attempts=run.get('attempts',[]),response=run.get('response')))
    write_json(OUTPUT/'comparison-result.json',dict(baseline_commit=BASELINE,
        frozen_identity=read_json(OUTPUT/'frozen'/'identity.json'),runs=rows,
        status='INCOMPLETE' if any(not r['result_applied'] for r in rows) else 'EXECUTED_NEEDS_HUMAN_REVIEW'))
    lines=['# 合成电价反馈 MRD/PRD 真实生成对比','',
        '同一冻结底稿、同一 DeepSeek 配置；旧版使用 dd43d52 的提示词、编译与阅读代码，当前版使用本工作区 v4。',
        '全部资料与架构观察均为合成数据；此记录是模型执行证据，不是产品验收。','',
        '| 版本 | 阶段 | 状态 | 模型 | 调用数 | Token（输入/输出） |','| --- | --- | --- | --- | ---: | --- |']
    for row in rows:
        usage='；'.join(str(t['prompt_tokens'])+'/'+str(t['completion_tokens']) for t in row['tokens'])
        lines.append(f'| {row["version"]} | {row["kind"]} | {row["status"]} | {row["model"]} | {row["calls"]} | {usage or "未返回"} |')
    lines += ['', '逐份阅读：[旧 MRD](baseline/MRD-reader.md)、[旧 PRD](baseline/PRD-reader.md)、'
              '[初轮 v4 MRD](current/MRD-reader.md)、[初轮 v4 PRD](current/PRD-reader.md)。']
    for label in labels[2:]:
        lines.append(f'后续候选（业务文字相同，来源定位修正）：[MRD](../live-v4-{label}/MRD-reader.md)、'
                     f'[PRD](../live-v4-{label}/PRD-reader.md)、'
                     f'[来源映射](../live-v4-{label}/source-ref-correction.json)。')
    lines += [
              '完整请求与响应在各分支 data/evidence/model-calls；机器记录见 comparison-result.json。',
              '内容覆盖、表达重复与审查发现由主管逐份核对。']
    failed=[r for r in rows if not r['result_applied']]
    if failed:
        lines += ['', '本轮未形成可用审查结论：'+ '；'.join(
            r['version']+'/'+r['kind']+' '+r['status']+' '+str(r['error']) for r in failed)
            +'。失败尝试保留在机器记录与模型调用证据中；不会自动重发。']
    (OUTPUT/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--worker')
    parser.add_argument('--candidate',help='新候选目录名，格式 candidate-<简短名称>')
    args=parser.parse_args()
    if args.worker:
        if args.worker not in ('baseline','current') and not re.fullmatch(r'candidate-[a-z0-9-]{1,32}',args.worker):
            raise RuntimeError('无效 worker 目录名')
        asyncio.run(worker(args.worker))
        return
    if args.candidate and not re.fullmatch(r'candidate-[a-z0-9-]{1,32}',args.candidate):
        raise RuntimeError('候选目录名须为 candidate-<小写字母数字连字符>')
    sys.path.insert(0,str(ROOT))
    config=config_read_only()
    identity=prepare(config, [args.candidate] if args.candidate else ())
    print('冻结底稿：'+identity['project_id']+'；合成材料 SHA256='+identity['material_sha256'],flush=True)
    if not args.execute:
        print('预检和隔离底稿已建立；--execute 执行真实对比。',flush=True)
        return
    for label in ((args.candidate,) if args.candidate else ('baseline','current')):
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',label],cwd=ROOT)
        report()
        if result.returncode:
            raise RuntimeError(label+' 阶段中断；已保存证据，禁止自动重发已有请求')
    report()
    print(OUTPUT/'comparison.md',flush=True)


if __name__=='__main__':
    main()
