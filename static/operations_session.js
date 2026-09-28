/* The cached shell contains no identity. Only the last active local account can open it. */
(function(){
  const key='hsc-offline-session-v1',body=document.body,offline=body.dataset.offlineShell==='true';
  let ready=true;
  try{
    if(offline){
      const saved=JSON.parse(localStorage.getItem(key)||'null');
      if(!saved?.userId || saved.appKind!==body.dataset.appKind)throw Error('No active offline account');
      Object.assign(body.dataset,saved);
    }else{
      const fields=['userId','userName','operationsRole','operationsOwner','pwaInstallDisabled','appKind','clientId','permissions'];
      const saved=Object.fromEntries(fields.map(field=>[field,body.dataset[field]||'']));
      localStorage.setItem(key,JSON.stringify(saved));
    }
  }catch(error){
    if(offline){
      ready=false;body.textContent='Conéctate e inicia sesión una vez para preparar este dispositivo. Tus pendientes se conservan.';
      body.style.cssText='padding:32px;font-family:system-ui';
    }
  }
  window.HscOperationsSession={ready,clear:()=>localStorage.removeItem(key)};
  const originalFetch=window.fetch.bind(window);
  window.fetch=function(input,options={}){
    const url=new URL(typeof input==='string'||input instanceof URL?input:input.url,location.href);
    if(url.origin===location.origin&&url.pathname.startsWith('/api/operaciones/')){
      const headers=new Headers(options.headers||input.headers||{});
      headers.set('X-HSC-Account',body.dataset.userId||'');
      options={...options,headers};
    }
    return originalFetch(input,options);
  };
})();
