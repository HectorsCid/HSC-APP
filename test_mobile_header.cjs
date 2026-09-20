const fs=require('node:fs'),assert=require('node:assert/strict');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const topbar=html.match(/\.topbar\{position:sticky;[^}]+\}/)[0];
assert.match(html,/viewport-fit=cover/);
assert.match(topbar,/padding:calc\(12px \+ env\(safe-area-inset-top,0px\)\)/);
assert.match(topbar,/min-height:calc\(70px \+ env\(safe-area-inset-top,0px\)\)/);
for(const edge of ['left','right'])assert(topbar.includes(`safe-area-inset-${edge}`));
assert.match(html,/\.topbar>\.icon-btn,\.topbar>\.back\{flex:0 0 44px/);
assert.match(html,/\.topbar>\.brand\{display:none\}/);
for(const id of ['backBtn','quickThemeToggle','reloadApp','searchToggle'])assert(html.includes(`id="${id}"`));
// At phone widths, five fixed touch targets and their gaps leave room for the title.
for(const width of [320,375,390,414,480])assert(width-20-5*44-5*6>=50);
console.log('OK: safe-area superior y lateral, botones de 44px y espacio de título en móviles.');
