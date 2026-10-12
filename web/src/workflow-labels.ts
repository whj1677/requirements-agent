import type { Blocker } from './api';

/** Present backend checkpoints within the three user-facing workflow steps. */
export function publicStep(step:number) {
  return step===1 ? {number:1,title:'提供信息'} : step<=4 ? {number:2,title:'完善需求'} : {number:3,title:'确认交付'};
}

export function publicBlocker(row:Blocker):Blocker {
  if(!['prerequisite_stage','stage_incomplete'].includes(row.code))return row;
  const action=row.actions.find(value=>value.kind==='stage');
  if(!action?.step)return row;
  const step=publicStep(action.step);
  return {...row,title:step.title,detail:undefined,
    message:`请在“${step.title}”中处理待确认内容。`,
    actions:row.actions.map(value=>value===action?{...value,label:`前往${step.title}`}:value)};
}
