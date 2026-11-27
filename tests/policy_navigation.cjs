const {strict:assert}=require('node:assert'), vm=require('node:vm'),fs=require('node:fs');
const elements=new Map();
function element(key){
  if(!elements.has(key))elements.set(key,{innerHTML:'',value:'',dataset:{},querySelector:element,
    querySelectorAll:s=>s==='[data-key]'?[element('client')]:s==='[data-pick]'?[element('pick')]:[],
    closest:element,before(){},replaceChildren(){}});
  return elements.get(key);
}
async function run(count){
  elements.clear();element('client').dataset.key='v';element('pick').dataset.pick='A';
  const model={client:{name:'Cliente'},configured:['A','B'].slice(0,count).map(name=>({name,branch:'86'})),
    rules:{},invoices:['06','08','10'].map(m=>({policy:'A',period:'2020-'+m,id:m,folio:m,date:'2020-11-01'})),templates:[]};
  const ctx={window:{},document:{querySelector:element},invoiceLifecycle:()=>({}),moneyMx:()=>'',fetch:async url=>({ok:true,json:async()=>url.endsWith('/v')?{ok:true,...model}:{ok:true,clients:[{id:'v',name:'Cliente'}]}})};
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/billing_policies.js'),'utf8'),ctx);
  await ctx.window.HscPolicies.show();await element('client').onclick();
  const detail=element('#policyDetail');
  if(count===2){assert.match(detail.innerHTML,/Elige una póliza/);element('pick').onclick();}
  assert.match(detail.innerHTML,/Meses de servicio/);
  element('#policyYear').onchange({target:{value:'2020'}});
  assert.match(detail.innerHTML,/Sin factura vinculada: Julio, Septiembre/);
  assert.match(detail.innerHTML,/Factura 10/);
  assert.doesNotMatch(detail.innerHTML,/Sin factura vinculada: Enero/);
}
(async()=>{await run(1);await run(2);})().catch(e=>{console.error(e);process.exitCode=1});
