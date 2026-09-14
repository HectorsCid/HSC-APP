const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const handlers={},calls=[];
const partner={url:'https://hsc.test/hsc-partner/',navigate:async url=>calls.push(['partner',url]),focus:()=>calls.push(['focus'])};
const admin={url:'https://hsc.test/hsc-tecnico/',navigate:async url=>calls.push(['admin',url]),focus:()=>calls.push(['focus'])};
const self={location:{origin:'https://hsc.test'},addEventListener:(key,fn)=>handlers[key]=fn,
  clients:{matchAll:async()=>[partner,admin],openWindow:async url=>calls.push(['new',url])}};
vm.runInNewContext(fs.readFileSync('static/service-worker.js','utf8'),{self,URL,Set});
(async()=>{
  let pending;
  handlers.notificationclick({notification:{close(){},data:{url:'/hsc-tecnico/?client=B&fault=F1'}},waitUntil:p=>pending=p});
  await pending;
  assert.equal(calls[0][0],'admin');assert.match(calls[0][1],/client=B&fault=F1/);
  calls.length=0;
  handlers.notificationclick({notification:{close(){},data:{url:'https://other.test/'}},waitUntil:p=>pending=p});
  assert.equal(calls.length,0);
  console.log('OK: abrir el aviso conserva la app destinataria y rechaza destinos externos.');
})().catch(error=>{console.error(error);process.exitCode=1});
