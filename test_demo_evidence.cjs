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
let mutationSequence=0;
const context = vm.createContext({HscPhotoPreflight:{check:async()=>[100,100]},$, Map, Set, Number, String, Image:class {
  naturalWidth=100;naturalHeight=100;
  async decode(){if(this.src.includes('bad'))throw Error('invalid');}
}, URL:{createObjectURL:f=>'blob:'+f.name,revokeObjectURL:()=>{}}, selectedEquipment:'HDI-sync',selectedRound:'2',
  indexedDB:undefined,
  activeReportContext:{existingEvidence:[]},document:{hidden:false,addEventListener:()=>{}},setInterval:()=>0,
  crypto:{randomUUID:()=>`mutation-${++mutationSequence}`},
  draftKey:()=>`${context.selectedEquipment}-R${context.selectedRound}`, escapeHtml:s=>String(s).replaceAll('<','&lt;'),toast:()=>{}});
vm.runInContext(block,context);context.deleteEvidencePhoto=async()=>{};
const file = (name,size=1024,type='image/jpeg')=>({name,size,type,lastModified:1});
async function select(files){const input=$('#reportEvidence');input.files=files;await input.onchange({target:input});}
async function capture(fileValue){const input=$('#reportEvidenceCamera');input.files=[fileValue];await input.onchange({target:input});}
(async()=>{
  await select([file('first.jpg'),file('second.jpg'),file('third.jpg')]);
  const firstBatch=vm.runInContext('evidenceForReport()',context);
  context.activeReportContext.existingEvidence=firstBatch.map(photo=>({position:photo.slot,mutation_id:photo.mutationId}));
  vm.runInContext('renderEvidence()',context);
  assert.match($('#evidencePickerHint').textContent,/Quedan 3/);
  await select([file('fourth.jpg'),file('fifth.jpg'),file('sixth.jpg')]);
  assert.match($('#evidenceStatus').textContent,/6 de 6/);
  assert.deepEqual(Array.from(vm.runInContext('evidenceForReport().map(photo=>photo.slot)',context)),[1,2,3,4,5,6]);
  context.selectedEquipment='HDI1';context.activeReportContext.existingEvidence=[];
  await select(Array.from({length:6},(_,i)=>file(`${i}.jpg`)));
  assert.match($('#evidenceStatus').textContent,/6 de 6/);
  assert.match($('#evidenceList').innerHTML,/Foto 6/);
  await select([file('extra.jpg')]);assert.match($('#evidenceError').textContent,/Quedan 0/);
  await $('#evidenceList').onclick({target:{closest:()=>({dataset:{index:'0',evidenceAction:'remove'}})}});
  assert.match($('#evidenceStatus').textContent,/5 de 6/);
  await select([file('1.jpg')]);assert.match($('#evidenceError').textContent,/repetida/);
  context.selectedEquipment='HDI2';vm.runInContext('renderEvidence()',context);
  assert.match($('#evidenceStatus').textContent,/0 de 6/);
  await select([file('good.jpg'),file('bad.jpg')]);assert.match($('#evidenceError').textContent,/No se pudo/);
  vm.runInContext('renderEvidence()',context);assert.match($('#evidenceStatus').textContent,/0 de 6/);
  await select([file('large.jpg',16*1024*1024)]);assert.match($('#evidenceError').textContent,/15 MB/);
  await select([file('photo.heic',1024,'image/heic')]);assert.match($('#evidenceStatus').textContent,/1 de 6/);
  await $('#evidenceList').onclick({target:{closest:()=>({dataset:{index:'0',evidenceAction:'remove'}})}});
  $('#reportEvidenceSlot').value='3';await capture(file('camera.jpg'));
  assert.match($('#evidenceList').innerHTML,/Foto 3/);
  await select([file('a.jpg')]);
  $('#evidenceList').onchange({target:{closest:()=>({value:'2',dataset:{index:'1'}})}});
  assert.match($('#evidenceList').innerHTML,/Foto 2/);
  context.selectedEquipment='HDI1';vm.runInContext('renderEvidence()',context);assert.match($('#evidenceStatus').textContent,/5 de 6/);
  context.selectedRound='4';vm.runInContext('renderEvidence()',context);assert.match($('#evidenceStatus').textContent,/0 de 6/);
  assert.match(html,/canvas\.toBlob/);assert.match(html,/capture="environment"/);
  console.log('OK: seis espacios, cámara, posición explícita, compresión, eliminación, duplicados, límites y aislamiento equipo/ronda.');
})().catch(error=>{console.error(error);process.exitCode=1;});
