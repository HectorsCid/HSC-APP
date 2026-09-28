const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

const html = fs.readFileSync('templates/app_operativa_demo.html', 'utf8');
const start = html.indexOf('  function normalizeReportDate');
const end = html.indexOf('  function fillReportFields', start);
assert.ok(start > 0 && end > start, 'No se encontró el normalizador de fechas del reporte.');
const context = vm.createContext({});
vm.runInContext(`${html.slice(start, end)};this.normalizeReportDate=normalizeReportDate;`, context);

assert.equal(context.normalizeReportDate('2026-09-27'), '2026-09-27');
assert.equal(context.normalizeReportDate('2026-09-27T10:30:00Z'), '2026-09-27');
assert.equal(context.normalizeReportDate('25/08/2026'), '2026-08-25');
assert.equal(context.normalizeReportDate('5-8-2026'), '2026-08-05');
assert.equal(context.normalizeReportDate(''), '');
assert.match(html, /data\.fin<data\.inicio/);
assert.match(html, /completedRemotely/);
console.log('OK: fechas ISO y fechas históricas se muestran en campos editables.');
