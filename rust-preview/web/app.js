'use strict';
const $=id=>document.getElementById(id);
let catalog=[], project, method, currentView, baseView, nextRow=4, count=1, measurements=[{}], previewId=null, busy=false, inputVersion=0;
const token=document.querySelector('meta[name="labwork-token"]').content;
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,body){const response=await fetch(path,{headers:body?{'Content-Type':'application/json','X-Labwork-Token':token}:{},method:body?'POST':'GET',body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
function message(text,success=false){$('message').hidden=false;$('message').textContent=text;$('message').className=success?'success':'';}
function invalidate(){const wasPreview=previewId;previewId=null;inputVersion++;$('commit').disabled=true;$('download').hidden=true;$('results').hidden=true;$('sheet-status').textContent='输入已更改，请重新预览';if(wasPreview&&baseView)sheet(baseView,true);}
function setInput(index,key,value){measurements[index][key]=value;document.querySelectorAll(`input[data-index="${index}"][data-key="${key}"]`).forEach(el=>{if(el.value!==value)el.value=value;});invalidate();}
function entry(){
  $('entry').innerHTML=`<table><thead><tr><th>平行测定</th>${method.fields.map(f=>`<th>${escape(f.label)}<small>（${escape(f.unit)}）</small></th>`).join('')}</tr></thead><tbody>${Array.from({length:count},(_,i)=>`<tr><td>第 ${i+1} 次</td>${method.fields.map(f=>`<td><input inputmode="decimal" aria-label="第${i+1}次${escape(f.label)}" data-index="${i}" data-key="${f.key}" value="${escape(measurements[i][f.key]||'')}" autocomplete="off"></td>`).join('')}</tr>`).join('')}</tbody></table>`;
  $('mobile-entry').innerHTML=Array.from({length:count},(_,i)=>`<h3>第 ${i+1} 次测定</h3><div class="group">${method.fields.map(f=>`<label>${escape(f.label)}（${escape(f.unit)}）<input inputmode="decimal" aria-label="第${i+1}次${escape(f.label)}" data-index="${i}" data-key="${f.key}" value="${escape(measurements[i][f.key]||'')}" autocomplete="off"></label>`).join('')}</div>`).join('');
  bindInputs();
  $('parallel').textContent=count===1?'＋ 第二次平行测定':'－ 移除第二次测定';
}
function bindInputs(){document.querySelectorAll('input[data-key]').forEach(el=>{
    if(el.dataset.bound)return;el.dataset.bound='1';
    el.addEventListener('input',()=>setInput(+el.dataset.index,el.dataset.key,el.value));
    el.addEventListener('keydown',event=>{
      const container=el.closest('#entry,#mobile-entry,#sheet'),inputs=[...container.querySelectorAll('input')],index=inputs.indexOf(el);
      let next;
      if(event.key==='Enter')next=index+(event.shiftKey?-1:1);
      if(event.key==='ArrowUp')next=index-method.fields.length;
      if(event.key==='ArrowDown')next=index+method.fields.length;
      if(event.key==='ArrowLeft'&&el.selectionStart===0)next=index-1;
      if(event.key==='ArrowRight'&&el.selectionStart===el.value.length)next=index+1;
      if(next!==undefined&&inputs[next]){event.preventDefault();inputs[next].focus();inputs[next].select();}
    });
    el.addEventListener('paste',event=>{
      const text=event.clipboardData.getData('text/plain');if(!text.includes('\t')&&!text.includes('\n'))return;
      const rows=text.replace(/\r/g,'').trimEnd().split('\n').map(r=>r.split('\t'));let i=+el.dataset.index,column=method.fields.findIndex(f=>f.key===el.dataset.key);
      if(rows.length+i>2||rows.some(r=>r.length+column>method.fields.length)){event.preventDefault();message('粘贴范围超过本方法的两次测定或字段列数。');return;}
      event.preventDefault();if(rows.length+i>count){count=rows.length+i;while(measurements.length<count)measurements.push({});}
      rows.forEach((row,k)=>row.forEach((value,c)=>measurements[i+k][method.fields[column+c].key]=value.trim()));invalidate();entry();if(baseView)sheet(baseView,true);
    });
  });
}
function sheet(view,draft=false){
  currentView=view;$('sheet-label').textContent=`${project.code} / ${view.name} · 原表布局`;
  const covered=new Set(),merges=new Map();view.merges.forEach(m=>{merges.set(`${m.row}:${m.col}`,m);for(let r=m.row;r<m.row+m.rowspan;r++)for(let c=m.col;c<m.col+m.colspan;c++)if(r!==m.row||c!==m.col)covered.add(`${r}:${c}`);});
  let html=`<colgroup><col style="width:40px">${view.columns.map(c=>`<col style="width:${c.width}px">`).join('')}</colgroup><thead><tr><th class="corner"></th>${view.columns.map(c=>`<th>${c.label}</th>`).join('')}</tr></thead><tbody>`;
  let previous=0;
  for(const row of view.rows){
    if(previous&&row.number>previous+1)html+=`<tr><td class="row-num">⋯</td><td colspan="${view.columns.length}" style="height:24px;color:#a0aaa3;background:#f9fbf9;text-align:center">中间记录已折叠，显示表头及最近记录</td></tr>`;
    html+=`<tr style="height:${row.height}px"><td class="row-num">${row.number}</td>`;
    row.cells.forEach((cell,index)=>{const key=`${row.number}:${index+1}`;if(covered.has(key))return;const merge=merges.get(key);html+=`<td data-ref="${cell.ref}" ${merge?`rowspan="${merge.rowspan}" colspan="${merge.colspan}"`:''} class="${cell.highlight?'new-cell':''}" title="${escape(cell.formula?'='+cell.formula:cell.raw)}">${escape(cell.value)}</td>`;});html+='</tr>';previous=row.number;
  }
  if(draft&&method){
    for(let i=0;i<count;i++){
      html+=`<tr class="draft-row"><td class="row-num">${nextRow+i}</td>`;
      view.columns.forEach((col,c)=>{const field=method.fields.find(f=>method.columns[method.kind==='water_diluted'&&f.key==='result'?'raw_result':f.key]===col.label);html+=field?`<td class="edit-cell"><input data-key="${field.key}" data-index="${i}" aria-label="${col.label}${nextRow+i} ${escape(field.label)}" inputmode="decimal" value="${escape(measurements[i][field.key]||'')}"></td>`:`<td style="background:#fafbf8;color:#8da194">${col.label===method.columns.batch?escape($('batch').value):col.label===method.columns.date?escape($('date').value):''}</td>`;});html+='</tr>';
    }
  }
  html+='</tbody>';$('sheet').innerHTML=html;
  const cellMap=new Map(view.rows.flatMap(r=>r.cells.map(c=>[c.ref,c])));
  $('sheet').querySelectorAll('td[data-ref]').forEach(td=>{const cell=cellMap.get(td.dataset.ref);Object.assign(td.style,view.styles[cell.style]||{});td.addEventListener('click',()=>{$('sheet').querySelector('.selected')?.classList.remove('selected');td.classList.add('selected');$('cell-address').textContent=cell.ref;$('formula-value').value=cell.formula?'='+cell.formula:cell.raw;});});
  $('sheet-tabs').innerHTML=view.sheet_names.map(name=>`<span class="${name.trim()===view.name.trim()?'active':''}">${escape(name)}</span>`).join('');
  $('sheet-note').textContent=`原表至第 ${view.last_row} 行 · 绿色为本次拟追加`;
  bindInputs();
}
async function selectMethod(key){
  if(busy)return;
  method=project.methods.find(m=>m.key===key);measurements=[{}];count=1;invalidate();$('message').hidden=true;
  $('title').textContent=method.name;$('breadcrumb').textContent=`检测项目 / ${project.code} / ${method.name}`;
  document.querySelectorAll('#methods button').forEach(b=>b.classList.toggle('active',b.dataset.key===key));
  $('parameters').textContent='候选参数：'+(Object.entries(method.params).map(([k,v])=>`${k}=${v}`).join('，')||'仪器/质量换算');entry();
  const selectedProject=project.code;
  try{const data=await api(`/api/sheet/${encodeURIComponent(selectedProject)}/${encodeURIComponent(key)}`);if(project.code!==selectedProject||method.key!==key)return;nextRow=data.next_row;baseView=data.view;sheet(data.view,true);$('sheet-status').textContent='原表副本已加载，黄色为本次输入';}catch(e){message(e.message);}
}
async function selectProject(code){
  if(busy)return;
  project=catalog.find(p=>p.code===code);document.querySelectorAll('#projects button').forEach(b=>b.classList.toggle('active',b.dataset.code===code));
  $('methods').innerHTML=project.methods.map(m=>`<button data-key="${m.key}">${escape(m.name)}</button>`).join('');
  $('methods').querySelectorAll('button').forEach(b=>b.onclick=()=>selectMethod(b.dataset.key));
  const supported=project.available&&project.methods.length;$('calculate').disabled=!supported;$('parallel').disabled=!supported;$('sample').disabled=!supported;
  if(supported)await selectMethod(project.methods[0].key);else{method=null;baseView=null;invalidate();$('entry').innerHTML='';$('mobile-entry').innerHTML='';$('sheet').innerHTML='';$('title').textContent=project.name;$('breadcrumb').textContent=`检测项目 / ${code}`;message('此项目的方法尚未迁移。当前验证版覆盖 D006 / Q，其他项目按原表逐方法核定后接入。');}
}
function request(){return {project:project.code,method:method.key,date:$('date').value,batch:$('batch').value.trim(),notes:$('notes').value,measurements:measurements.slice(0,count)};}
$('calculate').onclick=async()=>{
  if(busy||!method)return;busy=true;$('calculate').disabled=true;$('calculate').textContent='正在计算并生成预览…';
  const version=inputVersion;
  try{const data=await api('/api/preview',request());if(version!==inputVersion)throw Error('输入已发生变化，请重新计算预览');previewId=data.id;sheet(data.view);$('results').innerHTML=data.outputs.map((o,i)=>`<div class="result-card"><small>第 ${i+1} 次计算结果</small><strong>${escape(o.display)}</strong><span>${escape(o.unit)}</span></div>`).join('');$('results').hidden=false;$('commit').disabled=false;$('download').href=data.download;$('download').hidden=false;message(`计算完成，将追加至第 ${data.rows.join('、')} 行。确认写入前请核对绿色预览区域。`,true);$('sheet-status').textContent='预览已生成，尚未写入';}catch(e){invalidate();message(e.message);}finally{busy=false;$('calculate').disabled=false;$('calculate').textContent='计算并预览写入';}
};
$('commit').onclick=async()=>{if(!previewId||busy)return;busy=true;$('commit').disabled=true;try{const data=await api(`/api/commit/${previewId}`,{});message(data.message,true);$('sheet-status').textContent='工作副本写入成功';}catch(e){message(e.message);$('commit').disabled=false;}finally{busy=false;}};
$('parallel').onclick=()=>{if(!method||busy)return;if(count===1){count=2;measurements.push({});}else{count=1;measurements=measurements.slice(0,1);}invalidate();entry();if(baseView)sheet(baseView,true);};
$('sample').onclick=()=>{if(!method||busy)return;measurements=Array.from({length:count},()=>Object.fromEntries(method.fields.map(f=>[f.key,f.sample])));if(!$('batch').value)$('batch').value='PREVIEW-ONLY-001';invalidate();entry();if(baseView)sheet(baseView,true);};
$('reset').onclick=()=>{if(busy)return;measurements=Array.from({length:count},()=>({}));invalidate();entry();if(baseView)sheet(baseView,true);};
['date','batch','notes'].forEach(id=>$(id).addEventListener('input',invalidate));
$('mobile-preview').onclick=()=>{$('sheet-scroll').scrollIntoView({behavior:'smooth',block:'center'});};
$('history-button').onclick=async()=>{try{const rows=await api('/api/history');$('history').hidden=!$('history').hidden;$('history').innerHTML=`<h3>预览与写入历史</h3><table><thead><tr><th>项目 / 方法</th><th>批号</th><th>结果</th><th>目标行</th><th>状态</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${escape(r.request.project)} / ${escape(r.request.method)}</td><td>${escape(r.request.batch)}</td><td>${r.outputs.map(o=>escape(o.display)+' '+escape(o.unit)).join(' / ')}</td><td>${r.rows.join(', ')}</td><td>${r.state==='done'?'已写入':r.state==='writing'?'写入待恢复':'仅预览'}</td></tr>`).join('')}</tbody></table>`;}catch(e){message(e.message);}};
(async()=>{const now=new Date();$('date').value=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;try{const data=await api('/api/catalog');catalog=data.projects;$('projects').innerHTML=catalog.map(p=>`<button data-code="${p.code}"><span class="project-code">${p.code}</span><span class="project-count">${p.methods.length||'待接入'}</span></button>`).join('');$('projects').querySelectorAll('button').forEach(b=>b.onclick=()=>selectProject(b.dataset.code));await selectProject('D006');}catch(e){message(e.message);}})();
