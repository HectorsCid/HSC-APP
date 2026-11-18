const fs=require('node:fs'),assert=require('node:assert/strict'),vm=require('node:vm');
const html=fs.readFileSync('templates/facturas_inicio.html','utf8');
const expression=html.match(/const generate=(.*?);return `<div class="schedule-row/)[1];
const ctx=vm.createContext({encodeURIComponent,row:{id:'s1',template_id:'t1',active:true,last_status:'awaiting_confirmation',last_invoice_id:'previous-month'}});
let link=vm.runInContext(expression,ctx);
assert.match(link,/Revisar y generar factura/);
assert.match(link,/template_id=t1&amp;schedule_id=s1/);
for(const status of ['completed','email_error','error']){ctx.row.last_status=status;assert.equal(vm.runInContext(expression,ctx),'');}
ctx.row.last_status='awaiting_confirmation';ctx.row.active=false;assert.equal(vm.runInContext(expression,ctx),'');
console.log('OK: acceso de confirmación pendiente, incluso con factura del mes anterior; no ofrece regenerar facturas terminadas.');
