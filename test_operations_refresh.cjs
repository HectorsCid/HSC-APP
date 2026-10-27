const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const refresh=require('./static/operations_refresh.js');
const base={clients:[{id:'VC'}],equipment:[{id:'VC4',photo_url:'/photo?v=1'}],reports:[],stats:{pending_sync:0},source:'database',cached:false};
const gate=refresh.createGate(),sig=refresh.signature(base,[[],{},{}]);
assert.equal(gate.changed(sig),true);gate.commit(sig);
assert.equal(gate.changed(refresh.signature({...base,cached:true,source:'memory'},[[],{},{}])),false);
for(const changed of [ {...base,equipment:[{id:'VC4',photo_url:'/photo?v=2'}]}, {...base,reports:[{id:'R',completed:true}]}, {...base,stats:{pending_sync:1}} ])assert.equal(gate.changed(refresh.signature(changed,[[],{},{}])),true);
assert.equal(gate.changed(refresh.signature(base,[[{id:'pending'}],{},{}])),true);
assert.equal(gate.changed(refresh.signature(base,[[],{upload:{id:'new'}},{}])),true);
assert.equal(gate.changed(refresh.signature({...base,scope_valid:false},[[],{},{}])),true);
gate.reset();assert.equal(gate.changed(sig),true);
const history=refresh.createPhotoHistory(2);history.add('a');history.add('b');history.add('c');assert.equal(history.has('a'),false);assert.equal(history.has('c'),true);history.clear();assert.equal(history.has('c'),false);
const html=fs.readFileSync('templates/app_operativa_demo.html','utf8');
assert.ok(html.includes('if(!repaintGate.changed(nextSignature)){updateOfflineSyncUi();return;}'));
assert.ok(html.includes('payload.reports.slice()'));
assert.ok(html.includes('1920/Math.max(image.naturalWidth,image.naturalHeight)'));
assert.ok(html.includes("'image/jpeg',.9"));
console.log('OK: no redraw for unchanged data; changes, pending work and photo versions remain visible; bounded URL history; improved photo quality.');
(async()=>{
  const context=vm.createContext({HscRefresh:refresh,lastServerPayload:structuredClone(base),pendingFieldCount:0,
    repaintGate:refresh.createGate(),localPhotoUrls:new Map(),fieldActionStorage:async()=>[],
    pendingReportUploads:()=>({}),savedClientRounds:()=>({}),HscOffline:require('./static/operations_offline.js'),
    paints:0,updateOfflineSyncUi(){},applyMatrixBootstrap(){this;context.paints++;return true;}});
  const start=html.indexOf('  async function repaintPendingState(){'),end=html.indexOf('  async function loadMatrixData(',start);
  vm.runInContext(html.slice(start,end),context);
  await context.repaintPendingState();await context.repaintPendingState();
  assert.equal(context.paints,1,'No repaint for an identical background response');
  context.lastServerPayload.equipment[0].photo_url='/photo?v=3';await context.repaintPendingState();
  assert.equal(context.paints,2,'An actual changed photo remains visible');
  const parents=[{children:[],append(...nodes){this.children.push(...nodes)}},{children:[],append(...nodes){this.children.push(...nodes)}}];
  const componentContext=vm.createContext({escapeHtml:x=>x,$:()=>({elements:{namedItem:name=>({parentElement:parents[name==='a1'?0:1]})}}),document:{createElement:tag=>({tag})}});
  const componentStart=html.indexOf('  function setupReportComponents(){'),componentEnd=html.indexOf('  function openPhotoPicker(',componentStart);
  vm.runInContext(html.slice(componentStart,componentEnd),componentContext);
  for(let i=0;i<2;i++){const [label,select]=parents[i].children;assert.equal(select.name,`a${i+1}_component`);assert.equal(label.htmlFor,select.id);assert.ok(select.innerHTML.includes('Sin especificar'));assert.ok(select.innerHTML.includes('Resistencia'));}
  console.log('OK: actual repaint function preserves unchanged views; actual component selectors are correctly mounted.');
})().catch(error=>{console.error(error);process.exitCode=1;});
