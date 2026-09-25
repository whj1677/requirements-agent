// Fixed interpreter: spec values are rendered as text, never as code.
const root=document.getElementById('app');
const labels={normal:'正常',loading:'加载中',empty:'空数据',error:'错误',forbidden:'无权限'};
const el=(tag,text,parent)=>{const node=document.createElement(tag);if(text!=null)node.textContent=String(text);parent?.append(node);return node;};
const pages=new Map(spec.pages.map(page=>[page.page_id,page]));
const data=new Map();
for(const page of spec.pages)for(const region of page.regions)for(const c of region.components)
  if(c.type==='table'||c.type==='list')data.set(c.simulation?.dataset_id||c.component_id,c.rows.map(row=>[...row]));
const head=el('header',null,root);el('strong',spec.title,head);el('small','低保真交互 · 模拟数据 · 不连接业务系统 · UI v'+spec.draft_revision,head);
const controls=el('nav',null,root),content=el('main',null,root);
const pageSelect=el('select',null,controls);pageSelect.setAttribute('aria-label','页面选择');
for(const page of spec.pages){const option=el('option',page.title,pageSelect);option.value=page.page_id;}
const roleSelect=el('select',null,controls);roleSelect.setAttribute('aria-label','模拟角色');
const editRoles=[...new Set(spec.pages.flatMap(p=>p.regions.flatMap(r=>r.components.flatMap(c=>c.simulation?.editable_roles||[]))))];
for(const role of [...new Set([...(editRoles.length?editRoles:['编辑者']),'只读'])]){const option=el('option',role,roleSelect);option.value=role;}
const failureLabel=el('label',null,controls),failure=el('input',null,failureLabel);failure.type='checkbox';failure.setAttribute('aria-label','模拟保存失败');el('span','模拟保存失败（仅演示）',failureLabel);
const hasSimulation=spec.pages.some(p=>p.regions.some(r=>r.components.some(c=>c.simulation)));
roleSelect.hidden=!hasSimulation;failureLabel.hidden=!hasSimulation;
if(!hasSimulation)head.querySelector('small').textContent='静态需求草图 · 示例内容 · 不连接业务系统 · UI v'+spec.draft_revision;
let current=spec.pages[0],role=roleSelect.value;
pageSelect.onchange=()=>render(pages.get(pageSelect.value));roleSelect.onchange=()=>{role=roleSelect.value;render(current);};
function render(page){
  current=page;pageSelect.value=page.page_id;content.replaceChildren();el('h1',page.title,content);
  const stateBar=el('div',null,content),feedback=el('p',null,content),surface=el('div',null,content);feedback.setAttribute('role','status');
  const blocks=new Map(),fields=new Map(),tables=new Map(),filterInputs=new Map(),filters=new Map();
  const setState=name=>{feedback.textContent=(labels[name]||name)+' · '+(page.state_messages[name]||'模拟状态');surface.hidden=name!=='normal';};
  for(const name of page.states){const button=el('button',labels[name],stateBar);button.onclick=()=>setState(name);}
  const tableRows=(c,block)=>{
    block.querySelector('table')?.remove();block.querySelector('.empty-rows')?.remove();
    const table=el('table',null,block),row=el('tr',null,el('thead',null,table));
    for(const col of c.columns)el('th',col.label,row);
    if(c.interaction.action==='edit')el('th','操作',row);
    const body=el('tbody',null,table),dataset=data.get(c.simulation?.dataset_id||c.component_id)||[];
    dataset.forEach((values,index)=>{
      const filter=filters.get(c.component_id)||[];
      if(filter.some(([name,query,exact])=>{const value=String(values[c.columns.findIndex(col=>col.key===name)]??'').toLowerCase();return exact?value!==query:!value.includes(query);}))return;
      const tr=el('tr',null,body);values.forEach(value=>el('td',value,tr));
      if(c.interaction.action==='edit'){const button=el('button','编辑',el('td',null,tr));button.disabled=role==='只读';button.onclick=()=>open(c.interaction.target_id,index);}
    });
    if(!body.children.length){const empty=el('p','暂无匹配的模拟数据',block);empty.className='empty-rows';}
  };
  const open=(id,index)=>{
    const block=blocks.get(id);if(!block)return;
    if(role==='只读'){feedback.textContent='只读角色不能修改模拟数据。';return;}
    block._returnFocus=document.activeElement;block.hidden=false;block.dataset.editIndex=index==null?'':String(index);
    const form=fields.get(id),sim=block._simulation||{},dataset=data.get(sim.dataset_id)||[];
    const table=[...tables.values()].find(c=>(c.simulation?.dataset_id||c.component_id)===sim.dataset_id),values=index==null?[]:dataset[index]||[];
    if(form)for(const [name,input] of form){const col=table?.columns.findIndex(x=>x.key===name)??-1;input.value=col>=0?values[col]||'':'';}
    block._initial=JSON.stringify([...form||[]].map(([name,input])=>[name,input.value]));
    block._feedback.textContent='';
    block.querySelector('.discard-confirm')?.remove();
    form?.values().next().value?.focus();
  };
  const close=id=>{const block=blocks.get(id);block.hidden=true;block._returnFocus?.focus();};
  const cancel=id=>{
    const block=blocks.get(id),form=fields.get(id);
    const dirty=JSON.stringify([...form||[]].map(([name,input])=>[name,input.value]))!==block._initial;
    if(dirty&&!block.querySelector('.discard-confirm')){
      feedback.textContent='存在未保存的修改，请选择继续编辑或放弃修改。';
      const notice=el('div','存在未保存的修改，是否放弃？',block);notice.className='discard-confirm';notice.setAttribute('role','alert');
      const keep=el('button','继续编辑',notice),discard=el('button','放弃修改',notice);
      keep.onclick=()=>{notice.remove();form?.values().next().value?.focus();};
      discard.onclick=()=>{notice.remove();close(id);feedback.textContent='已取消；未保存的修改未写入模拟数据。';};keep.focus();
    }else if(!dirty){close(id);feedback.textContent='已取消；未保存的修改未写入模拟数据。';}
  };
  const save=id=>{
    const block=blocks.get(id),form=fields.get(id);if(!block||!form)return;
    const sim=block._simulation||{},dataset=data.get(sim.dataset_id),table=[...tables.values()].find(c=>(c.simulation?.dataset_id||c.component_id)===sim.dataset_id);
    if(role==='只读'||(sim.editable_roles?.length&&!sim.editable_roles.includes(role))){feedback.textContent='当前角色没有模拟修改权限。';return;}
    if(!dataset||!table){feedback.textContent='模拟数据集引用无效，未保存。';return;}
    const values=Object.fromEntries([...form].map(([name,input])=>[name,input.value.trim()]));
    const editIndex=block.dataset.editIndex===''?null:Number(block.dataset.editIndex);
    const fail=message=>{feedback.textContent='模拟保存失败：'+message+'；输入已保留。';block._feedback.textContent=feedback.textContent;};
    for(const [name,input] of form)if(input.required&&!values[name])return fail(input.getAttribute('aria-label')+'为必填');
    for(const rule of sim.rules||[]){const [a,b]=rule.field_names;
      if(rule.kind==='required'&&!values[a])return fail(a+'为必填');
      if(rule.kind==='non_negative'&&(!values[a]||!Number.isFinite(Number(values[a]))||Number(values[a])<0))return fail(a+'必须为非负数');
      if(rule.kind==='start_before_end'&&!(values[a]<values[b]))return fail(a+'必须早于'+b);
      if(rule.kind==='no_overlap'){
        const ai=table.columns.findIndex(x=>x.key===a),bi=table.columns.findIndex(x=>x.key===b);
        if(ai<0||bi<0)return fail('区间字段未绑定');
        if(dataset.some((row,i)=>i!==editIndex&&values[a]<row[bi]&&row[ai]<values[b]))return fail('区间与已有模拟数据重叠');
      }
    }
    if(failure.checked)return fail('已启用保存失败演示，原有数据未改变');
    const row=table.columns.map(col=>values[col.key]||'');
    if(editIndex==null)dataset.push(row);else dataset[editIndex]=row;
    for(const [tableId,c] of tables)if((c.simulation?.dataset_id||c.component_id)===sim.dataset_id)tableRows(c,blocks.get(tableId));
    close(id);feedback.textContent='模拟保存成功；仅预览环境中的数据已更新。';
  };
  for(const region of page.regions){const area=el('article',null,surface);area.dataset.region=region.name;
    for(const c of region.components){const block=el('section',null,area);blocks.set(c.component_id,block);block._simulation=c.simulation;
      if(['dialog','drawer','panel'].includes(c.type))block.hidden=true;
      if(c.type==='drawer'||c.type==='dialog'){
        block.className=c.type==='dialog'?'business-dialog':'drawer';block.setAttribute('role','dialog');block.setAttribute('aria-label',c.label);block.setAttribute('aria-modal','true');
        block.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();cancel(c.component_id);}if(event.key==='Tab'){const focusable=[...block.querySelectorAll('button,input,select')].filter(x=>!x.disabled);const first=focusable[0],last=focusable.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}};
      }
      block.dataset.kind=c.type;
      if(c.type!=='button')el('h2',c.label+(c.provisional?' · 待确认':''),block);
      const showDescription=spec.schema_version==='1.0'||['notice','text','heading','status'].includes(c.type);
      if(showDescription&&c.description)el('p',c.description,block);
      const form=new Map();for(const field of c.fields){const label=el('label',field.label+(field.required_state==='unknown'?'（必填性待确认）':''),block);
        const input=el(field.type==='select'?'select':'input',null,label);input.setAttribute('aria-label',field.label);
        if(field.type==='select')field.options.forEach(option=>el('option',option,input));else input.type=({number:'number',date:'date',time:'time'})[field.type]||'text';
        if(c.type==='filters'&&field.filter_all_option!==undefined)input._filterAllOption=field.filter_all_option;
        if(field.type==='read_only')input.readOnly=true;form.set(field.name,input);
        input.required=field.required_state==='required';
      }if(form.size)fields.set(c.component_id,form);
      block._feedback=el('p',null,block);block._feedback.setAttribute('aria-live','polite');
      if(c.type==='filters')for(const [name,input] of form)filterInputs.set(name,input);
      if(c.type==='table'||c.type==='list'){tables.set(c.component_id,c);tableRows(c,block);}
      const action=c.interaction;
      if(c.type==='button'||(action.action!=='none'&&action.action!=='edit')){const button=el('button',c.label||'模拟操作',block);
        if(['new','edit','save'].includes(action.action))button.disabled=role==='只读';
        button.onclick=()=>{
          if(action.action==='switch_state')setState(action.target_state||'normal');
          else if(action.action==='switch_page')render(pages.get(action.target_id));
          else if(['new','edit'].includes(action.action))open(action.target_id,null);
          else if(action.action==='save')save(action.target_id);
          else if(action.action==='cancel')cancel(action.target_id);
          else if(action.action==='close_panel')close(action.target_id);
          else if(action.action==='open_panel'&&blocks.has(action.target_id))blocks.get(action.target_id).hidden=false;
          else if(action.action==='filter'){filters.set(action.target_id,[...filterInputs].filter(([,input])=>input.value!==input._filterAllOption).map(([name,input])=>[name,input.value.trim().toLowerCase(),input.tagName==='SELECT']).filter(([,v])=>v));const target=tables.get(action.target_id);if(target)tableRows(target,blocks.get(action.target_id));}
        };
      }
      const info=el('details',null,block);el('summary',c.provisional?'待确认 · 查看说明与依据':'说明与依据',info);
      if(c.description&&!showDescription)el('p',c.description,info);
      el('small','关联 '+(c.ref_ids.join(' · ')||'装饰说明'),info);
    }
  }
  for(const region of page.regions)for(const c of region.components){
    if(['save','cancel'].includes(c.interaction.action)){
      const block=blocks.get(c.component_id),target=blocks.get(c.interaction.target_id);if(target&&block!==target)target.append(block);
    }
  }
  setState(page.states.includes('normal')?'normal':page.states[0]);
}
render(spec.pages[0]);
