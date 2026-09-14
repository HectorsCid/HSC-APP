const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const code=source.slice(source.indexOf('  function monthCalendarCells('),source.indexOf('  function renderMonthCalendar('));
const ns={localDate:v=>new Date(v+'T12:00:00'),matrixTasks:[{id:'A',client_id:'A',title:'Visita',scheduled_date:'2026-09-18'},{id:'B',client_id:'B'}],calendarPendingTasks:new Map([['NEW',{id:'NEW',client_id:'A'}]]),currentMode:'partner',selectedClient:'A',equipmentMeta:{}};
vm.runInNewContext(code,ns);
for(const [month,count] of [['2026-02',28],['2028-02',29],['2026-09',30],['2026-12',31]]){
 const cells=ns.monthCalendarCells(month).filter(Boolean);assert.equal(cells.length,count);assert.equal(cells[0],month+'-01');assert.equal(cells.at(-1),month+'-'+count);
}
assert.deepEqual(Array.from(ns.calendarTasks(),r=>r.id),['A','NEW']);
assert(source.includes("assigned_user_ids:$$('#taskTechnician input:checked').map"));
assert(source.includes('returnToMonthCalendar();renderAgenda();toast'));
console.log('OK: meses completos de 28/29/30/31 días, filtro por cliente, pendientes locales y retorno al calendario.');
