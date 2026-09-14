const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const find=name=>html.split(/\r?\n/).find(line=>line.trim().startsWith(`async function ${name}(`));
(async()=>{
  let jobs=[{id:'fixed-fault',url:'/api/operaciones/faults',body:{id:'fixed-fault'},files:[],label:'Falla'}],fail=true,posts=[];
  const ctx=vm.createContext({fieldActionsBusy:false,shouldSyncNow:()=>true,updateFieldActionStatus:async()=>{},toast:()=>{},loadMatrixData:async()=>{},fieldActionStorage:async(mode,item)=>{if(mode==='list')return structuredClone(jobs);if(mode==='delete')jobs=jobs.filter(j=>j.id!==item.id);if(mode==='save')jobs=jobs.map(j=>j.id===item.id?structuredClone(item):j);},operationsPost:async(url,body)=>{posts.push(body.id);if(fail)throw Error('Sin conexión');return {fault:{id:body.id}}}});
  vm.runInContext(find('drainFieldActions'),ctx);
  await ctx.drainFieldActions();assert.equal(jobs.length,1);assert.equal(jobs[0].error,'Sin conexión');
  fail=false;await ctx.drainFieldActions();assert.equal(jobs.length,0);assert.deepEqual(posts,['fixed-fault','fixed-fault']);
  let saved={},left=false,allowStorage=false;
  Object.assign(ctx,{persistEvidence:async()=>allowStorage,rememberObservationValues:()=>{},rememberPendingReportUpload:u=>saved[u.key]=u,pendingReportUploads:()=>saved,leaveFinishedReport:()=>left=true});
  vm.runInContext(find('queueReportOnDevice'),ctx);
  await assert.rejects(ctx.queueReportOnDevice({key:'r1',photos:[{}]}));assert.equal(left,false);assert.deepEqual(saved,{});
  allowStorage=true;await ctx.queueReportOnDevice({key:'r1',photos:[{}]});assert.equal(left,true);assert.ok(saved.r1);
  console.log('OK: falla conserva ID al reintentar; reporte sale sólo después del respaldo local.');
})().catch(error=>{console.error(error);process.exitCode=1});
