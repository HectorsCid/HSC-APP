const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
class Element {
  constructor(){this.value='';this.style={};this.dataset={};this.children=[];this.classList={remove(){},add(){}};}
  append(...nodes){this.children.push(...nodes);}
  add(node){this.append(node);}
  get options(){return this.children;}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(){}
  addEventListener(){}
  closest(){return {style:{},querySelector:()=>({style:{}})};}
  querySelectorAll(){return this.children.flatMap(node=>[node,...node.querySelectorAll()]).filter(node=>node.dataset.remaining);}
  click(){return this.onclick?.();}
}
const elements=new Map();
const get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
let invoices={a:{id:'a',folio:'10',payment_method:'PPD',remaining_balance:116,next_partiality_number:1,customer:{tax_id:'AAA010101AAA',legal_name:'Cliente',tax_system:'601',zip:'76116'}},b:{id:'b',folio:'11',payment_method:'PPD',remaining_balance:232,next_partiality_number:2,customer:{tax_id:'AAA010101AAA',legal_name:'Cliente',tax_system:'601',zip:'76116'}},c:{id:'c',remaining_balance:116,payment_method:'PPD',customer:{tax_id:'BBB010101BBB'}}};
const ctx=vm.createContext({console,URLSearchParams,Map,Date,Number,Array,Option:class extends Element {constructor(text,value){super();this.textContent=text;this.value=value;}},document:{getElementById:get,createElement:()=>new Element(),documentElement:{dataset:{theme:'light'}}},location:{search:''},localStorage:{setItem(){}},confirm:()=>false,fetch:async url=>({ok:true,json:async()=>url.includes('/info/')?invoices[url.split('/').pop()]:{ok:true,data:[]}})});
const source=fs.readFileSync('templates/pago_complemento.html','utf8').match(/<script>\s*const msg[\s\S]*?<\/script>/)[0].replace(/^<script>|<\/script>$/g,'');
vm.runInContext(source,ctx);
(async()=>{
  await new Promise(resolve=>setImmediate(resolve));
  await vm.runInContext("selectInvoice('a')",ctx);
  await vm.runInContext("selectInvoice('b')",ctx);
  assert.equal(get('btnCrear').disabled,false);
  assert.match(get('selectionTotal').textContent,/348/);
  vm.runInContext("selectedInvoices.get('a').amount=58;refreshTotal()",ctx);
  assert.match(get('selectionTotal').textContent,/290/);
  vm.runInContext("selectedInvoices.get('a').amount=999;refreshTotal()",ctx);
  assert.equal(get('btnCrear').disabled,true);
  await vm.runInContext("selectInvoice('c')",ctx);
  assert.equal(vm.runInContext('selectedInvoices.size',ctx),2);
  assert.match(get('msg').textContent,/mismo RFC/);
  vm.runInContext('selectedInvoices.clear();renderSelected()',ctx);
  assert.equal(get('btnCrear').disabled,true);
  console.log('OK: selección, total, sobrepago, cliente diferente y quitar todas');
  elements.clear();
  const calls=[],alerts=[];
  const replacement=vm.createContext({...ctx,location:{search:'?replacement_id=old'},confirm:()=>true,alert:text=>alerts.push(text),fetch:async(url,options)=>{
    calls.push({url,body:options?.body && JSON.parse(options.body)});
    return {ok:true,status:200,json:async()=>url.includes('/replacement/')?{ok:true,uuid:'old-uuid',documents:[{invoice:invoices.a,amount:58}],date:'2026-10-09T10:00:00',payment_form:'03'}:
      url.includes('/crear')?{ok:true,id:'new',uuid:'new-uuid',replaces_id:'old',replaces_uuid:'old-uuid'}:{ok:true,status_label:'Cancelación en proceso'}};
  }});
  vm.runInContext(source,replacement);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(get('btnCrear').disabled,false);
  assert.equal(get('btnBuscar').disabled,true);
  assert.equal(get('selectedInvoices').children[0].children[2].disabled,true);
  await get('btnCrear').click();
  assert.equal(calls.find(call=>call.url.endsWith('/crear')).body.replacement_id,'old');
  const cancel=calls.find(call=>call.url.endsWith('/cancel'));
  assert.equal(cancel.body.motive,'01');assert.equal(cancel.body.substitution_folio,'new-uuid');
  assert.match(replacement.location.href,/enviar_complemento=new/);
  assert.match(alerts[0],/Cancelación en proceso/);
  console.log('OK: corrección precargada, facturas fijas, sustituto y cancelación 01, envío por correo');
})().catch(error=>{console.error(error);process.exitCode=1;});
