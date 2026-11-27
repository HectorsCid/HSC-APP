/* Seguimiento de pólizas. No timbra ni envía al abrir una factura. */
window.HscPolicies=(()=>{
  const list=document.querySelector('#policyClients'),detail=document.querySelector('#policyDetail'),mail=document.querySelector('#policyMail');
  const base='/api/facturacion/polizas', enc=encodeURIComponent;
  const e=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let directory=null,key='',model=null,seq=0,view=0,serviceMonth='';
  async function api(url,opts){
    const res=await fetch(url,opts);const d=await res.json().catch(()=>({}));
    if(!res.ok||!d.ok)throw Error(d.error||'No se pudo consultar el expediente. Intenta nuevamente.');
    return d;
  }
  const post=(url,body)=>api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const path=()=>base+'/'+enc(key);
  const text=(host,msg)=>{host.textContent=msg;};
  function cards(){
    const q=document.querySelector('#q').value.trim().toLowerCase();
    const rows=directory.filter(c=>[c.name,c.rfc].join(' ').toLowerCase().includes(q));
    list.innerHTML=rows.map(c=>`<button class="policy-tile" data-key="${e(c.id)}">
      <span class="policy-logo">${c.photo?`<img loading="lazy" alt="" width="48" height="48" src="/api/operaciones/photo/client/${enc(c.id)}">`:e(c.name.slice(0,2).toUpperCase())}</span><strong>${e(c.name)}</strong><small>${c.policy_count?e(c.policy_count)+' póliza(s) por lote':'Historial de facturas'}${c.unconfirmed?' · '+e(c.unconfirmed)+' sin envío registrado':''}</small></button>`).join('')||(q?'Sin coincidencias.':'Todavía no hay pólizas. Crea la primera en Editar cliente → Configurar lote.');
    list.querySelectorAll('[data-key]').forEach(b=>b.onclick=()=>open(b.dataset.key));
    if(key&&!rows.some(c=>c.id===key))back();
  }
  function back(){++seq;key='';model=null;serviceMonth='';list.hidden=false;detail.hidden=true;detail.replaceChildren();document.querySelector('#policyBack').hidden=true;}
  document.querySelector('#policyBack').onclick=()=>{const id=key;back();[...list.querySelectorAll('[data-key]')].find(b=>b.dataset.key===id)?.focus();};
  async function show(force=false){
    const n=++view;
    try{
      if(!directory||force){text(list,'Cargando clientes…');const d=await api(base);if(n!==view)return;directory=d.clients;}
      const previous=key;cards();if(force&&key&&previous===key)await open(key);
    }catch(err){text(list,err.message);}
  }
  async function open(id){
    if(key!==id)serviceMonth='';
    key=id;model=null;const n=++seq;list.hidden=true;detail.hidden=false;document.querySelector('#policyBack').hidden=false;text(detail,'Consultando facturas…');
    try{const d=await api(path());if(n!==seq)return;model=d;render();}
    catch(err){if(n===seq)text(detail,err.message);}
  }
  const field=(label,name,value='',type='text')=>`<label>${label}<input class="cm-input" type="${type}" name="${name}" value="${e(value)}"></label>`;
  const check=(label,name,v)=>`<label><input type="checkbox" name="${name}" ${v?'checked':''}> ${label}</label>`;
  const delivery=i=>i.email_delivery?.sent?'Enviada · '+(i.email_delivery.last_sent_at||'').slice(0,10):'Por enviar';
  function render(){
    const {client:c,rules:r,invoices:all,templates}=model;
    const policy=all.filter(i=>i.policy&&(!serviceMonth||i.period===serviceMonth)).sort((a,b)=>b.period.localeCompare(a.period)||String(b.date||'').localeCompare(String(a.date||'')));
    const other=all.filter(i=>!i.policy);
    const configured=model.configured||[];
    const names=[...new Set([...configured.map(p=>p.name),...all.map(i=>i.policy).filter(Boolean)])];
    const today=new Date(), month=serviceMonth||today.getFullYear()+'-'+String(today.getMonth()+1).padStart(2,'0');
    function row(i){
      const canceled=invoiceLifecycle(i).blocksPayment;
      return `<div class="balance-pending-row"><div><strong>${e(i.policy?i.period+' · '+i.policy:'Factura '+i.folio)}</strong><small>Folio ${e(i.folio)} · ${e((i.date||'').slice(0,10))} · ${e(canceled?invoiceLifecycle(i).label:i.email_delivery?.sent?delivery(i):i.auto_note||(i.auto_state==='waiting'?'Automático pendiente':delivery(i)))}</small></div>
        <strong>${e(moneyMx(i.total))}</strong><div class="actions">
        ${i.policy&&!canceled?`<button class="btn btn-primary" data-mail="${e(i.id)}">${i.email_delivery?.sent?'Revisar envío':'Preparar envío'}</button>`:''}
        <a class="btn" href="/api/invoices/${enc(i.id)}/pdf" target="_blank">PDF</a>
        <button class="btn" data-link="${e(i.id)}">${i.policy?'Editar vínculo':'Vincular a póliza'}</button></div></div>`;
    }
    detail.innerHTML=`<h2>${e(c.name)}</h2>${configured.map(p=>`<p><strong>${e(p.name)}</strong> · ${e(p.branch)}</p>`).join('')}<details><summary>Configurar pólizas y correo</summary>
      <form id="policyRules" class="schedule-form-grid">${field('Para','email',r.email||c.email)}${field('CC','cc',r.cc)}
      ${field('Asunto · admite {folio}, {poliza} y {periodo}','subject',r.subject||'Factura {folio} · {poliza} · {periodo}')}
      <label>Mensaje<textarea class="cm-input" name="message">${e(r.message||'Buen día, adjunto la factura {folio} de {poliza}, correspondiente a {periodo}.')}</textarea></label>
      ${check('Requiere factura firmada','signed',r.signed)}${check('Requiere orden de compra','oc',r.oc)}
      ${check('Facturar el mes de servicio anterior','previous',r.previous)}
      <div class="wide">Envío por póliza (solo nuevas facturas; las anteriores no se envían al activar):
      ${names.map(n=>`<label style="display:block">${e(n)} <select data-policy-mode="${e(n)}"><option value="manual">Manual</option><option value="auto" ${r.automatic?.includes(n)?'selected':''}>Automático al completar</option></select></label>`).join('')}
      ${field('Clave de envío para activar automático (si el equipo no está autorizado)','send_key','','password')}</div>
      <button class="btn">Guardar configuración</button></form></details>
      <details><summary>Descargar relación por periodo</summary><form method="get" action="${path()}/relacion.csv">
      ${field('Desde','desde',month.slice(0,4)+'-01','month')}${field('Hasta','hasta',month,'month')}
      <button class="btn">Descargar CSV para Excel</button></form></details>
      <details open><summary>Facturas de póliza · ${policy.length}</summary><label>Mes de servicio <input type="month" id="policyMonth" value="${e(serviceMonth)}"></label>${policy.map(row).join('')||'<p>Sin facturas vinculadas para esta selección.</p>'}</details>
      <details><summary>Otras facturas · ${other.length}</summary>${other.map(row).join('')||'Sin otras facturas.'}</details>
      ${c.rfc?'':'<p>Vincula el RFC del cliente en Operaciones para consultar su facturación.</p>'}`;
    detail.querySelector('#policyMonth').onchange=ev=>{serviceMonth=ev.target.value;render();};
    const report=detail.querySelector('form[action]');
    report.onsubmit=async ev=>{
      ev.preventDefault();const p=new URLSearchParams(new FormData(report)),b=report.querySelector('button');
      if(!p.get('desde')||!p.get('hasta')||p.get('desde')>p.get('hasta')){alert('Selecciona el mes inicial y final en orden.');return;}
      b.disabled=true;b.textContent='Preparando relación…';
      try{
        const res=await fetch(report.action+'?'+p);
        if(!res.ok||!res.headers.get('Content-Type')?.includes('text/csv'))throw Error((await res.json().catch(()=>({}))).error||'No se pudo descargar la relación.');
        const url=URL.createObjectURL(await res.blob()),a=document.createElement('a');
        a.href=url;a.download='Relación '+c.name+' '+p.get('desde')+' a '+p.get('hasta')+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
      }catch(err){alert(err.message);}finally{b.disabled=false;b.textContent='Descargar CSV para Excel';}
    };
    detail.querySelector('#policyRules').onsubmit=async ev=>{
      ev.preventDefault();const f=ev.target,d=new FormData(f),id=key;
      try{await post(path()+'/rules',{...Object.fromEntries(d),signed:d.has('signed'),oc:d.has('oc'),previous:d.has('previous'),templates:d.getAll('tpl'),automatic:[...f.querySelectorAll('[data-policy-mode]')].filter(s=>s.value==='auto').map(s=>s.dataset.policyMode)});if(key===id)await open(id);}
      catch(err){alert(err.message);}
    };
    detail.querySelectorAll('[data-link]').forEach(b=>b.onclick=()=>edit(all.find(i=>i.id===b.dataset.link)));
    detail.querySelectorAll('[data-mail]').forEach(b=>b.onclick=()=>compose(all.find(i=>i.id===b.dataset.mail)));
  }
  function dialog(title,html){
    mail.innerHTML=`<div class="cm-head"><strong>${e(title)}</strong><button class="btn" data-close>Cerrar</button></div><div class="cm-body">${html}<p role="status"></p></div>`;
    mail.querySelector('[data-close]').onclick=()=>mail.close();
    if(!mail.open)mail.showModal();
  }
  function edit(i){
    const id=key,url=path()+'/link';
    const opts=(model.configured||[]).map(p=>[p.name,p.branch+' · '+p.name]);
    if(i.policy&&!opts.some(([n])=>n===i.policy))opts.push([i.policy,i.policy+' · histórica']);
    dialog('Vincular factura '+i.folio,`<form><label>Póliza del cliente<select name="policy" required><option value="">Elige una póliza</option>${opts.map(([n,label])=>`<option value="${e(n)}" ${n===i.policy?'selected':''}>${e(label)}</option>`).join('')}</select></label>${!opts.length?'<p>Crea una póliza en Editar cliente → Configurar lote.</p>':''}${field('Mes del servicio','period',i.period||i.date.slice(0,7),'month')}
      ${model.rules.automatic?.length&&!i.email_delivery?.sent?check('Aplicar envío automático a esta factura si la póliza lo permite','queue',false):''}
      <button class="btn btn-primary">Guardar vínculo</button>${i.policy?'<button type="button" class="btn" data-remove>Quitar vínculo</button>':''}</form>`);
    async function save(unlink){
      try{const d=new FormData(mail.querySelector('form'));await post(url,{id:i.id,...Object.fromEntries(d),queue:d.has('queue'),unlink});mail.close();await open(id);}
      catch(err){text(mail.querySelector('[role=status]'),err.message);}
    }
    mail.querySelector('form').onsubmit=ev=>{ev.preventDefault();save(false);};
    const rm=mail.querySelector('[data-remove]');if(rm)rm.onclick=()=>save(true);
  }
  let composer=0;
  async function compose(i){
    const n=++composer,id=key,url=path()+'/'+enc(i.id);
    dialog('Factura '+i.folio,'Preparando el correo…');
    try{
      const [d,status]=await Promise.all([api(url+'/packet'),api('/api/email/status')]);
      if(!mail.open||n!==composer||key!==id)return;
      const p=d.packet, fill=s=>s.replace(/\{(folio|poliza|periodo)\}/g,(_,k)=>({folio:i.folio,poliza:p.policy,periodo:p.period})[k]);
      dialog('Enviar factura '+i.folio,`${i.email_delivery?.sent?`<p>Último envío: ${e(i.email_delivery.last_sent_at)} · ${e((i.email_delivery.recipients||[]).join(', '))}</p>`:''}<p>PDF y XML incluidos · ${e(p.policy)} · ${e(p.period)}</p>
      <p>${e(p.warning|| (p.oc?'OC incluida: '+p.oc:p.required?'Falta la orden de compra.':'Sin orden de compra vinculada.'))}</p>
      <details ${p.required&&!p.oc?'open':''}><summary>${p.oc?'Cambiar OC':'Vincular OC'}</summary><form id="uploadPolicyOC">
      <input type="file" name="file" accept="application/pdf" required><select name="scope"><option value="invoice">Solo esta factura</option><option value="month">Esta póliza y mes</option><option value="year">Esta póliza todo el año</option></select>
      <button class="btn">Guardar OC</button></form></details>
      ${i.auto_state==='waiting'&&p.signed?`<form id="autoSigned"><label>Factura firmada (PDF)<input name="file" type="file" accept="application/pdf" required></label><button class="btn">Guardar para envío automático</button></form>`:''}
      <form id="sendPolicyMail" class="schedule-form-grid">${field('Para','email',p.email||i.customer_email)}${field('CC','cc',p.cc)}
      ${field('Asunto','subject',fill(p.subject||'Factura {folio} · {poliza} · {periodo}'))}
      <label>Mensaje<textarea name="message" class="cm-input">${e(fill(p.message||'Buen día, adjunto la factura {folio} de {poliza}, correspondiente a {periodo}.'))}</textarea></label>
      ${p.signed?'<label>Factura firmada (PDF)<input name="signed_pdf" type="file" accept="application/pdf" required></label>':''}
      ${status.trusted?'':field('Clave de envío','send_key','','password')}
      <button class="btn btn-primary" ${p.required&&!p.oc?'disabled':''}>${i.email_delivery?.sent?'Volver a enviar':'Enviar correo'}</button></form>`);
      if(['waiting','sending'].includes(i.auto_state))mail.querySelector('#sendPolicyMail').hidden=true;
      const signed=mail.querySelector('#autoSigned');
      if(signed)signed.onsubmit=async ev=>{
        ev.preventDefault();const b=signed.querySelector('button');b.disabled=true;
        try{await api(url+'/signed',{method:'POST',body:new FormData(signed)});mail.close();await show(true);}
        catch(err){text(mail.querySelector('[role=status]'),err.message);b.disabled=false;}
      };
      mail.querySelector('#uploadPolicyOC').onsubmit=async ev=>{
        ev.preventDefault();const f=ev.target,b=f.querySelector('button');b.disabled=true;
        try{await api(url+'/oc',{method:'POST',body:new FormData(f)});await compose(i);}
        catch(err){text(mail.querySelector('[role=status]'),err.message);}finally{b.disabled=false;}
      };
      mail.querySelector('#sendPolicyMail').onsubmit=async ev=>{
        ev.preventDefault();const f=ev.target,form=new FormData(f),b=f.querySelector('button');
        form.set('policy_client',id);form.set('policy_token',p.token);form.set('folio',i.folio||i.uuid);
        if(!String(form.get('email')).trim()){text(mail.querySelector('[role=status]'),'Indica el destinatario.');return;}
        b.disabled=true;b.textContent='Enviando…';mail.querySelector('[data-close]').disabled=true;mail.oncancel=evt=>evt.preventDefault();
        try{const sent=await api('/api/invoices/'+enc(i.id)+'/email',{method:'POST',body:form});mail.close();pageMsg.className='flash ok';pageMsg.textContent=sent.message;await show(true);}
        catch(err){text(mail.querySelector('[role=status]'),err.message);b.disabled=false;b.textContent='Enviar correo';}
        finally{mail.oncancel=null;mail.querySelector('[data-close]').disabled=false;}
      };
    }catch(err){if(n===composer&&mail.open)text(mail.querySelector('[role=status]'),err.message);}
  }
  document.querySelector('#policyReload').onclick=()=>show(true);
  return {show};
})();
