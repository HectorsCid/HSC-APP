const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync('templates/app_operativa_demo.html', 'utf8');
const start = html.indexOf('  const reportEvidenceByKey=');
const block = html.slice(start, html.indexOf("  $('#inviteClientSelect')", start));
const elements = new Map();
const $ = selector => {
  if (!elements.has(selector)) elements.set(selector, {value:'', textContent:'', innerHTML:'', disabled:false, onclick:()=>{}});
  return elements.get(selector);
};
const context = vm.createContext({$, Map, Set, Number, String, Image:class {
  naturalWidth=100;naturalHeight=100;
  async decode(){if(this.src.includes('bad'))throw Error('invalid');}
}, URL:{createObjectURL:f=>'blob:'+f.name,revokeObjectURL:()=>{}}, selectedEquipment:'HDI1',selectedRound:'2',
  draftKey:()=>`${context.selectedEquipment}-R${context.selectedRound}`, escapeHtml:s=>String(s).replaceAll('<','&lt;'),toast:()=>{}});
vm.runInContext(block,context);
const file = (name,size=1024,type='image/jpeg')=>({name,size,type,lastModified:1});
async function select(files){const input=$('#reportEvidence');input.files=files;await input.onchange({target:input});}
(async()=>{
  await select(Array.from({length:6},(_,i)=>file(`${i}.jpg`)));
  assert.match($('#evidenceStatus').textContent,/6 de 6/);
  assert.match($('#evidenceList').innerHTML,/Foto6/);
  await select([file('extra.jpg')]);assert.match($('#evidenceError').textContent,/Quedan 0/);
  $('#evidenceList').onclick({target:{closest:()=>({dataset:{index:'0',evidenceAction:'remove'}})}});
  assert.match($('#evidenceStatus').textContent,/5 de 6/);
  await select([file('1.jpg')]);assert.match($('#evidenceError').textContent,/repetida/);
  context.selectedEquipment='HDI2';vm.runInContext('renderEvidence()',context);
  assert.match($('#evidenceStatus').textContent,/0 de 6/);
  await select([file('good.jpg'),file('bad.jpg')]);assert.match($('#evidenceError').textContent,/No se pudo/);
  vm.runInContext('renderEvidence()',context);assert.match($('#evidenceStatus').textContent,/0 de 6/);
  await select([file('large.jpg',16*1024*1024)]);assert.match($('#evidenceError').textContent,/15 MB/);
  await select([file('photo.heic',1024,'image/heic')]);assert.match($('#evidenceError').textContent,/JPG/);
  await select([file('a.jpg'),file('b.jpg')]);
  $('#evidenceList').onclick({target:{closest:()=>({dataset:{index:'1',evidenceAction:'up'}})}});
  assert.ok($('#evidenceList').innerHTML.indexOf('b.jpg')<$('#evidenceList').innerHTML.indexOf('a.jpg'));
  context.selectedEquipment='HDI1';vm.runInContext('renderEvidence()',context);assert.match($('#evidenceStatus').textContent,/5 de 6/);
  context.selectedRound='4';vm.runInContext('renderEvidence()',context);assert.match($('#evidenceStatus').textContent,/0 de 6/);
  console.log('OK: seis fotos, orden, eliminación, duplicados, límites, lote inválido y aislamiento equipo/ronda. Decodificación simulada.');
})().catch(error=>{console.error(error);process.exitCode=1;});
