const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8'),nodes=new Map();
const $=key=>{if(!nodes.has(key))nodes.set(key,{value:'',hidden:false,textContent:'',scrollTop:100});return nodes.get(key)};
const cards=['VC3','VC4','VC5'].map(id=>({dataset:{equipment:id},hidden:false}));
const context=vm.createContext({$,String,$$:()=>cards,
  equipment:[['VC3','Gerencia planta','Carrier'],['VC4','Calidad','Daikin'],['VC5','Dirección','Trane']],
  equipmentMeta:{VC3:{location:'Oficina 2',model:'ABC-123',serial:'SN99',type:'Minisplit'},VC4:{department:'Ingeniería'},VC5:{}}});
const start=html.indexOf('  function filterTechnicianEquipment(){'),end=html.indexOf('  function renderEquipmentDetail(){',start);
vm.runInContext(html.slice(start,end),context);
function find(query){$('#technicianEquipmentSearch').value=query;$('#technicianEquipmentSearch').oninput();return cards.filter(c=>!c.hidden).map(c=>c.dataset.equipment)}
assert.deepEqual(find('carrier'),['VC3']);assert.deepEqual(find('VC4'),['VC4']);assert.deepEqual(find('direccion'),['VC5']);
assert.deepEqual(find('abc-123 sn99'),['VC3']);assert.deepEqual(find('ingenieria'),['VC4']);assert.deepEqual(find('minisplit oficina'),['VC3']);
assert.deepEqual(find('no existe'),[]);assert.equal($('#technicianEquipmentNoMatches').hidden,false);
cards.splice(0,1);assert.deepEqual(find('Carrier'),[],'Only search cards allowed by the current status filter');
assert.deepEqual(find(''),['VC4','VC5']);assert.equal($('#equipmentList').scrollTop,0);
console.log('OK: technician search matches ID, name, brand, model, serial, location, department and type without recreating cards.');
