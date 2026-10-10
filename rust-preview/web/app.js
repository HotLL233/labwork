'use strict';
const $=id=>document.getElementById(id);
let catalog=[],catalogInfo={},selectedSheet=null,project,method,baseView,nextRow=4,rows=[],previewId=null,busy=false,writing=false,inputVersion=0,selection=null,anchor=null,drag=null,undo=[];
let sheetRequestId=0,rawNext=null,workbookFingerprint=null;
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
$('nav-entry').onclick=()=>showWorkspace('entry');$('nav-manage').onclick=()=>showWorkspace('manage');$('manage-shortcut').onclick=()=>{showWorkspace('manage');$('source-import-path').focus();};
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
  $('sheet').querySelectorAll('td[data-ref]').forEach(td=>{const cell=cellMap.get(td.dataset.ref);Object.assign(td.style,view.styles[cell.style]||{});td.addEventListener('click',()=>{$('sheet').querySelector('.selected')?.classList.remove('selected');td.classList.add('selected');$('cell-address').textContent=cell.ref;$('formula-value').value=cell.formula?'='+cell.formula:cell.raw;if(!busy)$('format-range').value=cell.ref;});});
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
  $('sheet-select').value=key;$('calculate').textContent=previewLabel();$('action-title').textContent=method?.kind==='manual'?'手工录入与写入':'计算与写入';$('sample').hidden=!method?.fields.some(field=>field.sample);
  $('title').textContent=selectedSheet.name;$('breadcrumb').textContent=`${project.name} / ${code}`;
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
  $('project-select').value=project?.code||'';$('rules-project-name').textContent=project?.name||'';
  $('write-target').hidden=!project;$('write-receipt').hidden=true;$('write-target-path').textContent=project?.workbook_path||'尚未获取工作副本路径';
  renderSource();
  $('rules-panel').hidden=!project;$('download-workbook').hidden=!project;
  if(project)$('download-workbook').href='/api/workbook/'+encodeURIComponent(project.code)+'/download?month='+encodeURIComponent(selectedMonth);
  if(!project){$('methods').innerHTML='';$('title').textContent='请关联服务器工作簿';$('breadcrumb').textContent='工作簿';$('import-panel').open=true;$('sheet-select').innerHTML='<option>尚无工作表</option>';showWorkspace('manage');return;}
  $('methods').innerHTML=(project.sheets||[]).map(item=>`<button data-key="${escape(item.key)}">${escape(item.name)}</button>`).join('');
  $('sheet-select').innerHTML=project.sheets.map(item=>`<option value="${escape(item.key)}">${escape(item.name)}</option>`).join('');
  $('methods').querySelectorAll('button').forEach(button=>button.onclick=()=>selectSheet(button.dataset.key));
  renderRules();const first=project.sheets.find(item=>item.key===sheetKey)||project.sheets[0];
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
  $('source-link').disabled=busy||!project||!writable();$('source-unlink').disabled=busy||!link||!writable();
  $('source-status').textContent=project?.source_error?`关联原文件暂不可用：${project.source_error}。请核对路径和文件状态，或重新指定原文件路径；确认写入已停用。`:link?`已关联：${link.path}。录入、预览和确认写入以此文件为准。`:'此项目是上传副本，尚未关联原文件。填写原文件路径后，本项目将按原文件读取和写入，不会新增重复项目。';
  $('write-target-path').textContent=link?.path||'尚未关联原文件';
  $('write-target-note').textContent=project?.source_error?`原文件暂不可用：${project.source_error}。请核对或更换关联路径；不会改写工作副本来替代原文件写入。`:link?'确认写入将追加本次计算或手工录入结果到上述原文件；自动备份并检查冲突。':`上传副本：${project?.workbook_path||''}。请先关联服务器上的原文件，才能确认写入。`;
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
$('source-unlink').onclick=()=>setSource(null);
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
  $('history').innerHTML=`<h3>预览与写入历史</h3><table><thead><tr><th>项目 / 方法</th><th>批号 / 选区</th><th>结果</th><th>目标行</th><th>状态</th></tr></thead><tbody>${data.map(r=>{const format=r.request.operation==='format';return `<tr><td>${escape(r.request.project)} / ${escape(format?'格式设置 · '+r.request.sheet:r.request.method)}</td><td>${escape(format?r.request.range:[...new Set(r.request.entries?.map(e=>e.batch)||[r.request.batch])].join(' / '))}</td><td>${(r.outputs||[]).map(o=>escape(o.display)+' '+escape(o.unit)).join(' / ')}</td><td>${r.rows?.length?`${r.rows[0]}–${r.rows.at(-1)}`:'—'}</td><td>${r.state==='done'?'已写入':r.state==='writing'?`待完成写入 <button class="retry-write" data-id="${escape(r.id)}" ${!writable()?'disabled':''}>重试完成写入</button>`:'仅预览'}</td></tr>`;}).join('')}</tbody></table>`;
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
  sourceImportStatus();
}
function sourceImportStatus(){
  const code=$('source-import-code').value.trim(),exists=catalog.some(item=>item.code.toLowerCase()===code.toLowerCase());
  $('source-import-status').textContent=exists?'该项目已存在；选择左侧该项目后，使用下方“关联当前项目原文件”，不会新增重复项目。':'由你填写服务器本机现有文件的绝对路径；建立项目并关联同一原文件，确认写入时追加结果。';
  $('source-import-submit').disabled=busy||!writable()||exists||!code||!$('source-import-name').value.trim()||!$('source-import-path').value.trim();
}
for(const id of ['source-import-code','source-import-name','source-import-path'])$(id).addEventListener('input',sourceImportStatus);
$('source-import-form').onsubmit=async event=>{
  event.preventDefault();if(busy||!writable())return;
  const code=$('source-import-code').value.trim(),name=$('source-import-name').value.trim(),path=$('source-import-path').value.trim();
  if(!code||!name||!path)return;
  if(catalog.some(item=>item.code.toLowerCase()===code.toLowerCase())){message('该项目已存在；请选择这个项目并关联原文件，不需要再次导入。');return;}
  const ok=await exclusive(async()=>{await api('/api/workbook/'+encodeURIComponent(code)+'/source-import',{path,name});await loadCatalog();$('source-import-form').reset();});
  if(ok){await selectProject(code);showWorkspace('entry');message('工作簿已由你指定的路径关联，目录已生成；计算或手工结果将在确认后写入原文件。',true);}
};
for(const id of ['project-code','project-name','workbook-file'])$(id).addEventListener('input',workbookStatus);
$('workbook-file').addEventListener('change',()=>{const file=$('workbook-file').files[0];if(!file)return;const name=file.name.replace(/\.xlsx$/i,'');if(!$('project-name').value.trim())$('project-name').value=name;if(!$('project-code').value.trim()&&/^[A-Za-z0-9_-]{1,32}$/.test(name))$('project-code').value=name;workbookStatus();});
async function loadCatalog(){
  let data;try{data=await api('/api/catalog');}catch(error){if(!selectedMonth||error.message!=='月份不存在，请刷新目录')throw error;selectedMonth='';sessionStorage.removeItem('labwork-month');data=await api('/api/catalog');}catalogInfo=data;catalog=data.projects;selectedMonth=data.active_month;sessionStorage.setItem('labwork-month',selectedMonth);
  $('instance-info').textContent='版本 '+data.version+' · 端口 '+data.port+' · 数据目录：'+data.data_directory;
  $('lan-info').textContent=data.lan_url?'局域网：'+data.lan_url:'局域网访问未开启';
  $('projects').innerHTML=catalog.map(item=>`<button data-code="${escape(item.code)}" title="${escape(item.name)}"><span class="project-label"><span class="project-name">${escape(item.name)}</span><small>${escape(item.code)} · ${item.source_link?'已关联':'待关联'}</small></span><span class="project-count">${item.sheets.length} 表</span></button>`).join('')||'<p class="empty-projects">尚无项目，请在“工作簿管理”中关联服务器工作簿。</p>';
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
  if(ok){$('import-panel').open=false;await selectProject(code);showWorkspace('entry');message('副本已上传，工作表目录已生成；请点击“关联原文件”后再确认写入。',true);}
};
function renderRules(){
  const stored=project.monthly_rules||catalogInfo.monthly_rules?.[project.code]||[];
  $('month-rules').innerHTML='<table><thead><tr><th>工作表</th><th>数据起始行</th><th>清空输入列</th><th>确认</th></tr></thead><tbody>'+project.sheets.map((item,index)=>{
    const rule=stored.find(rule=>rule.sheet===item.name)||{};
    return `<tr data-rule="${index}"><td>${escape(item.name)}</td><td><input aria-label="${escape(item.name)} 数据起始行" type="number" min="3" max="1048576" data-rule-start value="${rule.start_row||''}" placeholder="例如 4"></td><td><input aria-label="${escape(item.name)} 清空输入列" data-rule-columns value="${escape((rule.columns||[]).join(','))}" placeholder="留空则整表保留"></td><td><input type="checkbox" data-rule-confirm aria-label="确认 ${escape(item.name)} 规则" ${project.rules_confirmed?'checked':''}></td></tr>`;
  }).join('')+'</tbody></table>';
  $('save-rules').disabled=!writable();$('month-rules').querySelectorAll('input').forEach(input=>input.disabled=!writable());
}
$('save-rules').onclick=async()=>{
  if(!project||busy||!writable())return;
  const rules=[];
  for(const row of $('month-rules').querySelectorAll('[data-rule]')){
    if(!row.querySelector('[data-rule-confirm]').checked){message('请核对并勾选每个工作表的规则；未确认时不会生成空白月模板。');return;}
    const start=Number(row.querySelector('[data-rule-start]').value),columns=row.querySelector('[data-rule-columns]').value.toUpperCase().split(/[,，\s]+/).filter(Boolean);
    if(!Number.isInteger(start)||start<3||start>1048576||columns.some(column=>!/^([A-Z]{1,3})$/.test(column))){message('起始行须为第3行或之后，清空列填写字母（例如 A,B,C）；留空保留整表。');return;}
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
