const assert=require('node:assert/strict');
const {visible,label,transmit,valvePosition,valveLabel}=require('./static/operations_repairs.js');
(async()=>{
  const data={id:'REP_1',mutation_id:'change1',equipment_key:'equipment:A1',result:'Quedó a 33 psi',photos:Array.from({length:9},(_,i)=>({id:'p'+i,stage:'after'}))};
  const row={id:data.id,data,localState:'pending',files:data.photos.map(p=>({id:p.id,blob:new Blob(['image']),name:'photo.jpg'}))};
  assert.equal(visible(row,'33 psi','equipment:A1'),true);assert.equal(visible(row,'33 psi','equipment:B1'),false);assert.equal(label(row),'Pendiente de envío');
  assert.equal(valvePosition([{steps:1},{steps:1},{steps:-1}]),1);assert.equal(valveLabel(1),'1/8 de vuelta a la derecha del inicio');assert.equal(valveLabel(0),'Posición original');
  let calls=[];const request=async(url,options)=>{calls.push({url,options});return {repair:{...data,status:'completed'}}};
  const result=await transmit(row,request);assert.equal(result.status,'completed');assert.equal(calls.length,11);assert.ok(calls[10].url.endsWith('/finish'));
  assert.equal(calls[1].options.body.get('mutation_id'),'change1');assert.equal(JSON.parse(calls[0].options.body).id,'REP_1');
  calls=[];let failed=false;try{await transmit(row,async(url,options)=>{calls.push(url);if(url.endsWith('/photos/p2'))throw Error('network');return {repair:data}})}catch(_){failed=true}
  assert.equal(failed,true);assert.equal(calls.some(u=>u.endsWith('/finish')),false);assert.equal(row.localState,'pending');assert.equal(row.files.length,9);
  calls=[];await transmit(row,request);assert.equal(JSON.parse(calls[0].options.body).mutation_id,'change1');
  await assert.rejects(transmit(row,async()=>({repair:{mutation_id:'newer'}})),error=>error.status===409);
  const vm=require('node:vm'),source=require('node:fs').readFileSync('./static/operations_repairs.js','utf8');
  const backupCode=source.slice(source.indexOf('async function backup()'),source.indexOf('function autosave()'));
  const leaveCode=source.slice(source.indexOf('async function leave()'),source.indexOf("$('#repairSaveDraft').onclick"));
  const status={textContent:''},context={clearTimeout(){},timer:1,editor:{id:'local',data:{work:'no perder'},files:row.files},capture(){},structuredClone,put:async()=>{throw Error('Sin espacio')},$:()=>status,lastError:'',photoBusy:false,api:{current:()=> 'repairEditor',toast(){}}};
  vm.createContext(context);vm.runInContext(backupCode+leaveCode,context);
  assert.equal(await vm.runInContext('leave()',context),false,'A failed durable write must retain the editor');
  assert.equal(context.editor.data.work,'no perder');assert.equal(context.editor.files.length,9);
  context.put=async()=>{};assert.equal(await vm.runInContext('leave()',context),true);assert.equal(context.timer,null);
  context.photoBusy=true;assert.equal(await vm.runInContext('leave()',context),false);
  console.log('Repair transport: nine photos, ordered finalization, retries and conflict protection passed');
})().catch(error=>{console.error(error);process.exit(1)});
