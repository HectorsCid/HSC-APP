const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/facturas_inicio.html', 'utf8');
// Check every inline script's syntax, not just the extracted test helpers.
for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
  if (match[1].trim()) new vm.Script(match[1]);
}
function extract(name) {
  const start = html.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start >= 0, name);
  return html.slice(start, html.indexOf('\n}', start) + 2);
}
let response = {ok:true,cancellation_status:'pending'};
const requests = [], alerts = [];
let reloads = 0;
const context = vm.createContext({
  fetch: async (...args) => {requests.push(args);return {ok:response.ok,json:async()=>response};},
  alert: message => alerts.push(message),
  errorText: value => value,
  load: async () => {reloads++;},
});
vm.runInContext(['escAttr','invoiceLifecycle','invoiceStatusControl','refreshInvoiceStatus'].map(extract).join('\n'),context);
for (const [state,label,blocked] of [['active','Activa',false],['pending','En proceso',true],['canceled','Cancelada',true],['rejected','Rechazada',false]]) {
  const value = context.invoiceLifecycle({cancellation_status:state});
  assert.equal(value.shortLabel,label);
  assert.equal(value.blocksPayment,blocked);
  const button = context.invoiceStatusControl({id:'doc',cancellation_status:state});
  assert.ok(button.includes(`status-${state}`));
  assert.ok(button.includes(label));
  assert.ok(!button.includes('Consultar cancelación'));
}
assert.equal(context.invoiceLifecycle({active:false}).state,'canceled');
assert.match(context.invoiceStatusControl({id:'"<x>'}), /&quot;&lt;x&gt;/);
(async()=>{
  const classes = new Set(['invoice-active','is-payment']);
  const row = {classList:{remove:(...names)=>names.forEach(name=>classes.delete(name)),add:name=>classes.add(name)}};
  const button = {disabled:false,innerHTML:'Activa',dataset:{cancelStatus:'rep-id'},closest:()=>row,setAttribute(){}};
  await context.refreshInvoiceStatus(button);
  assert.ok(button.innerHTML.includes('En proceso'));
  assert.ok(classes.has('invoice-pending'));
  assert.ok(classes.has('is-payment'));
  assert.ok(!classes.has('invoice-active'));
  response = {ok:true,cancellation_status:'canceled'};
  await context.refreshInvoiceStatus(button);
  assert.ok(button.innerHTML.includes('Cancelada'));
  assert.ok(classes.has('invoice-canceled'));
  assert.ok(!classes.has('invoice-pending'));
  response = {ok:false,error:'Sin conexión'};
  await context.refreshInvoiceStatus(button);
  assert.ok(button.innerHTML.includes('Cancelada'));
  assert.equal(button.disabled,false);
  assert.equal(alerts.at(-1),'Sin conexión');
  assert.equal(reloads,2);
  assert.ok(requests.every(args=>args.length===1)); // Only status GETs; never cancel or stamp.
  assert.ok(html.includes("tr.classList.add(`invoice-${lifecycle.state}`)"));
  assert.ok(!html.includes('lifecycle.blocksPayment?`<span class="badge'));
  console.log('OK: etiquetas, colores, consulta GET, transición de estado y recuperación de errores');
})().catch(error=>{console.error(error);process.exitCode=1;});
