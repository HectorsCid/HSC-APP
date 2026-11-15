const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const ctx=vm.createContext({currentMode:'tech',serverRole:'admin',normalizeReportDate:v=>String(v||'').slice(0,10),matrixReports:[
  {id:'a',matrix_id:'M1',client_id:'UVM',equipment_id:'U1',round:'1',end:'2026-11-15',completed:true},
  {id:'duplicate',matrix_id:'M1',client_id:'UVM',end:'2026-11-15',completed:true},
  {id:'b',client_id:'UVM',equipment_id:'U2',round:'1',end:'2026-11-15',completed:true},
  {id:'local',end:'2026-11-15',completed:true,pending_upload:true},
  {id:'draft',end:'2026-11-15',completed:false},
  {id:'older',end:'2026-11-14',completed:true}
],clientName:id=>id,equipment:[],escapeHtml:v=>String(v)});
const start=html.indexOf('  function agendaReportHistory('),end=html.indexOf('  function renderAgenda(){',start);
vm.runInContext(html.slice(start,end),ctx);
assert.equal(ctx.agendaReportHistory('2026-11-15').length,2);
assert.match(ctx.agendaHistoryMarkup('2026-11-15'),/2 mantenimientos · UVM/);
assert.match(ctx.agendaHistoryMarkup('2026-11-15'),/U1 · R1/);
assert.match(ctx.agendaHistoryMarkup('2026-11-13'),/No hay mantenimientos/);
ctx.currentMode='partner';assert.equal(ctx.agendaReportHistory('2026-11-15').length,0);
console.log('OK: historial diario por cliente, sin borradores ni duplicados, privado al modo técnico.');
