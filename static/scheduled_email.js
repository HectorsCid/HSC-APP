(() => {
  const zone = 'America/Mexico_City';
  const calendar = new Intl.DateTimeFormat('en-CA', {timeZone:zone, year:'numeric', month:'2-digit', day:'2-digit'});
  function tomorrow(hour) {
    const parts = Object.fromEntries(calendar.formatToParts(new Date()).map(({type,value})=>[type,value]));
    const day = new Date(Date.UTC(+parts.year,+parts.month-1,+parts.day+1));
    return `${day.toISOString().slice(0,10)}T${hour}:00`;
  }
  const labels = {pending:'Pendiente',sending:'Enviando',sent:'Enviado',cancelled:'Cancelado',uncertain:'Sin confirmación: revisa Enviados antes de reintentar'};

  function mount({anchor, send, sendKey, immediate}) {
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
    let url='', revision=0, available=false, editing=false, originalLabel='', active=false;
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
    function render(items) {
      active=items.some(job=>['pending','sending'].includes(job.state));
      jobs.replaceChildren();
      for(const job of items){
        const row=document.createElement('div');
        row.textContent=`${labels[job.state] || job.state} · ${job.when} · ${job.recipient}`;
        jobs.append(row);
        if(job.state!=='pending')continue;
        for(const [action, title] of [['reschedule','Cambiar hora'],['cancel','Cancelar envío']]){
          const button=document.createElement('button');button.type='button';button.textContent=title;
          button.onclick=async()=>{
            if(editing || send.disabled)return;
            if(action==='reschedule' && (mode.value!=='later' || !time.value)){
              mode.value='later';label();time.focus();note.textContent='Elige la nueva hora y vuelve a pulsar Cambiar hora.';return;
            }
            if(!window.confirm(action==='cancel'?'¿Cancelar este correo programado?':'¿Cambiar la fecha y hora de este correo programado?'))return;
            const current=revision;
            editing=true;send.disabled=true;
            try{
              const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,id:job.id,when:time.value,send_key:sendKey.value})});
              const data=await response.json();
              if(current!==revision)return;
              if(!response.ok||!data.ok)throw Error(data.error||'No se pudo modificar el envío.');
              render(data.items);note.textContent=action==='cancel'?'Envío cancelado.':'Horario actualizado.';
              mode.value='now';time.value='';
            }catch(error){if(current===revision)note.textContent=error.message;}
            finally{if(current===revision){editing=false;send.disabled=false;label();}}
          };
          row.append(' ',button);
        }
      }
    }
    return {
      async open(kind,id){
        const current=++revision;
        available=false;editing=false;active=false;box.hidden=true;mode.value='now';time.value='';jobs.replaceChildren();note.textContent='';
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
