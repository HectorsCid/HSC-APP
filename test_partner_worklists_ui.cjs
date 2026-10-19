const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
for(const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))if(script[1].trim())new vm.Script(script[1].replace(/\{\{[\s\S]*?\}\}/g,'null'));
assert.equal((html.match(/id="equipmentFilters"/g)||[]).length,1);
for(const filter of ['all','done','pending']){
  assert.ok(html.includes(`class="stat${filter==='all'?' active':''}" data-filter="${filter}"`));
  assert.ok(html.includes(`class="stat" data-partner-filter="${filter}"`));
}
assert.ok(!html.includes('data-filter="done">Realizados'));
assert.ok(html.includes("$$('[data-partner-filter]').forEach"));
assert.ok(html.includes("$$('.nav-btn').forEach"));
assert.ok(html.includes("$('#roundPendingCount').textContent=active.length-completed"));
const elements=new Map();
function get(id){if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',textContent:'',disabled:false,hidden:false,classList:{add(){},remove(){}},focus(){},querySelectorAll(){return this.checked||[];}});return elements.get(id);}
// Execute the real technician renderer: filters and counts follow the selected round.
const noop=()=>{};
const filterElements=new Map();
const filterGet=id=>{if(!filterElements.has(id))filterElements.set(id,{innerHTML:'',textContent:'',style:{}});return filterElements.get(id);};
const filterContext=vm.createContext({
  $:filterGet,$$:()=>[],renderClientDetail:noop,hydratePhotos:noop,renderClientFaults:noop,renderEquipmentDetail:noop,bindRoundReports:noop,
  equipment:[['A1','Uno',''],['A2','Dos',''],['B1','Otro','']],equipmentMeta:{A1:{client:'A',active:true},A2:{client:'A',active:true},B1:{client:'B',active:true}},
  demoRounds:{'1':{done:['A1']},'2':{done:['A2']}},selectedRound:'1',selectedClient:'A',equipmentFilter:'done',
  byEquipmentId:(a,b)=>a[0].localeCompare(b[0]),equipmentCard:e=>`[${e[0]}]`,reportCards:()=>'',
});
const renderStart=html.indexOf('function renderEquipment(){');
vm.runInContext(html.slice(renderStart,html.indexOf('\n  }',renderStart)+4),filterContext);
filterContext.renderEquipment();
assert.equal(filterGet('#equipmentList').innerHTML,'[A1]');
assert.equal(filterGet('#roundReports').textContent,1);
assert.equal(filterGet('#roundPendingCount').textContent,1);
filterContext.equipmentFilter='pending';filterContext.renderEquipment();
assert.equal(filterGet('#equipmentList').innerHTML,'[A2]');
filterContext.selectedRound='2';filterContext.renderEquipment();
assert.equal(filterGet('#equipmentList').innerHTML,'[A1]');
let id=0,failed=true,posted=[],accepted=[],toasts=[];
const state={clientId:'A',round:'2',worklists:[],equipment:[{id:'A1',name:'Uno',client_id:'A',active:true},{id:'B1',name:'Otro cliente',client_id:'B',active:true}]};
const context=vm.createContext({crypto:{randomUUID:()=>`id-${++id}`},Date,document:{createElement:()=>get(`element-${++id}`),body:{append(){}},getElementById:get,querySelector:()=>({after(){},append(){}})}});
vm.runInContext(fs.readFileSync('static/operations_partner_lists.js','utf8'),context);
const ui=context.HscPartnerLists.mount({canCreate:true,escape:x=>String(x).replaceAll('<','&lt;'),state:()=>state,toast:x=>toasts.push(x),accept:list=>{accepted.push(list);state.worklists.push(list);},save:async body=>{posted.push(body);if(failed)throw Error('Sin conexión');return {worklist:{...body,origin:'client',revision:1}};}});
(async()=>{
  get('partnerGenerateList').onclick();
  assert.ok(get('partnerListChoices').innerHTML.includes('A1'));
  assert.ok(!get('partnerListChoices').innerHTML.includes('B1'));
  get('partnerListName').value='Cocina';get('partnerListChoices').checked=[{value:'A1'}];
  await get('partnerListForm').onsubmit({preventDefault(){}});
  assert.equal(accepted.length,0);assert.equal(get('partnerListError').textContent,'Sin conexión');
  assert.equal(get('partnerListSave').disabled,false);
  failed=false;
  await get('partnerListForm').onsubmit({preventDefault(){}});
  assert.equal(posted[0].id,posted[1].id);assert.equal(posted[0].mutation_id,posted[1].mutation_id);
  assert.equal(accepted.length,1);assert.equal(accepted[0].client_id,'A');
  assert.equal(accepted[0].items[0].round,'2');assert.equal(accepted[0].items[0].equipment_id,'A1');
  assert.ok(get('partnerWorklistCards').innerHTML.includes('Cocina'));
  assert.ok(toasts.at(-1).includes('HSC ya puede verla'));
  console.log('OK: filtros superiores, scripts válidos, equipos propios, guardado confirmado y reintento idempotente');
})().catch(error=>{console.error(error);process.exitCode=1;});
