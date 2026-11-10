const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const page=fs.readFileSync('templates/facturas_inicio.html','utf8');
const form=fs.readFileSync('templates/pago_complemento.html','utf8');
const css=fs.readFileSync('static/billing_balance_layout.css','utf8');
assert.match(page,/id="panelEmitidas"[\s\S]*?<\/table>[\s\S]*?id="issuedPendingList"[\s\S]*?id="panelRecibidas"/);
assert.doesNotMatch(form,/pendingInvoicePanel|pendingInvoiceRows/);
assert.match(css,/#issuedPendingList\{[^}]*max-height:[^}]*overflow-y:auto/);
const ctx=vm.createContext({});
for(const name of ['pendingFromIssued','renderPendingComplements','invoiceLifecycle','escAttr','moneyMx']){
  const start=page.indexOf(`function ${name}(`);
  vm.runInContext(page.slice(start,page.indexOf('\n}',start)+2),ctx);
}
const invoice={id:'a',type:'I',payment_method:'PPD',customer_name:'A & B',folio:'12',date:'2026-11-09T07:00',remaining_balance:5};
const rows=ctx.pendingFromIssued([invoice,{...invoice,id:'b',remaining_balance:20},...[
  {paid:true},{remaining_balance:0},{type:'P'},{payment_method:'PUE'},{cancellation_status:'pending'},{cancellation_status:'canceled'},{active:false}
].map(change=>({...invoice,...change}))]);
assert.deepEqual(Array.from(rows,r=>r.id),['b','a']);
const host={};ctx.renderPendingComplements(host,rows);
assert.match(host.innerHTML,/A &amp; B/);
assert.match(host.innerHTML,/Factura 12 · 2026-11-09/);
assert.match(host.innerHTML,/\/pagos\/nuevo\?invoice_id=b/);
console.log('OK: pendientes en Emitidas, desplazamiento y facturas elegibles.');
