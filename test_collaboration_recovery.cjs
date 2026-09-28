const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const rules=require('./static/report_collaboration.js'),preflight=require('./static/photo_preflight.js');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const section=(a,b)=>html.slice(html.indexOf(a),html.indexOf(b,html.indexOf(a)));
const line=prefix=>html.split(/\r?\n/).find(row=>row.trim().startsWith(prefix));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  // Unchanged local fields adopt remote state and advance their baseline together.
  let merged=rules.reconcile({notas:'old',p1:'72'},{notas:'old',p1:''},{notas:'B',p1:'72'},{notas:'old',p1:'72'});
  assert.deepEqual(merged.values,{notas:'B',p1:'72'});assert.deepEqual(rules.changes(merged.values,merged.base),{});
  merged=rules.reconcile({inicio:'2026-09-28',notas:'default',p1:'72'},{inicio:'2026-09-28',notas:'default',p1:''},{inicio:'2026-09-20',notas:'B',p1:''});
  assert.equal(merged.values.inicio,'2026-09-20');assert.equal(merged.values.notas,'B');assert.deepEqual(rules.changes(merged.values,merged.base),{p1:'72'});
  merged=rules.reconcile({notas:'A'},{notas:'old'},{notas:'B'});
  assert.equal(merged.conflicts.length,1);assert.equal(merged.values.notas,'A');assert.equal(merged.base.notas,'old');
  const notes={type:'textarea',value:'old'},fields={notas:notes,namedItem:name=>name==='notas'?notes:null},faultFields={};
  const formFill=vm.createContext({defaultReportNotes:'default',
    $:selector=>selector==='#reportForm'?{elements:fields}:selector==='#reportSevereFault'?{checked:false}:faultFields});
  vm.runInContext(line('function updateSevereFaultFields(')+'\n'+line('function fillReportFields('),formFill);
  formFill.fillReportFields({notas:''});assert.equal(notes.value,'','Do not refill notes deliberately cleared by a collaborator');
  const upload={submissionId:'mine',client:'C',equipment:'E',round:'1',photos:[{mutationId:'photo-1'}]};
  const report={submission_id:'other',client_id:'C',equipment_id:'E',round:'1',evidence:[{mutation_id:'photo-1'}]};
  assert.equal(rules.confirmed(upload,report),false);report.submission_id='mine';assert.equal(rules.confirmed(upload,report),true);report.evidence=[];assert.equal(rules.confirmed(upload,report),false);

  // Run the actual editor save loop with controlled response order and typing during requests.
  let form={inicio:'2026-09-29',fin:'2026-09-29',notas:'old'},pending=[],calls=[];
  const context={key:'draft',client:'C',equipment:'E',round:'1',localBaseline:{inicio:'2026-09-28',fin:'2026-09-28',notas:'old'},revision:1,draftId:'R',conflicts:[]};
  const editor=vm.createContext({activeReportContext:context,reportDraftFlights:new Map(),HscReportCollaboration:rules,
    collectReportData:()=>({...form}),fillReportFields:values=>{form={...values}},ensureReportDates(){},renderReportConflicts(){},
    persistReportContext:(ctx,data)=>ctx.localData={...data},pendingReportUploads:()=>({}),shouldSyncNow:()=>true,toast(){},$:()=>({textContent:'',elements:{inicio:{get value(){return form.inicio}}}}),
    operationsPost:async(url,body)=>{calls.push(body);return new Promise(resolve=>pending.push(resolve))}
  });
  vm.runInContext(section('  function acceptReportState(','  function scheduleReportDraftSave(')+line('function changedReportFields(')+section('  async function saveReportContext(','  $(\'#saveDraft\').onclick'),editor);
  const first=editor.saveReportContext(context);await tick();assert.equal(calls.length,1);
  form.inicio=form.fin='2026-09-30';const second=editor.saveReportContext(context);await tick();assert.equal(calls.length,1,'Only one write can be in flight for a report');
  pending.shift()({draft:{id:'R',revision:2,payload:{...calls[0].payload,notas:'B'}}});await tick();
  assert.equal(form.inicio,'2026-09-30','Late acknowledgement must not erase newer typing');assert.equal(form.notas,'B');assert.equal(calls.length,2);
  assert.deepEqual(Object.keys(calls[1].changed_fields).sort(),['fin','inicio']);assert.equal(calls[1].base_values.inicio,'2026-09-29');
  pending.shift()({draft:{id:'R',revision:3,payload:calls[1].payload}});await Promise.all([first,second]);assert.equal(editor.reportDraftFlights.size,0);
  context.draftId='';context.localBaseline={...form};context.revision=-1;
  const creating=editor.saveReportContext(context);await tick();form.notas='Typed while creating';
  pending.shift()({draft:{id:'new',revision:0,payload:calls.at(-1).payload}});await tick();assert.equal(form.notas,'Typed while creating');
  pending.shift()({draft:{id:'new',revision:1,payload:calls.at(-1).payload}});await creating;

  // A receipt only retires the photos it acknowledges; newer work in another tab survives.
  let drafts={local:{key:'local',data:{p1:'newer'},baseValues:{p1:'old'}}},records=[{mutationId:'confirmed'},{mutationId:'later'}],forgotten=0;
  const cleanup=vm.createContext({account:{id:'T'},HscReportCollaboration:rules,localStorage:{removeItem(){}},reportBaselineKey:key=>key+':base',
    reportDrafts:()=>drafts,deleteEvidencePhoto:async(key,id)=>{records=records.filter(row=>row.mutationId!==id)},evidenceRecordsForKey:async()=>records,
    persistReportContext:(ctx,data)=>drafts[ctx.key]={...ctx,data},forgetReportDraft:key=>delete drafts[key],forgetPendingReportUpload:()=>forgotten++,
    URL:{revokeObjectURL(){}},reportEvidenceByKey:new Map(),loadedEvidenceKeys:new Set(),backgroundReportUploads:new Map()});
  vm.runInContext(section('  async function completePendingReportUpload(','  function blockReportUpload('),cleanup);
  await cleanup.completePendingReportUpload({key:'local',id:'R',data:{p1:'old'},photos:[{mutationId:'confirmed'}]});
  assert.deepEqual(records,[{mutationId:'later'}]);assert.equal(drafts.local.data.p1,'newer');assert.equal(drafts.local.completedRemotely,true);assert.equal(forgotten,1);
  drafts={local:{key:'local',data:{p1:'old'}}};records=[];
  await cleanup.completePendingReportUpload({key:'local',id:'R',data:{p1:'old'},photos:[]});assert.equal(drafts.local,undefined);

  // Manual synchronization resumes a draft and its durable photo without finalizing it.
  let photoAttempts=0,manualSave=false;
  const offline=vm.createContext({shouldSyncNow:manual=>manual,activeReportContext:null,pendingReportUploads:()=>({}),
    reportDrafts:()=>({K:{key:'K',data:{p1:'81'},baseValues:{p1:''}}}),
    saveReportContext:async(ctx,message,manual)=>{manualSave=manual;ctx.draftId='R';return {id:'R'}},
    evidenceRecordsForKey:async()=>[{name:'one.jpg',type:'image/jpeg',blob:new Blob(['photo']),mutationId:'photo',position:1,uploaded:false}],File,
    uploadDraftEvidence:async(photos,ctx,manual)=>{assert.equal(manual,true);assert.equal(ctx.draftId,'R');assert.equal(photos[0].mutationId,'photo');photoAttempts++}
  });
  vm.runInContext(section('  async function resumeReportDrafts(','  const defaultReportNotes='),offline);
  await offline.resumeReportDrafts(false);assert.equal(photoAttempts,0);
  await offline.resumeReportDrafts(true);assert.equal(photoAttempts,1);assert.equal(manualSave,true);

  // Cold navigation falls back to the neutral shell, while authentication responses never do.
  const handlers={},cached=[];let network='down',response;
  vm.runInNewContext(fs.readFileSync('static/service-worker.js','utf8'),{
    self:{location:{origin:'https://hsc.test'},addEventListener:(name,fn)=>handlers[name]=fn},URL,Response,AbortController,setTimeout,clearTimeout,
    caches:{match:async path=>{cached.push(path);return new Response('neutral shell')}},fetch:async()=>{if(network==='down')throw Error('offline');return new Response('login',{status:401})}
  });
  const event={request:{method:'GET',mode:'navigate',url:'https://hsc.test/hsc-tecnico/'},respondWith:p=>response=p};handlers.fetch(event);
  assert.equal(await (await response).text(),'neutral shell');assert.equal(cached.at(-1),'/offline/technician');
  network='up';handlers.fetch(event);assert.equal((await response).status,401);assert.equal(cached.length,1);

  // Neutral shell identity restoration, logout and cross-account request headers.
  const storage=new Map(),sessionSource=fs.readFileSync('static/operations_session.js','utf8');
  function session(offline,user){const body={dataset:{offlineShell:String(offline),appKind:'technician',userId:user||'',operationsOwner:'false'},style:{}};let request;
    const ctx={document:{body},localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},URL,Headers,location:{href:'https://hsc.test/hsc-tecnico/',origin:'https://hsc.test'},window:{fetch:(...args)=>request=args}};
    vm.runInNewContext(sessionSource,ctx);return {ctx,body,get request(){return request}};}
  assert.equal(session(true).ctx.window.HscOperationsSession.ready,false);
  session(false,'T1');const restored=session(true);assert.equal(restored.body.dataset.userId,'T1');
  restored.ctx.window.fetch('/api/operaciones/reports/draft');assert.equal(restored.request[1].headers.get('X-HSC-Account'),'T1');
  restored.ctx.window.HscOperationsSession.clear();assert.equal(session(true).ctx.window.HscOperationsSession.ready,false);
  session(false,'T2');assert.equal(session(true).body.dataset.userId,'T2');

  // Reject large PNGs before the browser creates a decoded image.
  function png(w,h){const b=new ArrayBuffer(24),v=new DataView(b);v.setUint32(0,0x89504e47);v.setUint32(16,w);v.setUint32(20,h);return b}
  assert.deepEqual(await preflight.check(new Blob([png(4000,3000)])),[4000,3000]);
  await assert.rejects(preflight.check(new Blob([png(8000,6000)])),/24 megapíxeles/);
  await assert.rejects(preflight.check(new Blob(['invalid'])),/verificar/);
  for(let n=0;n<30;n++)assert.doesNotThrow(()=>preflight.dimensions(new ArrayBuffer(n)));
  console.log('OK: field reconciliation, dates, typing during save/create, exact receipts, cold offline shell, account isolation and image preflight.');
})().catch(error=>{console.error(error);process.exitCode=1});
