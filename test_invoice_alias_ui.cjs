const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('templates/factura_nueva.html', 'utf8');
const top = html.slice(html.indexOf('id="invoiceDataGrid"'), html.indexOf('<details class="billing-more" id="fiscalDetails"'));
for (const id of ['aliasFactura','ordenCompra','folioCotizacion','retencionIsr','retencionIva','serie','cond']) {
  assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1, `Unique ${id}`);
  assert.ok(top.includes(`id="${id}"`), `${id} remains in the top section`);
}
assert.match(top, /label for="aliasFactura">Alias para guardar en Drive/);
assert.match(top, /id="aliasFactura" maxlength="100"/);
assert.match(top, /id="cond" type="hidden"/);
assert.ok(!html.includes('id="additionalOptions"'));
assert.ok(!html.includes('id="openMore"'));
assert.ok(!html.includes('Opciones adicionales</summary>'));
assert.ok(!html.includes('Condiciones (opcional)'));
assert.ok(!html.includes("getElementById('openMore')"));
for (const script of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
  if (script[1].trim()) new vm.Script(script[1].replace(/\{\{[\s\S]*?\}\}/g, 'null'));
}
// Test the actual template-save payload after moving the alias field.
const elements = new Map();
const get = id => {if (!elements.has(id)) elements.set(id, {value:''});return elements.get(id);};
get('aliasFactura').value = '  Mantenimiento octubre  ';
get('cond').value = 'Crédito legado';
get('ordenCompra').value = 'OC-123';
get('serie').value = 'HSC';
const context = vm.createContext({document:{getElementById:get},calc(){},templateName:{value:'Plantilla'},estado:{items:[]}});
const start = html.indexOf('function currentTemplatePayload(){');
const end = html.indexOf('\n    }',start) + '\n    }'.length;
vm.runInContext(html.slice(start,end),context);
const payload = context.currentTemplatePayload();
assert.equal(payload.conditions.invoice_alias,'Mantenimiento octubre');
assert.equal(payload.conditions.purchase_order,'OC-123');
assert.equal(payload.conditions.notes,'Crédito legado');
assert.ok(html.includes("document.getElementById('aliasFactura').value=conditions.invoice_alias||''"));
assert.ok(html.includes("alias_factura: (document.getElementById('aliasFactura').value||'').trim()"));
console.log('OK: alias arriba, sección adicional eliminada, referencias y retenciones únicas, plantillas compatibles y scripts válidos');
