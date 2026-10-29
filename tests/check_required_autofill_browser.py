"""Required draft completion through real UI/API, synthetic cloud sources only."""
import argparse,asyncio,copy,json,os,re,sys,tempfile
from pathlib import Path
import httpx,jsonschema
from playwright.async_api import async_playwright,expect
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests.check_requirement_entry_browser import Journey,RID,OTHER,TITLE,AC,FIELDS,ORIGIN
from app.main import create_app
from app.provider import Provider,DEFAULT,origin
from app.business_draft import PROTOCOL

class DraftProvider(Provider):
 business_draft_protocol=PROTOCOL
 def __init__(self,folder):
  super().__init__(folder/'absent.env');self.keys[origin(DEFAULT)]='OFFLINE_ONLY';self.requests=[]
 async def request(self,config,messages,evidence=None):
  header=json.loads(messages[0]['content'].split('可信任务头：')[1]);ctx=json.loads(messages[1]['content'][0]['text']);self.requests.append({'header':header,'context':ctx})
  assert header['refinement_completion']['mode'] in ('all','required','question')
  ex=next(ex for ex in ctx['excerpts'] if '合成入口验收资料' in ex['text']);refs=[dict(source=ex['source'],locator=ex['locator'])]
  def field(text,basis='fact'):return dict(text=text,basis=basis,sources=refs if basis=='fact' else [])
  answers={q['question_key']:field('建议采用本期已说明的授权范围；入口沿用既有平台，未授权内容不展示。','recommendation') for q in ctx['completion_questions']}
  completion={'answers':answers};rows=[];summaries={}
  if header['refinement_completion']['mode']!='question':
   completion.update(profile={'scenario':'new_module','dependencies':'independent'},change_types={r['row_key']:'new' for r in ctx['current_rows']},scope_rows=[r['row_key'] for r in ctx['current_rows']])
   summaries={k:field({'intent':'方便电价管理员查找已保存的电价模板。','users':'电价管理员','change_scope':'新增独立的电价模板列表与历史版本查询。'}.get(k,'本期沿用已保存平台资料。')) for k in header['required_summary_fields']}
   for r in ctx['current_rows']:
    rows.append(dict(row_key=r['row_key'],title=r['title'],statement=field(r['statement']),behavior={k:field('电价管理员从云平台进入列表，查看已保存的名称和所属地区。') if k in ('actor','entry','flow','data','result') else field('建议沿用既有授权并在失败时保留当前查询条件。','recommendation') for k in FIELDS},acceptance=[field(AC if r['title']==TITLE else '查询所选模板后显示已保存的历史版本名称。')],questions=[]))
  value=dict(summary='已起草可核对内容，尚未确认。',summary_fields=summaries,requirements=rows,limitations=[],completion=completion)
  if header.get('detail_required'):
   for row in rows:
    field_row=dict(id='F1',name='模板名称',meaning='已保存模板的展示名称',data_type='text',source='已保存模板数据',required='只读展示',default_value='无默认值',constraints='按已保存内容展示',visibility='在本期查询页面展示',editability='只读')
    field_row['evidence']={key:{'status':'proposal','reason':'合成界面验收草稿，待人工核对'} for key in field_row if key!='id'}
    row['detail_contract']=dict(schema_version=1,applicability={key:dict(applicable=key=='fields',reason='合成只读列表字段定义' if key=='fields' else '本期仅只读展示，不包含该类定义') for key in ('fields','interactions','flow','state_transitions')},fields=[field_row],interactions=[],flow=dict(nodes=[],edges=[]),state_transitions=[])
  jsonschema.validate(value,header['schema']);evidence.before_send(config);usage={'prompt_tokens':20,'completion_tokens':20,'total_tokens':40}
  evidence.response(json.dumps(value,ensure_ascii=False),http_status=200,finish_reason='stop',usage=usage)
  return value,dict(usage=usage,origin=origin(config),finish_reason='stop')

async def run(args,mode):
 out=args.output/mode;out.mkdir(parents=True);report={'status':'running','mode':mode,'page_errors':[],'api_requests':[],'real_model_calls':0}
 with tempfile.TemporaryDirectory(prefix='ra-required-') as folder:
  provider=DraftProvider(Path(folder));app=create_app(Path(folder)/'data',provider=provider)
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=ORIGIN) as client:
   j=Journey(args,'entry',app,provider,client,out,report)
   with app.state.store.edit(j.pid,j.project()['revision'],'合成必填缺项') as (p,_):
    p['product_context']['intent']='';p['context_field_states']['intent']={'status':'unknown'}
    p['questions']=[dict(id='Q-REPAIR',topic_key='repair',question='请核对是否按来源修正列表的显示内容？',why='需要核对原文',related_refs=[RID],source_refs=p['items'][0]['source_refs'],options=[],blocking=True,blocking_stage=3,status='open',answer=None),dict(id='Q-ACCESS',topic_key='access',question='本期未明确的查看范围采用什么方案？',why='待确认',related_refs=[RID],source_refs=p['items'][0]['source_refs'],options=[],blocking=True,blocking_stage=3,status='open',answer=None)]
   before=copy.deepcopy(j.project())
   async with async_playwright() as pw:
    b=await pw.chromium.launch();context=await b.new_context(viewport={'width':1366,'height':768});await context.route('**/*',j.dispatch);page=await context.new_page();j.page=page;page.on('pageerror',lambda e:report['page_errors'].append(str(e)))
    try:
     await page.goto(ORIGIN,wait_until='networkidle');await page.locator('aside.sidebar button[title="'+before['name']+'"]').click();work=j.work();await work.get_by_role('button',name='进入完善需求',exact=True).click();table=work.get_by_role('table',name='需求确认表');await expect(table).to_be_visible()
     if mode=='question':
      other=table.locator('[data-cell-key="question-Q-ACCESS"] textarea');await other.fill('人工已写的回答必须保留。')
      target=table.locator('[data-cell-key="question-Q-REPAIR"]');button=target.get_by_role('button',name='请 AI 提供建议',exact=True)
     else:
      target=table.locator('[data-cell-key="question-Q-REPAIR"]');button=work.get_by_role('button',name='AI 补充建议' if mode=='all' else 'AI 补全必填项',exact=True)
     await button.scroll_into_view_if_needed();offset=await target.evaluate("e=>e.getBoundingClientRect().top-e.closest('.refinement-sheet-scroll').getBoundingClientRect().top") if mode=='question' else None
     await button.click();dialog=page.get_by_role('dialog',name='核对本次任务',exact=True);await expect(dialog).to_be_visible();await dialog.get_by_role('button',name=re.compile('^(授权本次范围并运行|运行本次任务)$')).click()
     for _ in range(180):
      tasks=app.state.store.records(j.pid,'user_task')
      if tasks and tasks[-1]['status'] not in ('queued','running'):break
      await asyncio.sleep(.1)
     assert tasks[-1]['status'] in ('succeeded','partial'),tasks[-1]
     await expect(target.locator('textarea')).not_to_have_value('',timeout=20000)
     assert len(provider.requests)==1
     after=j.project();assert all(q['answer'] is None and q['status']=='open' for q in after['questions']);assert after['items'][:len(before['items'])]==before['items'] and after['documents']==before['documents']
     if mode=='question':
      await expect(other).to_have_value('人工已写的回答必须保留。')
      latest=await target.evaluate("e=>e.getBoundingClientRect().top-e.closest('.refinement-sheet-scroll').getBoundingClientRect().top")
      assert abs(latest-offset)<18,(latest,offset);report['scroll_delta']=latest-offset
      assert not after['refinement_completion'].get('candidate_ids')
      # A completed inline task must not cover the next bulk action.
      bulk=work.get_by_role('button',name='AI 补全必填项',exact=True)
      await bulk.scroll_into_view_if_needed();await bulk.click()
      await expect(dialog).to_be_visible();await dialog.get_by_role('button',name='返回保留输入',exact=True).click()
      assert len(provider.requests)==1
     else:
      await expect(table.locator('[data-cell-key="question-Q-ACCESS"] textarea')).not_to_have_value('')
      await expect(table.get_by_role('textbox',name='希望达到的目标',exact=True)).not_to_have_value('')
      await expect(work.locator('.sheet-missing-jump')).to_have_count(0)
      assert len(after['refinement_completion']['answers'])==2
      if mode=='all':
       summary=table.locator('[data-cell-key^="context-"] textarea')
       assert await summary.evaluate_all('es=>es.every(e=>e.value.trim())'),'All-summary drafting must not leave optional summary fields empty either'
     assert not report['page_errors'];report.update(status='passed',model_requests=1,human_answers_unchanged=True,original_items_unchanged=True,checks=['对应问题自动填入','无需手工输入必填内容' if mode!='question' else '单题不改其他内容且滚动稳定','仅草稿，未自动确认'])
     await page.screenshot(path=str(out/'result.png'));(out/'project-after.json').write_text(json.dumps(after,ensure_ascii=False,indent=2),'utf-8')
    except Exception as e:
     import traceback;report.update(status='failed',error=str(e),traceback=traceback.format_exc());await page.screenshot(path=str(out/'failure.png'),full_page=True)
    finally:await b.close()
 (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8');return report
async def main(args):
 args.output.mkdir(parents=True,exist_ok=True);results=[]
 for mode in ('question','required','all'):results.append(await run(args,mode))
 (args.output/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),'utf-8');print(json.dumps([{k:r.get(k) for k in ('mode','status','error','scroll_delta')} for r in results],ensure_ascii=False));assert all(r['status']=='passed' for r in results)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--static-build',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);asyncio.run(main(parser.parse_args()))
