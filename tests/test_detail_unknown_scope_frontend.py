"""Run shipped TypeScript projection, editor and table callbacks with isolated UI state."""
from tests.test_scope_frontend import run_ts
from tests.test_detail_bulk_adoption_frontend import HARNESS


SCOPE = HARNESS + r'''
const {detailUnknownScopeProperties,detailEditingIssues,detailInteractionRequired}=await load('src/pm-prototype/detail-contract.ts');
const {requiredIssues,aiInstructions}=await load('src/pm-prototype/RequirementTable.tsx',true);
const {scopeLabels}=await load('src/scope-draft.ts');
const {renderToStaticMarkup}=realRequire('react-dom/server');
function scopeFixture(){
  const f=projectFixture(),d=detail('已有业务',1);
  d.fields[0].default_value='记录存在时沿用现有告警编号';
  d.fields[0].evidence.default_value={status:'unknown',reason:'存储默认与显示占位未定；不改变已有业务'};
  const root=f.p.items.find(x=>x.id==='REQ-1'),candidate=f.p.items.find(x=>x.id==='C-new');
  root.detail_required=true;root.detail_contract=structuredClone(d);candidate.detail_contract=structuredClone(d);
  f.p.product_context.scope_ids=['REQ-1'];
  for(const key of Object.keys(scopeLabels))f.p.product_context[key]='本期已有明确口径 '+key;
  f.p.product_context.scope_ids=['REQ-1'];
  const property={path:'detail_contract.fields.FD-0.default_value',property_key:'P011',classification:'technical_only',
    known_business:'server-only-known-business',unknown_technical:'server-only-technical-limitation',explanation:'server-only-review-explanation'};
  const record={requirement_id:'REQ-1',candidate_id:'C-new',detail_hash:'a'.repeat(64),review_hash:'b'.repeat(64),properties:[property]};
  f.p.detail_unknown_scope_reviews=[record];f.base=initial(f.p,[]);f.mine=structuredClone(f.base);
  f.mine.rows['REQ-1'].statement='人工正文保持';f.mine.rows['REQ-1'].behavior.actor='两名值班员';
  f.mine.answers.Q1='本期只核对已说明的两名值班员。';
  f.mine.answer_implementations={Q1:[{item_id:'REQ-1',field:'behavior.actor',quote:'两名值班员'}]};
  return {...f,d,property,record,root,candidate,contract:f.mine.rows['REQ-1'].detail_contract};
}
const unknownAt=(d,path,props=[])=>detailEditingIssues(d,props).filter(x=>x.path===path&&x.message.includes('仍待明确'));
const detailMissing=(f,p=f.p)=>requiredIssues(f.mine,p.questions,p).filter(x=>x.item_id==='REQ-1'&&(x.path||'').startsWith('detail_contract'));
function technicalV2Fixture(){
  const f=scopeFixture();
  for(const contract of [f.root.detail_contract,f.candidate.detail_contract,f.contract]){
    contract.applicability.interactions={applicable:true,reason:'本期已有查询操作'};
    contract.interactions=[{id:'INT-scope',...Object.fromEntries(detailInteractionRequired.map(key=>[key,'本期已有明确口径 '+key])),evidence:{}}];
    contract.applicability.flow={applicable:true,reason:'本期查询操作从入口到显示结果'};
    contract.flow={nodes:[{id:'N-scope-start',kind:'start',label:'查看已有记录',interaction_id:'INT-scope'},{id:'N-scope-end',kind:'end',label:'本次查看结束'}],edges:[{id:'E-scope',from:'N-scope-start',to:'N-scope-end'}]};
  }
  const paths=[...['default_value','constraints','editability','required','source'].map(key=>['fields',key]),...['cancel','pending'].map(key=>['interactions',key])];
  f.record.protocol='detail-technical-scope-2';
  f.record.properties=paths.map(([section,key],index)=>{
    for(const contract of [f.root.detail_contract,f.candidate.detail_contract,f.contract]){
      contract[section][0][key]='资料未说明此技术实现 '+key;
      contract[section][0].evidence[key]={status:'unknown',reason:'已有业务不变，实现细节保留'};
    }
    return {...f.property,path:'detail_contract.'+section+'.'+f.contract[section][0].id+'.'+key,property_key:'P'+index};
  });
  return f;
}
'''


def test_projection_requires_well_formed_exact_owner_candidate_and_default_path():
    run_ts(SCOPE + r'''
      const f=scopeFixture(),before=JSON.stringify(f.p);
      assert.equal(detailUnknownScopeProperties(f.p,'REQ-1',f.contract).length,1);
      assert.equal(unknownAt(f.contract,f.property.path).length,1);
      assert.equal(unknownAt(f.contract,f.property.path,detailUnknownScopeProperties(f.p,'REQ-1',f.contract)).length,0);
      const mutations=[
        p=>delete p.detail_unknown_scope_reviews,p=>p.detail_unknown_scope_reviews=null,p=>p.detail_unknown_scope_reviews={},
        p=>p.detail_unknown_scope_reviews[0].properties='wrong-shape',p=>p.detail_unknown_scope_reviews[0].review_hash='',
        p=>p.detail_unknown_scope_reviews[0].requirement_id='REQ-2',p=>p.detail_unknown_scope_reviews[0].candidate_id='C-other',
        p=>p.items.find(x=>x.id==='C-new').target_item_id='REQ-2',p=>p.items.find(x=>x.id==='C-new').action='add',
        p=>p.items.find(x=>x.id==='C-new').kind='acceptance',p=>p.items.find(x=>x.id==='C-new').applies_to='as_is',
        p=>p.items.find(x=>x.id==='C-new').selection_status='rejected',p=>p.items.find(x=>x.id==='C-new').superseded_by='C-next',
        p=>p.items.find(x=>x.id==='C-new').recommendation_requirement_id='REQ-2',
        p=>p.detail_unknown_scope_reviews.push(structuredClone(p.detail_unknown_scope_reviews[0])),
        p=>p.detail_unknown_scope_reviews[0].properties[0].path='detail_contract.fields.FD-0.meaning',
        p=>p.detail_unknown_scope_reviews[0].properties[0].path='detail_contract.fields.absent.default_value',
        p=>p.detail_unknown_scope_reviews[0].properties[0].classification='business_gap',
        p=>p.detail_unknown_scope_reviews[0].properties[0].known_business='',
        p=>p.detail_unknown_scope_reviews[0].properties[0].unknown_technical='',
        p=>p.detail_unknown_scope_reviews[0].properties[0].explanation=null,
      ];
      mutations.forEach((change,n)=>{const p=structuredClone(f.p);change(p);assert.deepEqual(detailUnknownScopeProperties(p,'REQ-1',f.contract),[],'invalid authority '+n);});
      assert.deepEqual(detailUnknownScopeProperties(f.p,'REQ-2',f.contract),[]);
      assert.equal(JSON.stringify(f.p),before,'projection lookup never mutates project/candidate/proof');
    ''')


def test_confirmed_deferred_revision_requires_exact_root_confirmation_link():
    run_ts(SCOPE + r'''
      const f=scopeFixture();f.candidate.selection_status='deferred';
      assert.deepEqual(detailUnknownScopeProperties(f.p,'REQ-1',f.contract),[],'unconfirmed deferred is not an active authority');
      f.root.confirmed_revisions=[{candidate_id:'C-new'}];
      assert.equal(detailUnknownScopeProperties(f.p,'REQ-1',f.contract).length,1,'confirmed exact candidate remains authoritative after normal deferral');
      f.root.confirmed_revisions=[{candidate_id:'C-old'}];
      assert.deepEqual(detailUnknownScopeProperties(f.p,'REQ-1',f.contract),[]);
      f.root.confirmed_revisions=[{candidate_id:'C-new'}];f.candidate.selection_status='rejected';
      assert.deepEqual(detailUnknownScopeProperties(f.p,'REQ-1',f.contract),[]);
      f.candidate.selection_status='deferred';f.candidate.superseded_by='C-next';
      assert.deepEqual(detailUnknownScopeProperties(f.p,'REQ-1',f.contract),[]);
    ''')


def test_self_add_projection_survives_normal_selection_without_revision_link():
    run_ts(SCOPE + r'''
      const f=scopeFixture(),self={...structuredClone(f.root),id:'SELF-1',action:'add',selection_status:'candidate'};
      delete self.target_item_id;delete self.recommendation_requirement_id;delete self.confirmed_revisions;
      const p={...f.p,items:[self],detail_unknown_scope_reviews:[{...f.record,requirement_id:'SELF-1',candidate_id:'SELF-1'}]};
      for(const state of ['candidate','selected']){self.selection_status=state;assert.equal(detailUnknownScopeProperties(p,'SELF-1',self.detail_contract).length,1,'self/add '+state);}
      self.selection_status='deferred';assert.deepEqual(detailUnknownScopeProperties(p,'SELF-1',self.detail_contract),[]);
      self.selection_status='selected';self.target_item_id='REQ-other';assert.deepEqual(detailUnknownScopeProperties(p,'SELF-1',self.detail_contract),[]);
      delete self.target_item_id;self.superseded_by='SELF-next';assert.deepEqual(detailUnknownScopeProperties(p,'SELF-1',self.detail_contract),[]);
    ''')


def test_editing_issue_helper_rejects_non_default_or_malformed_technical_exemptions():
    run_ts(SCOPE + r'''
      const f=scopeFixture(),path='detail_contract.fields.FD-0.meaning',d=structuredClone(f.contract);
      d.fields[0].evidence.meaning={status:'unknown',reason:'真正业务含义待决定'};
      assert.equal(unknownAt(d,path,[{...f.property,path}]).length,1,'caller cannot exempt another business property');
      assert.equal(unknownAt(d,path,[f.property]).length,1);
      for(const prop of [{...f.property,classification:'business_gap'},{...f.property,known_business:''},{...f.property,unknown_technical:''}])
        assert.equal(unknownAt(d,f.property.path,[prop]).length,1,'unqualified scope retains default unknown');
      const duplicated=structuredClone(d);duplicated.fields.push(structuredClone(duplicated.fields[0]));
      assert.equal(unknownAt(duplicated,f.property.path,[f.property]).length,1,'ambiguous field IDs retain the original deduplicated unknown diagnostic');
      const blank=structuredClone(d);blank.fields[0].default_value='';
      assert.equal(unknownAt(blank,f.property.path,[f.property]).length,1,'blank business value cannot be exempted');
    ''')


def test_withdrawal_restore_and_actual_editor_change_recompute_every_local_count():
    run_ts(SCOPE + r'''
      const f=scopeFixture(),view=await tableFixture(f),availability=[];
      const props={...view.props,onReturnAvailabilityChange:v=>{if(v.missingCount!==undefined)availability.push(v.missingCount);}};
      view.view.render(props);assert.equal(detailMissing(f).length,0);
      let child=view.child('REQ-1');assert.equal(child.props.technicalScopeProperties.length,1);assert.equal(child.props.issues.length,0);
      const withdrawn={...f.p,detail_unknown_scope_reviews:[]};view.view.render({...props,project:withdrawn});
      child=view.child('REQ-1');assert.equal(child.props.technicalScopeProperties.length,0);assert.equal(child.props.issues.length,1);
      assert.equal(detailEditingIssues(child.props.value,child.props.technicalScopeProperties).length,1);assert.equal(availability.at(-1),1);
      assert(text(view.view.tree).includes('还有 1 项待补充'));
      view.view.render(props);assert.equal(view.child('REQ-1').props.issues.length,0);assert.equal(availability.at(-1),0);
      const before=JSON.stringify(f.p),editor=controller(DetailContractEditor,view.child('REQ-1').props);editor.render();
      const input=nodes(editor.tree).find(n=>n.type==='textarea'&&n.props['aria-label']?.endsWith('业务含义'));assert(input);
      input.props.onChange({target:{value:'后续人工修改业务含义'}});view.view.render();
      child=view.child('REQ-1');assert.equal(child.props.technicalScopeProperties.length,0);assert.equal(child.props.issues.length,1);
      assert.equal(availability.at(-1),1);assert.equal(JSON.stringify(f.p),before,'local edit cannot mutate reviewed server candidate');
      for(const change of [d=>d.fields[0].default_value+='变化',d=>d.fields[0].evidence.default_value.reason+='变化',d=>d.applicability.fields.reason+='变化']){
        const d=structuredClone(f.contract);change(d);assert.deepEqual(detailUnknownScopeProperties(f.p,'REQ-1',d),[]);
      }
    ''')


def test_editor_keeps_original_unknown_and_server_issue_visible_with_technical_note():
    run_ts(SCOPE + r'''
      const f=scopeFixture(),before=JSON.stringify(f.contract),message='服务端仍要求核对的真实业务问题';
      const props={requirementId:'REQ-1',title:'告警归属',value:f.contract,technicalScopeProperties:detailUnknownScopeProperties(f.p,'REQ-1',f.contract),issues:[{path:f.property.path,message}],onChange:()=>{throw Error('unexpected edit');}};
      const view=controller(DetailContractEditor,props);view.render();const html=renderToStaticMarkup(view.tree);
      assert(html.includes('技术未定，业务确认保留'));assert(html.includes(f.property.known_business));assert(html.includes(f.property.unknown_technical));assert(html.includes(message));
      const cell=nodes(view.tree).find(n=>n.props['data-cell-key']==='requirement-REQ-1-'+f.property.path);assert(cell);
      assert(text(cell).includes('待明确'),'raw evidence identity remains unknown');
      assert(text(cell).includes('技术事项保留'),'technical limitation does not demand another business decision');
      assert.equal(f.contract.fields[0].evidence.default_value.status,'unknown');assert.equal(JSON.stringify(f.contract),before);
    ''')


def test_actual_confirm_callback_and_ai_context_never_submit_display_projection():
    run_ts(SCOPE + r'''
      const f=scopeFixture(),view=await tableFixture(f),calls=[],serverMessage='服务端业务门槛仍有效';
      assert.equal(requiredIssues(view.view.sheet(),f.p.questions,f.p).length,0);
      const props={...view.props,mutate:async(path,payload)=>{calls.push({path,payload});return {confirmed:false,issues:[{kind:'requirement',item_id:'REQ-1',path:f.property.path,message:serverMessage}]};}};
      view.view.render(props);const submit=buttons(view.view.tree).find(n=>n.props.className==='primary');assert(submit&&!submit.props.disabled);
      click(submit);await view.view.settle();assert.equal(calls.length,1,'actual confirmation callback reached isolated mutate');
      assert.equal(calls[0].path,'/refinement-confirmation');const payload=calls[0].payload,serialized=JSON.stringify(payload);
      for(const token of ['detail_unknown_scope_reviews','known_business','unknown_technical','review_hash','server-only-known-business'])assert(!serialized.includes(token),'display-only field not submitted: '+token);
      assert.deepEqual(payload.rows[0].detail_contract,f.contract);assert.equal(payload.rows[0].detail_contract.fields[0].evidence.default_value.status,'unknown');
      assert.equal(payload.answers.find(x=>x.question_id==='Q1').answer,f.mine.answers.Q1);assert.equal(payload.context.scope_profile.scenario,'modify_existing');assert.equal(payload.rows[0].statement,'人工正文保持');
      const instructions=aiInstructions('起草当前需求',view.view.sheet(),f.base);
      assert(!instructions.includes('detail_unknown_scope_reviews')&&!instructions.includes('server-only-known-business'));
      const child=view.child('REQ-1');assert(child.props.issues.some(x=>x.message===serverMessage),'server rejection must not be suppressed by local projection');
    ''')


def test_v2_finite_properties_keep_legacy_and_record_only_protocol_boundary():
    run_ts(SCOPE + r'''
      const f=technicalV2Fixture(),before=JSON.stringify([f.p,f.contract]);
      const projected=detailUnknownScopeProperties(f.p,'REQ-1',f.contract);
      assert.equal(projected.length,7);
      for(const property of projected)assert.equal(unknownAt(f.contract,property.path,projected).length,0);
      assert.equal(JSON.stringify([f.p,f.contract]),before,'review never changes unknown values or evidence');
      for(const protocol of [undefined,'detail-default-scope-1']){
        const p=structuredClone(f.p),record=p.detail_unknown_scope_reviews[0];record.protocol=protocol;
        record.properties.forEach(property=>property.protocol='detail-technical-scope-2');
        const actual=detailUnknownScopeProperties(p,'REQ-1',f.contract);
        assert.equal(actual.length,1,'wire property cannot upgrade legacy record');
        assert(actual[0].path.endsWith('.default_value'));
      }
      const unrecognized=structuredClone(f.p);unrecognized.detail_unknown_scope_reviews[0].protocol='future-unknown';
      assert.deepEqual(detailUnknownScopeProperties(unrecognized,'REQ-1',f.contract),[]);
      const gap=structuredClone(f.p);gap.detail_unknown_scope_reviews[0].properties[3].classification='business_gap';
      const accepted=detailUnknownScopeProperties(gap,'REQ-1',f.contract);
      assert.equal(accepted.length,6);
      assert.equal(unknownAt(f.contract,f.record.properties[3].path,accepted).length,1,'real business gap remains blocked');
      const changed=structuredClone(f.contract);changed.interactions[0].cancel+=' changed';
      assert.deepEqual(detailUnknownScopeProperties(f.p,'REQ-1',changed),[],'local edit invalidates review');
    ''')


def test_v2_editor_and_confirmation_callback_keep_technical_unknowns_out_of_payload_metadata():
    run_ts(SCOPE + r'''
      const f=technicalV2Fixture(),before=JSON.stringify(f.contract),view=await tableFixture(f),calls=[];
      assert.equal(detailMissing(f).length,0);
      const child=view.child('REQ-1'),editor=controller(DetailContractEditor,child.props);editor.render();
      assert.equal(child.props.technicalScopeProperties.length,7);
      for(const property of f.record.properties){
        const cell=nodes(editor.tree).find(n=>n.props['data-cell-key']==='requirement-REQ-1-'+property.path);
        assert(cell&&text(cell).includes('技术事项保留'),property.path);
        assert(text(cell).includes('待明确'),'original unknown remains visible');
      }
      view.view.render({...view.props,mutate:async(path,payload)=>{calls.push({path,payload});return {confirmed:false,issues:[]};}});
      const submit=buttons(view.view.tree).find(n=>n.props.className==='primary');assert(submit&&!submit.props.disabled);
      click(submit);await view.view.settle();assert.equal(calls.length,1);
      assert.equal(calls[0].path,'/refinement-confirmation');
      assert.deepEqual(calls[0].payload.rows[0].detail_contract,f.contract);
      for(const token of ['detail_unknown_scope_reviews','detail-technical-scope-2','known_business','unknown_technical','review_hash'])
        assert(!JSON.stringify(calls[0].payload).includes(token),'display metadata not submitted: '+token);
      assert.equal(JSON.stringify(f.contract),before);
    ''')
