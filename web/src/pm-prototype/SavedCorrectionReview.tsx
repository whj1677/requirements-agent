import React,{useRef,useState} from 'react';
import {api,type Project,type JsonObject as Obj} from '../api';
import {requirementBehaviorLabels} from './requirement-fields';
import {DetailContractChanges} from './DetailContractChanges';

export function SavedCorrectionReview({project,finding,busy,mutate,onSaved,onSources}:{project:Project;finding:Obj;busy:boolean;
  mutate:(path:string,body:Obj,method?:string)=>Promise<any>;onSaved:()=>void;onSources:(refs?:Obj[])=>void}){
  const [preview,setPreview]=useState<Obj|null>(null),[working,setWorking]=useState(false),[error,setError]=useState('');
  const lock=useRef(false),intent=useRef({hash:'',key:''});
  async function prepare(){
    if(lock.current)return;lock.current=true;setWorking(true);setError('');
    try{setPreview(await api('/projects/'+project.id+'/prewrite-review/findings/'+finding.id+'/saved-correction-preview','POST',{expected_revision:project.revision}));}
    catch(e){setError((e as Error).message);}finally{lock.current=false;setWorking(false);}
  }
  async function confirm(){
    if(lock.current||!preview)return;lock.current=true;setWorking(true);setError('');
    if(intent.current.hash!==preview.preview_hash)intent.current={hash:preview.preview_hash,key:crypto.randomUUID()};
    try{await mutate('/prewrite-review/saved-correction-commit',{expected_revision:preview.expected_revision,preview_id:preview.preview_id,
      preview_hash:preview.preview_hash,idempotency_key:intent.current.key,review_confirmed:true});onSaved();}
    catch(e){setError((e as Error).message);}finally{lock.current=false;setWorking(false);}
  }
  const disabled=busy||working,stale=!!preview&&preview.expected_revision!==project.revision;
  return <section className="prewrite-saved-correction" aria-label="已有修改确认">
    {!preview?<button className="primary" type="button" disabled={disabled} onClick={()=>void prepare()}>{working?'正在读取已有修改…':'查看已起草修改（不调用 AI）'}</button>:<>
      <h4>核对后，一次确认以下修改</h4>
      <p>同时保留并核对你已保存的回答。只有点击下方确认按钮，才会替换旧条款。</p>
      {preview.payload.answers.map((answer:Obj,index:number)=><details key={index}><summary>已保存的回答：{answer.answer}</summary><p>{answer.question}</p></details>)}
      {preview.payload.targets.map((target:Obj)=><article key={target.id} className="prewrite-correction-target">
        <h4>{target.kind==='acceptance'?'验收条件':'需求'}：{target.title}</h4>
        <div className="prewrite-correction-diff"><div><b>修改前</b><p>{target.before?.statement}</p></div><div><b>确认后</b><p>{target.after.statement}</p></div></div>
        {target.after.kind==='requirement'&&<details open><summary>同时核对业务说明与来源</summary>
          {Object.entries(requirementBehaviorLabels).map(([field,label])=><div key={field}><b>{label}</b>
            {target.before?.behavior?.[field]!==target.after.behavior?.[field]&&<p className="answer-old">原来：{target.before?.behavior?.[field]||'未填写'}</p>}
            <p>{target.after.behavior?.[field]||'未填写'}</p></div>)}
          <p>以上业务说明一并纳入本次人工核对。</p></details>}
        {target.before?.change_type!==target.after.change_type&&<p>需求性质将更新为：{({new:'新增功能',modified:'修改已有功能',preserved:'保持已有功能'} as Obj)[target.after.change_type]||'待核对'}</p>}
        <DetailContractChanges before={target.before?.detail_contract} after={target.after.detail_contract}/>
        <button type="button" onClick={()=>onSources(target.after.source_refs||[])}>核对这处修改的来源</button>
      </article>)}
      {stale&&<p role="alert">需求或回答已有变化，请重新查看最新修改后确认。</p>}
      <div className="prewrite-actions"><button className="primary" type="button" disabled={disabled||stale} onClick={()=>void confirm()}>{working?'正在保存…':'确认这些修改'}</button>
        <button type="button" disabled={disabled} onClick={()=>setPreview(null)}>返回，暂不确认</button></div>
    </>}
    {error&&<div role="alert" className="connected-error"><p>{error}</p><button type="button" disabled={disabled} onClick={()=>void prepare()}>重新查看最新修改</button></div>}
  </section>;
}
