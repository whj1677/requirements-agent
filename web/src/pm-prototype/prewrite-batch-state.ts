import type {Project,JsonObject as Obj} from '../api';
import {initial,requirements,sheetQuestions,inScopeQuestion,sheetImplementationTargets,requirementPayload,linkedClausePayload,type Sheet} from './refinement-sheet-state';
import {requirementBehaviorLabels as fields} from './requirement-fields';
import {detailPathValue} from './detail-contract';

export const pendingChecks=(p:Project)=>(p.prewrite_review?.findings||[]).filter((f:Obj)=>f.status!=='resolved'&&(f.severity==='blocker'||f.blocks_document||f.requires_confirmation));
export const batchCompletion=(p:Project)=>{
  const c=(p as Obj).refinement_completion;
  return c?.mode==='prewrite'&&c.applied_revision===p.revision?c:null;
};
/** A review recommendation is a displayed draft, never a saved human answer. */
export function answerOffer(p:Project,f:Obj):{text:string;kind:'saved'|'draft'|'missing';reused?:boolean}{
  const q=p.questions.find(q=>q.id===f.question_id);
  if(q?.answer?.trim())return {text:q.answer,kind:'saved'};
  // A correction can explicitly cite an already answered question. Carry that
  // exact answer into the review draft; never treat it as a new confirmation.
  const message=String(f.message||'');
  if(f.decision_kind==='correction'&&/已保存回答|已答决定|已答口径/.test(message)){
    const ids=new Set(message.match(/Q-[A-Za-z0-9_-]+/g)||[]);
    const prior=p.questions.filter(old=>old.id!==q?.id&&ids.has(old.id)&&old.status==='answered'&&old.answer?.trim());
    if(prior.length===1)return {text:prior[0].answer,kind:'draft',reused:true};
  }
  const completion=batchCompletion(p),draft=completion?.answers?.[q?.id||'']?.text;
  const recommendation=typeof q?.recommendation==='string'?q.recommendation:q?.recommendation?.text;
  const text=draft||f.recommended_answer||recommendation;
  return typeof text==='string'&&text.trim()?{text,kind:'draft'}:{text:'',kind:'missing'};
}
export function checkRoots(p:Project,f:Obj){
  const roots=requirements(p),ids=new Set(roots.map(i=>i.id));
  const refs=[...(f.related_refs||[]),...p.questions.filter(q=>q.id===f.question_id).flatMap(q=>q.related_refs||[])];
  return [...new Set(refs.flatMap((id:string)=>{
    const item=p.items.find(i=>i.id===id),original=item?.target_item_id||id;
    if(ids.has(original))return [original];
    const clause=p.items.find(i=>i.id===original);
    return roots.filter(root=>(clause?.related_refs||[]).includes(root.id)||(root.related_refs||[]).includes(original)).map(i=>i.id);
  }))];
}
/** All offers share one draft per clause, so shared rows cannot overwrite each other. */
export function initialBatch(p:Project,states:Obj[]):Sheet{
  const sheet=initial(p,states),completion=batchCompletion(p);
  const offered=new Set<string>([...pendingChecks(p).flatMap((f:Obj)=>f.saved_correction?.candidate_ids||[]),...(completion?.candidate_ids||[])]);
  for(const item of p.items.filter(i=>offered.has(i.id)&&i.selection_status==='candidate'&&!i.superseded_by)){
    const row=sheet.rows[item.target_item_id||''];
    if(item.kind==='requirement'&&row){
      Object.assign(row,{statement:item.statement,behavior:{...row.behavior,...item.behavior},candidate_id:item.id,
        ...(item.change_type&&item.change_type!=='unspecified'?{change_type:item.change_type}:{}),
        ...(item.detail_contract?{detail_contract:structuredClone(item.detail_contract)}:{})});
    }else if(item.kind==='acceptance'){
      for(const row of Object.values(sheet.rows)){
        const ac=row.acceptance_items.find(ac=>ac.id===item.target_item_id);
        if(ac)Object.assign(ac,{statement:item.statement,candidate_id:item.id,ai_suggested:true});
      }
    }else if(sheet.linked_clauses?.[item.target_item_id||'']){
      Object.assign(sheet.linked_clauses[item.target_item_id!],{statement:item.statement,behavior:{...item.behavior}});
    }
  }
  if(completion)for(const [qid,answer] of Object.entries(completion.answers||{})){
    if(!p.questions.find(q=>q.id===qid)?.answer)sheet.answers[qid]=(answer as Obj).text||'';
  }
  for(const finding of pendingChecks(p)){
    const offer=answerOffer(p,finding);
    if(finding.question_id)sheet.answers[finding.question_id]=offer.text;
  }
  return sheet;
}

/** Already applied advice is an input to the next revision, not a live offer. */
export function refreshBatchCandidates(p:Project,sheet:Sheet,fresh:Sheet):Sheet{
  const next=structuredClone(sheet);
  for(const [id,row] of Object.entries(next.rows)){
    const original=p.items.find(i=>i.id===id),candidate=p.items.find(i=>i.id===row.candidate_id);
    const applied=original?.confirmed_revisions?.some((r:Obj)=>r.candidate_id===row.candidate_id);
    if(row.candidate_id&&applied&&candidate?.selection_status!=='candidate')row.candidate_id=fresh.rows[id]?.candidate_id;
    for(const ac of row.acceptance_items){
      const originalAc=p.items.find(i=>i.id===ac.id),offer=p.items.find(i=>i.id===ac.candidate_id);
      if(ac.candidate_id&&originalAc?.confirmed_revisions?.some((r:Obj)=>r.candidate_id===ac.candidate_id)&&offer?.selection_status!=='candidate'){
        ac.candidate_id=fresh.rows[id]?.acceptance_items.find(a=>a.id===ac.id)?.candidate_id;
        delete ac.invalid_candidate;
      }
    }
  }
  return next;
}

export function fieldText(item:Obj,path:string){
  const value=path==='statement'?item.statement:path.startsWith('behavior.')?item.behavior?.[path.slice(9)]:detailPathValue(item.detail_contract,path);
  return typeof value==='string'?value:'';
}

export function batchImplementations(p:Project,sheet:Sheet){
  const findings=p.prewrite_review?.findings||[];
  return Object.fromEntries(sheetQuestions(p).filter(q=>inScopeQuestion(p,q,sheet.scope)&&sheet.answers[q.id]?.trim()).map(q=>{
    const refs=sheetImplementationTargets(p,sheet,q).flatMap(item=>{
      const quoted=findings.filter((f:Obj)=>f.question_id===q.id).flatMap((f:Obj)=>f.quotes||[]).filter((v:Obj)=>v.object_id===item.id).map((v:Obj)=>v.path);
      const prior=(sheet.answer_implementations?.[q.id]||[]).filter(r=>r.item_id===item.id).map(r=>r.field);
      const original=p.items.find(i=>i.id===item.id)||{};
      const changed=['statement',...Object.keys(fields).map(f=>'behavior.'+f)].filter(path=>fieldText(item,path)!==fieldText(original,path));
      // Review quotes may point at whole containers or source excerpts. Those
      // are useful evidence but not editable string properties for confirmation.
      const valid=(path:string)=>path==='statement'||Object.keys(fields).some(f=>path==='behavior.'+f)||/^detail_contract\.(?:fields|interactions|state_transitions|flow\.(?:nodes|edges))\.[A-Za-z0-9_-]{1,80}\.[a-z_]+$/.test(path);
      const usable=(paths:string[])=>paths.filter(path=>valid(path)&&fieldText(item,path).trim());
      const choices=[usable(quoted),usable(changed),usable(prior),['statement']];
      const paths=[...new Set<string>(choices.find(paths=>paths.length)||[])];
      return paths.flatMap(field=>fieldText(item,field).trim()?[{item_id:item.id,field,quote:fieldText(item,field)}]:[]);
    });
    return [q.id,refs];
  }));
}

export function confirmationPayload(p:Project,sheet:Sheet){
  const qs=sheetQuestions(p).filter(q=>inScopeQuestion(p,q,sheet.scope));
  return {expected_revision:sheet.revision,review_confirmed:true,
    context:{values:sheet.values,field_states:sheet.states,scope_profile:sheet.profile,scope_ids:sheet.scope},
    rows:sheet.scope.map(id=>({...requirementPayload(sheet.rows[id]),confirmed_question_ids:qs.filter(q=>(q.related_refs||[]).includes(id)&&sheet.answers[q.id]?.trim()).map(q=>q.id)})),
    answers:qs.map(q=>({question_id:q.id,answer:sheet.answers[q.id]||''})),linked_clauses:linkedClausePayload(p,sheet),
    answer_implementations:batchImplementations(p,sheet)};
}
