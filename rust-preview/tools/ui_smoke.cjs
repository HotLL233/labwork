// Independent headless Edge profile; only use an isolated test server/data directory.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('C:/labwork/tmp/labwork-ui-qa/node_modules/playwright');
const [base,outputArgument]=process.argv.slice(2);
if(!base||!outputArgument)throw Error('Usage: node tools/ui_smoke.cjs <isolated base URL> <output directory>');
const output=path.resolve(outputArgument);
const password=process.env.LABWORK_QA_PASSWORD;
const sourcePath=process.env.LABWORK_QA_SOURCE;
if(!password)throw Error('Set LABWORK_QA_PASSWORD for the isolated test server');
if(!sourcePath)throw Error('Set LABWORK_QA_SOURCE to an isolated disposable workbook copy for source-write checks');
const sourceBefore=fs.readFileSync(sourcePath);
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
          assert.equal(unlinkedPreview.status(),200,await unlinkedPreview.text());
          assert(await page.locator('#commit').isDisabled(),'unlinked preview must not enable original write');
          const unsubmittedBatch=await page.locator(`${panel} input[data-key="batch"]`).first().inputValue();
          await page.locator('#link-source-shortcut').click();
          assert((await page.locator('#source-project-name').textContent()).includes('/ EF'),'CTA must select current existing project');
          assert.equal(await page.locator('#source-path').inputValue(),'','association must be manually specified by user');
          await page.locator('#source-path').fill(sourcePath);
          const [linked]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/source')&&response.request().method()==='POST'),page.locator('#source-link').click()]);
          assert.equal(linked.status(),200,await linked.text());
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
        assert.equal(preview.status(),200,await preview.text());
        await page.waitForFunction(()=>!document.querySelector('#commit').disabled);
        await page.screenshot({path:path.join(output,'preview-390.png'),fullPage:true});
        const batch=await page.locator(`${panel} input[data-key="batch"]`).first().inputValue();
        await page.locator('#format-panel > summary').click();
        await page.locator('#format-range').fill('C3:E3');
        await page.locator('#format-number').selectOption('number');
        await page.locator('#format-decimals').fill('3');
        await page.locator('#format-font').selectOption('Arial');
        await page.locator('#format-height').fill('28');
        const [formatted]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/format')&&response.request().method()==='POST'),page.locator('#format-save').click()]);
        assert.equal(formatted.status(),200,await formatted.text());
        await page.waitForFunction(()=>document.querySelector('#format-status').textContent.includes('已保存'));
        assert.equal(await page.locator(`${panel} input[data-key="batch"]`).first().inputValue(),batch,'format save must retain unsaved entries');
        assert(await page.locator('#commit').isDisabled(),'format save must invalidate old preview');
        const formattedRow=await page.locator('#sheet').evaluate(table=>{const row=[...table.querySelectorAll('tbody tr')].find(row=>row.querySelector('.row-num')?.textContent==='3');return{height:parseFloat(row.style.height),font:row.querySelector('[data-ref="C3"]').style.fontFamily};});
        assert(Math.abs(formattedRow.height-28*4/3)<.1,'saved row height must reload');assert(formattedRow.font.includes('Arial'),'saved font must reload');
        await page.locator('#format-panel > summary').click();
        await page.locator('#format-range').fill('A4');
        await page.locator('#format-number').selectOption('yyyy/mm/dd');
        await page.locator('#format-size').fill('14');
        const [dateFormatted]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/format')&&response.request().method()==='POST'),page.locator('#format-save').click()]);
        assert.equal(dateFormatted.status(),200,await dateFormatted.text());
        await page.waitForFunction(()=>document.querySelector('#format-status').textContent.includes('已保存'));
        assert.match(await page.locator('#sheet td[data-ref="A4"]').textContent(),/^\d{4}\/\d{2}\/\d{2}$/,'saved date display must reload');
        const [repreview]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/preview')&&response.request().method()==='POST'),page.locator('#calculate').click()]);
        assert.equal(repreview.status(),200,await repreview.text());
        const candidate=await repreview.json(),candidateBytes=Buffer.from(await (await page.request.get(base+candidate.download)).body());
        const beforeCalculatedWrite=fs.readFileSync(sourcePath);
        await page.waitForFunction(()=>!document.querySelector('#commit').disabled);
        const [commit]=await Promise.all([page.waitForResponse(response=>response.url().includes('/api/commit/')&&response.request().method()==='POST'),page.locator('#commit').click()]);
        assert.equal(commit.status(),200,await commit.text());
        await page.waitForFunction(()=>document.querySelector('#action-state').textContent.includes('已写入'));
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
        assert.equal(manualPreview.status(),200,await manualPreview.text());
        const manualCandidate=await manualPreview.json(),manualBytes=Buffer.from(await (await page.request.get(base+manualCandidate.download)).body()),beforeManualWrite=fs.readFileSync(sourcePath);
        const [manualCommit]=await Promise.all([page.waitForResponse(response=>response.url().includes('/api/commit/')&&response.request().method()==='POST'),page.locator('#commit').click()]);
        assert.equal(manualCommit.status(),200,await manualCommit.text());
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
    assert.equal(sourceImport.status(),200,await sourceImport.text());
    await page.waitForFunction(()=>document.querySelector('#write-target-path').textContent.endsWith('new-project-source.xlsx'));
    assert.deepEqual(fs.readFileSync(newSource),sourceBefore,'new-project source association must not modify file');
    assert.equal((await (await page.request.get(base+'/api/catalog')).json()).projects.length,catalog.projects.length+1);
    assert.deepEqual(errors,[],'browser JavaScript errors');
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,version:catalog.version,viewports:[390,320,768,1440],project:'EF',method:method.key,manual_source_write:true,checks:['login_form','responsive_no_page_overflow','mobile_selectors','separate_management','unlinked_confirm_disabled','unlinked_format_disabled','user_manually_binds_current_project','binding_no_duplicate_project','source_link_no_write','binding_retains_unsubmitted_rows','actual_source_import','actual_format_save','font_row_height_reloaded','custom_date_survives_append','unsaved_entries_preserved','format_invalidates_preview','original_target_visible','actual_preview','calculated_original_matches_preview','manual_original_matches_preview','no_js_errors']},null,2));
    console.log('UI smoke passed; screenshots and result.json saved.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
