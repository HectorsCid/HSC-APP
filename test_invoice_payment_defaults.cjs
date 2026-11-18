const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('templates/factura_nueva.html','utf8');
function extract(name){const start=html.indexOf(`    async function ${name}(`);return html.slice(start,html.indexOf('\n    }',start)+6);}
const fields=Object.fromEntries(['cliente','rememberPayment','forma','metodo','rfc','razon','cp','regimen','uso','email','retencionIsr','retencionIva'].map(name=>[name,{value:''}]));
fields.cliente.value='UVM';fields.forma.value='99';fields.metodo.value='PPD';fields.rememberPayment.checked=true;
let body, saved,ok=true;
const catalogs={ok:true,regimes:[],cfdi_uses:[],payment_forms:[],payment_methods:[]};
const context=vm.createContext({document:{getElementById:id=>fields[id]},fiscalCatalogRequest:0,window:{registerInvoiceClient:(name,data)=>{saved={name,...data};fields.cliente.value=name;}},
  fetch:async(url,options)=>{if(options?.body)body=JSON.parse(options.body);return {ok,json:async()=>url.startsWith('/api/catalogos')?catalogs:{ok,cliente:'UVM'}};},
  replaceCatalog:(id,rows,wanted,fallback)=>{fields[id].value=wanted||fallback;},syncForma:()=>{if(fields.metodo.value==='PPD')fields.forma.value='99';}
});
vm.runInContext(extract('loadFiscalCatalogs')+'\n'+extract('persistClientFiscalData'),context);
(async()=>{
  await context.loadFiscalCatalogs('RFC',{regimen:'601',uso:'G03'});
  assert.equal(fields.metodo.value,'PPD');assert.equal(fields.forma.value,'99');
  await context.loadFiscalCatalogs('RFC',{metodo:'PUE',forma:'03'});
  assert.equal(fields.metodo.value,'PUE');assert.equal(fields.forma.value,'03');
  fields.metodo.value='PPD';fields.forma.value='99';
  await context.persistClientFiscalData();
  assert.equal(body.metodo_pago_default,'PPD');assert.equal(saved.metodo_pago_default,'PPD');
  fields.rememberPayment.checked=false;saved=null;
  await context.persistClientFiscalData();assert.equal(body.metodo_pago_default,undefined);assert.equal(saved,null);
  fields.rememberPayment.checked=true;ok=false;await context.persistClientFiscalData();assert.equal(saved,null);
  assert.match(html,/invoiceClientPaymentDefaults\(pageClientName\)/);
  console.log('OK: PPD/PUE se conserva al refrescar catálogos y guardado actualiza la copia del cliente; cotizaciones usan su preferencia.');
})().catch(error=>{console.error(error);process.exitCode=1;});
