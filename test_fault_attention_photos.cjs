const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
const get=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',files:[],classList:{add(){},remove(){}},append(){},after(){},querySelectorAll(selector){
  const html=get('faultAttentionSlots').innerHTML;
  const attr=selector.includes('remove')?'attention-remove':'attention-slot';
  return [...html.matchAll(new RegExp(`data-${attr}="(\\d+)"`,'g'))].map(match=>({dataset:{[attr==='attention-remove'?'attentionRemove':'attentionSlot']:match[1]},files:[]}));
}});return elements.get(id);};
let uuid=0,failed=true,revoked=[];const posts=[];
const ctx=vm.createContext({document:{getElementById:get,createElement:()=>get('photos')},crypto:{randomUUID:()=>`CAPTURE-${++uuid}`},URL:{createObjectURL:file=>'blob:'+file.name,revokeObjectURL:url=>revoked.push(url)},FormData:class {constructor(){this.entries=[];}append(...args){this.entries.push(args);}},confirm:()=>true,Date});
vm.runInContext(fs.readFileSync('static/operations_fault_attention.js','utf8'),ctx);
const ui=ctx.HscFaultAttention.mount({escape:String,canWrite:true,toast(){},compress:async file=>file,refresh(){},fetch:async(url,options)=>{
  if(options?.method==='POST'){posts.push(options.body);return {ok:!failed,json:async()=>failed?{ok:false,error:'Sin red'}:{ok:true}};}
  return {ok:true,json:async()=>({ok:true,attentions:[]})};
}});
(async()=>{
  ui.open({id:'FAULT'});
  assert.equal((get('faultAttentionSlots').innerHTML.match(/<b>Foto /g)||[]).length,6);
  get('faultAttentionBulk').files=Array.from({length:6},(_,i)=>({name:`photo${i+1}.jpg`,size:100}));
  await get('faultAttentionBulk').onchange();
  assert.ok(get('faultAttentionMessage').textContent.includes('6 de 6'));
  get('faultResolutionNotes').value='Cambio de pieza';get('faultResolutionNotes').oninput();
  await get('resolveFault').onclick();
  assert.equal(get('resolveFault').textContent,'Reintentar guardado');
  assert.equal(posts[0].entries.filter(row=>row[0].startsWith('photo_')).length,6);
  assert.ok(get('faultAttentionMessage').textContent.includes('no se duplicará'));
  assert.equal(revoked.length,0);
  failed=false;await get('resolveFault').onclick();
  assert.equal(posts[0].entries.find(row=>row[0]==='id')[1],posts[1].entries.find(row=>row[0]==='id')[1]);
  assert.equal(revoked.length,6);
  assert.equal(get('resolveFault').disabled,false);
  assert.equal(get('faultAttentionMessage').textContent,'Atención y fotografías guardadas.');
  const app=fs.readFileSync('templates/app_operativa_demo.html','utf8');
  assert.ok(app.includes('faultAttentionUi?.open(fault)'));
  assert.ok(app.includes('faultAttentionUi.canOpen(fault.id)'));
  console.log('OK: seis espacios, envío completo, captura retenida ante fallo, reintento sin duplicar y liberación de memoria');
})().catch(error=>{console.error(error);process.exitCode=1;});
