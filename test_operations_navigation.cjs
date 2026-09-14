const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/app_operativa_demo.html', 'utf8');
const source = html.slice(html.indexOf('  function applyMatrixBootstrap('), html.indexOf('  async function loadMatrixData('));
const element = {classList:{toggle(){},add(){}},textContent:'',innerHTML:''};
const context = vm.createContext({
  clients:[],equipment:[],clientMeta:{B:{}},equipmentMeta:{B1:{client:'B'}},
  matrixLoaded:true,selectedClient:'B',selectedEquipment:'B1',selectedRound:'3',
  matrixReports:[],matrixFaults:[],matrixTasks:[],matrixExpenses:[],matrixObservationOptions:[],
  demoRounds:Object.fromEntries(['1','2','3','4'].map(r=>[r,{}])),
  savedClientRounds:()=>({}),pendingReportUploads:()=>({}),renderObservationPickers(){},
  localStorage:{setItem(){}},clientRoundsStorageKey:'rounds',byEquipmentId:(a,b)=>a[0].localeCompare(b[0]),
  $:()=>element,document:{body:element},escapeHtml:x=>x,
  renderClients(){},refreshRound(){},renderAgenda(){},renderExpenses(){},announcePendingExpenses(){},
  restored:0,saved:0,
});
vm.runInContext('function restoreOperationalNavigation(){restored++} function saveOperationalNavigation(){saved++}',context);
vm.runInContext(source,context);
const payload={clients:[{id:'A',name:'A',selected_round:'1'},{id:'B',name:'B',selected_round:'2'}],equipment:[{id:'A1',client_id:'A'},{id:'B1',client_id:'B'}]};
context.applyMatrixBootstrap(payload);
assert.equal(context.selectedClient,'B','Actualizar después de resolver no debe cambiar de empresa');
assert.equal(context.selectedEquipment,'B1');
assert.equal(context.selectedRound,'3');
assert.equal(context.restored,0,'Una actualización no debe restaurar navegación antigua');
context.selectedEquipment='A1';
context.applyMatrixBootstrap(payload);
assert.equal(context.selectedClient,'B');
assert.equal(context.selectedEquipment,'B1','El equipo debe pertenecer a la empresa seleccionada');
context.matrixLoaded=false;
context.applyMatrixBootstrap(payload);
assert.equal(context.restored,1,'Restaurar navegación solo en la primera carga');
assert.match(html,/const clients=\[\];/);
assert.match(html,/body:not\(\.operations-ready\) main/);
assert.match(html,/navigation-v2-\$\{account.id\}/);
console.log('OK: recargas conservan empresa/equipo/ronda, navegación por cuenta y arranque sin catálogo ilustrativo.');
