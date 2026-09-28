const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),{test}=require('node:test');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const section=(a,b)=>html.slice(html.indexOf(a),html.indexOf(b,html.indexOf(a)));
const tick=()=>new Promise(resolve=>setImmediate(resolve));

function photoEditor(){
  const elements=new Map(),photos=[];
  const $=key=>{if(!elements.has(key))elements.set(key,{value:key==='#reportEvidenceSlot'?'1':'',textContent:'',setAttribute(name,value){this[name]=value}});return elements.get(key)};
  const ctx=vm.createContext({$,draftKey:()=> 'T1:E1:R1',evidenceForReport:()=>photos,vacantEvidenceSlots:()=>[1,2,3,4,5,6],
    account:{id:'T1'},reportPhotoBackupFailed:false,
    sourceFingerprint:f=>f.name,newEvidenceMutationId:()=> 'M'+photos.length,compressEvidenceFile:async f=>f,
    URL:{createObjectURL:()=> 'blob:fake',revokeObjectURL(){}},renderEvidence(){},shouldSyncNow:()=>false,evidenceBusy:false,
    current:'report',reportLocalBackupFailed:false,editorDirty:false,backupReportText(){},
    window:{addEventListener:(name,fn)=>ctx.exitHandler=fn},
    openEvidenceDb:async()=>({transaction(){const tx={error:new Error('QuotaExceededError'),objectStore:()=>({put(){}})};setImmediate(()=>tx.onabort());return tx},close(){}})});
  vm.runInContext(section('  async function persistEvidence(','  async function deleteEvidencePhoto('),ctx);
  vm.runInContext(section('  async function handleEvidenceSelection(',"  $('#reportEvidence').onchange="),ctx);
  vm.runInContext(html.split(/\r?\n/).find(x=>x.includes("window.addEventListener('beforeunload',event=>")),ctx);
  return {ctx,photos,$};
}
const fakePhoto=i=>({name:`camera-${i}.jpg`,size:999,type:'image/jpeg'});
function localEditors(){const disk=new Map(),storage={getItem:k=>disk.get(k)||null,setItem:(k,v)=>disk.set(k,v),removeItem:k=>disk.delete(k),get length(){return disk.size},key:i=>[...disk.keys()][i]};const create=require('./static/report_local_store.js').create;return {a:create(storage,'A','tab1'),b:create(storage,'A','tab2'),other:create(storage,'B','tab3'),storage};}
const draft=()=>({key:'A:E:R',accountId:'A',localBaseline:{p1:'10',p2:'20'},revision:1});

test('same-field alternatives survive restart and require an explicit choice',()=>{
  const {a,b,storage}=localEditors(),context=draft();a.save(context,{p1:'70',p2:'20'});b.save(context,{p1:'80',p2:'20'});
  const recovered=require('./static/report_local_store.js').create(storage,'A','restart').drafts()[context.key];
  assert.equal(recovered.localConflicts.length,1);assert.deepEqual(new Set([recovered.localConflicts[0].local,recovered.localConflicts[0].remote]),new Set(['70','80']));
  a.resolve(context.key,'p1','70');assert.equal(b.drafts()[context.key].localConflicts.length,0);assert.equal(b.drafts()[context.key].data.p1,'70');
});
test('a late acknowledgement only removes the versions it actually confirmed',()=>{
  const {a,b}=localEditors(),context=draft();const sent=a.save(context,{p1:'70',p2:'20'});
  b.save(context,{p1:'10',p2:'80'});a.forget(context.key,sent.localVersions);
  assert.equal(b.drafts()[context.key].data.p2,'80');
});
test('explicit discard removes every editor record and photo marker for that report',()=>{
  const {a,b}=localEditors(),context=draft();a.save(context,{p1:'70',p2:'20'});b.save(context,{p1:'10',p2:'80'});
  a.photo(context.key,'photo-a',false);b.photo(context.key,'photo-b',true);a.forgetAll(context.key);
  assert.equal(a.drafts()[context.key],undefined);
});
test('a confirmed report retires identical copies from every window but preserves different work',()=>{
  const {a,b}=localEditors(),context=draft();a.save(context,{p1:'70',p2:'20'});b.save(context,{p1:'70',p2:'20'});
  a.retireConfirmed(context.key,{p1:'70',p2:'20'});assert.equal(a.drafts()[context.key],undefined);
  a.save(context,{p1:'70',p2:'20'});b.save(context,{p1:'70',p2:'80'});a.retireConfirmed(context.key,{p1:'70',p2:'20'});
  assert.equal(a.drafts()[context.key].data.p2,'80');
});
test('pending submissions stay isolated by account and cannot be resurrected',()=>{
  const {a,b,other}=localEditors(),upload={key:'A:E:R',accountId:'A',submissionId:'one',data:{p1:'70'}};
  a.rememberPending(upload);b.rememberPending({...upload,submissionId:'two'});assert.equal(Object.values(a.pending()).length,2);
  assert.equal(Object.keys(other.pending()).length,0);assert.throws(()=>other.rememberPending(upload));
  a.forgetPending(upload.key,'one');assert.equal(b.rememberPending(upload),false);assert.equal(Object.values(a.pending())[0].submissionId,'two');
});
test('changing session DOM never rebinds old pending requests to the new account',async()=>{
  let sent;const body={dataset:{offlineShell:'false',userId:'A',appKind:'technician'}},window={fetch:async(input,options)=>{sent=options.headers.get('X-HSC-Account')}};
  const ctx=vm.createContext({document:{body},window,localStorage:{setItem(){},removeItem(){}},URL,Headers,location:{href:'https://hsc.invalid/hsc-tecnico/',origin:'https://hsc.invalid'}});
  vm.runInContext(fs.readFileSync('static/operations_session.js','utf8'),ctx);body.dataset.userId='B';
  await window.fetch('/api/operaciones/reports/finalize',{method:'POST'});assert.equal(sent,'A');
});
test('pending indicator counts the new per-window journal',()=>{
  const ctx=vm.createContext({account:{id:'A'},localStorage:{getItem:()=>null},pendingReportUploads:()=>({}),reportDrafts:()=>({r:{key:'r',data:{p1:'70'},baseValues:{p1:'10'},draftId:'draft'}}),HscReportCollaboration:require('./static/report_collaboration.js')});
  vm.runInContext(section('  function pendingReportDraftRows()','  function updateOfflineSyncUi()'),ctx);
  assert.equal(ctx.pendingReportDraftCount(),1);
});
test('a no-op journal without a server id does not leave a permanent pending banner',()=>{
  const ctx=vm.createContext({pendingReportUploads:()=>({}),reportDrafts:()=>({r:{key:'r',data:{p1:'10'},baseValues:{p1:'10'},draftId:''}}),HscReportCollaboration:require('./static/report_collaboration.js')});
  vm.runInContext(section('  function pendingReportDraftRows()','  function updateOfflineSyncUi()'),ctx);
  assert.equal(ctx.pendingReportDraftCount(),0);
});
test('a completed receipt without newer changes does not leave a permanent pending banner',()=>{
  const ctx=vm.createContext({pendingReportUploads:()=>({}),reportDrafts:()=>({r:{key:'r',data:{p1:'10'},baseValues:{p1:'10'},draftId:'D1',completedRemotely:true}}),HscReportCollaboration:require('./static/report_collaboration.js')});
  vm.runInContext(section('  function pendingReportDraftRows()','  function pendingSyncSummary()'),ctx);
  assert.equal(ctx.pendingReportDraftCount(),0);
});
test('a completed report with real local changes is reviewable instead of an impossible upload',()=>{
  const ctx=vm.createContext({pendingReportUploads:()=>({}),reportDrafts:()=>({r:{key:'r',data:{p1:'20'},baseValues:{p1:'10'},draftId:'D1',completedRemotely:true}}),HscReportCollaboration:require('./static/report_collaboration.js')});
  vm.runInContext(section('  function pendingReportDraftRows()','  function pendingSyncSummary()'),ctx);
  assert.equal(ctx.pendingReportDraftCount(),0);assert.equal(ctx.pendingReportReviewCount(),1);
});
test('manual sync removes an identical local receipt after confirming the completed report',async()=>{
  const forgotten=[],saved={key:'r',draftId:'D1',editReportId:'',completedRemotely:true,data:{p1:'10'},baseValues:{p1:'0'},conflicts:[],pendingPhotoIds:[]};
  const ctx=vm.createContext({shouldSyncNow:()=>true,reportDrafts:()=>({r:saved}),hscFetch:async()=>({ok:true,json:async()=>({ok:true,report:{state:'completed',payload:{p1:'10'}}})}),
    evidenceRecordsForKey:async()=>[],HscReportCollaboration:require('./static/report_collaboration.js'),forgetAllReportDrafts:key=>forgotten.push(key),persistReportContext(){}});
  vm.runInContext(section('  async function reconcileCompletedReportDrafts(','  async function resumeReportDrafts('),ctx);
  await ctx.reconcileCompletedReportDrafts(true);assert.deepEqual(forgotten,['r']);
});
test('discard hides immediately and cleans again after an in-flight save settles',async()=>{
  let release,forgotten=0,updated=0;const flight=new Promise(resolve=>release=resolve),context={key:'r',discarding:false};
  const ctx=vm.createContext({reportDrafts:()=>({r:{key:'r'}}),activeReportContext:context,clearTimeout(){},reportDraftSaveTimer:1,
    reportDraftFlights:new Map([['r',flight]]),reportPhotoFlights:new Map(),clearEvidenceDraft:async()=>{},
    forgetAllReportDrafts:()=>{forgotten+=1},updateOfflineSyncUi:()=>{updated+=1}});
  vm.runInContext(section('  async function discardPendingReportCopy(','  async function reconcileCompletedReportDrafts('),ctx);
  const pending=ctx.discardPendingReportCopy('r');await tick();assert.equal(context.discarding,true);assert.equal(await pending,true);assert.equal(forgotten,1);assert.equal(updated,1);assert.equal(ctx.activeReportContext,null);
  release();await tick();assert.equal(forgotten,2);assert.equal(updated,2);
});
test('discard also removes the legacy draft so migration cannot restore it',()=>{
  const disk=new Map([['legacy',JSON.stringify({r:{key:'r',data:{p1:'10'}}})]]),localStorage={getItem:key=>disk.get(key)||null,setItem:(key,value)=>disk.set(key,value),removeItem:key=>disk.delete(key)};
  const ctx=vm.createContext({localStorage,reportDraftStorageKey:'legacy',reportDiscardMarkerKey:key=>'discard:'+key,localReportStore:{forgetAll(){}},reportBaselineKey:key=>key+':base'});
  vm.runInContext(section('  function forgetAllReportDrafts(','  function persistReportContext('),ctx);ctx.forgetAllReportDrafts('r');
  assert.equal(JSON.parse(disk.get('legacy')).r,undefined);
});
test('a discard marker rejects responses from an older editor session',()=>{
  const disk=new Map([['prefix:discard:r','200']]),localStorage={getItem:key=>disk.get(key)||null},ctx=vm.createContext({localReportStore:{prefix:'prefix:'},localStorage});
  vm.runInContext(section('  const reportDiscardMarkerKey=','  function reportDrafts()'),ctx);
  assert.equal(ctx.reportContextWasDiscarded({key:'r',localSessionStartedAt:100}),true);
  assert.equal(ctx.reportContextWasDiscarded({key:'r',localSessionStartedAt:201}),false);
});
test('discard deletes the shared draft and every local copy only after confirmation',async()=>{
  const elements=new Map(),$=key=>{if(!elements.has(key))elements.set(key,{});return elements.get(key)},requests=[],forgotten=[],cleared=[];
  const ctx=vm.createContext({$,activeReportContext:{key:'r',draftId:'D1',equipment:'E',round:1},confirm:()=>true,evidenceBusy:false,
    reportDraftSaveTimer:0,clearTimeout(){},reportDraftFlights:new Map(),reportPhotoFlights:new Map(),shouldSyncNow:()=>true,
    operationsDelete:async url=>requests.push(url),pendingReportUploads:()=>({one:{key:'r',submissionId:'S1'}}),
    forgetPendingReportUpload:(key,id)=>forgotten.push(id),clearEvidenceDraft:async key=>cleared.push(key),forgetAllReportDrafts:key=>forgotten.push(key),
    releaseEvidenceMemory(){},updateOfflineSyncUi(){},canonicalPath:()=>[],showView(){},browserHistoryReady:false,toast(){}});
  vm.runInContext(section("  $('#discardDraft').onclick=async()=>{",'  function localPendingReport('),ctx);
  await $('#discardDraft').onclick();assert.deepEqual(requests,['/api/operaciones/reports/draft/D1']);assert.deepEqual(cleared,['r']);assert.deepEqual(forgotten,['S1','r']);
});
test('replacing shared evidence waits for the edit draft to be created',async()=>{
  const requests=[];let saves=0,refreshed=0;
  const context={key:'r',draftId:'',existingEvidence:[{mutation_id:'M1',position:1}]};
  const ctx=vm.createContext({activeReportContext:context,shouldSyncNow:()=>true,confirm:()=>true,
    saveActiveReportDraft:async()=>{saves+=1;context.draftId='D1'},operationsDelete:async url=>requests.push(url),
    localReportStore:{photo(){}},deleteEvidencePhoto:async()=>{},refreshReportCollaboration:async()=>{refreshed+=1}});
  vm.runInContext(section('  async function removeSharedEvidence(','  function renderExistingEvidence()'),ctx);
  assert.equal(await ctx.removeSharedEvidence('M1',1),true);assert.equal(saves,1);
  assert.deepEqual(requests,['/api/operaciones/reports/D1/evidence/M1']);assert.equal(refreshed,1);
});
test('legacy evidence without an identifier becomes replaceable after creating the edit draft',async()=>{
  const requests=[];let refreshed=0;
  const context={key:'r',draftId:'',existingEvidence:[{mutation_id:'',position:1}]};
  const ctx=vm.createContext({activeReportContext:context,shouldSyncNow:()=>true,confirm:()=>true,
    saveActiveReportDraft:async()=>{context.draftId='D1'},operationsDelete:async url=>requests.push(url),
    localReportStore:{photo(){}},deleteEvidencePhoto:async()=>{},refreshReportCollaboration:async()=>{
      refreshed+=1;context.existingEvidence=[{mutation_id:'legacy-original-1',position:1}];
    }});
  vm.runInContext(section('  async function removeSharedEvidence(','  function renderExistingEvidence()'),ctx);
  assert.equal(await ctx.removeSharedEvidence('',1),true);
  assert.deepEqual(requests,['/api/operaciones/reports/D1/evidence/legacy-original-1']);assert.equal(refreshed,2);
});
test('permission failure preserves the image until explicit recovery succeeds',async()=>{
  const {ctx,photos,$}=photoEditor();ctx.openEvidenceDb=async()=>{throw new DOMException('denied','SecurityError')};
  await ctx.handleEvidenceSelection({target:{files:[fakePhoto(1)]}});assert.equal(photos[0].backedUp,false);assert.match($('#evidenceError').textContent,/SIN RESPALDO/i);
  ctx.openEvidenceDb=async()=>({transaction(){const tx={objectStore:()=>({put(){}})};setImmediate(()=>tx.oncomplete());return tx},close(){}});
  await ctx.persistEvidence();assert.equal(photos[0].backedUp,true);assert.equal(ctx.reportPhotoBackupFailed,false);
});

test('IndexedDB abort never announces a protected photo and warns before closing',async()=>{
  const {ctx,photos,$}=photoEditor();
  await ctx.handleEvidenceSelection({target:{files:[fakePhoto(1)]}});
  assert.equal(photos.length,1,'Keep the original available to retry');
  assert.doesNotMatch($('#evidenceError').textContent,/Fotos conservadas/);
  assert.match($('#evidenceError').textContent,/sin respaldo|no.*respald|no.*guard/i);
  let warned=false;ctx.exitHandler({preventDefault(){warned=true}});
  assert.equal(warned,true,'An unprotected photo must block ordinary close');
});

test('first five photos are durable before the sixth compression can be suspended',async()=>{
  const {ctx}=photoEditor();let compressed=0,durable=0;
  ctx.persistEvidence=async(key,photos)=>{durable+=photos?.length||1;return true};
  ctx.compressEvidenceFile=async f=>{if(++compressed===6)await new Promise(()=>{});return f};
  ctx.handleEvidenceSelection({target:{files:Array.from({length:6},(_,i)=>fakePhoto(i))}});
  await tick();assert.equal(compressed,6);assert.ok(durable>=5);
});

test('two offline editors preserve both disjoint changes in durable recovery',()=>{
  const disk=new Map(),localStorage={getItem:k=>disk.get(k)||null,setItem:(k,v)=>disk.set(k,v),removeItem:k=>disk.delete(k),get length(){return disk.size},key:i=>[...disk.keys()][i]};
  function editor(id){const ctx=vm.createContext({localStorage,reportDraftStorageKey:'account-T1',reportBaselineKey:k=>k+':base',account:{id:'T1'},reportEditorId:id,HscReportCollaboration:require('./static/report_collaboration.js'),localReportStore:require('./static/report_local_store.js').create(localStorage,'T1',id),reportContextWasDiscarded:()=>false,reportChannel:null,window:{addEventListener(){}}});
    vm.runInContext(section('  function reportDrafts()','  function renderReportConflicts()'),ctx);return ctx;}
  const a=editor('tab-a'),b=editor('tab-b'),common={key:'T1:E1:R1',equipment:'E1',client:'C',round:'1',localBaseline:{p1:'10',p2:'20'}};
  a.persistReportContext({...common},{p1:'70',p2:'20'});
  b.persistReportContext({...common},{p1:'10',p2:'80'});
  const recovered=b.reportDrafts()[common.key];
  assert.equal(recovered.data.p1,'70');assert.equal(recovered.data.p2,'80');
});
