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
    const preview = document.createElement('dialog');
    preview.className = 'scheduled-mail-preview';
    preview.setAttribute('aria-labelledby', `${message.id}-preview-title`);
    preview.innerHTML = `<header>
      <h2 id="${message.id}-preview-title">Mensaje guardado</h2>
      <button type="button" autofocus>Cerrar</button>
    </header>
    <div class="scheduled-mail-preview-body">
      <div data-saved-text tabindex="0"></div>
      <div data-editor hidden>
        <label>Mensaje<textarea rows="8"></textarea></label>
        <label data-preview-auth hidden>Clave privada de envío<input type="password" autocomplete="off"></label>
      </div>
    </div>
    <footer>
      <small role="status"></small>
      <button type="button" data-edit>Modificar mensaje</button>
      <button type="button" data-save hidden>Guardar cambios</button>
    </footer>`;
    const previewBody = preview.querySelector('.scheduled-mail-preview-body');
    const previewText = preview.querySelector('[data-saved-text]');
    const previewEditor = preview.querySelector('[data-editor]');
    const previewMessage = previewEditor.querySelector('textarea');
    const previewAuth = preview.querySelector('[data-preview-auth]');
    const previewKey = previewAuth.querySelector('input');
    const previewStatus = preview.querySelector('[role="status"]');
    const editButton = preview.querySelector('[data-edit]');
    const saveButton = preview.querySelector('[data-save]');
    const closeButton = preview.querySelector('header button');
    let activeJob, savingMessage = false;
    closeButton.addEventListener('click', () => preview.close());
    preview.addEventListener('cancel', event => {
      if (savingMessage) event.preventDefault();
    });
    preview.addEventListener('close', () => { previewKey.value = ''; });
    document.body.append(preview);
    function showSavedText(job) {
      previewText.textContent = job?.message ?? 'El envío ya no está pendiente.';
      previewText.hidden = false;
      previewEditor.hidden = true;
      previewAuth.hidden = true;
      previewKey.value = '';
      editButton.hidden = job?.state !== 'pending';
      saveButton.hidden = true;
      previewBody.scrollTop = 0;
    }
    function showMessage() {
      if (!message.readOnly || preview.open || editing) return;
      previewStatus.textContent = '';
      showSavedText(activeJob);
      preview.showModal();
    }
    editButton.addEventListener('click', () => {
      previewMessage.value = activeJob.message;
      previewText.hidden = true;
      previewEditor.hidden = false;
      editButton.hidden = true;
      saveButton.hidden = false;
      previewStatus.textContent = '';
      previewMessage.focus();
      previewMessage.setSelectionRange(0, 0);
      previewMessage.scrollTop = 0;
    });
    saveButton.addEventListener('click', async () => {
      if (savingMessage || activeJob?.state !== 'pending') return;
      if (!previewMessage.value.trim()) {
        previewStatus.textContent = 'Escribe el mensaje del correo.';
        previewMessage.focus();
        return;
      }
      const job = activeJob;
      const current = revision;
      savingMessage = true;
      closeButton.disabled = saveButton.disabled = previewMessage.readOnly = true;
      previewStatus.textContent = 'Guardando cambios…';
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            action: 'edit_message', id: job.id, message: previewMessage.value,
            send_key: previewKey.value || sendKey.value
          })
        });
        const data = await response.json();
        if (current !== revision) return;
        if (response.status === 403) {
          previewAuth.hidden = false;
          previewKey.focus();
        }
        if (Array.isArray(data.items)) {
          render(data.items);
          showSavedText(activeJob?.id === job.id ? activeJob : null);
          closeButton.disabled = false;
          closeButton.focus();
        }
        previewStatus.textContent = response.ok && data.ok && Array.isArray(data.items)
          ? 'Mensaje actualizado.'
          : data.error || 'No se pudo confirmar el cambio.';
      } catch (_) {
        if (current === revision) previewStatus.textContent = 'No se pudo confirmar el cambio. Puedes volver a intentar.';
      } finally {
        savingMessage = false;
        closeButton.disabled = saveButton.disabled = previewMessage.readOnly = false;
      }
    });
    message.addEventListener('click', showMessage);
    message.addEventListener('keydown', event => {
      if (!message.readOnly || !['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      showMessage();
    });
    let draftMessage;
    let url='', revision=0, available=false, editing=false, originalLabel='', confirming='';
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
      activeJob = items.find(job=>['pending','sending'].includes(job.state));
      if (typeof activeJob?.message === 'string') {
        if (!message.readOnly) draftMessage = message.value;
        if (message.value !== activeJob.message || !message.readOnly) {
          message.value = activeJob.message;
          message.scrollTop = 0;
        }
        message.readOnly = true;
        message.setAttribute('aria-haspopup', 'dialog');
        messageLabel.textContent = 'Mensaje guardado del envío programado';
      } else if (message.readOnly) {
        message.value = draftMessage;
        message.readOnly = false;
        message.removeAttribute('aria-haspopup');
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
        if (preview.open) preview.close();
        message.readOnly = false;
        message.removeAttribute('aria-haspopup');
        messageLabel.textContent = originalMessageLabel;
        activeJob = undefined;
        available=false;editing=false;confirming='';box.hidden=true;mode.value='now';time.value='';jobs.replaceChildren();note.textContent='';
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
        if(activeJob)throw Error('Este folio ya tiene un correo pendiente o en proceso. Cancélalo antes de iniciar otro envío.');
        if(!available||mode.value!=='later')return false;
        if(!time.value){time.focus();throw Error('Selecciona la fecha y hora del envío.');}
        form.append('scheduled_for',time.value);
        return true;
      }
    };
  }
  window.HscScheduledEmail={mount};
})();
