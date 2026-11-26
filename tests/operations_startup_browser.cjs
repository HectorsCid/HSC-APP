// Cold start on an empty mobile profile. Requests never leave the local fixture.
const assert=require('node:assert/strict'),path=require('node:path'),os=require('node:os'),{spawn}=require('node:child_process');
let pw;try{pw=require('playwright')}catch{pw=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const origin='http://127.0.0.1:8766';
(async()=>{
  const server=spawn('python',['-B',path.join(__dirname,'offline_fixture.py')],{windowsHide:true});
  let browser,log='';server.stderr.on('data',chunk=>log+=chunk);
  try{
    for(let i=0;i<100&&!log.includes('Running on '+origin)&&server.exitCode===null;i++)await new Promise(r=>setTimeout(r,100));
    assert.ok(log.includes('Running on '+origin),log);
    browser=await pw.chromium.launch({channel:'msedge',headless:true});
    const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,serviceWorkers:'block'});
    await ctx.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
    const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto(origin+'/hsc-tecnico/');
    assert.deepEqual(errors,[]);
    await page.locator('body.operations-ready [data-client="TEST"]').click({timeout:10000});
    const tiles=page.locator('.view.active [data-client-dest]');
    assert.equal(await tiles.count(),4);
    await tiles.first().click();await page.locator('.view.active #equipmentList').waitFor();
    await page.locator('#backBtn').click();
    await page.locator('[data-home-filter="pending"]').click();
    await page.locator('.view.active #equipmentFilters [data-filter="pending"][aria-pressed="true"]').waitFor();
    await page.locator('#backBtn').click();await page.locator('[data-client-dest="clientSettings"]').click();
    await page.locator('.view.active #editOperationalClient').click();
    await page.locator('[data-view="clientEditor"].active').waitFor();await page.locator('#backBtn').click();
    await page.locator('button[data-client-layout="classic"]').click();
    await page.locator('#backBtn').click();
    await page.locator('#equipmentList').waitFor();await page.locator('#roundReportList').waitFor();
    await page.reload();await page.locator('body.operations-ready #equipmentList').waitFor();
    assert.equal(await page.locator('button[data-client-layout="classic"]').getAttribute('aria-pressed'),'true');
    await page.locator('#classicSettings').click();await page.locator('button[data-client-layout="tiles"]').click();
    await page.locator('#backBtn').click();await page.locator('.hub-grid').waitFor();
    await page.locator('#clientToday').click();
    const pick=page.locator('#worklistChoices [data-add-work]');
    await pick.first().click();await pick.nth(1).click();
    assert.equal(await page.locator('#worklistSelected .work-selected').count(),2);
    assert.equal(await pick.first().getAttribute('aria-pressed'),'true');
    const first=await page.locator('#worklistSelected .work-selected').first().innerText();
    await page.locator('[data-move-work="0"][data-step="1"]').click();
    assert.notEqual(await page.locator('#worklistSelected .work-selected').first().innerText(),first);
    await page.locator('#saveWorklist').click();await page.locator('#clientActiveRoute .client-worklist-items button').first().waitFor();
    assert.equal(await page.locator('#clientActiveRoute .client-worklist-items button').count(),2);
    assert.deepEqual(errors,[]);console.log('OK: arranque limpio, ficha del cliente y acceso a equipos.');
  }finally{try{await browser?.close()}finally{server.kill()}}
})().catch(e=>{console.error(e);process.exitCode=1});
