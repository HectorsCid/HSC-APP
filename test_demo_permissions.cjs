const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync('templates/app_operativa_demo.html', 'utf8');
const script = html.slice(html.indexOf('<script>') + 8, html.lastIndexOf('</script>'));

new vm.Script(script);
assert.match(html, /ID interno \(inmutable\)/);
assert.match(html, /data-tech-permission="editClientData"/);
assert.match(html, /data-tech-permission="editIds"/);
assert.match(html, /function can\(permission\)/);
assert.match(html, /function openRoot\(name\)\{historyStack=\[name\]/);
assert.match(html, /\$\('#backBtn'\)\.hidden=historyStack\.length<=1/);
assert.match(html, /data-switch-mode="tech"/);
assert.match(html, /data-switch-mode="admin"/);
assert.match(html, /HSC Partner no puede crear, modificar ni finalizar reportes/);
assert.match(html, /id="inviteTechPermissions"/);
assert.match(html, /id="inviteClientPermissions"/);
assert.match(html, /\/api\/operaciones\/clients/);
assert.match(html, /\/api\/operaciones\/equipment/);
assert.match(html, /\/api\/operaciones\/reports\/draft/);
assert.match(html, /Sincronizar Google/);
assert.doesNotMatch(html, />Guardar en maqueta</);
assert.doesNotMatch(script, /location\.(href|assign)|history\.back/);

console.log('OK: IDs protegidos, permisos por técnico, modos y regreso interno presentes.');
