const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const html = fs.readFileSync('templates/app_operativa_demo.html', 'utf8');
const start = html.indexOf('  function changedReportFields');
const end = html.indexOf('  async function saveActiveReportDraft', start);
assert.ok(start >= 0 && end > start, 'No se encontró el comparador de cambios del reporte.');
const context = vm.createContext({Object, String});
vm.runInContext(`${html.slice(start, end)};this.changedReportFields=changedReportFields;`, context);

const baseline = {inicio:'2026-09-27', fin:'2026-09-27', notas:'Dato compartido', p1:''};
const offline = {...baseline, p1:'72'};
const remote = {...baseline, notas:'Cambio reciente de otro técnico'};
const changes = context.changedReportFields(offline, baseline);
const merged = {...remote, ...changes};
assert.equal(merged.notas, 'Cambio reciente de otro técnico');
assert.equal(merged.p1, '72');
assert.match(html, /changed_fields:upload\.changedFields\|\|\{\}/);
assert.match(html, /reportBaselineKey/);
console.log('OK: el reintento offline mezcla sólo cambios reales y conserva datos compartidos.');
