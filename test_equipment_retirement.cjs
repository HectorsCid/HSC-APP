const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function harness(post,owner=true,online=true){
  const elements={};
  function element(){return {hidden:false,disabled:false,textContent:'',innerHTML:'',classList:{values:new Set(),add(v){this.values.add(v)},remove(v){this.values.delete(v)},contains(v){return this.values.has(v)}},focus(){},listeners:{},addEventListener(k,fn){this.listeners[k]=fn},querySelector(id){return elements[id.slice(1)]??=(element())}};}
  elements.removeOperationalEquipment=element();
  const mounted=[],toasts=[],ctx={window:{},navigator:{onLine:online},document:{getElementById:id=>elements[id],createElement:()=>element(),body:{appendChild:e=>mounted.push(e)}}};
  vm.runInNewContext(fs.readFileSync('static/equipment_retirement.js','utf8'),ctx);
  let refreshed=0,shown=0;
  const api=ctx.window.HscEquipmentRetirement.mount({isOwner:owner,post,selected:()=>({id:'E/1',name:'Equipo'}),escape:s=>String(s).replaceAll('<','&lt;'),toast:s=>toasts.push(s),refresh:async()=>{refreshed++;return true},showClient:()=>shown++});
  return {elements,mounted,api,toasts,counts:()=>({refreshed,shown})};
}
(async()=>{
  let calls=[];
  const h=harness(async(url,body)=>{calls.push({url,body});return url.endsWith('removal-preview')?{ok:true,id:'E/1',action:'delete',fingerprint:'HASH',references:{}}:{ok:true,action:'delete',applied:true}});
  await h.elements.removeOperationalEquipment.onclick();
  assert.equal(calls.length,1);assert(calls[0].url.includes('E%2F1'));
  assert.equal(h.elements.equipmentRemovalConfirm.textContent,'Confirmar eliminación');
  assert.equal(h.elements.equipmentRemovalConfirm.hidden,false);
  await h.elements.equipmentRemovalConfirm.onclick();
  assert.equal(calls[1].body.confirm,true);assert.equal(calls[1].body.fingerprint,'HASH');
  assert.deepEqual(h.counts(),{refreshed:1,shown:1});
  assert.equal(h.mounted[0].classList.contains('open'),false);
  await h.elements.equipmentRemovalConfirm.onclick();assert.equal(calls.length,2);
  const fail=harness(async(url)=>{if(url.endsWith('remove'))throw Error('Falló la conexión');return {id:'E/1',action:'archive',references:{Reportes:1},fingerprint:'H'}});
  await fail.elements.removeOperationalEquipment.onclick();
  assert.equal(fail.elements.equipmentRemovalConfirm.textContent,'Confirmar baja');
  await fail.elements.equipmentRemovalConfirm.onclick();
  assert(fail.elements.equipmentRemovalError.textContent.includes('Falló la conexión'));
  assert.equal(fail.elements.equipmentRemovalClose.disabled,false);
  fail.elements.equipmentRemovalClose.onclick();assert.equal(fail.mounted[0].classList.contains('open'),false);
  const no=harness(async()=>{throw Error('No debe llamarse')},false);assert.equal(no.api,null);assert.equal(no.mounted.length,0);
  const offline=harness(async()=>{throw Error('No debe llamarse')},true,false);await offline.elements.removeOperationalEquipment.onclick();assert.equal(offline.toasts.length,1);
  const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
  assert(html.includes('id="removeOperationalEquipment" data-admin-only'));
  assert(html.includes('post:operationsPost'));assert(html.includes('Boolean(item.retired)'));
  console.log('OK: confirmación previa, baja con historial, errores cerrables, sin doble envío, solo propietario y solo conectado.');
})().catch(error=>{console.error(error);process.exitCode=1});
