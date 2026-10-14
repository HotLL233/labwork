// Independent headless Edge profile; only use an isolated test server/data directory.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const {chromium}=require('C:/labwork/tmp/labwork-ui-qa/node_modules/playwright');
const [base,outputArgument]=process.argv.slice(2);
if(!base||!outputArgument)throw Error('Usage: node tools/ui_smoke.cjs <isolated base URL> <output directory>');
const output=path.resolve(outputArgument);
const password=process.env.LABWORK_QA_PASSWORD;
const sourcePath=process.env.LABWORK_QA_SOURCE;
if(!password)throw Error('Set LABWORK_QA_PASSWORD for the isolated test server');
if(!sourcePath)throw Error('Set LABWORK_QA_SOURCE to an isolated disposable workbook copy for source-write checks');
const sourceBefore=fs.readFileSync(sourcePath);
const executable=process.env.LABWORK_QA_EXE;
if(!executable)throw Error('Set LABWORK_QA_EXE to the tested release executable');
function filesUnder(directory){if(!fs.existsSync(directory))return[];return fs.readdirSync(directory,{withFileTypes:true}).flatMap(item=>{const full=path.join(directory,item.name);return item.isDirectory()?[full,...filesUnder(full)]:[full];}).sort();}
fs.mkdirSync(output,{recursive:true});
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const errors=[];
  try{
    const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1});
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto(base+'/login');
    assert.equal(await page.locator('#username').inputValue(),'labwork');
    assert.equal(await page.locator('#username').getAttribute('autocomplete'),'username');
    assert.equal(await page.locator('#password').getAttribute('autocomplete'),'current-password');
    assert.equal(await page.locator('#login-form').getAttribute('method'),'post');
    assert(await page.locator('#remember').isChecked());
    await page.screenshot({path:path.join(output,'login-390.png'),fullPage:true});
    await page.locator('#password').fill(password);
    await Promise.all([page.waitForURL(base+'/'),page.locator('#login-submit').click()]);
    await page.waitForFunction(()=>document.querySelector('#project-select').options.length>0);
    let catalog=await (await page.request.get(base+'/api/catalog')).json();
    if(!catalog.projects.some(project=>project.code==='EF')){
      await page.locator('#nav-manage').click();
      if(!(await page.locator('#import-form').isVisible()))await page.locator('#import-panel > summary').click();
      await page.locator('#workbook-file').setInputFiles(sourcePath);
      await page.locator('#project-code').fill('EF');await page.locator('#project-name').fill('EF隔离检测');
      const [upload]=await Promise.all([page.waitForResponse(response=>response.url().includes('/api/workbook/EF?')&&response.request().method()==='POST'),page.locator('#import-workbook').click()]);
      assert.equal(upload.status(),200,upload.status()===200?'':await upload.text());
      await page.waitForFunction(()=>document.querySelector('#project-select').value==='EF');
      catalog=await (await page.request.get(base+'/api/catalog')).json();
    }
    const project=catalog.projects.find(project=>project.code==='EF');
    assert(project,'isolated fixture must include imported EF workbook');
    const method=project.methods.find(method=>method.kind==='workbook_formula'&&method.sheet.trim()==='二氟乙酸钠')||project.methods.find(method=>method.kind==='workbook_formula')||project.methods.find(method=>method.kind!=='manual');
    assert(method,'EF must have a usable formula method');
    const sheet=project.sheets.find(sheet=>sheet.name.trim()===method.sheet.trim());
    assert(sheet,'EF method must map to an imported sheet');
    async function assertContinuousSheet(stage,view){
      if(!view){const response=await page.request.get(base+'/api/sheet/EF/'+method.key);assert.equal(response.status(),200);view=(await response.json()).view;}
      const expected=Array.from({length:view.last_row},(_,index)=>index+1);
      assert.deepEqual(view.rows.map(row=>row.number),expected,stage+' API');
      await page.waitForFunction(expected=>{const actual=[...document.querySelectorAll('#sheet tbody tr:not(.draft-row) .row-num')].map(node=>Number(node.textContent));return actual.length===expected.length&&actual.every((number,index)=>number===expected[index]);},expected);
      const numbers=await page.locator('#sheet').evaluate(table=>[...table.querySelectorAll('tbody tr:not(.draft-row) .row-num')].map(node=>Number(node.textContent)));
      assert.deepEqual(numbers,expected,stage+' DOM');
      assert(!(await page.locator('#sheet').textContent()).includes('中间历史记录已折叠'));
    }
    for(const width of [390,320,768,1440]){
      await page.setViewportSize({width,height:width<700?844:1000});
      await page.locator('#nav-entry').click();
      if(width<700){
        assert(await page.locator('#project-select').isVisible());
        await page.locator('#project-select').selectOption('EF');
        await page.locator('#sheet-select').selectOption(sheet.key);
      }else{
        await page.locator('#projects button[data-code="EF"]').click();
        await page.locator(`#methods button[data-key="${sheet.key}"]`).click();
      }
      await page.locator('#sheet-panel').waitFor({state:'visible'});
      if(await page.locator('#method-choice').isVisible())await page.locator('#method-select').selectOption(method.key);
      await page.locator('#sheet-panel').waitFor({state:'visible'});
      assert(!(await page.locator('#import-form').isVisible()),'import form must not occupy entry view');
      await assertContinuousSheet('original method view '+width);
      assert(await page.locator('#calculate').isVisible(),'calculate action must be visible');
      assert(await page.locator('#commit').isVisible(),'write action must be visible');
      assert(await page.locator('#calculate').isEnabled());
      assert(await page.locator('#write-target').isVisible());
      if(width===390){assert((await page.locator('#write-target-path').textContent()).includes('尚未关联'));assert(await page.locator('#link-source-shortcut').isVisible());assert(await page.locator('#format-save').isDisabled());}
      else assert.equal(await page.locator('#write-target-path').textContent(),sourcePath);
      const overflow=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
      assert(overflow.page<=overflow.viewport+1,JSON.stringify({width,...overflow}));
      await page.screenshot({path:path.join(output,`entry-${width}.png`),fullPage:true});
      await page.locator('#format-panel > summary').click();
      const formatOverflow=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1);
      assert(formatOverflow,'format settings must not overflow page');
      await page.screenshot({path:path.join(output,`format-${width}.png`),fullPage:true});
      await page.locator('#format-panel > summary').click();
      if(width===390){
        const panel='#mobile-entry';
        await page.locator(`${panel} input[data-key="batch"]`).first().fill('UI-QA-'+Date.now());
        const overrides=JSON.parse(process.env.LABWORK_QA_INPUTS||'{}');
        for(const field of method.fields){
          const value=String(overrides[field.key]??field.sample??'').trim()||(field.input_type==='text'?'QA':'1');
          await page.locator(`${panel} input[data-key="${field.key}"]`).first().fill(value);
        }
        {
          const [unlinkedPreview]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/preview')&&response.request().method()==='POST'),page.locator('#calculate').click()]);
          assert.equal(unlinkedPreview.status(),200,unlinkedPreview.status()===200?"":await unlinkedPreview.text());
          assert(await page.locator('#commit').isDisabled(),'unlinked preview must not enable original write');
          const unsubmittedBatch=await page.locator(`${panel} input[data-key="batch"]`).first().inputValue();
          await page.locator('#link-source-shortcut').click();
          assert((await page.locator('#source-project-name').textContent()).includes('/ EF'),'CTA must select current existing project');
          assert.equal(await page.locator('#source-path').inputValue(),'','association must be manually specified by user');
          await page.locator('#source-path').fill(sourcePath);
          const [linked]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/source')&&response.request().method()==='POST'),page.locator('#source-link').click()]);
          assert.equal(linked.status(),200,linked.status()===200?"":await linked.text());
          await page.waitForFunction(()=>document.querySelector('#source-status').textContent.includes('已关联'));
          assert.deepEqual(fs.readFileSync(sourcePath),sourceBefore,'linking must not modify source workbook');
          await page.locator('#nav-entry').click();
          assert.equal(await page.locator(`${panel} input[data-key="batch"]`).first().inputValue(),unsubmittedBatch,'linking must retain unsubmitted rows');
          assert(await page.locator('#commit').isDisabled(),'binding invalidates old preview');
          const afterLink=await (await page.request.get(base+'/api/catalog')).json();
          assert.equal(afterLink.projects.length,catalog.projects.length,'binding existing project must not add duplicate');
          assert.equal(await page.locator('#write-target-path').textContent(),sourcePath);
        }
        const [preview]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/preview')&&response.request().method()==='POST'),page.locator('#calculate').click()]);
        assert.equal(preview.status(),200,preview.status()===200?"":await preview.text());
        await page.waitForFunction(()=>!document.querySelector('#commit').disabled);
        await assertContinuousSheet('calculation preview',(await preview.json()).view);
        await page.screenshot({path:path.join(output,'preview-390.png'),fullPage:true});
        const batch=await page.locator(`${panel} input[data-key="batch"]`).first().inputValue();
        await page.locator('#format-panel > summary').click();
        await page.locator('#format-range').fill('C3:E3');
        await page.locator('#format-number').selectOption('number');
        await page.locator('#format-decimals').fill('3');
        await page.locator('#format-font').selectOption('Arial');
        await page.locator('#format-height').fill('28');
        const [formatted]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/format')&&response.request().method()==='POST'),page.locator('#format-save').click()]);
        assert.equal(formatted.status(),200,formatted.status()===200?"":await formatted.text());
        await page.waitForFunction(()=>document.querySelector('#format-status').textContent.includes('已保存'));
        await assertContinuousSheet('refresh after format save');
        assert.equal(await page.locator(`${panel} input[data-key="batch"]`).first().inputValue(),batch,'format save must retain unsaved entries');
        assert(await page.locator('#commit').isDisabled(),'format save must invalidate old preview');
        const formattedRow=await page.locator('#sheet').evaluate(table=>{const row=[...table.querySelectorAll('tbody tr')].find(row=>row.querySelector('.row-num')?.textContent==='3');return{height:parseFloat(row.style.height),font:row.querySelector('[data-ref="C3"]').style.fontFamily};});
        assert(Math.abs(formattedRow.height-28*4/3)<.1,'saved row height must reload');assert(formattedRow.font.includes('Arial'),'saved font must reload');
        await page.locator('#format-panel > summary').click();
        await page.locator('#format-range').fill('A4');
        await page.locator('#format-number').selectOption('yyyy/mm/dd');
        await page.locator('#format-size').fill('14');
        const [dateFormatted]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/format')&&response.request().method()==='POST'),page.locator('#format-save').click()]);
        assert.equal(dateFormatted.status(),200,dateFormatted.status()===200?"":await dateFormatted.text());
        await page.waitForFunction(()=>document.querySelector('#format-status').textContent.includes('已保存'));
        assert.match(await page.locator('#sheet td[data-ref="A4"]').textContent(),/^\d{4}\/\d{2}\/\d{2}$/,'saved date display must reload');
        const [repreview]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/preview')&&response.request().method()==='POST'),page.locator('#calculate').click()]);
        assert.equal(repreview.status(),200,repreview.status()===200?"":await repreview.text());
        const candidate=await repreview.json(),candidateBytes=Buffer.from(await (await page.request.get(base+candidate.download)).body());
        const beforeCalculatedWrite=fs.readFileSync(sourcePath);
        await page.waitForFunction(()=>!document.querySelector('#commit').disabled);
        const [commit]=await Promise.all([page.waitForResponse(response=>response.url().includes('/api/commit/')&&response.request().method()==='POST'),page.locator('#commit').click()]);
        assert.equal(commit.status(),200,commit.status()===200?"":await commit.text());
        await page.waitForFunction(()=>document.querySelector('#action-state').textContent.includes('已写入'));
        await assertContinuousSheet('refresh after confirmation');
        assert.match(await page.locator('#sheet td[data-ref="A4"]').textContent(),/^\d{4}\/\d{2}\/\d{2}$/,'confirmed append must retain custom date format');
        assert(await page.locator('#write-receipt').isVisible());
        assert((await page.locator('#write-receipt-text').textContent()).includes('已写入原文件：'+sourcePath));
        assert(!fs.readFileSync(sourcePath).equals(beforeCalculatedWrite),'confirmed calculated result must change original, not merely previous format saves');
        assert.deepEqual(fs.readFileSync(sourcePath),candidateBytes,'original file bytes must equal calculated preview after confirmation');
        await page.screenshot({path:path.join(output,'written-390.png'),fullPage:true});
        const manual=project.methods.find(method=>method.kind==='manual'&&method.fields.length);
        assert(manual,'EF must include a manual registration sheet');
        const manualSheet=project.sheets.find(sheet=>sheet.name.trim()===manual.sheet.trim());
        await page.locator('#sheet-select').selectOption(manualSheet.key);
        await page.locator('#sheet-panel').waitFor({state:'visible'});
        if(await page.locator('#method-choice').isVisible())await page.locator('#method-select').selectOption(manual.key);
        const manualBatch='UI-MANUAL-'+Date.now();
        await page.locator(`${panel} input[data-key="batch"]`).first().fill(manualBatch);
        for(const field of manual.fields)await page.locator(`${panel} input[data-key="${field.key}"]`).first().fill(field.input_type==='text'?'手工结果QA':'1.25');
        assert.equal(await page.locator('#calculate').textContent(),'预览写入');
        const [manualPreview]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/preview')&&response.request().method()==='POST'),page.locator('#calculate').click()]);
        assert.equal(manualPreview.status(),200,manualPreview.status()===200?"":await manualPreview.text());
        const manualCandidate=await manualPreview.json(),manualBytes=Buffer.from(await (await page.request.get(base+manualCandidate.download)).body()),beforeManualWrite=fs.readFileSync(sourcePath);
        const [manualCommit]=await Promise.all([page.waitForResponse(response=>response.url().includes('/api/commit/')&&response.request().method()==='POST'),page.locator('#commit').click()]);
        assert.equal(manualCommit.status(),200,manualCommit.status()===200?"":await manualCommit.text());
        assert(!fs.readFileSync(sourcePath).equals(beforeManualWrite),'manual input must change original');
        assert.deepEqual(fs.readFileSync(sourcePath),manualBytes,'original bytes must equal manual preview after confirmation');
        await page.waitForFunction(()=>document.querySelector('#action-state').textContent.includes('已写入原文件'));
        await page.screenshot({path:path.join(output,'manual-written-390.png'),fullPage:true});
      }
      await page.locator('#nav-manage').click();
      assert(await page.locator('#source-import-form').isVisible());
      assert(!(await page.locator('#import-form').isVisible()),'upload copy must remain secondary collapsed entry');
      assert(!(await page.locator('#calculate').isVisible()));
      const managed=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
      assert(managed.page<=managed.viewport+1,JSON.stringify({width,view:'manage',...managed}));
      await page.screenshot({path:path.join(output,`manage-${width}.png`),fullPage:true});
    }
    const newSource=path.join(path.dirname(sourcePath),'new-project-source.xlsx');fs.writeFileSync(newSource,sourceBefore);
    await page.locator('#source-import-path').fill(newSource);await page.locator('#source-import-code').fill('UI_NEW');await page.locator('#source-import-name').fill('UI新项目');
    const [sourceImport]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/source-import')&&response.request().method()==='POST'),page.locator('#source-import-submit').click()]);
    assert.equal(sourceImport.status(),200,sourceImport.status()===200?"":await sourceImport.text());
    await page.waitForFunction(()=>document.querySelector('#write-target-path').textContent.endsWith('new-project-source.xlsx'));
    assert.deepEqual(fs.readFileSync(newSource),sourceBefore,'new-project source association must not modify file');
    assert.equal((await (await page.request.get(base+'/api/catalog')).json()).projects.length,catalog.projects.length+1);
    await page.locator('#nav-manage').click();
    let promptText='';page.once('dialog',async dialog=>{promptText=dialog.message();await dialog.dismiss();});
    await page.locator('#source-unlink').click();
    assert(promptText.includes('原文件不会删除'));assert(await page.locator('#source-unlink').isVisible());
    page.once('dialog',dialog=>dialog.accept());
    const [unlink]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/source')&&response.request().method()==='POST'),page.locator('#source-unlink').click()]);
    assert.equal(unlink.status(),200,unlink.status()===200?"":await unlink.text());
    await page.waitForFunction(()=>document.querySelector('#source-unlink').hidden);
    let managedCatalog=await (await page.request.get(base+'/api/catalog')).json();
    assert(managedCatalog.projects.some(item=>item.code==='UI_NEW'&&!item.source_link),'unlink retains project');
    assert.deepEqual(fs.readFileSync(newSource),sourceBefore,'unlink never changes original');
    await page.locator('#source-path').fill(newSource);
    const [relink]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/source')&&response.request().method()==='POST'),page.locator('#source-link').click()]);
    assert.equal(relink.status(),200,relink.status()===200?"":await relink.text());await page.waitForFunction(()=>!document.querySelector('#source-unlink').hidden);
    if(!(await page.locator('#import-form').isVisible()))await page.locator('#import-panel > summary').click();
    await page.locator('#workbook-file').setInputFiles(newSource);await page.locator('#project-code').fill('UI_COPY');await page.locator('#project-name').fill('重复上传副本');
    const [copyImport]=await Promise.all([page.waitForResponse(response=>response.url().includes('/api/workbook/UI_COPY?')&&response.request().method()==='POST'),page.locator('#import-workbook').click()]);
    assert.equal(copyImport.status(),200,copyImport.status()===200?"":await copyImport.text());await page.waitForFunction(()=>document.querySelector('#project-select').value==='UI_COPY');
    await page.locator('#nav-manage').click();assert(!(await page.locator('#source-unlink').isVisible()),'unlinked upload should show delete action without unusable unlink');assert(await page.locator('#project-remove').isEnabled());
    const historyBefore=await (await page.request.get(base+'/api/history')).json();
    page.once('dialog',async dialog=>{promptText=dialog.message();await dialog.dismiss();});await page.locator('#project-remove').click();
    assert(promptText.includes('UI_COPY')&&promptText.includes(managedCatalog.active_month));
    assert((await (await page.request.get(base+'/api/catalog')).json()).projects.some(item=>item.code==='UI_COPY'),'cancel removal retains project');
    page.once('dialog',dialog=>dialog.accept());
    const [removeCopy]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/UI_COPY/remove')&&response.request().method()==='POST'),page.locator('#project-remove').click()]);
    assert.equal(removeCopy.status(),200,removeCopy.status()===200?"":await removeCopy.text());await page.waitForFunction(()=>![...document.querySelector('#project-select').options].some(option=>option.value==='UI_COPY'));
    assert.deepEqual(fs.readFileSync(newSource),sourceBefore,'remove import leaves original untouched');
    assert.deepEqual(await (await page.request.get(base+'/api/history')).json(),historyBefore,'removing unused duplicate retains all history');
    managedCatalog=await (await page.request.get(base+'/api/catalog')).json();
    assert.equal(await page.locator('#rules-panel').count(),0,'management must not require a per-sheet rule editor');
    await page.locator('#projects button[data-code="EF"]').click();await page.locator('#nav-entry').click();await page.locator(`#methods button[data-key="${sheet.key}"]`).click();
    await page.locator('#sheet-panel').waitFor({state:'visible'});
    await page.locator('#sheet td[data-ref="C3"]').click();
    // Playwright restores screenshot caret styles as style=""; ignore only that empty attribute.
    const entryState=await page.evaluate(()=>({html:document.querySelector('#sheet').innerHTML.replaceAll(' style=""',''),formula:document.querySelector('#formula-value').value,range:document.querySelector('#format-range').value,entries:[...document.querySelectorAll('#entry input')].map(input=>input.value)}));
    await page.locator('#nav-manage').click();
    managedCatalog=await (await page.request.get(base+'/api/catalog')).json();
    const oldMonth=managedCatalog.active_month,[year,monthNumber]=oldMonth.split('-').map(Number),nextDate=new Date(Date.UTC(year,monthNumber,1)),newMonth=nextDate.toISOString().slice(0,7);
    await page.locator('#new-month').fill(newMonth);
    const filesBeforePreview=filesUnder(managedCatalog.data_directory),sourceBeforePreview=fs.readFileSync(sourcePath);
    const [initialTemplate]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')&&response.request().method()==='POST'),page.locator('#create-month').click()]);
    assert.equal(initialTemplate.status(),200,initialTemplate.status()===200?"":await initialTemplate.text());await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);
    const allTargets=(await initialTemplate.json()).projects.map(item=>item.source_path).filter(Boolean);assert(allTargets.every(file=>!fs.existsSync(file)),'new month source paths must not be created by preview');
    if(await page.locator('#month-preview-project').inputValue()!=='EF'){
      const [selected]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-project').selectOption('EF')]);assert.equal(selected.status(),200);await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);
    }
    const [templateSheet]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-sheet-select').selectOption(sheet.name)]);
    assert.equal(templateSheet.status(),200,templateSheet.status()===200?"":await templateSheet.text());const template=await templateSheet.json();await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);
    const rule=managedCatalog.monthly_rules.EF.find(rule=>rule.sheet===sheet.name);
    assert(template.view.rows.some(row=>row.number<rule.start_row&&row.cells.some(cell=>cell.value)),'actual inherited headers must remain');
    assert(template.view.rows.some(row=>row.cells.some(cell=>cell.formula)),'actual inherited formulas must remain');
    assert(template.view.rows.filter(row=>row.number>=rule.start_row).every(row=>row.cells.every(cell=>!rule.columns.includes(cell.ref.replace(/[0-9]/g,''))||cell.formula||!cell.raw)),'confirmed input columns must be blank');
    const originalDate=template.original_view.rows.flatMap(row=>row.cells).find(cell=>cell.ref==='A4');assert(originalDate?.raw,'fixture original date must be nonempty for before/after checks');
    await page.locator('#month-preview-mode').selectOption('original');assert.equal(await page.locator('#month-preview-sheet td[data-ref="A4"]').textContent(),originalDate.value,'original comparison displays the old value');
    await page.locator('#month-preview-mode').selectOption('candidate');assert.equal(await page.locator('#month-preview-sheet td[data-ref="A4"]').textContent(),'','automatic preview blanks date without a rules setup');
    assert(await page.locator('#month-preview-sheet td[data-ref="A4"]').evaluate(cell=>cell.classList.contains('cleared-cell')),'cleared input is visibly marked');
    await page.locator('#month-preview-range').fill('A4');
    const [kept]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-keep').click()]);assert.equal(kept.status(),200);await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);assert.equal(await page.locator('#month-preview-sheet td[data-ref="A4"]').textContent(),originalDate.value,'keep restores original value');
    await page.locator('#month-preview-range').fill('A4');
    const [cleared]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-clear').click()]);assert.equal(cleared.status(),200);await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);assert.equal(await page.locator('#month-preview-sheet td[data-ref="A4"]').textContent(),'');
    const validCandidateHtml=await page.locator('#month-preview-sheet').innerHTML();
    await page.locator('#month-preview-range').fill('A1');
    const [invalidClear]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-clear').click()]);assert.equal(invalidClear.status(),400);await page.waitForFunction(()=>document.querySelector('#month-preview-status').textContent.includes('失败的调整未保存'));
    assert.equal(await page.locator('#month-preview-sheet').innerHTML(),validCandidateHtml,'rejected header clear retains the last valid candidate');assert(await page.locator('#month-preview-confirm').isDisabled());
    const [retryValid]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-retry').click()]);assert.equal(retryValid.status(),200);await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);assert.equal((await retryValid.json()).adjustments.length,2,'failed adjustment must not poison the next preview');
    // A real disposable destination conflict must keep the candidate visible and disable confirmation.
    const conflictTarget=allTargets[0];fs.mkdirSync(path.dirname(conflictTarget),{recursive:true});fs.writeFileSync(conflictTarget,sourceBefore);
    const [conflictedConfirm]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month')&&response.request().method()==='POST'),page.locator('#month-preview-confirm').click()]);assert.equal(conflictedConfirm.status(),409);
    await page.waitForFunction(()=>document.querySelector('#month-preview-status').textContent.includes('重新预览'));
    assert(await page.locator('#month-preview').isVisible(),'failed confirmation retains the dialog');assert(await page.locator('#month-preview-confirm').isDisabled());assert(await page.locator('#month-preview-retry').isVisible());assert((await page.locator('#month-preview-status').textContent()).includes('已存在'),'confirmation error appears inside the dialog');
    const [blocked]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-retry').click()]);assert.equal(blocked.status(),200);const blockedData=await blocked.json();assert.equal(blockedData.can_create,false);await page.waitForFunction(()=>!document.querySelector('#month-preview-issues').hidden);
    assert(await page.locator('#month-preview-confirm').isDisabled());assert(await page.locator('#month-preview-retry').isVisible(),'blocked preview must retain the refresh entry');assert((await page.locator('#month-preview-issues').textContent()).includes('已存在'));assert(await page.locator('#month-preview-sheet td[data-ref="A1"]').count(),'destination conflict still renders actual table');
    await page.screenshot({path:path.join(output,'month-preview-blocked-1440.png'),fullPage:false});fs.unlinkSync(conflictTarget);
    const [unblocked]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-retry').click()]);assert.equal(unblocked.status(),200);await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);
    const formulaCell=template.view.rows.flatMap(row=>row.cells).find(cell=>cell.formula);
    await page.locator(`#month-preview-sheet td[data-ref="${formulaCell.ref}"]`).click();
    assert((await page.locator('#month-preview-cell').textContent()).includes('='+formulaCell.formula),'template formulas must be inspectable');
    const entryAfterTemplate=await page.evaluate(()=>({html:document.querySelector('#sheet').innerHTML.replaceAll(' style=""',''),formula:document.querySelector('#formula-value').value,range:document.querySelector('#format-range').value,entries:[...document.querySelectorAll('#entry input')].map(input=>input.value)}));
    assert.deepEqual(entryAfterTemplate,entryState,'template rendering and cell selection must leave entry state unchanged');
    await page.locator('.preview-scroll').evaluate(element=>element.scrollTop=0);
    for(const width of [320,390,768,1440]){
      await page.setViewportSize({width,height:width<700?844:1000});
      const overflow=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth,dialog:document.querySelector('#month-preview').scrollWidth,dialogWidth:document.querySelector('#month-preview').clientWidth}));
      assert(overflow.page<=overflow.viewport+1&&overflow.dialog<=overflow.dialogWidth+1,JSON.stringify({width,...overflow}));
      await page.screenshot({path:path.join(output,`month-preview-${width}.png`),fullPage:false});
    }
    await page.locator('#month-preview-close').click();assert(!(await page.locator('#month-preview').isVisible()));
    assert.deepEqual(filesUnder(managedCatalog.data_directory),filesBeforePreview,'cancelled preview cannot create month directories/backups/files');assert.deepEqual(fs.readFileSync(sourcePath),sourceBeforePreview,'preview cannot change current source');assert(allTargets.every(file=>!fs.existsSync(file)));
    const [confirmedPreview]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#create-month').click()]);assert.equal(confirmedPreview.status(),200);await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);
    await page.locator('#month-preview-range').fill('A4');
    const [confirmAdjustment]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month/preview')),page.locator('#month-preview-keep').click()]);assert.equal(confirmAdjustment.status(),200);const confirmData=await confirmAdjustment.json();await page.waitForFunction(()=>!document.querySelector('#month-preview-confirm').disabled);
    const [created]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/month')&&response.request().method()==='POST'),page.locator('#month-preview-confirm').click()]);assert.equal(created.status(),200,created.status()===200?"":await created.text());assert.equal(created.request().postDataJSON().fingerprint,confirmData.fingerprint);assert.deepEqual(created.request().postDataJSON().adjustments,confirmData.adjustments,'confirmed creation submits the exact preview adjustments');
    await page.waitForFunction(month=>document.querySelector('#month-select').value===month,newMonth);
    assert(confirmData.projects.filter(item=>item.source_path).every(item=>fs.existsSync(item.source_path)),'confirmation creates the previewed source paths');
    const createdCatalog=await (await page.request.get(base+'/api/catalog',{headers:{'X-Labwork-Month':newMonth}})).json();assert.equal(createdCatalog.active_month,newMonth);
    const createdView=await (await page.request.get(base+'/api/book-sheet/EF/'+sheet.key,{headers:{'X-Labwork-Month':newMonth}})).json();assert.equal(createdView.view.rows.flatMap(row=>row.cells).find(cell=>cell.ref==='A4').value,originalDate.value,'confirmed original source includes the retained date');
    const createdSources=createdCatalog.projects.filter(item=>item.source_link).map(item=>[item.source_link.path,fs.readFileSync(item.source_link.path)]);
    await page.setViewportSize({width:390,height:844});await page.locator('#nav-manage').click();
    for(const item of createdCatalog.projects){
      await page.locator('#project-select').selectOption(item.code);await page.waitForFunction(code=>document.querySelector('#source-project-name').textContent.endsWith('/ '+code),item.code);
      page.once('dialog',dialog=>dialog.accept());const [removed]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/'+item.code+'/remove')&&response.request().method()==='POST'),page.locator('#project-remove').click()]);assert.equal(removed.status(),200,removed.status()===200?"":await removed.text());
      await page.waitForFunction(code=>![...document.querySelector('#project-select').options].some(option=>option.value===code),item.code);
    }
    assert(await page.locator('#project-select').isDisabled());assert(await page.locator('#sheet-select').isDisabled());assert(!(await page.locator('#source-panel').isVisible()));assert(!(await page.locator('#download-workbook').isVisible()));assert(!(await page.locator('#rules-panel').isVisible()));assert(await page.locator('#source-import-form').isVisible());
    for(const [file,bytes] of createdSources)assert.deepEqual(fs.readFileSync(file),bytes,'deleting all imports keeps their original source files');
    assert.equal((await (await page.request.get(base+'/api/catalog',{headers:{'X-Labwork-Month':oldMonth}})).json()).projects.length,managedCatalog.projects.length,'prior month projects remain available');
    assert.deepEqual(await (await page.request.get(base+'/api/history',{headers:{'X-Labwork-Month':oldMonth}})).json(),historyBefore,'prior month history retained');
    await page.screenshot({path:path.join(output,'empty-catalog-390.png'),fullPage:true});
    assert.deepEqual(errors,[],'browser JavaScript errors');
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,version:catalog.version,executable_sha256:crypto.createHash('sha256').update(fs.readFileSync(executable)).digest('hex'),viewports:[390,320,768,1440],project:'EF',method:method.key,manual_source_write:true,checks:['login_form','responsive_no_page_overflow','mobile_selectors','separate_management','unlinked_confirm_disabled','unlinked_format_disabled','user_manually_binds_current_project','binding_no_duplicate_project','source_link_no_write','binding_retains_unsubmitted_rows','actual_source_import','actual_format_save','font_row_height_reloaded','custom_date_survives_append','unsaved_entries_preserved','format_invalidates_preview','original_target_visible','actual_preview','sheet_rows_continuous_after_preview_commit_format','calculated_original_matches_preview','manual_original_matches_preview','unlink_retains_project_source','remove_import_retains_source_history','remove_last_empty_catalog','month_preview_actual_blank_headers_formulas','month_preview_cancel_no_files','month_preview_confirm','month_preview_responsive','month_preview_preserves_entry_state','no_rule_editor','month_preview_original_comparison','month_preview_clear_keep_adjustments','month_preview_failed_adjustment_retains_table','month_preview_blocked_destination_still_renders','no_js_errors']},null,2));
    console.log('UI smoke passed; screenshots and result.json saved.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
