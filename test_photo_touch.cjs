const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
const code=html.slice(html.indexOf('  function openPhotoPicker(button){'),html.indexOf("  $$('[data-photo-input]')"));
assert(code.includes('input.click()'));
assert(!/await|setTimeout|Promise/.test(code));
let clicks=0;
const inputs={};
for(const id of ['opEquipmentCamera','opEquipmentPhoto','reportEvidenceCamera','reportEvidence']){
 assert.match(html,new RegExp('<button[^>]*type="button"[^>]*data-photo-input="'+id+'"'));
 inputs[id]={disabled:false,click:()=>clicks++};
}
const context=vm.createContext({document:{getElementById:id=>inputs[id]}});
vm.runInContext(code,context);
for(const id of Object.keys(inputs)){
 const button={dataset:{photoInput:id},disabled:false};
 const before=clicks;context.openPhotoPicker(button);assert.equal(clicks,before+1);
 inputs[id].disabled=true;context.openPhotoPicker(button);assert.equal(clicks,before+1);
 inputs[id].disabled=false;button.disabled=true;context.openPhotoPicker(button);assert.equal(clicks,before+1);
}
assert.match(html,/\.photo-trigger\{[^}]*min-height:52px/);
assert.match(html,/-webkit-user-select:none/);
assert.match(html,/id="reportEvidence"[^>]*multiple/);
console.log('OK: cuatro botones reales, apertura síncrona, bloqueo deshabilitado y selección múltiple conservada.');
