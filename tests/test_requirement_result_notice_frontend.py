"""Actual table feedback must not infer adopted AI content from a revision change."""
from tests.test_scope_frontend import run_ts


LOAD = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';
const source=ts.createSourceFile('RequirementTable.tsx',fs.readFileSync('src/pm-prototype/RequirementTable.tsx','utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const table=source.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='RequirementTable');
function noticeInEffect(hook,needle){
  const effect=table.body.statements.find(n=>ts.isExpressionStatement(n)&&ts.isCallExpression(n.expression)&&n.expression.expression.getText(source)===hook&&n.expression.arguments[0].getText(source).includes(needle));
  assert(effect,'exercise the shipped '+hook+' for '+needle);
  const calls=[];
  function visit(node){if(ts.isCallExpression(node)&&node.expression.getText(source)==='setNotice')calls.push(node.arguments[0].getText(source));ts.forEachChild(node,visit);}
  visit(effect.expression.arguments[0]);assert.equal(calls.length,1);return calls[0];
}
const restore=noticeInEffect('useEffect','requirement-confirmation/state');
const completion=noticeInEffect('useLayoutEffect','aiAnchor.current');
function evaluate(expression,bindings){
  const code=ts.transpileModule('const result=('+expression+');',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText+';return result;';
  return new Function(...Object.keys(bindings),code)(...Object.values(bindings));
}
const noFalseSuccess=message=>assert(!/已补入|已填入|已确认/.test(message),message);
'''


def test_revision_change_cannot_claim_ai_content_was_inserted():
    run_ts(LOAD + r'''
      for(const report of [
        {accepted:[],rejected:[{code:'BUSINESS_DRAFT_DETAIL_FLOW'}]},
        {accepted:[{requirement_id:'REQ-A'}],field_rejected:[{code:'DETAIL_SOURCE'}]},
        {accepted:[{requirement_id:'REQ-A'}],rejected:[]},
      ]){
        const bindings={saved:{revision:41},project:{revision:42,business_draft_reviews:[report]},next:{merge_conflicts:[],rows:{'REQ-A':{statement:'人工原话'}}}};
        const before=JSON.stringify(bindings),message=evaluate(restore,bindings);
        noFalseSuccess(message);assert(message.includes('已有草稿和手工修改已保留'));
        assert(message.includes('本次结果与未载入原因'));assert.equal(JSON.stringify(bindings),before);
      }
    ''')


def test_same_revision_restore_and_conflict_feedback_remain_accurate():
    run_ts(LOAD + r'''
      const bindings={saved:{revision:42},project:{revision:42},next:{merge_conflicts:[]}};
      assert.equal(evaluate(restore,bindings),'已恢复当前浏览器中未提交的表格；尚未正式确认。');
      bindings.next.merge_conflicts=['REQ-A：人工与远端均已修改'];
      const message=evaluate(restore,bindings);
      noFalseSuccess(message);assert(message.includes('合并冲突'));assert(message.includes('你的修改已保留'));
    ''')


def test_completion_reports_gaps_without_promising_filled_or_confirmed_content():
    run_ts(LOAD + r'''
      for(const left of [0,1,4]){
        const message=evaluate(completion,{left});noFalseSuccess(message);
        assert(message.includes('仍需核对'));assert(message.includes('已有草稿和手工修改已保留'));
        assert(message.includes('本次结果与未载入原因'));
        if(left)assert(message.includes('仍有 '+left+' 项待补充'));
        else assert(!message.includes('项待补充'));
      }
    ''')
