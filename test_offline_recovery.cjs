const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const offline=require('./static/operations_offline.js');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
for(const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))if(match[1].trim())new vm.Script(match[1]);
assert.equal(offline.canSend('auto',undefined,false),true);
assert.equal(offline.canSend('wifi',undefined,false),false);
assert.equal(offline.canSend('wifi',undefined,true),true);
assert.equal(offline.canSend('offline','wifi',false),false);
const base={clients:[{id:'UDA',name:'Arkansas'}],equipment:[{id:'UDA14',name:'Anterior',client_id:'UDA'}],tasks:[],expenses:[],faults:[]};
const actions=[{id:'edit',createdAt:1,url:'/api/operaciones/equipment',body:{items:[{id:'UDA14',name:'Nuevo',client_id:'UDA'}]},photoPreview:'blob:local'},
 {id:'task',createdAt:2,url:'/api/operaciones/tasks',body:{id:'T1',title:'Revisar'}},
 {id:'expense',createdAt:3,url:'/api/operaciones/expenses',body:{id:'G1',amount:100}},
 {id:'fault',createdAt:4,url:'/api/operaciones/faults',body:{id:'F1',description:'Falla'}}];
for(let n=0;n<3;n++){
 const view=offline.overlay(JSON.parse(JSON.stringify(base)),actions);
 assert.equal(view.equipment[0].name,'Nuevo');assert.equal(view.equipment[0].photo_url,'blob:local');
 assert.equal(view.equipment[0].pending_upload,true);assert.equal(view.tasks.length,1);
 assert.equal(view.expenses[0].amount,100);assert.equal(view.faults[0].id,'F1');
 assert.equal(base.equipment[0].name,'Anterior');
}
assert.equal(offline.overlay(base,actions,false).equipment[0].pending_upload,false);
const source=html.slice(html.indexOf('  async function hscFetch('),html.indexOf('  function cacheServerPayload('));
(async()=>{
 let mode='ok';
 const ctx=vm.createContext({AbortController,setTimeout,clearTimeout,serverConnection:'checking',fetch:async(url,{signal})=>{
   if(mode==='timeout')return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(Error(),{name:'AbortError'}))));
   const status=mode==='auth'?401:mode==='server'?503:200;
   return {status,ok:status===200,redirected:false,text:async()=>JSON.stringify({ok:status===200})};
 }});
 vm.runInContext(source,ctx);
 assert.equal((await ctx.hscFetch('/test')).status,200);assert.equal(ctx.serverConnection,'online');
 mode='server';await ctx.hscFetch('/test');assert.equal(ctx.serverConnection,'server');
 mode='auth';await ctx.hscFetch('/test');assert.equal(ctx.serverConnection,'auth');
 mode='timeout';await assert.rejects(ctx.hscFetch('/test',{},5),/tardó demasiado/);assert.equal(ctx.serverConnection,'unreachable');
 mode='ok';await ctx.hscFetch('/test');assert.equal(ctx.serverConnection,'online');
 console.log('OK: scripts compilables; política de red, ediciones/foto/gasto/falla locales sobreviven refrescos; 503, 401, timeout y recuperación.');
})().catch(error=>{console.error(error);process.exitCode=1});
