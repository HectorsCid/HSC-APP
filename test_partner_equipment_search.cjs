const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
for(const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))if(script[1].trim())new vm.Script(script[1].replace(/\{\{[\s\S]*?\}\}/g,'null'));
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,{value:'',hidden:false,textContent:'',scrollTop:80});return elements.get(id)};
const cards=['VC3','VC27','VC28','OTHER','OFF'].map(id=>({dataset:{partnerEquipment:id},hidden:false}));
let doneIds=new Set(['VC3']);
const context=vm.createContext({$,Map,String,Boolean,$$:()=>cards,selectedClient:'VC',selectedRound:'1',partnerEquipmentFilter:'all',
  equipment:[['VC3','Gerencia planta'],['VC27','Sala Quiropráctico'],['VC28','Laboratorio de Calidad'],['OTHER','Gerencia'],['OFF','Gerencia inactiva']],
  equipmentMeta:{VC3:{client:'VC',active:true,location:'Oficina 2'},VC27:{client:'VC',active:true,location:'Área médica'},VC28:{client:'VC',active:true,location:'Planta baja'},OTHER:{client:'B',active:true},OFF:{client:'VC',active:false}},
  reportForEquipment:id=>({completed:doneIds.has(id)})});
const start=html.indexOf('  function filterPartnerEquipment(){'),end=html.indexOf('  function renderPartnerDashboard(){',start);
vm.runInContext(html.slice(start,end),context);
const visible=()=>cards.filter(c=>!c.hidden).map(c=>c.dataset.partnerEquipment);
function search(query){$('#partnerEquipmentSearch').value=query;$('#partnerEquipmentSearch').oninput()}
search('g');assert.deepEqual(visible(),['VC3']);assert.equal($('#partnerEquipmentTotal').textContent,'1 de 3 equipos · R1');
search('quiropractico');assert.deepEqual(visible(),['VC27']);
search('VC28');assert.deepEqual(visible(),['VC28']);
search('area medica');assert.deepEqual(visible(),['VC27']);
search('planta');assert.deepEqual(visible(),['VC3','VC28']);
context.partnerEquipmentFilter='pending';context.filterPartnerEquipment();assert.deepEqual(visible(),['VC28']);assert.equal($('#partnerEquipmentTotal').textContent,'1 de 2 equipos · R1');
context.partnerEquipmentFilter='done';context.filterPartnerEquipment();assert.deepEqual(visible(),['VC3']);
search('inexistente');assert.deepEqual(visible(),[]);assert.equal($('#partnerEquipmentNoMatches').hidden,false);
$('#partnerEquipmentSearch').value='';$('#partnerEquipmentSearch').onsearch();assert.deepEqual(visible(),['VC3']);assert.equal($('#partnerEquipmentNoMatches').hidden,true);
context.selectedRound='2';doneIds=new Set(['VC28']);context.filterPartnerEquipment();assert.deepEqual(visible(),['VC28']);assert.equal($('#partnerEquipmentTotal').textContent,'1 equipos · R2');
context.partnerEquipmentFilter='all';search('   ');assert.deepEqual(visible(),['VC3','VC27','VC28']);assert.equal($('#partnerEquipmentFull').scrollTop,0);
assert(html.includes('filterPartnerEquipment();'));assert(html.includes('id="partnerEquipmentSearch"'));
console.log('OK: búsqueda por nombre, clave y ubicación; sin acentos; filtros y ronda combinados; limpiar y sin resultados; empresa e inactivos protegidos.');
