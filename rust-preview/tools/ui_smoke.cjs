// Independent headless Edge profile; only use an isolated test server/data directory.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('C:/labwork/tmp/labwork-ui-qa/node_modules/playwright');
const [base,output]=process.argv.slice(2);
if(!base||!output)throw Error('Usage: node tools/ui_smoke.cjs <isolated base URL> <output directory>');
const password=process.env.LABWORK_QA_PASSWORD;
if(!password)throw Error('Set LABWORK_QA_PASSWORD for the isolated test server');
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
    const catalog=await (await page.request.get(base+'/api/catalog')).json();
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
      const overflow=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
      assert(overflow.page<=overflow.viewport+1,JSON.stringify({width,...overflow}));
      await page.screenshot({path:path.join(output,`entry-${width}.png`),fullPage:true});
      if(width===390){
        const panel='#mobile-entry';
        await page.locator(`${panel} input[data-key="batch"]`).first().fill('UI-QA-'+Date.now());
        const overrides=JSON.parse(process.env.LABWORK_QA_INPUTS||'{}');
        for(const field of method.fields){
          const value=String(overrides[field.key]??field.sample??'').trim()||(field.input_type==='text'?'QA':'1');
          await page.locator(`${panel} input[data-key="${field.key}"]`).first().fill(value);
        }
        const [preview]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/preview')&&response.request().method()==='POST'),page.locator('#calculate').click()]);
        assert.equal(preview.status(),200,await preview.text());
        await page.waitForFunction(()=>!document.querySelector('#commit').disabled);
        await page.screenshot({path:path.join(output,'preview-390.png'),fullPage:true});
        const [commit]=await Promise.all([page.waitForResponse(response=>response.url().includes('/api/commit/')&&response.request().method()==='POST'),page.locator('#commit').click()]);
        assert.equal(commit.status(),200,await commit.text());
        await page.waitForFunction(()=>document.querySelector('#action-state').textContent.includes('已写入'));
        await page.screenshot({path:path.join(output,'written-390.png'),fullPage:true});
      }
      await page.locator('#nav-manage').click();
      assert(await page.locator('#import-form').isVisible());
      assert(!(await page.locator('#calculate').isVisible()));
      const managed=await page.evaluate(()=>({page:document.documentElement.scrollWidth,viewport:innerWidth}));
      assert(managed.page<=managed.viewport+1,JSON.stringify({width,view:'manage',...managed}));
      await page.screenshot({path:path.join(output,`manage-${width}.png`),fullPage:true});
    }
    assert.deepEqual(errors,[],'browser JavaScript errors');
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,viewports:[390,320,768,1440],project:'EF',method:method.key,checks:['login_form','responsive_no_page_overflow','mobile_selectors','separate_management','actual_preview','actual_commit','no_js_errors']},null,2));
    console.log('UI smoke passed; screenshots and result.json saved.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
