const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8'),nodes=new Map();
const $=key=>{if(!nodes.has(key))nodes.set(key,{value:'',hidden:false,textContent:'',scrollTop:100});return nodes.get(key)};
const cards=['VC3','VC4','VC5'].map(id=>({dataset:{equipmentRow:id},hidden:false}));
$('#equipmentList').addEventListener=(event,fn)=>{$('#equipmentList')[event]=fn};
const context=vm.createContext({$,String,URL,location:new URL('https://hsc.test/'),$$:()=>cards,
  equipment:[['VC3','Gerencia planta','Carrier'],['VC4','Calidad','Daikin'],['VC5','Dirección','Trane']],
  equipmentMeta:{VC3:{location:'Oficina 2',model:'ABC-123',serial:'SN99',type:'Minisplit'},VC4:{department:'Ingeniería'},VC5:{}}});
const start=html.indexOf('  function filterTechnicianEquipment(){'),end=html.indexOf('  function renderEquipmentDetail(){',start);
vm.runInContext(html.slice(start,end),context);
function find(query){$('#technicianEquipmentSearch').value=query;$('#technicianEquipmentSearch').oninput();return cards.filter(c=>!c.hidden).map(c=>c.dataset.equipmentRow)}
const elements=[];context.document={createElement:tag=>{const el={tag,style:{},setAttribute(){},append(){},addEventListener(){},showModal(){this.open=true},close(){this.open=false}};elements.push(el);return el},body:{append(){}}};
context.equipmentMeta.VC3.photo='/api/operaciones/photo/equipment/VC3?v=2';$('#equipmentList').click({target:{closest:()=>({dataset:{enlargeEquipment:'VC3'}})}});
const zoom=elements.find(e=>e.tag==='img');assert.equal(zoom.src,'https://hsc.test/api/operaciones/photo/equipment/VC3?v=2&preview=1440');zoom.onerror();assert.equal(zoom.src,context.equipmentMeta.VC3.photo);assert(elements[0].open);elements.find(e=>e.tag==='button').onclick();assert(!elements[0].open);
assert.deepEqual(find('carrier'),['VC3']);assert.deepEqual(find('VC4'),['VC4']);assert.deepEqual(find('direccion'),['VC5']);
assert.deepEqual(find('abc-123 sn99'),['VC3']);assert.deepEqual(find('ingenieria'),['VC4']);assert.deepEqual(find('minisplit oficina'),['VC3']);
assert.deepEqual(find('no existe'),[]);assert.equal($('#technicianEquipmentNoMatches').hidden,false);
cards.splice(0,1);assert.deepEqual(find('Carrier'),[],'Only search cards allowed by the current status filter');
assert.deepEqual(find(''),['VC4','VC5']);assert.equal($('#equipmentList').scrollTop,0);
console.log('OK: technician search matches ID, name, brand, model, serial, location, department and type without recreating cards.');
