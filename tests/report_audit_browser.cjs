/* Real Chromium + IndexedDB + service worker, against report_browser_fixture.py only. */
const assert=require('node:assert/strict'),path=require('node:path'),os=require('node:os'),fs=require('node:fs'),{spawn}=require('node:child_process');
let playwright;try{playwright=require('playwright')}catch(_){playwright=require(path.join(os.homedir(),'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'))}
const origin='http://127.0.0.1:8787';
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function openReport(page){
  await page.goto(origin+'/hsc-tecnico/');
  // The catalog arrives after navigation. isVisible() does not wait, so checking
  // it immediately could skip the client and try to click its hidden equipment.
  await page.locator('#reportForm:visible, #clientList [data-client="TEST"]:visible, #equipmentList [data-equipment="TEST1"]:visible, #newReport:visible').first().waitFor({state:'visible'});
  if(await page.locator('#reportForm').isVisible())return;
  const client=page.locator('#clientList [data-client="TEST"]');
  if(await client.isVisible())await client.click();
  if(!await page.locator('#newReport').isVisible())await page.locator('#equipmentList [data-equipment="TEST1"]').click();
  await page.locator('#newReport').click();
  await page.locator('#reportForm').waitFor({state:'visible'});
}
async function records(page){return page.evaluate(()=>new Promise((resolve,reject)=>{const r=indexedDB.open('hsc-operaciones-offline',1);r.onsuccess=()=>{const db=r.result,q=db.transaction('evidence').objectStore('evidence').getAll();q.onsuccess=()=>{resolve(q.result.map(({blob,...row})=>({...row,bytes:blob.size})));db.close()};q.onerror=()=>reject(q.error)};r.onerror=()=>reject(r.error)}))}
async function evidenceOpen(page){await page.locator('#reportEvidenceSection').evaluate(el=>el.open=true)}
(async()=>{
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'hsc-browser-audit-'));let server,browser,serverLog='';
  const stopServer=async()=>{if(!server||server.exitCode!==null)return;await new Promise(resolve=>{server.once('exit',resolve);server.kill()})};
  const startServer=async()=>{let bound=false;server=spawn('python',['-B',path.join(__dirname,'report_browser_fixture.py'),'--port','8787','--state-dir',directory],{windowsHide:true});const log=chunk=>{serverLog+=chunk;if(String(chunk).includes('Running on '+origin))bound=true};server.stdout.on('data',log);server.stderr.on('data',log);for(let i=0;i<100;i++){if(server.exitCode!==null)break;try{if(bound&&(await fetch(origin+'/healthz')).ok)return}catch(_){}await sleep(100)}throw Error('Synthetic server failed to bind its own port: '+serverLog.slice(-1000))};
  const errors=[];
  try{
    await startServer();browser=await playwright.chromium.launch({channel:'chrome',headless:true});
    const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
    let delayFirstCatalog=true;
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!==origin)return route.abort();
      // Keep the navigation race reproducible even when the local server is fast.
      if(delayFirstCatalog&&url.pathname==='/api/operaciones/bootstrap'){delayFirstCatalog=false;await sleep(400)}
      return route.continue();
    });
    await context.addInitScript(()=>{const original=IDBObjectStore.prototype.put;IDBObjectStore.prototype.put=function(...args){if(window.__denyPhotoWrite&&this.name==='evidence')throw new DOMException('Synthetic disk full','QuotaExceededError');return original.apply(this,args)}});
    const a=await context.newPage();a.on('pageerror',error=>errors.push(error.message));
    await openReport(a);assert.equal(delayFirstCatalog,false);console.log('PASS browser: opens the report after a deliberately delayed initial catalog.');
    await a.evaluate(()=>Promise.race([navigator.serviceWorker.ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('service worker not ready')),10000))]));console.log('Browser: service worker ready');
    const photo=await (await context.request.get(origin+'/test/photo.jpg?width=6000&height=4000')).body();
    const b=await context.newPage();b.on('pageerror',error=>errors.push(error.message));await openReport(b);
    console.log('Browser: second tab open');await context.setOffline(true);await evidenceOpen(a);
    await a.evaluate(()=>window.__denyPhotoWrite=true);
    await a.locator('#reportEvidence').setInputFiles([{name:'quota.jpg',mimeType:'image/jpeg',buffer:photo}]);
    await a.waitForFunction(()=>document.querySelector('#evidenceError').textContent.includes('SIN RESPALDO'));
    assert.equal(await a.locator('#evidenceList img').count(),1);
    assert.equal(await a.locator('#reportForm button[type="submit"]').isDisabled(),true);
    assert.equal((await records(a)).length,0);
    assert.equal(await a.evaluate(()=>{const event=new Event('beforeunload',{cancelable:true});window.dispatchEvent(event);return event.defaultPrevented}),true);
    await a.evaluate(()=>window.__denyPhotoWrite=false);await a.locator('#retryEvidenceBackup').click();
    await a.waitForFunction(()=>document.querySelector('#evidenceList').textContent.includes('respaldo local confirmado'));
    assert.equal((await records(a)).length,1);
    await a.locator('[data-evidence-action="remove"]').click();
    await a.waitForFunction(()=>document.querySelectorAll('#evidenceList img').length===0);
    await a.locator('#reportEvidence').setInputFiles(Array.from({length:6},(_,i)=>({name:`synthetic-${i}.jpg`,mimeType:'image/jpeg',buffer:photo})));
    await a.waitForFunction(()=>document.querySelectorAll('#evidenceList .editor-note').length===6&&[...document.querySelectorAll('#evidenceList .editor-note')].every(el=>el.textContent.includes('respaldo local confirmado')));
    assert.equal((await records(a)).length,6);
    console.log('PASS browser: disk full keeps preview, blocks finalization/close; retry confirms real IndexedDB; six 24MP source photos compressed and persisted.');
    await a.locator('#reportForm [name="p1"]').fill('70');
    await b.locator('#reportForm [name="p2"]').evaluate(el=>{el.value='80';el.dispatchEvent(new Event('input',{bubbles:true}))});
    await b.waitForFunction(()=>document.querySelector('#reportForm [name="p1"]').value==='70');
    await a.waitForFunction(()=>document.querySelector('#reportForm [name="p2"]').value==='80');
    await a.reload();await openReport(a);await evidenceOpen(a);
    await a.waitForFunction(()=>document.querySelectorAll('#evidenceList img').length===6);
    assert.equal(await a.locator('#reportForm [name="p1"]').inputValue(),'70');
    assert.equal(await a.locator('#reportForm [name="p2"]').inputValue(),'80');
    assert.equal((await records(a)).length,6);
    console.log('PASS browser: two offline tabs merge disjoint fields; offline reload restores both values and six IndexedDB blobs.');
    await stopServer();await startServer();
    await context.setOffline(false);
    await a.evaluate(()=>window.dispatchEvent(new Event('online')));
    await a.waitForFunction(()=>document.querySelector('#reportCollaborationStatus').textContent.includes('compartido'),{},{timeout:15000});
    // Explicit sync button drives the normal app path, including recovered photos.
    await a.locator('#syncPendingNow').evaluate(el=>el.click());
    let remote;
    for(let attempt=0;attempt<20;attempt++){
      const reply=await context.request.get(origin+'/api/operaciones/reports/draft?equipment_id=TEST1&round=1',{headers:{'X-HSC-Account':'TECH-A'}});
      remote=(await reply.json()).draft;
      if(remote?.id)remote=(await (await context.request.get(origin+'/api/operaciones/reports/'+encodeURIComponent(remote.id),{headers:{'X-HSC-Account':'TECH-A'}})).json()).report;
      if(remote?.evidence?.length===6)break;
      await sleep(500);
    }
    if(remote?.evidence?.length!==6)console.log('Browser recovery diagnostics',JSON.stringify(remote),await a.locator('#evidenceError').textContent(),await a.locator('#reportConflicts').textContent(),errors,serverLog.slice(-3000));
    assert.equal(remote?.evidence?.length,6,'All six recovered photos reach the real synthetic server');
    assert.equal(remote.payload.p1,'70');assert.equal(remote.payload.p2,'80');
    const rows=await records(a);assert.ok(rows.every(row=>row.accountId==='TECH-A'));
    assert.deepEqual(errors,[]);
    console.log('PASS browser: after an actual server restart, reconnect uploads six recovered photos and merged measurements, bound to TECH-A.');
    await b.close();
    let lostReply=false,submissionIds=[];
    await context.route('**/api/operaciones/reports/finalize',async route=>{submissionIds.push(route.request().postDataJSON().submission_id);if(!lostReply){lostReply=true;await route.fetch();await route.abort('failed')}else await route.continue()});
    a.on('dialog',dialog=>dialog.accept());
    await a.locator('#reportForm button[type="submit"]').click();
    for(let i=0;i<100&&!lostReply;i++)await sleep(100);
    assert.equal(lostReply,true,'Finalization reached the server');
    await a.waitForFunction(()=>Object.keys(localStorage).filter(key=>key.includes('hsc-report-journal-v1:TECH-A:pending:')).length===0,{},{timeout:20000});
    const completed=await (await context.request.get(origin+'/api/operaciones/reports/'+encodeURIComponent(remote.id),{headers:{'X-HSC-Account':'TECH-A'}})).json();
    assert.equal(completed.report.completed,true);assert.equal(completed.report.evidence.length,6);
    assert.equal(lostReply,true);assert.ok(submissionIds.length>=2);assert.equal(new Set(submissionIds).size,1);
    console.log('PASS browser: finalization accepted with lost HTTP reply is recovered using the same submission; six server photos preserved.');
    await context.close();
  }finally{await browser?.close();await stopServer();const resolved=path.resolve(directory);assert.equal(path.dirname(resolved),path.resolve(os.tmpdir()));assert.ok(path.basename(resolved).startsWith('hsc-browser-audit-'));fs.rmSync(resolved,{recursive:true,force:true})}
})().catch(error=>{console.error(error);process.exitCode=1});
