const root = document.getElementById('app');
const labels = {normal:'正常',loading:'加载中',empty:'空数据',error:'错误',forbidden:'无权限'};
const element = (tag,text,parent) => { const n=document.createElement(tag); if(text!=null)n.textContent=text; if(parent)parent.append(n); return n; };
element('h1',spec.title,root); element('p',spec.design_intent,root);
const tabs=element('nav',null,root), content=element('div',null,root);
function render(page){
 content.replaceChildren(); element('h2',page.title,content);
 const controls=element('div',null,content), status=element('aside',null,content), normal=element('div',null,content);
 function state(s){status.textContent=(labels[s]||s)+' · '+(page.state_messages[s]||'模拟状态');normal.hidden=s!=='normal';}
 page.states.forEach(s=>{const b=element('button',labels[s],controls);b.onclick=()=>state(s);});
 const targets={};
 for(const region of page.regions){const area=element('article',null,normal);area.dataset.region=region.name;
  for(const c of region.components){const block=element('section',null,area);targets[c.component_id]=block;
   if(c.type==='dialog')block.hidden=true;
   element('strong',c.label+(c.provisional?' · 待确认':''),block); element('p',c.description,block);
   for(const field of c.fields){const l=element('label',field.label+(field.required_state==='unknown'?'（必填性待确认）':''),block);
    const input=element(field.type==='select'?'select':'input',null,l);
    if(field.type==='select')field.options.forEach(o=>element('option',o,input));else input.type=field.type==='number'?'number':field.type==='date'?'date':'text';
    if(field.type==='read_only')input.readOnly=true;
   }
   if(c.columns.length){const table=element('table',null,block), head=element('tr',null,element('thead',null,table));c.columns.forEach(x=>element('th',x.label,head));const body=element('tbody',null,table);c.rows.forEach(row=>{const tr=element('tr',null,body);row.forEach(x=>element('td',x,tr));});}
   if(c.type==='button'||c.interaction.action!=='none'){const b=element('button',c.label||'模拟操作',block);b.onclick=()=>{const a=c.interaction;if(a.action==='switch_state')state(a.target_state||'normal');else if(targets[a.target_id])targets[a.target_id].hidden=a.action==='close_panel';};}
   element('small','关联 '+(c.ref_ids.join(' · ')||'装饰说明'),block);
  }
 }
 state(page.states.includes('normal')?'normal':page.states[0]);
}
spec.pages.forEach(p=>{const b=element('button',p.title,tabs);b.onclick=()=>render(p);});
render(spec.pages[0]);
