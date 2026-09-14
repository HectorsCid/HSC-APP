const fs=require('fs'),vm=require('vm'),assert=require('assert');
const text=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const body=text.slice(text.indexOf('  async function populateTaskForm(){'),text.indexOf('  function refreshTaskEquipment(){'));
const elements={};
const ns={account:{id:'owner',name:'Admin',isOwner:true},operationUsers:[],agendaDate:'2026-09-14',clients:[],escapeHtml:v=>v,refreshTaskEquipment:()=>{},$:id=>elements[id]??=( {}),fetch:async()=>({ok:true,json:async()=>({ok:true,users:[{id:'T1',name:'Técnico uno',role:'technician',status:'active'},{id:'T2',name:'Técnico dos',role:'technician',status:'active'},{id:'T3',name:'Suspendido',role:'technician',status:'suspended'}]})})};
vm.runInNewContext(body,ns);
(async()=>{await ns.populateTaskForm();const html=elements['#taskTechnician'].innerHTML;assert(html.includes('Técnico uno'));assert(html.includes('Técnico dos'));assert(!html.includes('Suspendido'));assert(html.includes('type="checkbox"'));console.log('OK: abrir misión carga técnicos activos sin visitar Usuarios y permite selección múltiple.');})().catch(e=>{console.error(e);process.exitCode=1});
