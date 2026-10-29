import type {Project} from './api';
import {aiInstructions,requiredIssues} from './pm-prototype/RequirementTable';
import {initial,restoreSheet,sheetQuestions,inScopeQuestion,type Sheet} from './pm-prototype/refinement-sheet-state';
import {prewriteCanDraft} from './prewrite-review';

/** Use the same displayed draft and validation as explicit table confirmation. */
export function documentPreparation(project:Project,states:Record<string,any>[],allowReviewDraft=false){
  const key=project.id+'-refinement-sheet',fresh=initial(project,states);
  let sheet=fresh;
  try{
    const saved=JSON.parse(sessionStorage.getItem(key)||'null') as Sheet|null;
    const base=JSON.parse(sessionStorage.getItem(key+'-base')||'null') as Sheet|null;
    if(saved?.rows&&saved?.values)sheet=restoreSheet(saved,base,fresh);
  }catch{/* An unreadable browser draft must not overwrite saved project data. */}
  const reviewFindings=allowReviewDraft&&prewriteCanDraft(project.prewrite_review)?project.prewrite_review!.findings.filter((f:any)=>f.status!=='resolved'):[];
  const pending=new Set(reviewFindings.map((f:any)=>f.question_id));
  const issues=requiredIssues(sheet,sheetQuestions(project).filter(q=>inScopeQuestion(project,q,sheet.scope)),project).filter(issue=>!(issue.kind==='question'&&pending.has(issue.question_id))&&
    !reviewFindings.some((f:any)=>f.kind==='structure'&&!f.hard_block&&f.path===issue.path&&(f.related_refs||[]).includes(issue.item_id)));
  return {issues,instruction:aiInstructions('用户准备生成文档，请先一次补全所有必填未填项，给出具体可核对的需求、验收与回答草稿；已有人工内容保留，未明确的决定标为推荐方案。完成后交由用户确认，不生成文档、不代替确认。缺项：'+JSON.stringify(issues.map(({kind,field,path,item_id,question_id})=>({kind,field,path,item_id,question_id}))).slice(0,1800),sheet,fresh)};
}
