'use strict';
const $=id=>document.getElementById(id);
let catalog=[],catalogInfo={},selectedSheet=null,project,method,baseView,nextRow=4,rows=[],previewId=null,busy=false,writing=false,inputVersion=0,selection=null,anchor=null,drag=null,undo=[];
let sheetRequestId=0,rawNext=null;
let selectedMonth=sessionStorage.getItem('labwork-month')||'';
const token=document.querySelector('meta[name="labwork-token"]').content;
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const today=()=>{const d=new Date(),month=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;return selectedMonth&&selectedMonth!==month?selectedMonth+'-01':`${month}-${String(d.getDate()).padStart(2,'0')}`;};
const keys=()=>['date','batch',...(method?.fields||[]).map(f=>f.key),'notes'];
const writable=()=>catalogInfo.writable===true;
const fields=()=>[{key:'date',label:'检测日期',unit:''},{key:'batch',label:'样品批号',unit:''},...method.fields,{key:'notes',label:'备注',unit:''}];
const emptyRow=()=>({date:LabGrid.labels(rows).at(-1)?.date||today(),batch:LabGrid.labels(rows).at(-1)?.batch||'',notes:''});
async function responseData(response){if(response.status===401){location.assign('/login');throw Error('请登录后继续');}const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
async function api(path,body){const response=await fetch(path,{headers:{...(selectedMonth?{'X-Labwork-Month':selectedMonth}:{}),...(body?{'Content-Type':'application/json','X-Labwork-Token':token}:{})},method:body?'POST':'GET',body:body?JSON.stringify(body):undefined});return responseData(response);}
function message(text,success=false){$('message').hidden=false;$('message').textContent=text;$('message').className=success?'success':'';}
function invalidate(){const hadPreview=previewId;previewId=null;inputVersion++;$('commit').disabled=true;$('download').hidden=true;$('results').hidden=true;$('sheet-status').textContent='输入已更改，请重新预览';if(hadPreview&&baseView)sheet(baseView,true);}
function remember(){undo.push(rows.map(r=>({...r})));if(undo.length>30)undo.shift();}
function refresh(){entry();if(baseView)sheet(baseView,true);markSelection();}
function changed(next){remember();rows=next;invalidate();refresh();}
function input(i,key){const numeric=method.fields.some(f=>f.key===key);return `<input data-index="${i}" data-key="${key}" aria-label="第${i+1}行 ${escape(fields().find(f=>f.key===key).label)}" ${numeric?'inputmode="decimal"':''} ${key==='date'?'placeholder="YYYY-MM-DD"':key==='batch'?'maxlength="100"':key==='notes'?'maxlength="2000"':''} autocomplete="off" value="${escape(rows[i][key]||'')}">`;}
function entry(){
  $('entry').innerHTML=`<table><thead><tr><th>测定行</th>${fields().map(f=>`<th>${escape(f.label)}${f.unit?`<small>（${escape(f.unit)}）</small>`:''}</th>`).join('')}</tr></thead><tbody>${rows.map((r,i)=>`<tr><td>${i+1}</td>${keys().map(k=>`<td data-index="${i}" data-key="${k}">${input(i,k)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  $('mobile-entry').innerHTML=rows.map((r,i)=>`<h3>测定行 ${i+1} <button class="quiet mobile-select" data-index="${i}">选择整行</button></h3><div class="group">${fields().map(f=>`<label>${escape(f.label)}${f.unit?`（${escape(f.unit)}）`:''}${input(i,f.key)}</label>`).join('')}</div>`).join('');
  $('mobile-entry').querySelectorAll('.mobile-select').forEach(b=>b.onclick=()=>{anchor={r:+b.dataset.index,c:0};selection={r0:anchor.r,r1:anchor.r,c0:0,c1:keys().length-1};markSelection();});
  $('row-count').textContent=`${rows.length} / 200 行`;bindInputs();
}
function select(i,key,extend=false){const c=keys().indexOf(key);if(!extend||!anchor)anchor={r:i,c};selection={r0:Math.min(anchor.r,i),r1:Math.max(anchor.r,i),c0:Math.min(anchor.c,c),c1:Math.max(anchor.c,c)};markSelection();}
function markSelection(){
  document.querySelectorAll('.fill-handle').forEach(e=>e.remove());
  document.querySelectorAll('input[data-key]').forEach(el=>{const r=+el.dataset.index,c=keys().indexOf(el.dataset.key);el.classList.toggle('range-selected',!!selection&&r>=selection.r0&&r<=selection.r1&&c>=selection.c0&&c<=selection.c1);});
  if(!selection){$('selection-label').textContent='点击单元格，Shift＋点击或方向键扩展选区';return;}
  $('selection-label').textContent=`选中第 ${selection.r0+1}–${selection.r1+1} 行，${fields()[selection.c0].label}–${fields()[selection.c1].label}`;
  for(const id of ['entry','sheet']){
    const cell=$(id).querySelector(`td[data-index="${selection.r1}"][data-key="${keys()[selection.c1]}"]`);if(!cell)continue;
    const handle=document.createElement('button');handle.className='fill-handle';handle.type='button';handle.setAttribute('aria-label','向下拖动填充选区');handle.title='向下拖动填充；或使用填充按钮';cell.append(handle);
    handle.addEventListener('pointerdown',e=>{if(busy)return;e.preventDefault();e.stopPropagation();drag={selection:{...selection},end:selection.r1,container:$(id)};handle.setPointerCapture(e.pointerId);});
    handle.addEventListener('pointermove',e=>{if(!drag)return;const target=document.elementFromPoint(e.clientX,e.clientY)?.closest('td[data-index]');if(target&&drag.container.contains(target)){drag.end=Math.max(drag.selection.r1,+target.dataset.index);$('selection-label').textContent=`将填充至第 ${drag.end+1} 行`;}const scroll=drag.container.closest('.entry-scroll,.sheet-scroll');if(scroll){const box=scroll.getBoundingClientRect();if(e.clientY>box.bottom-30)scroll.scrollTop+=20;if(e.clientY<box.top+30)scroll.scrollTop-=20;}});
    handle.addEventListener('pointerup',()=>{const active=drag;drag=null;if(active&&active.end>active.selection.r1)fill(active.end);});
    handle.addEventListener('pointercancel',()=>{drag=null;markSelection();});
  }
}
function fill(end,mode=$('fill-mode').value){if(busy||!selection)return;try{const source={...selection};changed(LabGrid.fill(rows,keys(),source,end,mode));selection={...source,r1:end};markSelection();message(`已填充至第 ${end+1} 行，请重新计算预览。`,true);}catch(e){message(e.message);}}
function bindInputs(){document.querySelectorAll('input[data-key]').forEach(el=>{
  if(el.dataset.bound)return;el.dataset.bound='1';
  el.addEventListener('pointerdown',e=>select(+el.dataset.index,el.dataset.key,e.shiftKey));
  el.addEventListener('focus',()=>{if(!selection||+el.dataset.index<selection.r0||+el.dataset.index>selection.r1||keys().indexOf(el.dataset.key)<selection.c0||keys().indexOf(el.dataset.key)>selection.c1)select(+el.dataset.index,el.dataset.key);$('cell-address').textContent=`录入 ${+el.dataset.index+1} / ${fields().find(f=>f.key===el.dataset.key).label}`;$('formula-value').value=el.value;});
  el.addEventListener('input',()=>{if(writing){el.value=rows[+el.dataset.index][el.dataset.key]||'';return;}rows[+el.dataset.index][el.dataset.key]=el.value;document.querySelectorAll(`input[data-index="${el.dataset.index}"][data-key="${el.dataset.key}"]`).forEach(other=>{if(other!==el)other.value=el.value;});const fromSheet=!!el.closest('#sheet'),i=el.dataset.index,key=el.dataset.key,position=el.selectionStart;invalidate();if(fromSheet){const replacement=$('sheet').querySelector(`input[data-index="${i}"][data-key="${key}"]`);if(replacement&&replacement!==el){replacement.focus();replacement.setSelectionRange(position,position);}}});
  el.addEventListener('keydown',e=>{
    if(writing)return;
    const k=keys(),r=+el.dataset.index,c=k.indexOf(el.dataset.key),container=el.closest('#entry,#mobile-entry,#sheet');let nr=r,nc=c;
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='d'){e.preventDefault();if(selection&&selection.r1>selection.r0){const end=selection.r1;selection={...selection,r1:selection.r0};fill(end,'copy');}return;}
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'&&undo.length){e.preventDefault();rows=undo.pop();selection=null;anchor=null;invalidate();refresh();return;}
    if(e.key==='Enter')nr+=e.shiftKey?-1:1;
    else if(e.key==='ArrowUp')nr--;
    else if(e.key==='ArrowDown')nr++;
    else if(e.key==='Tab'){nc+=e.shiftKey?-1:1;if(nc>=k.length){nc=0;nr++;}if(nc<0){nc=k.length-1;nr--;}}
    else if(e.key==='ArrowLeft'&&(e.shiftKey||el.selectionStart===0))nc--;
    else if(e.key==='ArrowRight'&&(e.shiftKey||el.selectionStart===el.value.length))nc++;
    else return;
    const target=container.querySelector(`input[data-index="${nr}"][data-key="${k[nc]}"]`);
    if(target){e.preventDefault();select(nr,k[nc],e.shiftKey&&e.key.startsWith('Arrow'));target.focus();target.select();}
  });
  el.addEventListener('paste',e=>{if(writing){e.preventDefault();return;}const text=e.clipboardData.getData('text/plain');if(!text.includes('\t')&&!text.includes('\n'))return;e.preventDefault();try{changed(LabGrid.paste(rows,keys(),+el.dataset.index,keys().indexOf(el.dataset.key),text,emptyRow));}catch(err){message(err.message);}});
  el.addEventListener('copy',e=>{if(!selection||(selection.r0===selection.r1&&selection.c0===selection.c1))return;e.preventDefault();e.clipboardData.setData('text/plain',rows.slice(selection.r0,selection.r1+1).map(r=>keys().slice(selection.c0,selection.c1+1).map(k=>r[k]||'').join('\t')).join('\n'));});
});}
function sheet(view,draft=false){
  $('sheet-label').textContent=`${project.code} / ${view.name} · 原表布局`;
  const covered=new Set(),merges=new Map();view.merges.forEach(m=>{merges.set(`${m.row}:${m.col}`,m);for(let r=m.row;r<m.row+m.rowspan;r++)for(let c=m.col;c<m.col+m.colspan;c++)if(r!==m.row||c!==m.col)covered.add(`${r}:${c}`);});
  let html=`<colgroup><col style="width:40px">${view.columns.map(c=>`<col style="width:${c.width}px">`).join('')}</colgroup><thead><tr><th class="corner"></th>${view.columns.map(c=>`<th>${c.label}</th>`).join('')}</tr></thead><tbody>`,previous=0;
  for(const row of view.rows){
    if(previous&&row.number>previous+1)html+=`<tr><td class="row-num">⋯</td><td colspan="${view.columns.length}" style="height:24px;color:#a0aaa3;background:#f9fbf9;text-align:center">中间历史记录已折叠</td></tr>`;
    html+=`<tr style="height:${row.height}px"><td class="row-num">${row.number}</td>`;
    row.cells.forEach((cell,index)=>{const key=`${row.number}:${index+1}`;if(covered.has(key))return;const merge=merges.get(key);html+=`<td data-ref="${cell.ref}" ${merge?`rowspan="${merge.rowspan}" colspan="${merge.colspan}"`:''} class="${cell.highlight?'new-cell':''}" title="${escape(cell.formula?'='+cell.formula:cell.raw)}">${escape(cell.value)}</td>`;});html+='</tr>';previous=row.number;
  }
  if(draft&&method&&writable()){rows.forEach((row,i)=>{html+=`<tr class="draft-row"><td class="row-num">${nextRow+i}</td>`;view.columns.forEach(col=>{const f=fields().find(f=>method.columns[method.kind==='water_diluted'&&f.key==='result'?'raw_result':f.key]===col.label);html+=f?`<td class="edit-cell" data-index="${i}" data-key="${f.key}">${input(i,f.key)}</td>`:'<td></td>';});html+='</tr>';});}
  $('sheet').innerHTML=html+'</tbody>';
  const cellMap=new Map(view.rows.flatMap(r=>r.cells.map(c=>[c.ref,c])));
  $('sheet').querySelectorAll('td[data-ref]').forEach(td=>{const cell=cellMap.get(td.dataset.ref);Object.assign(td.style,view.styles[cell.style]||{});td.addEventListener('click',()=>{$('sheet').querySelector('.selected')?.classList.remove('selected');td.classList.add('selected');$('cell-address').textContent=cell.ref;$('formula-value').value=cell.formula?'='+cell.formula:cell.raw;});});
  $('sheet-tabs').innerHTML=(project.sheets||[]).map(item=>`<button data-sheet="${escape(item.key)}" class="${item.key===selectedSheet?.key?'active':''}">${escape(item.name)}</button>`).join('');$('sheet-tabs').querySelectorAll('button').forEach(button=>button.onclick=()=>selectSheet(button.dataset.sheet));
  $('sheet-note').textContent=`原表至第 ${view.last_row} 行 · 绿色为本次拟追加 · 同组日期/批号仅首行填写`;bindInputs();markSelection();
}
function clearView(){
  sheetRequestId++;rawNext=null;$('sheet-pager').hidden=true;
  method=null;baseView=null;rows=[];undo=[];anchor=null;selection=null;invalidate();
  $('entry').innerHTML='';$('mobile-entry').innerHTML='';$('sheet').innerHTML='';$('parameters').textContent='';
  $('sheet-panel').hidden=true;document.querySelectorAll('.calc-only').forEach(el=>el.hidden=true);
}
async function selectSheet(key,start=1){
  if(busy||!project)return;
  selectedSheet=project.sheets.find(item=>item.key===key);if(!selectedSheet)return;
  clearView();$('message').hidden=true;
  const code=project.code,month=catalogInfo.active_month,requestId=sheetRequestId;
  method=project.methods.find(item=>item.key===selectedSheet.method_key)||null;
  $('title').textContent=selectedSheet.name;$('breadcrumb').textContent=`${project.name} / ${code}`;
  document.querySelectorAll('#methods button').forEach(button=>button.classList.toggle('active',button.dataset.key===key));
  const editable=!!method&&writable();
  document.querySelectorAll('.calc-only').forEach(el=>el.hidden=!editable);
  $('browse-note').hidden=editable;
  $('browse-note').textContent=!writable()?'历史月份只读，可浏览原表。':'原表浏览 · 此工作表尚无已校核计算方法。';
  if(editable){rows=[{date:today(),batch:'',notes:''}];entry();$('calculate').disabled=false;$('parameters').textContent='计算参数：'+Object.entries(method.params).map(([k,v])=>`${k}=${v}`).join('，');}
  try{
    const path=editable?`/api/sheet/${encodeURIComponent(code)}/${encodeURIComponent(method.key)}`:`/api/book-sheet/${encodeURIComponent(code)}/${encodeURIComponent(key)}?start=${start}`;
    const data=await api(path);
    if(busy||requestId!==sheetRequestId||project?.code!==code||selectedSheet?.key!==key||catalogInfo.active_month!==month)return;
    nextRow=data.next_row||4;baseView=data.view;sheet(baseView,editable);$('sheet-panel').hidden=false;
    $('sheet-status').textContent=editable?'工作副本 · 黄色单元格可录入':'工作簿只读浏览';
    if(data.view.columns_truncated){$('browse-note').hidden=false;$('browse-note').textContent+=' · 页面最多显示前200列，完整内容可下载工作簿查看。';}
    rawNext=data.view.next_row||null;$('sheet-pager').hidden=editable;
    $('page-label').textContent=`第 ${data.view.start_row||start} 行起${data.view.truncated?' · 分页浏览':''}`;
    $('page-first').disabled=start===1;$('page-next').disabled=!rawNext;
  }catch(e){message(e.message);}
}
async function selectProject(code,sheetKey){
  if(busy)return;project=catalog.find(item=>item.code===code);selectedSheet=null;clearView();
  document.querySelectorAll('#projects button').forEach(button=>button.classList.toggle('active',button.dataset.code===code));
  $('rules-panel').hidden=!project;$('download-workbook').hidden=!project;
  if(project)$('download-workbook').href='/api/workbook/'+encodeURIComponent(project.code)+'/download?month='+encodeURIComponent(selectedMonth);
  if(!project){$('methods').innerHTML='';$('title').textContent='请导入项目工作簿';$('breadcrumb').textContent='工作簿';$('import-panel').open=true;return;}
  $('methods').innerHTML=(project.sheets||[]).map(item=>`<button data-key="${escape(item.key)}">${escape(item.name)}</button>`).join('');
  $('methods').querySelectorAll('button').forEach(button=>button.onclick=()=>selectSheet(button.dataset.key));
  renderRules();const first=project.sheets.find(item=>item.key===sheetKey)||project.sheets[0];
  if(first)await selectSheet(first.key);else{$('title').textContent=project.name;message('此工作簿没有可显示的工作表。');}
}
$('page-first').onclick=()=>{if(selectedSheet)selectSheet(selectedSheet.key,1);};
$('page-next').onclick=()=>{if(selectedSheet&&rawNext)selectSheet(selectedSheet.key,rawNext);};
$('calculate').onclick=async()=>{
  if(busy||!method||!writable())return;busy=true;$('calculate').disabled=true;$('calculate').textContent='正在计算全部批号…';const version=inputVersion;
  try{const payload={project:project.code,method:method.key,entries:LabGrid.entries(rows,method.fields)},data=await api('/api/preview',payload);if(version!==inputVersion)throw Error('输入已变化，请重新计算');previewId=data.id;sheet(data.view);$('results').innerHTML=data.outputs.map((o,i)=>`<div class="result-card"><small>${escape(data.entries[i].date)} · ${escape(data.entries[i].batch)} · 第 ${i+1} 行</small><strong>${escape(o.display)}</strong><span>${escape(o.unit)}</span></div>`).join('');$('results').hidden=false;$('commit').disabled=false;$('download').href=data.download;$('download').hidden=false;message(`全部 ${data.rows.length} 行已计算，按日期和批号归组写入第 ${data.rows[0]}–${data.rows.at(-1)} 行。请核对绿色预览。`,true);$('sheet-status').textContent='预览已生成，尚未写入';}catch(e){invalidate();message(e.message);}finally{busy=false;$('calculate').disabled=false;$('calculate').textContent='全部批号计算并预览';}
};
$('commit').onclick=async()=>{if(!previewId||busy||!writable())return;busy=true;writing=true;document.querySelectorAll('input[data-key]').forEach(el=>el.readOnly=true);$('commit').disabled=true;try{const data=await api(`/api/commit/${previewId}`,{});message(data.message,true);const info=await api(`/api/sheet/${project.code}/${method.key}`);baseView=info.view;nextRow=info.next_row;rows=[emptyRow()];undo=[];selection=null;anchor=null;previewId=null;refresh();$('results').hidden=true;$('download').hidden=true;$('sheet-status').textContent='工作副本写入成功，可录入下一批';}catch(e){message(e.message);$('commit').disabled=false;}finally{busy=false;writing=false;document.querySelectorAll('input[data-key]').forEach(el=>el.readOnly=false);}};
function addRows(newBatch=false){if(!method||busy)return;const n=Number($('add-count').value);if(!Number.isInteger(n)||n<1||rows.length+n>LabGrid.maxRows){message('新增行数须为整数，合计最多200行');return;}const seed=LabGrid.labels(rows)[selection?.r1??rows.length-1]||emptyRow();const next=rows.map(r=>({...r})),batch=newBatch?LabGrid.series([seed.batch||''],1,'batch'):seed.batch;for(let i=0;i<n;i++)next.push({date:seed.date||today(),batch:batch===seed.batch&&newBatch?'':batch,notes:'',_requireBatch:newBatch&&i===0});changed(next);select(next.length-n,'batch');$('entry').querySelector(`input[data-index="${next.length-n}"][data-key="batch"]`)?.focus();}
$('parallel').onclick=()=>addRows(false);$('add-batch').onclick=()=>addRows(true);
$('remove-row').onclick=()=>{if(!method||busy||!selection)return;if(rows.length-(selection.r1-selection.r0+1)<1){message('至少保留一行测定');return;}const next=LabGrid.remove(rows,selection.r0,selection.r1);selection=null;anchor=null;changed(next);};
$('fill-down').onclick=()=>fill(Number($('fill-to').value)-1);$('fill-end').onclick=()=>fill(rows.length-1);
$('sample').onclick=()=>{if(!method||busy)return;changed(rows.map((r,i)=>({...r,date:r.date||today(),batch:r.batch||`PREVIEW-ONLY-${String(i+1).padStart(3,'0')}`,...Object.fromEntries(method.fields.map(f=>[f.key,f.sample]))})));};
$('reset').onclick=()=>{if(busy||!method)return;selection=null;anchor=null;changed([{date:today(),batch:'',notes:''}]);};
$('undo').onclick=()=>{if(busy||!method||!undo.length)return;rows=undo.pop();selection=null;anchor=null;invalidate();refresh();};
$('mobile-preview').onclick=()=>{$('sheet-scroll').scrollIntoView({behavior:'smooth',block:'center'});};
$('history-button').onclick=async()=>{try{const data=await api('/api/history');$('history').hidden=!$('history').hidden;$('history').innerHTML=`<h3>预览与写入历史</h3><table><thead><tr><th>项目 / 方法</th><th>批号</th><th>结果</th><th>目标行</th><th>状态</th></tr></thead><tbody>${data.map(r=>`<tr><td>${escape(r.request.project)} / ${escape(r.request.method)}</td><td>${escape([...new Set(r.request.entries?.map(e=>e.batch)||[r.request.batch])].join(' / '))}</td><td>${r.outputs.map(o=>escape(o.display)+' '+escape(o.unit)).join(' / ')}</td><td>${r.rows[0]}–${r.rows.at(-1)}</td><td>${r.state==='done'?'已写入':r.state==='writing'?'写入待恢复':'仅预览'}</td></tr>`).join('')}</tbody></table>`;}catch(e){message(e.message);}};
function workbookStatus(){
  const code=$('project-code').value.trim();
  const exists=catalog.some(item=>item.code.toLowerCase()===code.toLowerCase());
  $('workbook-status').textContent=exists?'该项目已有工作簿，不能覆盖。':'支持 .xlsx，最大20 MB；导入保存独立副本，原件不修改。';
  $('import-workbook').disabled=busy||exists||!code||!$('project-name').value.trim()||!$('workbook-file').files.length||!writable();
}
for(const id of ['project-code','project-name','workbook-file'])$(id).addEventListener('input',workbookStatus);
$('workbook-file').addEventListener('change',()=>{const file=$('workbook-file').files[0];if(!file)return;const name=file.name.replace(/\.xlsx$/i,'');if(!$('project-name').value.trim())$('project-name').value=name;if(!$('project-code').value.trim()&&/^[A-Za-z0-9_-]{1,32}$/.test(name))$('project-code').value=name;workbookStatus();});
async function loadCatalog(){
  let data;try{data=await api('/api/catalog');}catch(error){if(!selectedMonth||error.message!=='月份不存在，请刷新目录')throw error;selectedMonth='';sessionStorage.removeItem('labwork-month');data=await api('/api/catalog');}catalogInfo=data;catalog=data.projects;selectedMonth=data.active_month;sessionStorage.setItem('labwork-month',selectedMonth);
  $('instance-info').textContent='版本 '+data.version+' · 端口 '+data.port+' · 数据目录：'+data.data_directory;
  $('lan-info').textContent=data.lan_url?'局域网：'+data.lan_url:'局域网访问未开启';
  $('projects').innerHTML=catalog.map(item=>`<button data-code="${escape(item.code)}" title="${escape(item.name)}"><span class="project-label"><span class="project-name">${escape(item.name)}</span><small>${escape(item.code)}</small></span><span class="project-count">${item.sheets.length} 表</span></button>`).join('')||'<p class="empty-projects">尚无项目，点击“导入项目工作簿”开始。</p>';
  $('projects').querySelectorAll('button').forEach(button=>button.onclick=()=>selectProject(button.dataset.code));
  const months=[...new Set([...(data.months||[]),data.active_month].filter(Boolean))].sort().reverse();
  $('month-select').innerHTML=months.map(month=>`<option value="${escape(month)}" ${month===data.active_month?'selected':''}>${escape(month)}</option>`).join('');
  $('month-status').textContent=writable()?'可录入':'历史只读';
  if(!$('new-month').value)$('new-month').value=data.current_month||today().slice(0,7);
  workbookStatus();
}
async function exclusive(action){
  if(busy)return false;
  busy=true;writing=true;invalidate();
  const controls=[...document.querySelectorAll('button,input,select')].map(el=>[el,el.disabled]);
  controls.forEach(([el])=>el.disabled=true);
  try{await action();return true;}catch(e){message(e.message);return false;}
  finally{busy=false;writing=false;controls.forEach(([el,disabled])=>el.disabled=disabled);workbookStatus();}
}
$('import-form').onsubmit=async event=>{
  event.preventDefault();if(busy||!writable())return;
  const code=$('project-code').value.trim(),name=$('project-name').value.trim(),file=$('workbook-file').files[0];
  if(!code||!name||!file)return;
  if(catalog.some(item=>item.code.toLowerCase()===code.toLowerCase())){message('该项目已存在，不能覆盖工作簿。');return;}
  if(!/\.xlsx$/i.test(file.name)||!file.size||file.size>20*1024*1024){message('请选择不超过20 MB的非空 .xlsx 工作簿。');return;}
  const ok=await exclusive(async()=>{
    $('workbook-status').textContent='正在导入并读取工作表…';
    await responseData(await fetch('/api/workbook/'+encodeURIComponent(code)+'?name='+encodeURIComponent(name),{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Labwork-Token':token,'X-Labwork-Month':selectedMonth},body:file}));
    await loadCatalog();$('import-form').reset();
  });
  if(ok){$('import-panel').open=false;await selectProject(code);message('项目已导入，工作表目录已生成。',true);}
};
function renderRules(){
  const stored=project.monthly_rules||catalogInfo.monthly_rules?.[project.code]||[];
  $('month-rules').innerHTML='<table><thead><tr><th>工作表</th><th>数据起始行</th><th>清空输入列</th><th>确认</th></tr></thead><tbody>'+project.sheets.map((item,index)=>{
    const rule=stored.find(rule=>rule.sheet===item.name)||{};
    return `<tr data-rule="${index}"><td>${escape(item.name)}</td><td><input aria-label="${escape(item.name)} 数据起始行" type="number" min="1" max="1048576" data-rule-start value="${rule.start_row||''}" placeholder="例如 4"></td><td><input aria-label="${escape(item.name)} 清空输入列" data-rule-columns value="${escape((rule.columns||[]).join(','))}" placeholder="留空则整表保留"></td><td><input type="checkbox" data-rule-confirm aria-label="确认 ${escape(item.name)} 规则" ${project.rules_confirmed?'checked':''}></td></tr>`;
  }).join('')+'</tbody></table>';
  $('save-rules').disabled=!writable();$('month-rules').querySelectorAll('input').forEach(input=>input.disabled=!writable());
}
$('save-rules').onclick=async()=>{
  if(!project||busy||!writable())return;
  const rules=[];
  for(const row of $('month-rules').querySelectorAll('[data-rule]')){
    if(!row.querySelector('[data-rule-confirm]').checked){message('请核对并勾选每个工作表的规则；未确认时不会生成空白月模板。');return;}
    const start=Number(row.querySelector('[data-rule-start]').value),columns=row.querySelector('[data-rule-columns]').value.toUpperCase().split(/[,，\s]+/).filter(Boolean);
    if(!Number.isInteger(start)||start<1||start>1048576||columns.some(column=>!/^([A-Z]{1,3})$/.test(column))){message('请填写有效起始行及列字母，例如 A,B,C；清空列可留空保留整表。');return;}
    rules.push({sheet:project.sheets[+row.dataset.rule].name,start_row:start,columns:[...new Set(columns)]});
  }
  const code=project.code;
  if(await exclusive(async()=>{await api('/api/month-rules/'+encodeURIComponent(code),{rules});await loadCatalog();})){project=catalog.find(item=>item.code===code);renderRules();message('月模板规则已确认保存。',true);}
};
async function changeMonth(month){
  const code=project?.code,key=selectedSheet?.key;
  if(await exclusive(async()=>{const result=await api('/api/month',{month});selectedMonth=result.month;sessionStorage.setItem('labwork-month',selectedMonth);await loadCatalog();})){await selectProject(catalog.some(item=>item.code===code)?code:catalog[0]?.code,key);message('已切换至 '+catalogInfo.active_month,true);}
  else $('month-select').value=catalogInfo.active_month;
}
$('month-select').onchange=()=>changeMonth($('month-select').value);
$('create-month').onclick=()=>{const month=$('new-month').value;if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)){message('请选择有效月份。');return;}changeMonth(month);};
(async()=>{try{await loadCatalog();await selectProject(catalog[0]?.code);}catch(e){message(e.message);}})();
