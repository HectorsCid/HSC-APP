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

const restoreSource=html.slice(html.indexOf('  let restoringReportOpen=false;'),html.indexOf("  window.addEventListener('popstate'"));
let address=new URL('https://hsc.test/hsc-partner/?client=B&fault=F1&v=release#hsc-partner');
const nav=vm.createContext({URL,URLSearchParams,location:{get href(){return address.href},get search(){return address.search}},
  account:{id:'C1',isOwner:false},appKind:'partner',serverRole:'client',navigationStorageKey:'nav',
  localStorage:{getItem:()=>JSON.stringify({view:'partner',client:'B',equipment:'B1',round:'3',mode:'partner'})},
  clientMeta:{B:{}},equipmentMeta:{B1:{client:'B'}},equipment:[['B1']],selectedClient:'B',selectedEquipment:'B1',selectedRound:'3',currentMode:'partner',
  matrixFaults:[{id:'F1',client_id:'B'}],opened:0,historyStack:[],current:'partner',
  selectClientRound(){},rememberClientRound(){},refreshRound(){},canonicalPath:view=>[view],
  showView(view){nav.current=view},initializeBrowserNavigation(){},$:()=>({}),
  $$:selector=>selector==='[data-fault-id]'?[{dataset:{faultId:'F1'},click(){nav.opened++}}]:[],setTimeout(){},
});
nav.window={history:{state:null,replaceState(state,unused,url){this.state=state;address=new URL(url,address)}}};
vm.runInContext(restoreSource,nav);
nav.restoreOperationalNavigation();assert.equal(nav.opened,1);assert.equal(nav.current,'partnerFaults');
assert.equal(address.searchParams.has('fault'),false);assert.equal(address.searchParams.has('client'),false);assert.equal(address.searchParams.get('v'),'release');
nav.window.history.state={hscOperations:true,userId:'C1',view:'partnerDocuments',client:'B',equipment:'B1',round:'3',mode:'partner'};
nav.restoreOperationalNavigation();assert.equal(nav.current,'partnerDocuments');assert.equal(nav.opened,1,'Recargar no debe volver a abrir el aviso anterior');
nav.account.isOwner=true;nav.appKind='technician';nav.serverRole='admin';nav.restoreOperationalNavigation();assert.equal(nav.currentMode,'partner');assert.equal(nav.current,'partnerDocuments');
console.log('OK: destino de notificación de un solo uso y recarga en la misma pantalla, incluida vista Partner administrativa.');
const backSource=html.slice(html.indexOf('  function parentView('),html.indexOf('  function initializeBrowserNavigation('));
const goBackSource=html.split(/\r?\n/).find(line=>line.trim().startsWith('function goBack('));
let modalOpen=true;
Object.assign(nav,{document:{querySelector:()=>modalOpen?{classList:{remove(){modalOpen=false}}}:null},browserHistoryReady:false,handlingHistoryPop:false,activeReportContext:null});
vm.runInContext(backSource+'\n'+goBackSource,nav);
nav.currentMode='tech';nav.current='reportView';nav.goBack();assert.equal(nav.current,'reportView','Atrás primero cierra la ventana');
nav.goBack();assert.equal(nav.current,'equipment');nav.goBack();assert.equal(nav.current,'client');nav.goBack();assert.equal(nav.current,'clients');nav.goBack();assert.equal(nav.current,'clients');
nav.currentMode='partner';nav.current='reportView';nav.goBack();assert.equal(nav.current,'partnerReports');nav.goBack();assert.equal(nav.current,'partner');
assert.ok(!['report','clientEditor','equipmentEditor'].includes(nav.current));
console.log('OK: Atrás cierra ventanas y sigue reporte → equipo → cliente, sin reabrir formularios.');
