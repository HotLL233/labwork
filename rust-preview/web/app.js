'use strict';
const $=id=>document.getElementById(id);
let catalog=[],catalogInfo={},selectedSheet=null,project,method,baseView,nextRow=4,rows=[],previewId=null,busy=false,writing=false,inputVersion=0,selection=null,anchor=null,drag=null,undo=[];
let sheetRequestId=0,rawNext=null,workbookFingerprint=null;
let monthPreview=null,monthPreviewRequest=0,monthPreviewBusy=false,monthPreviewBlocked=false,monthAdjustments=[],monthSelection=null,monthAnchor=null,monthDrag=false,monthDragged=false,monthFocus={};
let sourceFolderPreview=null;
let selectedMonth=sessionStorage.getItem('labwork-month')||'';
const token=document.querySelector('meta[name="labwork-token"]').content;
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const today=()=>{const d=new Date(),month=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;return selectedMonth&&selectedMonth!==month?selectedMonth+'-01':`${month}-${String(d.getDate()).padStart(2,'0')}`;};
const keys=()=>['date','batch',...(method?.fields||[]).map(f=>f.key),'notes'];
const writable=()=>catalogInfo.writable===true;
const linked=()=>!!project?.source_link&&!project?.source_error;
const fields=()=>[{key:'date',label:'检测日期',unit:''},{key:'batch',label:'样品批号',unit:''},...method.fields,{key:'notes',label:'备注',unit:''}];
const emptyRow=()=>({date:LabGrid.labels(rows).at(-1)?.date||today(),batch:LabGrid.labels(rows).at(-1)?.batch||'',notes:''});
async function responseData(response){if(response.status===401){location.assign('/login');throw Error('请登录后继续');}const data=await response.json();if(!response.ok)throw Error(data.error||'请求失败');return data;}
async function api(path,body){const response=await fetch(path,{headers:{...(selectedMonth?{'X-Labwork-Month':selectedMonth}:{}),...(body?{'Content-Type':'application/json','X-Labwork-Token':token}:{})},method:body?'POST':'GET',body:body?JSON.stringify(body):undefined});return responseData(response);}
function showWorkspace(view){if(busy)return;$('entry-view').hidden=view!=='entry';$('management-view').hidden=view!=='manage';for(const [id,value] of [['nav-entry','entry'],['nav-manage','manage']]){$(id).classList.toggle('active',view===value);$(id).setAttribute('aria-pressed',String(view===value));}}
$('nav-entry').onclick=()=>showWorkspace('entry');$('nav-manage').onclick=()=>showWorkspace('manage');$('manage-shortcut').onclick=()=>{showWorkspace('manage');$('source-folder-path').focus();};
$('link-source-shortcut').onclick=()=>{showWorkspace('manage');$('source-panel').scrollIntoView({behavior:'smooth',block:'start'});$('source-path').focus();};
$('project-select').onchange=()=>selectProject($('project-select').value);$('sheet-select').onchange=()=>selectSheet($('sheet-select').value);
$('logout').onclick=async()=>{if(busy)return;try{await api('/api/logout',{});location.assign('/login');}catch(e){message(e.message);}};
const previewLabel=()=>method?.kind==='manual'?'预览写入':'计算并预览';
function message(text,success=false){$('message').hidden=false;$('message').textContent=text;$('message').className=success?'success':'';}
function invalidate(){const hadPreview=previewId;previewId=null;inputVersion++;$('commit').disabled=true;$('download').hidden=true;$('results').hidden=true;$('sheet-status').textContent='输入已更改，请重新预览';$('action-state').textContent=linked()?'录入后生成预览':'请先关联原文件，才能确认写入';if(hadPreview&&baseView)sheet(baseView,true);}
function remember(){undo.push(rows.map(r=>({...r})));if(undo.length>30)undo.shift();}
function refresh(){entry();if(baseView)sheet(baseView,true);markSelection();}
function changed(next){remember();rows=next;invalidate();refresh();}
function input(i,key){const numeric=method.fields.some(f=>f.key===key&&f.input_type!=='text');return `<input data-index="${i}" data-key="${key}" aria-label="第${i+1}行 ${escape(fields().find(f=>f.key===key).label)}" ${numeric?'inputmode="decimal"':''} ${key==='date'?'placeholder="YYYY-MM-DD"':key==='batch'?'maxlength="100"':key==='notes'?'maxlength="2000"':''} autocomplete="off" value="${escape(rows[i][key]||'')}">`;}
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
function sheet(view,draft=false,target=$('sheet')){
  const entrySheet=target===$('sheet');
  if(entrySheet)$('sheet-label').textContent=`${project.code} / ${view.name} · 原表布局`;
  const covered=new Set(),merges=new Map();view.merges.forEach(m=>{merges.set(`${m.row}:${m.col}`,m);for(let r=m.row;r<m.row+m.rowspan;r++)for(let c=m.col;c<m.col+m.colspan;c++)if(r!==m.row||c!==m.col)covered.add(`${r}:${c}`);});
  let html=`<colgroup><col style="width:40px">${view.columns.map(c=>`<col style="width:${c.width}px">`).join('')}</colgroup><thead><tr><th class="corner"></th>${view.columns.map(c=>`<th>${c.label}</th>`).join('')}</tr></thead><tbody>`;
  for(const row of view.rows){
    html+=`<tr style="height:${row.height}px"><td class="row-num">${row.number}</td>`;
    row.cells.forEach((cell,index)=>{const key=`${row.number}:${index+1}`;if(covered.has(key))return;const merge=merges.get(key);html+=`<td data-ref="${cell.ref}" ${merge?`rowspan="${merge.rowspan}" colspan="${merge.colspan}"`:''} class="${cell.highlight?'new-cell':''}" title="${escape(cell.formula?'='+cell.formula:cell.raw)}">${escape(cell.value)}</td>`;});html+='</tr>';
  }
  if(entrySheet&&draft&&method&&writable()){rows.forEach((row,i)=>{html+=`<tr class="draft-row"><td class="row-num">${nextRow+i}</td>`;view.columns.forEach(col=>{const f=fields().find(f=>method.columns[method.kind==='water_diluted'&&f.key==='result'?'raw_result':f.key]===col.label);html+=f?`<td class="edit-cell" data-index="${i}" data-key="${f.key}">${input(i,f.key)}</td>`:'<td></td>';});html+='</tr>';});}
  target.innerHTML=html+'</tbody>';
  const cellMap=new Map(view.rows.flatMap(r=>r.cells.map(c=>[c.ref,c])));
  target.querySelectorAll('td[data-ref]').forEach(td=>{const cell=cellMap.get(td.dataset.ref);Object.assign(td.style,view.styles[cell.style]||{});td.addEventListener('click',event=>{if(entrySheet){target.querySelector('.selected')?.classList.remove('selected');td.classList.add('selected');$('cell-address').textContent=cell.ref;$('formula-value').value=cell.formula?'='+cell.formula:cell.raw;if(!busy)$('format-range').value=cell.ref;}else{if(!monthDragged)selectMonthCell(cell.ref,event.shiftKey);monthDragged=false;showMonthCell(cell.ref);}});});
  if(!entrySheet)return;
  $('sheet-tabs').innerHTML=(project.sheets||[]).map(item=>`<button data-sheet="${escape(item.key)}" class="${item.key===selectedSheet?.key?'active':''}">${escape(item.name)}</button>`).join('');$('sheet-tabs').querySelectorAll('button').forEach(button=>button.onclick=()=>selectSheet(button.dataset.sheet));
  $('sheet-note').textContent=`原表至第 ${view.last_row} 行 · 绿色为本次拟追加 · 同组日期/批号仅首行填写`;bindInputs();markSelection();
}
function showParameters(){
  const params=method.params||{};
  $('parameters').textContent=params.source||(method.kind==='manual'?'手工录入：逐项填写结果，预览后写入关联原文件。':method.kind==='workbook_formula'?'按工作簿公式计算；请核对结果后确认写入原文件。':'按已配置检测方法计算；请核对结果后确认写入原文件。');
  const names={blank:'空白值',c:'标准溶液浓度',factor:'换算系数',mw:'摩尔质量'};
  const values=Object.entries(params).filter(([key])=>!['source','template_row'].includes(key));
  $('calculation-details').hidden=!values.length;
  $('calculation-values').innerHTML=values.map(([key,value])=>{const label=key.startsWith('f:')?`${key.slice(2)} 列公式`:key.startsWith('fixed:')?`固定参数 ${key.slice(6)}`:names[key]||key;return `<dt>${escape(label)}</dt><dd>${escape(value)}</dd>`;}).join('');
}
function clearView(){
  sheetRequestId++;rawNext=null;workbookFingerprint=null;$('format-panel').open=false;$('format-form').reset();$('format-status').textContent='未选择的格式保持原样。';$('sheet-pager').hidden=true;
  method=null;baseView=null;rows=[];undo=[];anchor=null;selection=null;invalidate();
  $('entry').innerHTML='';$('mobile-entry').innerHTML='';$('sheet').innerHTML='';$('parameters').textContent='';$('calculation-details').hidden=true;
  $('sheet-panel').hidden=true;$('method-choice').hidden=true;document.querySelectorAll('.calc-only').forEach(el=>el.hidden=true);
}
async function selectSheet(key,start=1,methodKey,draftRows){
  if(busy||!project)return;
  selectedSheet=project.sheets.find(item=>item.key===key);if(!selectedSheet)return;
  clearView();$('message').hidden=true;
  const code=project.code,month=catalogInfo.active_month,requestId=sheetRequestId;
  const choices=project.methods.filter(item=>item.sheet?.trim()===selectedSheet.name.trim()||item.key===selectedSheet.method_key);
  method=choices.find(item=>item.key===(methodKey||selectedSheet.method_key))||choices[0]||null;
  $('method-choice').hidden=choices.length<2;$('method-select').innerHTML=choices.map(item=>`<option value="${escape(item.key)}" ${item.key===method?.key?'selected':''}>${escape(item.name)}</option>`).join('');
  $('sheet-select').value=key;$('calculate').textContent=previewLabel();$('sample').hidden=!method?.fields.some(field=>field.sample);
  $('title').textContent=selectedSheet.name;
  document.querySelectorAll('#methods button').forEach(button=>button.classList.toggle('active',button.dataset.key===key));
  const editable=!!method&&writable();
  document.querySelectorAll('.calc-only').forEach(el=>el.hidden=!editable);
  $('browse-note').hidden=editable;
  $('browse-note').textContent=!writable()?'历史月份只读，可浏览原表。':selectedSheet.reason||'此表未识别到可用的计算或手工录入规则。可浏览和下载原表，不会生成未经验证的计算结果。';
  if(editable){rows=draftRows?.length&&method.key===methodKey?draftRows.map(row=>({...row})):[{date:today(),batch:'',notes:''}];entry();$('calculate').disabled=false;showParameters();}
  try{
    const path=editable?`/api/sheet/${encodeURIComponent(code)}/${encodeURIComponent(method.key)}`:`/api/book-sheet/${encodeURIComponent(code)}/${encodeURIComponent(key)}?start=${start}`;
    const data=await api(path);
    if(busy||requestId!==sheetRequestId||project?.code!==code||selectedSheet?.key!==key||catalogInfo.active_month!==month)return;
    nextRow=data.next_row||4;workbookFingerprint=data.fingerprint||null;baseView=data.view;sheet(baseView,editable);updateFormatState();$('sheet-panel').hidden=false;
    $('sheet-status').textContent=editable?(linked()?'关联原文件 · 黄色单元格可录入':'上传副本 · 关联原文件后可写入'):'工作簿只读浏览';
    if(data.view.columns_truncated){$('browse-note').hidden=false;$('browse-note').textContent+=' · 页面最多显示前200列，完整内容可下载工作簿查看。';}
    rawNext=data.view.next_row||null;$('sheet-pager').hidden=editable;
    $('page-label').textContent=`第 ${data.view.start_row||start} 行起${data.view.truncated?' · 分页浏览':''}`;
    $('page-first').disabled=start===1;$('page-next').disabled=!rawNext;
  }catch(e){message(e.message);}
}
async function selectProject(code,sheetKey,methodKey,draftRows){
  if(busy)return;project=catalog.find(item=>item.code===code);selectedSheet=null;clearView();
  document.querySelectorAll('#projects button').forEach(button=>button.classList.toggle('active',button.dataset.code===code));
  $('project-select').value=project?.code||'';$('project-select').disabled=!catalog.length;$('sheet-select').disabled=!project;
  $('write-target').hidden=!project;$('write-receipt').hidden=true;$('write-target-path').textContent=project?.workbook_path||'尚未获取工作副本路径';
  renderSource();
  $('download-workbook').hidden=!project;
  if(project)$('download-workbook').href='/api/workbook/'+encodeURIComponent(project.code)+'/download?month='+encodeURIComponent(selectedMonth);
  if(!project){$('methods').innerHTML='';$('title').textContent='请关联项目总文件夹';$('browse-note').hidden=true;$('sheet-select').innerHTML='<option>尚无工作表</option>';showWorkspace('manage');return;}
  $('methods').innerHTML=(project.sheets||[]).map(item=>`<button data-key="${escape(item.key)}">${escape(item.name)}</button>`).join('');
  $('sheet-select').innerHTML=project.sheets.map(item=>`<option value="${escape(item.key)}">${escape(item.name)}</option>`).join('');
  $('methods').querySelectorAll('button').forEach(button=>button.onclick=()=>selectSheet(button.dataset.key));
  const first=project.sheets.find(item=>item.key===sheetKey)||project.sheets[0];
  if(project.source_error){selectedSheet=first||null;$('title').textContent=project.name;showWorkspace('manage');message('关联原文件暂不可用：'+project.source_error+'。请在当前项目关联入口核对或重新指定路径。');return;}
  if(first)await selectSheet(first.key,1,methodKey,draftRows);else{$('title').textContent=project.name;message('此工作簿没有可显示的工作表。');}
}
function showWriteReceipt(data){
  const written=data.rows||[],sync=data.source_sync,path=sync?.path||project?.source_link?.path;
  $('write-receipt-text').textContent=sync?.state==='synced'?`已写入原文件：${path} · ${data.sheet||selectedSheet?.name||''}${written.length?` · 第 ${written[0]}–${written.at(-1)} 行`:''}；服务器备份已保留。`:`原文件尚未写入，请先关联原文件并重新预览。${written.length?`预览行：${written[0]}–${written.at(-1)}`:''}`;
  $('write-receipt-download').href=data.download||$('download-workbook').href;$('write-receipt').hidden=false;
}
function renderSource(){
  const link=project?.source_link;
  $('source-panel').hidden=!project;$('source-project-name').textContent=project?`${project.name} / ${project.code}`:'';
  $('source-path').value=link?.path||'';$('source-path').disabled=busy||!writable();
  $('source-link').disabled=busy||!project||!writable();$('source-unlink').hidden=!link;$('source-unlink').disabled=busy||!link||!writable();$('project-remove').disabled=busy||!project||!writable();
  $('project-remove-note').textContent=writable()?'删除当前月份的导入项目和关联；服务器原文件、历史记录及备份保留。':'历史月份只读；请切换到最新月份管理导入项目。';
  $('source-status').textContent=project?.source_error?`关联原文件暂不可用：${project.source_error}。请核对路径和文件状态，或重新指定原文件路径；确认写入已停用。`:link?`已关联：${link.path}。录入、预览和确认写入以此文件为准。`:'此项目是上传副本，尚未关联原文件。填写原文件路径后，本项目将按原文件读取和写入，不会新增重复项目。';
  $('write-target-path').textContent=link?.path||'尚未关联原文件';
  $('write-target-note').hidden=linked();
  $('write-target-note').textContent=project?.source_error?`原文件暂不可用：${project.source_error}。请核对或更换关联路径；不会改写工作副本来替代原文件写入。`:linked()?'':`上传副本：${project?.workbook_path||''}。请先关联服务器上的原文件，才能确认写入。`;
  $('link-source-shortcut').hidden=!project||linked();$('link-source-shortcut').textContent=project?.source_error?'核对关联路径':'关联原文件';
  $('link-source-shortcut').disabled=busy||!writable();
  $('write-target').classList.toggle('unlinked',!linked());
  $('commit').textContent='确认写入原文件';$('commit').disabled=busy||!writable()||!linked()||!previewId;
}
async function setSource(path){
  if(busy||!project||!writable())return;
  const code=project.code,key=selectedSheet?.key,methodKey=method?.key,savedRows=rows.map(row=>({...row}));
  const ok=await exclusive(async()=>{await api('/api/workbook/'+encodeURIComponent(code)+'/source',{path});await loadCatalog();});
  if(ok){await selectProject(code,key,methodKey,savedRows);message(path?'本项目已关联原文件，未新增项目；尚未修改原文件。录入数据已保留，请重新预览并确认写入原文件。':'已解除原文件关联；确认写入已停用。录入数据已保留。',true);}
}
$('source-form').onsubmit=event=>{event.preventDefault();const path=$('source-path').value.trim();if(!path){message('请填写服务器本机的 .xlsx 文件完整路径。');return;}return setSource(path);};
$('source-unlink').onclick=()=>{if(busy||!project?.source_link||!writable())return;if(confirm(`解除 ${project.name} / ${project.code} 的原文件关联？\n项目和工作簿副本保留，服务器原文件不会删除；解除后不能确认写入。`))return setSource(null);};
$('project-remove').onclick=async()=>{
  if(busy||!project||!writable())return;
  const code=project.code;
  if(!confirm(`删除导入项目 ${project.name} / ${code}（${catalogInfo.active_month}）？\n本月目录和原文件关联将移除；服务器原文件、历史记录及备份保留。未提交的录入将丢弃。`))return;
  const ok=await exclusive(async()=>{await api('/api/workbook/'+encodeURIComponent(code)+'/remove',{});await loadCatalog();});
  if(ok){await selectProject(catalog[0]?.code);showWorkspace('manage');message('已删除本月导入项目；原文件、历史记录及备份已保留。',true);}
};
function updateFormatState(){
  const disabled=busy||!writable()||!linked()||!workbookFingerprint;
  $('format-form').querySelectorAll('input,select,button').forEach(input=>input.disabled=disabled);
  $('format-decimals').disabled=disabled||!['number','percent'].includes($('format-number').value);
  $('format-text-color').disabled=disabled||!$('format-text-enabled').checked;
  $('format-fill-color').disabled=disabled||!$('format-fill-enabled').checked;
  $('format-save').disabled=disabled;
  if(!writable())$('format-status').textContent='历史月份只读，不能修改格式。';
  else if(!linked())$('format-status').textContent='请先关联原文件，再保存格式。';
}
for(const id of ['format-number','format-text-enabled','format-fill-enabled'])$(id).onchange=updateFormatState;
function formatRequest(){
  const range=$('format-range').value.trim().toUpperCase();
  if(!/^[A-Z]{1,3}[1-9][0-9]{0,6}(:[A-Z]{1,3}[1-9][0-9]{0,6})?$/.test(range))throw Error('请输入单元格或范围，例如 A1 或 A1:F10。');
  const request={sheet:selectedSheet.name,range,fingerprint:workbookFingerprint},type=$('format-number').value;
  if(type){
    if(['number','percent'].includes(type)){const count=Number($('format-decimals').value);if(!Number.isInteger(count)||count<0||count>8)throw Error('小数位数须为0至8。');request.number_format='0'+(count?'.'+'0'.repeat(count):'')+(type==='percent'?'%':'');}
    else request.number_format=type;
  }
  for(const [id,key] of [['format-font','font_name'],['format-align','alignment']])if($(id).value)request[key]=$(id).value;
  for(const [id,key,min,max] of [['format-size','font_size',6,72],['format-width','column_width',2,100],['format-height','row_height',8,200]])if($(id).value!==''){const value=Number($(id).value);if(!Number.isFinite(value)||value<min||value>max)throw Error(key==='font_size'?'字号须为6至72磅。':key==='row_height'?'行高须为8至200磅。':'列宽须为2至100个Excel字符。');request[key]=value;}
  if($('format-bold').value!=='')request.bold=$('format-bold').value==='true';
  if($('format-text-enabled').checked)request.font_color=$('format-text-color').value;
  if($('format-fill-enabled').checked)request.fill_color=$('format-fill-color').value;
  if(Object.keys(request).length===3)throw Error('请至少选择一项要修改的格式。');
  return request;
}
$('format-form').onsubmit=async event=>{
  event.preventDefault();if(busy||!selectedSheet||!writable()||!linked()||!workbookFingerprint)return;
  let request;try{request=formatRequest();}catch(error){message(error.message);return;}
  const key=selectedSheet.key,methodKey=method?.key,savedRows=rows.map(row=>({...row})),start=baseView?.start_row||1;
  const ok=await exclusive(async()=>{await api('/api/workbook/'+encodeURIComponent(project.code)+'/format',request);});
  if(ok){await selectSheet(key,start,methodKey,savedRows);$('format-status').textContent='格式已保存到关联原文件。';message('格式已保存到原文件；未写入的录入数据已保留，原预览已失效，请重新计算。',true);}
  updateFormatState();
};
$('method-select').onchange=()=>{if(selectedSheet)selectSheet(selectedSheet.key,1,$('method-select').value);};
$('page-first').onclick=()=>{if(selectedSheet)selectSheet(selectedSheet.key,1);};
$('page-next').onclick=()=>{if(selectedSheet&&rawNext)selectSheet(selectedSheet.key,rawNext);};
$('calculate').onclick=async()=>{
  if(busy||!method||!writable())return;busy=true;$('calculate').disabled=true;$('calculate').textContent='正在生成预览…';$('action-state').textContent='正在生成预览';const version=inputVersion;
  try{const payload={project:project.code,method:method.key,entries:LabGrid.entries(rows,method.fields)},data=await api('/api/preview',payload);if(version!==inputVersion)throw Error('输入已变化，请重新计算');previewId=data.id;sheet(data.view);$('results').innerHTML=data.outputs.map((o,i)=>`<div class="result-card"><small>${escape(data.entries[i].date)} · ${escape(data.entries[i].batch)} · 第 ${i+1} 行</small><strong>${escape(o.display)}</strong><span>${escape(o.unit)}</span></div>`).join('');$('results').hidden=false;$('download').href=data.download;$('download').hidden=false;message(`全部 ${data.rows.length} 行${method.kind==='manual'?'已校验':'已计算'}，预览第 ${data.rows[0]}–${data.rows.at(-1)} 行。${linked()?'请核对绿色预览，确认后写入关联原文件。':'尚未关联原文件，请点击上方“关联原文件”；关联后重新预览即可写入。'}`,linked());$('sheet-status').textContent='预览已生成，尚未写入';$('action-state').textContent=linked()?'预览已就绪 · 确认写入原文件':'预览已就绪 · 请先关联原文件';}catch(e){invalidate();message(e.message);}finally{busy=false;$('calculate').disabled=false;$('calculate').textContent=previewLabel();renderSource();}
};
$('commit').onclick=async()=>{if(!previewId||busy||!writable()||!linked())return;$('write-receipt').hidden=true;busy=true;writing=true;document.querySelectorAll('input[data-key]').forEach(el=>el.readOnly=true);$('commit').disabled=true;try{const data=await api(`/api/commit/${previewId}`,{});message(data.message,true);showWriteReceipt(data);const info=await api(`/api/sheet/${project.code}/${method.key}`);baseView=info.view;workbookFingerprint=info.fingerprint||null;nextRow=info.next_row;rows=[emptyRow()];undo=[];selection=null;anchor=null;previewId=null;refresh();$('results').hidden=true;$('download').hidden=true;$('sheet-status').textContent='原文件写入成功，可录入下一批';$('action-state').textContent='已写入原文件 · 可继续录入';}catch(e){message(e.message);}finally{busy=false;writing=false;renderSource();document.querySelectorAll('input[data-key]').forEach(el=>el.readOnly=false);}};
function addRows(newBatch=false){if(!method||busy)return;const n=Number($('add-count').value);if(!Number.isInteger(n)||n<1||rows.length+n>LabGrid.maxRows){message('新增行数须为整数，合计最多200行');return;}const seed=LabGrid.labels(rows)[selection?.r1??rows.length-1]||emptyRow();const next=rows.map(r=>({...r})),batch=newBatch?LabGrid.series([seed.batch||''],1,'batch'):seed.batch;for(let i=0;i<n;i++)next.push({date:seed.date||today(),batch:batch===seed.batch&&newBatch?'':batch,notes:'',_requireBatch:newBatch&&i===0});changed(next);select(next.length-n,'batch');$('entry').querySelector(`input[data-index="${next.length-n}"][data-key="batch"]`)?.focus();}
$('parallel').onclick=()=>addRows(false);$('add-batch').onclick=()=>addRows(true);
$('remove-row').onclick=()=>{if(!method||busy||!selection)return;if(rows.length-(selection.r1-selection.r0+1)<1){message('至少保留一行测定');return;}const next=LabGrid.remove(rows,selection.r0,selection.r1);selection=null;anchor=null;changed(next);};
$('fill-down').onclick=()=>fill(Number($('fill-to').value)-1);$('fill-end').onclick=()=>fill(rows.length-1);
$('sample').onclick=()=>{if(!method||busy)return;changed(rows.map((r,i)=>({...r,date:r.date||today(),batch:r.batch||`PREVIEW-ONLY-${String(i+1).padStart(3,'0')}`,...Object.fromEntries(method.fields.map(f=>[f.key,f.sample]))})));};
$('reset').onclick=()=>{if(busy||!method)return;selection=null;anchor=null;changed([{date:today(),batch:'',notes:''}]);};
$('undo').onclick=()=>{if(busy||!method||!undo.length)return;rows=undo.pop();selection=null;anchor=null;invalidate();refresh();};
$('mobile-preview').onclick=()=>{$('sheet-scroll').scrollIntoView({behavior:'smooth',block:'center'});};
async function loadHistory(show=true){
  const data=await api('/api/history');$('history').hidden=!show;
  $('history').innerHTML=`<h3>预览与写入历史</h3><table><thead><tr><th>项目 / 方法</th><th>批号 / 选区</th><th>结果</th><th>目标行</th><th>状态</th></tr></thead><tbody>${data.map(r=>{const format=r.request.operation==='format';return `<tr><td>${escape(r.request.project)} / ${escape(format?'格式设置 · '+r.request.sheet:r.request.method)}</td><td>${escape(format?r.request.range:[...new Set(r.request.entries?.map(e=>e.batch)||[r.request.batch])].join(' / '))}</td><td>${(r.outputs||[]).map(o=>escape(o.display)+' '+escape(o.unit)).join(' / ')}</td><td>${r.rows?.length?`${r.rows[0]}–${r.rows.at(-1)}`:'—'}</td><td>${r.state==='done'?'已写入':r.state==='removed'?'项目已移除':r.state==='writing'?`待完成写入 <button class="retry-write" data-id="${escape(r.id)}" ${!writable()?'disabled':''}>重试完成写入</button>`:'仅预览'}</td></tr>`;}).join('')}</tbody></table>`;
  $('history').querySelectorAll('.retry-write').forEach(button=>button.onclick=()=>retryWrite(button.dataset.id));
}
async function retryWrite(id){
  if(busy||!writable())return;
  $('write-receipt').hidden=true;let result;
  const code=project?.code,key=selectedSheet?.key,methodKey=method?.key,savedRows=rows.map(row=>({...row})),start=baseView?.start_row||1,currentPreview=previewId===id;
  const ok=await exclusive(async()=>{result=await api('/api/commit/'+encodeURIComponent(id),{});});
  if(!ok)return;
  showWriteReceipt(result);
  if(project?.code===code&&selectedSheet?.key===key&&result.sheet===selectedSheet?.name)await selectSheet(key,start,methodKey,currentPreview&&result.rows?.length?undefined:savedRows);
  message(result.message||'写入已完成，未重复追加记录。',true);
  try{await loadHistory(true);}catch(error){message('写入已完成，但历史刷新失败：'+error.message);}
}
$('history-button').onclick=async()=>{if(busy)return;try{await loadHistory($('history').hidden);}catch(error){message(error.message);}};
function workbookStatus(){
  const code=$('project-code').value.trim();
  const exists=catalog.some(item=>item.code.toLowerCase()===code.toLowerCase());
  $('workbook-status').textContent=exists?'该项目已存在；请使用此项目的“关联原文件”，不要再次上传。':'支持 .xlsx，最大20 MB；上传只保存副本，不会关联电脑上的原文件。';
  $('import-workbook').disabled=busy||exists||!code||!$('project-name').value.trim()||!$('workbook-file').files.length||!writable();
  sourceFolderStatus();
}
function sourceFolderStatus(){
  $('source-folder-scan').disabled=busy||!$('source-folder-path').value.trim();
  $('source-folder-bind').hidden=!sourceFolderPreview;
  $('source-folder-bind').disabled=busy||sourceFolderPreview?.can_bind!==true;
}
function invalidateSourceFolder(){
  sourceFolderPreview=null;$('source-folder-results').hidden=true;$('source-folder-results').innerHTML='';sourceFolderStatus();
}
$('source-folder-path').addEventListener('input',()=>{invalidateSourceFolder();$('source-folder-status').textContent='路径已更改，请扫描后核对识别结果。';});
$('source-folder-form').onsubmit=async event=>{
  event.preventDefault();if(busy)return;
  const path=$('source-folder-path').value.trim();if(!path)return;
  invalidateSourceFolder();
  $('source-folder-status').textContent='正在识别项目和月份…';
  const ok=await exclusive(async()=>{
    sourceFolderPreview=await api('/api/source-folder/scan',{path});
    const entries=sourceFolderPreview.entries||[],issues=sourceFolderPreview.issues||[];
    $('source-folder-results').innerHTML='<table><thead><tr><th>项目</th><th>月份</th><th>原文件</th><th>识别结果</th></tr></thead><tbody>'+entries.map(item=>`<tr><td>${escape(item.name)}<small>${escape(item.project)}</small></td><td>${escape(item.month)}</td><td>${escape(item.path)}</td><td>${escape(item.message||({import:'待导入关联',link:'待关联',relink:'待迁移关联',linked:'已关联',conflict:'冲突'}[item.state])||item.state)}</td></tr>`).join('')+'</tbody></table>'+(issues.length?'<ul class="folder-issues">'+issues.map(issue=>`<li>${escape(issue)}</li>`).join('')+'</ul>':'');
    $('source-folder-results').hidden=false;
    $('source-folder-status').textContent=`识别到 ${entries.length} 个工作簿。`+(sourceFolderPreview.can_bind?'核对项目、月份和路径后确认关联；原文件保持原样。':'请处理识别问题后重新扫描。');
  });
  if(!ok)$('source-folder-status').textContent='扫描未完成，请核对路径及上方错误后重试。';
};
$('source-folder-bind').onclick=async()=>{
  if(busy||sourceFolderPreview?.can_bind!==true)return;
  const request={path:sourceFolderPreview.root,fingerprint:sourceFolderPreview.fingerprint};
  let result;
  const ok=await exclusive(async()=>{
    result=await api('/api/source-folder/bind',request);selectedMonth=result.month;sessionStorage.setItem('labwork-month',selectedMonth);await loadCatalog();
  });
  if(ok){await selectProject(catalog[0]?.code);showWorkspace('entry');message(`总文件夹已关联：新增 ${result.imported} 个，关联 ${result.linked} 个，已有 ${result.skipped} 个。原文件未修改，可选择项目和月份开始录入。`,true);}
  else{invalidateSourceFolder();$('source-folder-status').textContent=result?'关联已完成，但目录刷新失败，请刷新页面核对。':'关联未完成，请重新扫描并核对。';}
};
for(const id of ['project-code','project-name','workbook-file'])$(id).addEventListener('input',workbookStatus);
$('workbook-file').addEventListener('change',()=>{const file=$('workbook-file').files[0];if(!file)return;const name=file.name.replace(/\.xlsx$/i,'');if(!$('project-name').value.trim())$('project-name').value=name;if(!$('project-code').value.trim()&&/^[A-Za-z0-9_-]{1,32}$/.test(name))$('project-code').value=name;workbookStatus();});
async function loadCatalog(){
  invalidateMonthPreview(true);
  invalidateSourceFolder();
  let data;try{data=await api('/api/catalog');}catch(error){if(!selectedMonth||error.message!=='月份不存在，请刷新目录')throw error;selectedMonth='';sessionStorage.removeItem('labwork-month');data=await api('/api/catalog');}catalogInfo=data;catalog=data.projects;selectedMonth=data.active_month;sessionStorage.setItem('labwork-month',selectedMonth);
  $('instance-info').textContent='版本 '+data.version+' · 端口 '+data.port+' · 数据目录：'+data.data_directory;
  $('lan-info').textContent=data.lan_url?'局域网：'+data.lan_url:'局域网访问未开启';
  if(!$('source-folder-path').value.trim())$('source-folder-path').value=data.source_folder||'';
  $('projects').innerHTML=catalog.map(item=>`<button data-code="${escape(item.code)}" title="${escape(item.name)}"><span class="project-label"><span class="project-name">${escape(item.name)}</span><small>${escape(item.code)} · ${item.source_link?'已关联':'待关联'}</small></span><span class="project-count">${item.sheets.length} 表</span></button>`).join('')||'<p class="empty-projects">尚无项目，请在“工作簿管理”中关联总文件夹。</p>';
  $('project-select').innerHTML=catalog.map(item=>`<option value="${escape(item.code)}">${escape(item.name)} · ${escape(item.code)}</option>`).join('')||'<option>尚无项目</option>';
  $('projects').querySelectorAll('button').forEach(button=>button.onclick=()=>selectProject(button.dataset.code));
  const months=[...new Set([...(data.months||[]),data.active_month].filter(Boolean))].sort().reverse();
  $('month-select').innerHTML=months.map(month=>`<option value="${escape(month)}" ${month===data.active_month?'selected':''}>${escape(month)}</option>`).join('');
  $('month-status').textContent=writable()?'可录入':'历史只读';
  if(!$('new-month').value)$('new-month').value=data.current_month||today().slice(0,7);
  workbookStatus();
}
async function exclusive(action){
  if(busy)return false;
  invalidateMonthPreview(true);
  busy=true;writing=true;invalidate();
  const controls=[...document.querySelectorAll('button,input,select')].map(el=>[el,el.disabled]);
  controls.forEach(([el])=>el.disabled=true);
  try{await action();return true;}catch(e){message(e.message);return false;}
  finally{busy=false;writing=false;controls.forEach(([el,disabled])=>el.disabled=disabled);workbookStatus();renderSource();$('month-preview-confirm').disabled=monthPreviewBusy||monthPreviewBlocked||monthPreview?.can_create!==true;}
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
  if(ok){$('import-panel').open=false;await selectProject(code);showWorkspace('entry');message('副本已上传，工作表目录已生成；请点击“关联原文件”后再确认写入。',true);}
};
async function changeMonth(month){
  const code=project?.code,key=selectedSheet?.key;
  if(await exclusive(async()=>{const result=await api('/api/month',{month});selectedMonth=result.month;sessionStorage.setItem('labwork-month',selectedMonth);await loadCatalog();})){await selectProject(catalog.some(item=>item.code===code)?code:catalog[0]?.code,key);}
  else $('month-select').value=catalogInfo.active_month;
}
$('month-select').onchange=()=>changeMonth($('month-select').value);
function invalidateMonthPreview(close=false,reset=true){
  monthPreview=null;monthPreviewRequest++;monthPreviewBusy=false;monthPreviewBlocked=false;$('month-preview-confirm').disabled=true;
  if(reset){monthAdjustments=[];monthSelection=null;monthAnchor=null;monthFocus={};$('month-preview-mode').value='candidate';}
  if(close&&$('month-preview').open)$('month-preview').close();
}
function monthPoint(ref){const match=/^([A-Z]+)([1-9][0-9]*)$/.exec(ref);return match?{ref,row:+match[2],col:[...match[1]].reduce((value,char)=>value*26+char.charCodeAt(0)-64,0)}:null;}
function selectMonthCell(ref,extend=false){
  const point=monthPoint(ref);if(!point||!monthPreview||monthPreviewBusy)return;
  if(!extend||!monthAnchor)monthAnchor=point;
  monthSelection={r0:Math.min(monthAnchor.row,point.row),r1:Math.max(monthAnchor.row,point.row),c0:Math.min(monthAnchor.col,point.col),c1:Math.max(monthAnchor.col,point.col)};
  const first=(monthAnchor.col<=point.col?monthAnchor.ref:point.ref).replace(/\d+$/,'')+monthSelection.r0,last=(monthAnchor.col>=point.col?monthAnchor.ref:point.ref).replace(/\d+$/,'')+monthSelection.r1;
  $('month-preview-range').value=first===last?first:first+':'+last;
  markMonthSelection();
}
function markMonthSelection(){
  $('month-preview-sheet').querySelectorAll('td[data-ref]').forEach(td=>{const point=monthPoint(td.dataset.ref);td.classList.toggle('template-selected',!!monthSelection&&point.row>=monthSelection.r0&&point.row<=monthSelection.r1&&point.col>=monthSelection.c0&&point.col<=monthSelection.c1);});
}
function showMonthCell(ref){
  if(!monthPreview)return;
  const original=monthPreview.original_view.rows.flatMap(row=>row.cells).find(cell=>cell.ref===ref),candidate=monthPreview.view.rows.flatMap(row=>row.cells).find(cell=>cell.ref===ref);
  const value=cell=>cell?.formula?'='+cell.formula:cell?.value||'（空白）';
  $('month-preview-cell').textContent=`${ref} · 原值：${value(original)} → 新模板：${value(candidate)}`;
}
function renderMonthTemplate(){
  if(!monthPreview)return;
  const original=$('month-preview-mode').value==='original';
  sheet(original?monthPreview.original_view:monthPreview.view,false,$('month-preview-sheet'));
  const before=new Map(monthPreview.original_view.rows.flatMap(row=>row.cells).map(cell=>[cell.ref,cell]));
  const cleared=new Set(monthPreview.view.rows.flatMap(row=>row.cells).filter(cell=>!cell.formula&&before.get(cell.ref)?.raw!==cell.raw).map(cell=>cell.ref));
  $('month-preview-sheet').querySelectorAll('td[data-ref]').forEach(td=>{td.classList.toggle('cleared-cell',cleared.has(td.dataset.ref));if(cleared.has(td.dataset.ref))td.title+=` · 原值：${before.get(td.dataset.ref)?.value||'（空白）'}；新模板已清空`;});
  $('month-preview-comparison').textContent=`${original?'查看原表':'查看新模板'} · 本页 ${cleared.size} 个有值单元格已清空（绿色）；表头、格式与公式保留。`;
  markMonthSelection();
}
async function loadMonthPreview(code,sheetName,start=1){
  const month=$('new-month').value;if(busy)return;
  if(!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)){message('请选择有效月份。');return;}
  const previous=monthPreview,previousFocus=monthFocus;
  const requestId=++monthPreviewRequest;monthPreview=null;monthPreviewBusy=true;
  if(!$('month-preview').open)$('month-preview').showModal();
  $('month-preview-title').textContent=`${month} 空白模板预览`;
  $('month-preview-status').textContent='正在生成空白模板预览…';$('month-preview-status').className='notice';
  $('month-preview-retry').hidden=true;
  if(!previous){$('month-preview-issues').hidden=true;$('month-preview-issues').textContent='';$('month-preview-sheet-state').textContent='';$('month-preview-comparison').textContent='';}
  if(!previous){$('month-preview-project').innerHTML='';$('month-preview-sheet-select').innerHTML='';$('month-preview-target').textContent='';$('month-preview-page').textContent='';$('month-preview-sheet').innerHTML='';}
  $('month-preview-cell').textContent='点击单元格对比原值与新模板；Shift＋点击或拖动可选择区域';
  if(monthFocus.code!==code||monthFocus.sheet!==sheetName){monthSelection=null;monthAnchor=null;$('month-preview-range').value='';}
  monthFocus={code,sheet:sheetName,start};
  for(const id of ['month-preview-project','month-preview-sheet-select','month-preview-first','month-preview-next','month-preview-confirm','month-preview-range','month-preview-clear','month-preview-keep','month-preview-undo','month-preview-mode'])$(id).disabled=true;
  try{
    const data=await api('/api/month/preview',{month,...(code?{project:code}:{}),...(sheetName?{sheet:sheetName}:{}),start,adjustments:monthAdjustments});
    if(requestId!==monthPreviewRequest||month!==$('new-month').value)return;
    monthPreview=data;monthPreviewBlocked=false;monthAdjustments=(data.adjustments||monthAdjustments).map(item=>({...item}));monthFocus={code:data.project,sheet:data.sheet,start:data.view.start_row||start};
    $('month-preview-title').textContent=`${data.month} 空白模板预览`;
    $('month-preview-status').textContent=`继承 ${data.previous} · ${data.projects.length} 个项目；已自动生成空白候选，未识别的数据表保留原样。核对表格后确认；预览不会写入文件。`;
    $('month-preview-project').innerHTML=data.projects.map(item=>`<option value="${escape(item.code)}" ${item.code===data.project?'selected':''}>${escape(item.name)} · ${escape(item.code)}</option>`).join('');
    const item=data.projects.find(item=>item.code===data.project);
    $('month-preview-sheet-select').innerHTML=(item?.sheets||[]).map(name=>`<option value="${escape(name)}" ${name===data.sheet?'selected':''}>${escape(name)}</option>`).join('');
    $('month-preview-target').textContent=item?.source_path?'新月份原文件：'+item.source_path:catalog.find(project=>project.code===data.project)?.source_link?'原文件已关联；新月份目标路径需核对，请查看下方提示。':'此项目尚未关联原文件；新月份仅生成平台工作簿副本。';
    const state=item?.sheet_states?.find(item=>item.sheet===data.sheet);
    const directory=data.sheet.trim()==='目录';
    $('month-preview-sheet-state').textContent=directory?'目录完整保留。':state?.message||(state?.state==='keep'?'此表未识别出检测数据区，默认完整保留。':'已自动识别检测数据区并清空输入。');
    $('month-preview-sheet-state').className=state?.state==='keep'?'notice preserved-sheet':'card-note';
    $('month-preview-issues').textContent=(data.issues||[]).join('；');$('month-preview-issues').hidden=!data.issues?.length;
    renderMonthTemplate();
    $('month-preview-page').textContent=`第 ${data.view.start_row||start} 行起${data.view.truncated?' · 分页浏览':''}${data.view.columns_truncated?' · 前200列':''}`;
    $('month-preview-first').disabled=(data.view.start_row||start)===1;$('month-preview-next').disabled=!data.view.next_row;
    for(const id of ['month-preview-project','month-preview-sheet-select','month-preview-range','month-preview-clear','month-preview-keep','month-preview-mode'])$(id).disabled=false;
    $('month-preview-clear').disabled=directory;$('month-preview-keep').disabled=directory;
    $('month-preview-undo').disabled=!monthAdjustments.length;$('month-preview-confirm').disabled=data.can_create!==true;
    $('month-preview-retry').hidden=data.can_create===true;$('month-preview-retry').disabled=false;
    return true;
  }catch(error){if(requestId===monthPreviewRequest){
    monthPreview=previous;monthPreviewBlocked=true;
    if(previous){monthFocus=previousFocus;monthAdjustments=(previous.adjustments||[]).map(item=>({...item}));$('month-preview-project').value=previous.project;$('month-preview-sheet-select').value=previous.sheet;for(const id of ['month-preview-project','month-preview-sheet-select','month-preview-range','month-preview-clear','month-preview-keep','month-preview-mode'])$(id).disabled=false;$('month-preview-clear').disabled=previous.sheet.trim()==='目录';$('month-preview-keep').disabled=previous.sheet.trim()==='目录';}
    $('month-preview-status').textContent=error.message+(previous?'；已保留上次有效预览，失败的调整未保存。':'')+' 请核对后重新预览。';$('month-preview-status').className='notice preview-error';$('month-preview-retry').hidden=false;$('month-preview-retry').disabled=false;$('month-preview-undo').disabled=!monthAdjustments.length;
  }return false;}
  finally{if(requestId===monthPreviewRequest)monthPreviewBusy=false;}
}
$('new-month').addEventListener('input',()=>invalidateMonthPreview(true));
$('create-month').onclick=()=>{invalidateMonthPreview();return loadMonthPreview(project?.code,selectedSheet?.name);};
$('month-preview-project').onchange=()=>loadMonthPreview($('month-preview-project').value);
$('month-preview-sheet-select').onchange=()=>loadMonthPreview($('month-preview-project').value,$('month-preview-sheet-select').value);
$('month-preview-first').onclick=()=>{if(monthPreview&&!monthPreviewBusy)return loadMonthPreview(monthPreview.project,monthPreview.sheet,1);};
$('month-preview-next').onclick=()=>{if(monthPreview?.view.next_row&&!monthPreviewBusy)return loadMonthPreview(monthPreview.project,monthPreview.sheet,monthPreview.view.next_row);};
$('month-preview-mode').onchange=renderMonthTemplate;
$('month-preview-retry').onclick=()=>loadMonthPreview(monthFocus.code,monthFocus.sheet,monthFocus.start||1);
async function adjustMonthTemplate(operation){
  if(monthPreviewBusy||!monthPreview||monthPreview.sheet.trim()==='目录')return;
  const range=$('month-preview-range').value.trim().toUpperCase();
  if(!/^[A-Z]{1,3}[1-9][0-9]{0,6}(:[A-Z]{1,3}[1-9][0-9]{0,6})?$/.test(range)){ $('month-preview-cell').textContent='请点击或拖选单元格，也可填写范围，例如 A4:B10。';return; }
  if(monthAdjustments.length>=100){$('month-preview-cell').textContent='最多调整100个区域；可先撤销上一项调整。';return;}
  monthAdjustments.push({project:monthPreview.project,sheet:monthPreview.sheet,range,operation});$('month-preview-mode').value='candidate';renderMonthTemplate();
  return loadMonthPreview(monthFocus.code,monthFocus.sheet,monthFocus.start);
}
$('month-preview-clear').onclick=()=>adjustMonthTemplate('clear');$('month-preview-keep').onclick=()=>adjustMonthTemplate('keep');
$('month-preview-undo').onclick=()=>{if(monthPreviewBusy||!monthAdjustments.length)return;monthAdjustments.pop();return loadMonthPreview(monthFocus.code,monthFocus.sheet,monthFocus.start||1);};
$('month-preview-sheet').addEventListener('pointerdown',event=>{if(event.pointerType!=='mouse'||event.button!==0||monthPreviewBusy)return;const cell=event.target.closest('td[data-ref]');if(!cell)return;event.preventDefault();monthDrag=true;monthDragged=false;selectMonthCell(cell.dataset.ref,event.shiftKey);});
$('month-preview-sheet').addEventListener('pointermove',event=>{if(!monthDrag||event.buttons!==1)return;const cell=event.target.closest('td[data-ref]');if(cell){monthDragged=true;selectMonthCell(cell.dataset.ref,true);showMonthCell(cell.dataset.ref);}});
document.addEventListener('pointerup',()=>{monthDrag=false;});
$('month-preview-close').onclick=()=>{invalidateMonthPreview(true);};
$('month-preview').addEventListener('cancel',event=>{if(busy){event.preventDefault();return;}invalidateMonthPreview();});
$('month-preview-confirm').onclick=async()=>{
  if(busy||monthPreviewBusy||monthPreviewBlocked||!monthPreview||monthPreview.can_create!==true||monthPreview.month!==$('new-month').value)return;
  const {month,fingerprint}=monthPreview,adjustments=monthAdjustments.map(item=>({...item})),code=project?.code;
  busy=true;monthPreviewBusy=true;$('month-preview-confirm').disabled=true;$('month-preview-close').disabled=true;
  for(const id of ['month-preview-project','month-preview-sheet-select','month-preview-clear','month-preview-keep','month-preview-undo','month-preview-range','month-preview-mode','month-preview-first','month-preview-next'])$(id).disabled=true;
  let created=false;
  try{
    const result=await api('/api/month',{month,fingerprint,adjustments});created=true;selectedMonth=result.month;sessionStorage.setItem('labwork-month',selectedMonth);invalidateMonthPreview(true);await loadCatalog();busy=false;
    await selectProject(catalog.some(item=>item.code===code)?code:catalog[0]?.code);showWorkspace('manage');message('已按预览创建 '+month+'，旧月份完整保留。',true);
  }catch(error){if(created)message('月份已创建，但目录刷新失败：'+error.message+'，请刷新页面。');else{monthPreviewBlocked=true;$('month-preview-status').textContent=error.message+' 请重新预览后再确认。';$('month-preview-status').className='notice preview-error';$('month-preview-retry').hidden=false;$('month-preview-retry').disabled=false;}}
  finally{busy=false;monthPreviewBusy=false;$('month-preview-close').disabled=false;$('month-preview-confirm').disabled=monthPreviewBlocked||monthPreview?.can_create!==true;}
};
(async()=>{try{await loadCatalog();await selectProject(catalog[0]?.code);}catch(e){message(e.message);}})();
