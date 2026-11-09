(() => {
  const zone = 'America/Mexico_City';
  const calendar = new Intl.DateTimeFormat('en-CA', {timeZone:zone, year:'numeric', month:'2-digit', day:'2-digit'});
  function tomorrow(hour) {
    const parts = Object.fromEntries(calendar.formatToParts(new Date()).map(({type,value})=>[type,value]));
    const day = new Date(Date.UTC(+parts.year,+parts.month-1,+parts.day+1));
    return `${day.toISOString().slice(0,10)}T${hour}:00`;
  }
  const labels = {pending:'Pendiente',sending:'Enviando',sent:'Enviado',uncertain:'Sin confirmación: revisa Enviados antes de reintentar'};

  function mount({anchor, send, sendKey, message, immediate}) {
    const box = document.createElement('section');
    box.className = 'scheduled-mail';
    box.hidden = true;
    box.innerHTML = `<label>Momento de envío
      <select><option value="now">Enviar ahora</option><option value="later">Programar para…</option></select></label>
      <div data-time hidden><label>Fecha y hora (Ciudad de México)<input type="datetime-local" step="60"></label>
      <div><button type="button" data-hour="07">Mañana 7:00 a. m.</button> <button type="button" data-hour="08">Mañana 8:00 a. m.</button></div></div>
      <div data-jobs></div><small role="status"></small>`;
    anchor.before(box);
    const mode=box.querySelector('select'), time=box.querySelector('input'), jobs=box.querySelector('[data-jobs]'), note=box.querySelector('small');
    const messageLabel = message.labels[0];
    const originalMessageLabel = messageLabel.textContent;
    let draftMessage;
    let url='', revision=0, available=false, editing=false, originalLabel='', active=false, confirming='';
    const label=()=>{
      if(!send.disabled){
        if(mode.value==='later')send.textContent='Programar envío';
        else if(originalLabel)send.innerHTML=originalLabel;
        else send.textContent=immediate;
      }
      box.querySelector('[data-time]').hidden=mode.value!=='later';
    };
    mode.onchange=label;
    box.querySelectorAll('[data-hour]').forEach(button=>button.onclick=()=>{time.value=tomorrow(button.dataset.hour);});
    function actionButton(title, run) {
      const button=document.createElement('button');
      button.type='button';button.textContent=title;button.onclick=run;
      return button;
    }
    async function change(action, job, items) {
      if(editing || send.disabled)return;
      const current=revision;
      editing=true;send.disabled=true;
      render(items);
      note.textContent=action==='cancel'?'Cancelando el envío…':'Actualizando el horario…';
      try{
        const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,id:job.id,when:time.value,send_key:sendKey.value})});
        const data=await response.json();
        if(current!==revision)return;
        if(Array.isArray(data.items)){
          confirming='';
          items=data.items;
        }
        if(!response.ok||!data.ok||!Array.isArray(data.items))throw Error(data.error||'No se pudo confirmar el cambio.');
        note.textContent=action==='cancel'?'Cancelación guardada. No necesitas hacer nada más.':'Horario actualizado.';
        mode.value='now';time.value='';
      }catch(error){
        if(current===revision)note.textContent=error instanceof TypeError || error instanceof SyntaxError
          ?'No se pudo confirmar el cambio. Vuelve a abrir esta ventana para consultar el estado.'
          :error.message;
      }finally{
        if(current===revision){editing=false;send.disabled=false;render(items);label();}
      }
    }
    function render(items) {
      const activeJob = items.find(job=>['pending','sending'].includes(job.state));
      active = Boolean(activeJob);
      if (typeof activeJob?.message === 'string') {
        if (!message.readOnly) draftMessage = message.value;
        message.value = activeJob.message;
        message.readOnly = true;
        messageLabel.textContent = 'Mensaje guardado del envío programado';
      } else if (message.readOnly) {
        message.value = draftMessage;
        message.readOnly = false;
        messageLabel.textContent = originalMessageLabel;
      }
      jobs.replaceChildren();
      for(const job of items){
        const row=document.createElement('div');
        row.textContent=job.state==='cancelled'
          ?`Envío cancelado. Este correo no se enviará. Estaba programado para el ${job.when} · ${job.recipient}`
          :`${labels[job.state] || job.state} · ${job.when} · ${job.recipient}`;
        jobs.append(row);
        if(job.state!=='pending' || editing)continue;
        if(confirming===job.id){
          const prompt=document.createElement('p');
          prompt.textContent='¿Quieres cancelar este correo programado?';
          const confirm=actionButton('Sí, cancelar envío',()=>change('cancel',job,items));
          const keep=actionButton('Conservar envío',()=>{
            confirming='';note.textContent='';render(items);
            jobs.querySelector('[data-cancel]')?.focus();
          });
          keep.dataset.keep='';
          row.append(prompt,confirm,' ',keep);
          continue;
        }
        const reschedule=actionButton('Cambiar hora',()=>{
          if(send.disabled)return;
          if(mode.value!=='later' || !time.value){
            mode.value='later';label();time.focus();note.textContent='Elige la nueva hora y vuelve a pulsar Cambiar hora.';return;
          }
          if(window.confirm('¿Cambiar la fecha y hora de este correo programado?'))change('reschedule',job,items);
        });
        const cancel=actionButton('Cancelar envío',()=>{
          if(send.disabled)return;
          confirming=job.id;note.textContent='';render(items);
          jobs.querySelector('[data-keep]')?.focus();
        });
        cancel.dataset.cancel='';
        row.append(' ',reschedule,' ',cancel);
      }
    }
    return {
      async open(kind,id){
        const current=++revision;
        message.readOnly = false;
        messageLabel.textContent = originalMessageLabel;
        available=false;editing=false;active=false;confirming='';box.hidden=true;mode.value='now';time.value='';jobs.replaceChildren();note.textContent='';
        originalLabel=send.innerHTML;label();
        if(!id)return;
        url=`/api/scheduled-email/${kind}/${encodeURIComponent(id)}`;
        try{
          const response=await fetch(url,{cache:'no-store'}), data=await response.json();
          if(current!==revision)return;
          available=response.ok&&data.ok&&data.available;
          box.hidden=!available;
          if(available)render(data.items);
        }catch(_){ /* Sending immediately remains available. */ }
      },
      append(form){
        if(active)throw Error('Este folio ya tiene un correo pendiente o en proceso. Cancélalo antes de iniciar otro envío.');
        if(!available||mode.value!=='later')return false;
        if(!time.value){time.focus();throw Error('Selecciona la fecha y hora del envío.');}
        form.append('scheduled_for',time.value);
        return true;
      }
    };
  }
  window.HscScheduledEmail={mount};
})();
