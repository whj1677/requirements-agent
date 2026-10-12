import type { Project, JsonObject as Obj } from './api';

export const scopeLabels: Record<string,string> = {product:'产品或系统',module:'页面或模块',intent:'希望达到的目标',current_state:'当前现状',users:'目标使用者',value:'问题与改动价值',change_scope:'本次改动',preserve_scope:'保持不变',out_of_scope:'暂不涉及',priority:'优先级与依据'};
export type ScopeProfile = {scenario:'unspecified'|'new_product'|'new_module'|'modify_existing';dependencies:'unknown'|'independent'|'shared'};
export type FieldState = {status:'known'|'not_applicable'|'none'|'unknown';note?:string};
export const defaultScopeProfile: ScopeProfile = {scenario:'unspecified',dependencies:'unknown'};

export function contextSnapshot(p:Project) {
  return {values:p.product_context||{},scope_ids:p.product_context?.scope_ids||[],scope_profile:(p as Obj).scope_profile||defaultScopeProfile,field_states:(p as Obj).context_field_states||{}};
}

/** Suggestions populate blank fields; explicit human text and decisions win. */
export function scopeDraft(p:Project):Obj {
  const saved=p.product_context||{},states=(p as Obj).context_field_states||{},proposal=p.product_context_proposal||{};
  return Object.fromEntries(Object.keys(scopeLabels).map(key=>[key,
    String(saved[key]||'').trim()||['none','not_applicable'].includes(states[key]?.status)?saved[key]||'':proposal[key]||saved[key]||''
  ]));
}

export function fieldStatus(key:string,values:Obj,states:Record<string,FieldState>,profile:ScopeProfile):FieldState['status'] {
  if(states[key])return states[key].status;
  if(String(values[key]||'').trim())return 'known';
  if(['current_state','preserve_scope'].includes(key)&&(profile.scenario==='new_product'||profile.scenario==='new_module'&&profile.dependencies==='independent'))return 'not_applicable';
  return 'unknown';
}

export function suggestedKeys(p:Project,values:Obj,states:Record<string,FieldState>):string[] {
  return Object.keys(scopeLabels).filter(key=>!String(p.product_context?.[key]||'').trim()&&!!String(values[key]||'').trim()&&values[key]===p.product_context_proposal?.[key]&&!['none','not_applicable'].includes(states[key]?.status));
}

/** The final review accepts visible suggestions; a draft save keeps unknowns. */
export function scopeSaveStates(p:Project,values:Obj,states:Record<string,FieldState>,profile:ScopeProfile,keys:string[],confirmed=false) {
  const suggestions=new Set(suggestedKeys(p,values,states));
  return Object.fromEntries(keys.map(key=>[key,confirmed&&suggestions.has(key)
    ?{...states[key],status:'known'}:states[key]||{status:fieldStatus(key,values,states,profile)}]));
}

export function isScopeFieldRequired(key:string,profile:ScopeProfile):boolean {
  if(['intent','change_scope','users'].includes(key))return true;
  return ['current_state','preserve_scope'].includes(key)&&!(profile.scenario==='new_product'||profile.scenario==='new_module'&&profile.dependencies==='independent');
}
