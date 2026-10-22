const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const cards=source.slice(source.indexOf('  function faultCards('),source.indexOf('  function bindPartnerFaults('));
const context={equipment:[['VC3','Gerencia planta']],equipmentMeta:{VC3:{client:'VC'}},clientName:()=> 'Cliente',account:{isOwner:true},escapeHtml:v=>String(v||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')};
vm.runInNewContext(cards,context);
const fault={id:'F1',equipment_id:'VC3',description:'Capacitor dañado <prueba>',reported_at:'2026-09-23T23:45:56-06:00',status:'Reparada',priority:'Electrónica'};
let html=context.faultCards([fault]);
assert(html.includes('<h3>Gerencia planta<span class="fault-equipment-id">VC3</span></h3>'));
assert(html.indexOf('Gerencia planta')<html.indexOf('Capacitor dañado'));
assert(html.includes('<p class="fault-description">Capacitor dañado &lt;prueba></p>'));
assert(html.includes('<p class="fault-date">23/09/2026</p>'));
assert(!html.includes('23:45'));assert(!html.includes('-06:00'));
assert(html.includes('fault-card resolved'));assert(html.includes('data-fault-menu="F1"'));assert(html.includes('Electrónica'));
for(const date of ['23/09/2026 10:34:00','2026-09-23','2026-09-23 23:59:00+00:00','23/09/2026, 10:00 a. m.']){
  assert(context.faultCards([{...fault,reported_at:date}]).includes('<p class="fault-date">23/09/2026</p>'));
}
context.account.isOwner=false;html=context.faultCards([{...fault,equipment_id:'NO_EXISTE',reported_at:''}],true);
assert(html.includes('NO_EXISTE'));assert(html.includes('Cliente'));assert(!html.includes('data-fault-menu'));assert(!html.includes('fault-date'));
assert(context.faultCards([]).includes('No hay fallas'));
console.log('OK: equipo e ID primero, falla debajo, fecha sin hora ni cambio de día, estados/acciones intactos y texto escapado.');
