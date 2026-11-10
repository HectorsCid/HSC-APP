(function(root){
  'use strict';
  const today=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'America/Mexico_City',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  function contactOptions(emails){
    const seen=new Set(), options=[];
    const add=(value,label)=>{
      for(const email of String(value||'').split(/[,;\n]+/).map(item=>item.trim()).filter(Boolean)){
        const key=email.toLowerCase();if(seen.has(key))continue;
        seen.add(key);options.push({email,label});
      }
    };
    add(emails.complementos,'Preferido para complementos');
    for(const contact of emails.contactos||[])add(contact.correo,contact.nombre||'Contacto del cliente');
    add(emails.cuentas_pagar,'Cuentas por pagar');add(emails.compras,'Compras');add(emails.frecuentes,'Correo frecuente');
    return options;
  }
  async function json(url,body){
    const response=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok||!data.ok)throw Object.assign(Error(typeof data.error==='string'?data.error:'No se pudo confirmar la operación.'),{status:response.status,stage:data.stage});
    return data;
  }
  async function execute(state, fields, request=json){
    if(!state.rep){
      const {invoice_id,amount,rfc,date}=state;
      state.rep=await request('/api/pagos/crear-rapido',{...fields,invoice_id,amount,rfc,expected_date:date});
    }
    const folio=state.rep.uuid;
    return request(`/api/invoices/${encodeURIComponent(state.rep.id)}/email`,{
      email:fields.email,cc:fields.cc,send_key:fields.send_key,folio,
      subject:`Complemento de pago HSC · Factura ${state.folio||state.invoice_id}`,
      message:'Buen día. Enviamos el complemento de pago correspondiente. Se adjuntan el PDF y XML. Gracias.\n\nHSC Refrigeración'
    });
  }
  function mount({refresh,notice}){
    const $=id=>document.getElementById(id), list=$('issuedPendingList'), modal=$('quickPayment'),form=$('quickPaymentForm'),submit=$('quickPaymentConfirm'),close=$('quickPaymentClose'),status=$('quickPaymentStatus'),keyLabel=$('quickPaymentKeyLabel'),contacts=$('quickPaymentContacts');
    const field=name=>form.elements.namedItem(name);
    const contactPicker=$('quickPaymentContactPicker');
    function showContacts(options,message){
      contacts.replaceChildren();
      if(!options.length){contacts.textContent=message;return;}
      for(const {email,label} of options){
        const button=document.createElement('button');button.type='button';button.className='btn';button.textContent=`${label} · ${email}`;
        button.onclick=()=>{field('email').value=email;contactPicker.open=false;field('email').focus();};
        contacts.append(button);
      }
    }
    const attempts=new Map();
    let active=null,busy=false,trigger=null;
    const describe=state=>`${state.client} · Factura ${state.folio||state.invoice_id} · ${Number(state.amount).toLocaleString('es-MX',{style:'currency',currency:'MXN'})} MXN · ${state.date} (Ciudad de México)`;
    const setBusy=value=>{busy=value;close.disabled=value;submit.disabled=value;};
    function dismiss(){
      if(busy)return;
      const refreshNeeded=active?.rep||active?.uncertain;
      field('send_key').value='';active=null;modal.classList.remove('show');
      if(refreshNeeded)refresh();else trigger?.focus();
    }
    close.onclick=dismiss;
    modal.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();dismiss();}};
    list.addEventListener('click',async event=>{
      const button=event.target.closest('[data-quick-payment]');if(!button||busy||active)return;
      const {quickPayment:invoice_id,rfc,client,folio,amount}=button.dataset;
      const state=attempts.get(invoice_id)||{invoice_id,rfc,client,folio,amount,date:today()};active=state;trigger=button;
      form.reset();contactPicker.open=false;showContacts([],'Consultando contactos…');field('method').disabled=Boolean(state.rep);
      field('email').value=state.email||'';field('cc').value=state.cc||'';field('remember').checked=Boolean(state.remember);
      $('quickPaymentSummary').textContent=describe(state);keyLabel.hidden=true;
      submit.textContent=state.rep?'Reintentar solo correo':'Timbrar y enviar';submit.disabled=true;
      status.textContent=state.uncertain?'No se confirmó el timbrado. Revisa Emitidas o Facturama antes de volver a emitir.':'Consultando correo del cliente…';
      modal.classList.add('show');close.focus();
      if(state.uncertain)return;
      try{
        const mail=await json('/api/email/status');if(active!==state)return;
        if(!mail.configured)throw Error('El correo de salida no está configurado. Usa el formulario normal si solo quieres timbrar.');
        keyLabel.hidden=mail.trusted;field('send_key').required=!mail.trusted;
        if(mail.trusted){
          const data=await json('/api/clientes/contactos-seleccionado',{nombre:client,rfc}).catch(()=>null);if(active!==state)return;
          const emails=data?.correos||{};
          if(!field('email').value)field('email').value=emails.complementos||emails.cuentas_pagar||'';
          showContacts(contactOptions(emails),data?'No hay correos guardados para este cliente. Puedes escribir uno.':'No se pudieron consultar los contactos. Puedes escribir un correo.');
        }else showContacts([],'Los contactos guardados se muestran en navegadores autorizados. Por ahora puedes escribir el correo manualmente.');
        status.textContent=state.rep?`Complemento ya timbrado (${state.rep.uuid}). Revisa Enviados antes de reintentar si no se confirmó el correo.`:'';
        submit.disabled=false;
      }catch(error){if(active===state){status.textContent=error.message;showContacts([],'No se pudieron consultar los contactos.');}}
    });
    form.onsubmit=async event=>{
      event.preventDefault();if(!active||busy||submit.disabled)return;
      const state=active, fields={email:field('email').value.trim(),cc:field('cc').value.trim(),payment_form:field('method').value,send_key:field('send_key').value};
      const addresses=[fields.email,fields.cc].join(',').split(/[,;\n]+/).map(s=>s.trim()).filter(Boolean);
      if(!fields.email||addresses.some(s=>!/^\S+@\S+\.\S+$/.test(s))){status.textContent='Revisa los correos en Para y CC.';field('email').focus();return;}
      if(!state.rep&&state.date!==today()){status.textContent='Cambió el día. Cierra y vuelve a abrir para revisar la fecha.';return;}
      Object.assign(state,{email:fields.email,cc:fields.cc,remember:field('remember').checked});
      setBusy(true);status.textContent=state.rep?'Enviando el complemento ya timbrado…':'Timbrando y enviando el complemento…';
      try{
        const result=await execute(state,fields);
        attempts.delete(state.invoice_id);
        let preference='';
        if(state.remember){try{await json('/api/clientes/correo-complementos',{rfc:state.rfc,email:fields.email});}catch(_){preference=' No se pudo guardar el destinatario preferido.';}}
        setBusy(false);dismiss();notice(`Complemento timbrado y enviado a ${fields.email}.${result.sent_copy_saved===false?' No se confirmó la copia en Enviados.':''}${state.rep.warning?' '+state.rep.warning:''}${preference}`);
      }catch(error){
        setBusy(false);
        if(error.status===403){keyLabel.hidden=false;field('send_key').required=true;}
        if(state.rep){
          attempts.set(state.invoice_id,state);field('method').disabled=true;
          status.textContent=`Complemento ya timbrado (${state.rep.uuid}). No se confirmó el correo: ${error.message} Revisa Enviados antes de reintentar.`;
          submit.textContent='Reintentar solo correo';
        }else if(!error.status||error.stage==='stamp_payment'||error.status>=500){
          state.uncertain=true;attempts.set(state.invoice_id,state);submit.disabled=true;
          status.textContent=`${error.message} No vuelvas a timbrar sin comprobar si ya se emitió.`;
        }else status.textContent=error.message;
      }
    };
  }
  if(typeof module!=='undefined')module.exports={execute,today,contactOptions};else root.HscQuickPayment={mount};
})(typeof window!=='undefined'?window:globalThis);
