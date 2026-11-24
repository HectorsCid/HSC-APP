const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const queue=require('./static/receipt_queue.js');
const photo=()=>new Blob([new Uint8Array([255,216,255,224])],{type:'image/jpeg'});
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
(async()=>{
  // Simulate a restored iPhone file with bytes but no filename, plus an unreadable second photo.
  let rows=[{id:'F1',createdAt:1,url:'/api/operaciones/faults',body:{id:'F1'},files:[photo(),new Blob(['invalid']),photo()]},
    {id:'round',createdAt:2,url:'/api/operaciones/clients/HDI/round',body:{round:'2'},files:[]},
    {id:'resolve',createdAt:3,url:'/api/operaciones/faults/F1/resolve',body:{},files:[]}];
  const posted=[],uploads=[];
  const ctx=vm.createContext({fieldActionsBusy:false,shouldSyncNow:()=>true,HscReceiptQueue:queue,
    updateFieldActionStatus:async()=>{},fieldActionStorage:async(mode,item)=>{
      if(mode==='list')return structuredClone(rows);
      if(mode==='save')rows=rows.map(row=>row.id===item.id?structuredClone(item):row);
      if(mode==='delete')rows=rows.filter(row=>row.id!==item.id);
    },operationsPost:async(url)=>{posted.push(url);return url.endsWith('/faults')?{ok:true,fault:{id:'F1'}}:{ok:true};},
    hscFetch:async(url,{body})=>{const file=body.get('file');assert(file.name.endsWith('.jpg'));assert(file.size>0);uploads.push(url);return {ok:true,status:200,json:async()=>({ok:true})};},
    fieldRevision:0,lastServerPayload:null,toast:()=>{},repaintPendingState:async()=>{},loadMatrixData:async()=>{},updateOfflineSyncUi:()=>{}
  });
  vm.runInContext(html.slice(html.indexOf('  async function drainFieldActions('),html.indexOf('  const connectionBanner=')),ctx);
  await ctx.drainFieldActions();
  assert.equal(rows.length,2);assert.equal(rows[0].faultPhotoBlocked,true);
  assert.equal(rows[0].faultPhotosUploaded,1);assert.equal(rows[0].faultPhotoIndex,1);
  assert(posted.includes('/api/operaciones/clients/HDI/round'),'independent round must continue');
  assert(!posted.some(url=>url.endsWith('/resolve')),'dependent fault must wait');
  const original=rows[0];rows[0]=await queue.replaceFaultPhoto(original,photo());
  assert.equal(original.files[1].size,7,'original not modified before explicit replacement');
  await ctx.drainFieldActions(true);
  assert.equal(rows.length,0);assert.equal(posted.filter(url=>url.endsWith('/faults')).length,1,'do not repeat confirmed fault save');
  assert.deepEqual(uploads.map(url=>url.split('/').at(-1)),['1','2','3'],'resume at failed photo, not photo 1');
  assert(posted.some(url=>url.endsWith('/resolve')));
  rows=[{id:'invalid',createdAt:1,url:'/api/operaciones/tasks',body:{id:'T1'}},{id:'dependent',createdAt:2,url:'/api/operaciones/tasks/T1/complete',body:{}},{id:'independent',createdAt:3,url:'/api/operaciones/expenses',body:{id:'G1'}}];
  ctx.operationsPost=async url=>{if(url.endsWith('/tasks'))throw Object.assign(Error('Revisar actividad'),{status:400});return {ok:true};};
  await ctx.drainFieldActions();assert.deepEqual(rows.map(x=>x.id),['invalid','dependent']);
  assert(html.includes('faultPhotoRetryAfter=Date.now()+60000'));
  // HTTP rejection keeps all the photos and checkpoints the confirmed base record.
  const action={url:'/api/operaciones/faults',body:{id:'F2'},files:[photo()]};
  await assert.rejects(queue.sendFault(action,{post:async()=>({fault:{id:'F2'}}),persist:async()=>{},upload:async()=>{throw Object.assign(Error('bad request'),{status:400});}}));
  assert.equal(action.faultSavedResult.fault.id,'F2');assert.equal(action.files.length,1);
  // Omit only the failed slot. Never renumber later evidence or remove confirmed slots.
  const blocked={url:'/api/operaciones/faults',body:{id:'F3'},files:[photo(),new Blob(['invalid']),photo()],faultSavedResult:{fault:{id:'F3'}},faultPhotosUploaded:1,faultPhotoIndex:1,faultPhotoBlocked:true};
  const omitted=await queue.replaceFaultPhoto(blocked,null,true),positions=[];
  assert.equal(blocked.files[1].size,7,'omission must not mutate original before storage succeeds');
  assert.equal(omitted.files.length,3);assert.equal(omitted.files[1],null);
  await queue.sendFault(omitted,{post:async()=>assert.fail('confirmed base must not be posted'),persist:async()=>{},upload:async url=>positions.push(url.split('/').at(-1))});
  assert.deepEqual(positions,['3']);assert.equal(omitted.faultPhotosUploaded,3);
  const only=await queue.replaceFaultPhoto({...action,faultPhotoBlocked:true,faultPhotoIndex:0},null,true);
  await queue.sendFault(only,{post:async()=>assert.fail('confirmed base'),persist:async()=>{},upload:async()=>assert.fail('omitted photo must not upload')});
  assert.equal(only.faultPhotosUploaded,1,'last photo omission completes the queue item');
  console.log('OK: fault photos restored without name, independent queue progresses, dependent action waits, replacement and checkpoint recovery.');
})().catch(error=>{console.error(error);process.exitCode=1;});
