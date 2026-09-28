/* Local Flask only; real Chrome at phone dimensions, no external network. */
const assert=require('node:assert/strict'),path=require('node:path'),os=require('node:os'),fs=require('node:fs'),{spawn}=require('node:child_process');
let playwright;try{playwright=require('playwright')}catch(_){playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const origin='http://127.0.0.1:8789',sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
(async()=>{
  let browser,log='',bound=false;
  const server=spawn('python',['-B',path.join(__dirname,'sync_recovery_fixture.py')],{windowsHide:true});
  const record=chunk=>{log+=chunk;if(String(chunk).includes('Running on '+origin))bound=true};server.stdout.on('data',record);server.stderr.on('data',record);
  try{
    for(let i=0;i<100&&!bound&&server.exitCode===null;i++)await sleep(100);
    assert.ok(bound,log.slice(-2000));
    browser=await playwright.chromium.launch({channel:'chrome',headless:true});
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
    const page=await context.newPage(),errors=[];page.on('pageerror',err=>errors.push(err.message));
    await page.goto(origin+'/hsc-tecnico/');
    await page.locator('#openSyncDiagnostics').evaluate(el=>el.click());
    const panel=page.locator('#syncRecoveryPanel');await panel.waitFor({state:'visible'});
    assert.match(await panel.innerText(),/Sincronización pausada/);
    for(const text of ['Operación','Fecha y hora','Revisión enviada','Último fallo','Cambios en espera'])assert.ok((await panel.innerText()).includes(text),text+' missing in '+await panel.innerText());
    await panel.getByRole('button',{name:'Revisar y reanudar'}).click();
    const dialog=page.getByRole('dialog',{name:'Revisar y reanudar'}),confirm=dialog.getByRole('button',{name:'Confirmar y reanudar'});
    await dialog.waitFor({state:'visible'});assert.ok(await confirm.isDisabled());
    assert.ok((await dialog.innerText()).includes('ya no haya una solicitud anterior ejecutándose'));
    for(const width of [320,390]){
      await page.setViewportSize({width,height:844});
      assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth),'No horizontal overflow at '+width);
      const bounds=await confirm.boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width&&bounds.y+bounds.height<=844,'Confirmation remains visible at '+width);
    }
    const screenshot=path.resolve(__dirname,'../../.work/sync-recovery-mobile.png');fs.mkdirSync(path.dirname(screenshot),{recursive:true});
    await page.screenshot({path:screenshot});
    const box=await dialog.boundingBox();assert.ok(box.x>=0&&box.x+box.width<=390&&box.height<=844);
    assert.ok(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth),'No horizontal overflow on mobile');
    await dialog.getByRole('checkbox').check();assert.ok(await confirm.isEnabled());
    // A second owner resolves and the SAME outbox operation starts a new attempt.
    await context.request.post(origin+'/test/new-attempt');
    await confirm.click();
    await page.waitForFunction(()=>document.querySelector('[data-recovery-result]').textContent.includes('El estado cambió'));
    assert.ok(await dialog.isVisible());
    let state=(await(await context.request.get(origin+'/api/operaciones/sync-status')).json()).sheet_delivery;
    assert.ok(state.requires_confirmation);assert.equal(state.waiting_changes,1);
    await dialog.getByRole('button',{name:'Cerrar',exact:true}).click();
    await page.locator('#runSyncDiagnostics').click();
    // New dialog obtains a fresh reference, then the real route restarts its writer.
    await panel.getByRole('button',{name:'Revisar y reanudar'}).click();
    await context.request.post(origin+'/test/enable-worker');
    await dialog.getByRole('checkbox').check();await confirm.click();
    await page.waitForFunction(()=>document.querySelector('[data-recovery-result]').textContent.includes('Sincronización completada'));
    assert.ok(await dialog.isVisible());
    assert.match(await dialog.locator('[data-recovery-audit]').innerText(),/Propietario de prueba/);
    state=(await(await context.request.get(origin+'/api/operaciones/sync-status')).json()).sheet_delivery;
    assert.equal(state.waiting_changes,0);assert.equal(state.last_recovery.actor_id,'owner');
    assert.deepEqual(errors,[]);
    console.log('PASS Chrome mobile: visible details, explicit consent, stale-attempt rejection, preserved pending changes, owner audit, queue restart and success in same dialog; screenshot '+screenshot);
  }catch(error){console.error(log.slice(-1800));throw error}
  finally{if(browser)await browser.close();if(server.exitCode===null){await new Promise(resolve=>{server.once('exit',resolve);server.kill()})}}
})().catch(error=>{console.error(error);process.exitCode=1});
