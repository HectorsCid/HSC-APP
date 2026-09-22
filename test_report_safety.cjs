const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const line=prefix=>html.split(/\r?\n/).find(row=>row.trim().startsWith(prefix));
const sign=vm.createContext({});vm.runInContext(line('function toggleReportTemperatureSign('),sign);
assert.equal(sign.toggleReportTemperatureSign('18.5'),'-18.5');
assert.equal(sign.toggleReportTemperatureSign('-18.5'),'18.5');
assert.ok(html.includes("for(const name of ['t1','t2'])"),'Ambas temperaturas del reporte normal deben ofrecer el signo menos');
(async()=>{
  let full=true,photoSaved=false,localWrites=0;
  const ctx=vm.createContext({activeReportContext:{key:'T1:A1:R1'},evidenceBusy:false,
    reportLocalBackupFailed:false,collectReportData:()=>({p1:'33'}),
    localStorage:{setItem(){if(full)throw Error('quota');localWrites++;}},
    persistEvidence:async()=>photoSaved,evidenceForReport:()=>[{}],
    current:'report',editorDirty:false,toast(){},saveActiveReportDraft(){},
    originalShowView(name){ctx.current=name;},parentView:()=> 'equipment',
    document:{querySelector:()=>null},canonicalPath:x=>[x],historyStack:[],browserHistoryReady:false,
  });
  vm.runInContext(line('function backupReportText(')+'\n'+line('async function backupReportBeforeLeaving(')+'\n'+line('showView=function(')+'\n'+line('async function goBack('),ctx);
  await ctx.goBack();assert.equal(ctx.current,'report','Sin espacio no debe abandonar formulario');
  assert.equal(ctx.reportLocalBackupFailed,true);
  full=false;await ctx.goBack();assert.equal(ctx.current,'report','Sin respaldo de fotos no debe abandonar formulario');
  photoSaved=true;await ctx.goBack();assert.equal(ctx.current,'equipment');assert.equal(ctx.reportLocalBackupFailed,false);
  ctx.current='report';ctx.evidenceBusy=true;await ctx.showView('profile');assert.equal(ctx.current,'report','Preparar cámara bloquea salida prematura');
  assert.ok(localWrites);

  const savedReports=new Map();let networkUp=true;
  const editor=vm.createContext({account:{id:'T1'},shouldSyncNow:()=>networkUp,toast(){},
    localStorage:{setItem:(k,v)=>savedReports.set(k,v),getItem:k=>savedReports.get(k)},
    hscFetch:async()=>({ok:true,json:async()=>({ok:true,report:{id:'R1',payload:{p1:'33'}}})}),
  });
  vm.runInContext(html.slice(html.indexOf('  async function loadCompletedReportForEdit('),html.indexOf('  async function saveActiveReportDraft(')),editor);
  assert.equal((await editor.loadCompletedReportForEdit('R1')).payload.p1,'33');
  networkUp=false;assert.equal((await editor.loadCompletedReportForEdit('R1')).payload.p1,'33');
  await assert.rejects(editor.loadCompletedReportForEdit('R2'),/edición vacía/);
  editor.account.id='T2';await assert.rejects(editor.loadCompletedReportForEdit('R1'),/edición vacía/);

  let resetCalls=0,cleared=0;
  const upload={id:'draft-edit',key:'local',client:'A',equipment:'A1',round:'1',photos:[],data:{p1:'33'}};
  const report={id:'original',client_id:'A',equipment_id:'A1',round:'1',matrix_id:'A1_R 1',state:'completed',completed:true};
  const worker=vm.createContext({hscFetch:async()=>({ok:true,json:async()=>({ok:true,report})}),
    operationsPost:async url=>{assert.equal(url,'/api/operaciones/reports/finalize');return {report};},
    fieldRevision:0,lastServerPayload:{reports:[]},matrixReports:[{id:'offline:local'}],matrixFaults:[],
    cacheServerPayload(){},completePendingReportUpload:async()=>cleared++,refreshRound(){},toast(){},
    URL:{revokeObjectURL(){}},backgroundReportUploads:new Map(),
  });
  const recovery=html.slice(html.indexOf('  async function recoverConfirmedReportUpload('),html.indexOf('  async function restoreEvidence('));
  vm.runInContext(recovery,worker);
  assert.equal(await worker.recoverConfirmedReportUpload(upload),true);assert.equal(cleared,1);
  assert.equal(worker.matrixReports[0].id,'original');
  worker.operationsPost=async()=>{throw Error('fault delivery transaction incomplete')};
  await assert.rejects(worker.recoverConfirmedReportUpload(upload));assert.equal(cleared,1,'No limpiar antes de completar efectos pendientes');
  worker.recoverConfirmedReportUpload=async()=>true;
  worker.operationsPost=async()=>{resetCalls++;};
  vm.runInContext(html.slice(html.indexOf('  async function finishReportInBackground('),html.indexOf('  async function recoverConfirmedReportUpload(')),worker);
  await worker.finishReportInBackground(upload);assert.equal(resetCalls,0,'Una confirmación recuperada no reinicia fotos');
  console.log('OK: fallos de espacio/fotos retienen formulario; recibos recuperan folio y evitan reenviar fotos.');
})().catch(error=>{console.error(error);process.exitCode=1});
